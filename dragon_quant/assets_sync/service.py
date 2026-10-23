"""push / pull / status / verify 编排（普通函数 + 共享 helper）。

M6 起按**分区**处理（``date`` / ``period`` / ``period_raw`` / ``static``）：
  - push 按 (分区, source) 判定新鲜度，新鲜源复用远端条目（0 本地读）；
  - pull 对 ``merge_granularity="row"`` 的新表做行级合并，prep/auction 保持分片级门禁；
  - status 增加 ``dates_drift`` 弱信号与 ``tables`` 覆盖图。

返回统一 dict：``{"ok", "fatal", "failed", "summary"}``：
  - ``fatal=True`` 表示配置/传输/正式 manifest 不可读等阻断级错误 → CLI 退出 2；
  - ``ok=False`` 且非 fatal（部分分片失败、收缩被拒、pull 冲突）→ CLI 退出 1；
  - ``drift`` 是弱信号，不计入 ``ok``。
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from datetime import date, timedelta

from dragon_quant._version import __version__
from dragon_quant.assets_sync import codec, manifest as manifest_mod, tables
from dragon_quant.assets_sync.backends.s3 import content_md5_b64
from dragon_quant.assets_sync.errors import (
    AmbiguousError,
    IntegrityError,
    NotFoundError,
    StorageError,
)
from dragon_quant.assets_sync.models import AssetShard, RestoreReport, SyncReport
from dragon_quant.assets_sync.sources import sources_by_name
from dragon_quant.assets_sync.state import (
    ProcessLock,
    default_paths,
    load_state,
    now_iso,
    save_state,
)

CODEC_VERSION = 2
DEFAULT_DATE_LIMIT = 30
_MIN_FREE_BYTES = 16 * 1024 * 1024
WARN_SHARD_BYTES = 16 * 1024 * 1024
MAX_SHARD_BYTES = 32 * 1024 * 1024


def _result(ok: bool, fatal: bool, failed: list, summary: dict) -> dict:
    summary = dict(summary)
    summary["fatal"] = bool(fatal)
    return {"ok": bool(ok), "fatal": bool(fatal), "failed": list(failed),
            "summary": summary}


def _calendar_dates(date_from: str, date_to: str) -> list[str]:
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    out = []
    while start <= end:
        out.append(start.isoformat())
        start += timedelta(days=1)
    return out


def _remote_manifest_partitions(backend) -> set[str]:
    partitions = set()
    for meta in backend.list("manifests/"):
        name = meta.key
        if not name.startswith("manifests/") or not name.endswith(".json"):
            continue
        value = name[len("manifests/"): -len(".json")]
        if codec.is_partition(value):
            partitions.add(value)
    return partitions


def _diff_keys(local: dict[str, str], remote_manifest) -> list[str]:
    out = []
    for key in sorted(set(local) | set(remote_manifest.assets)):
        entry = remote_manifest.assets.get(key)
        if entry is None or local.get(key) != entry.sha256:
            out.append(key)
    return out


def _load_blob(entry, blob: bytes) -> bytes:
    if entry.blob_size and len(blob) != entry.blob_size:
        raise IntegrityError(f"blob 大小不一致: {entry.blob}")
    blob_sha, _ = codec.hashes(blob)
    if entry.blob_sha256 and blob_sha != entry.blob_sha256:
        raise IntegrityError(f"blob 传输哈希不一致: {entry.blob}")
    try:
        content = codec.decompress(blob)
    except OSError as exc:
        raise IntegrityError(f"blob 解压失败: {entry.blob}: {exc}") from exc
    sha256, md5 = codec.hashes(content)
    if sha256 != entry.sha256:
        raise IntegrityError(f"内容 sha256 不一致: {entry.blob}")
    if entry.md5 and md5 != entry.md5:
        raise IntegrityError(f"内容 md5 不一致: {entry.blob}")
    if entry.size and len(content) != entry.size:
        raise IntegrityError(f"内容大小不一致: {entry.blob}")
    return content


def _sample(items: list, count: int) -> list:
    if count <= 0:
        return []
    if len(items) <= count:
        return list(items)
    if count == 1:
        return [items[0]]
    step = (len(items) - 1) / (count - 1)
    indexes = sorted({int(round(index * step)) for index in range(count)})
    return [items[index] for index in indexes]


def _created_signature(manifest) -> dict:
    return {
        key: (
            entry.created_at,
            tuple(sorted((str(k), str(v)) for k, v in (entry.created_at_map or {}).items())),
        )
        for key, entry in manifest.assets.items()
    }


def _put_manifest(backend, manifest) -> None:
    body = codec.manifest_to_bytes(manifest)
    backend.put(
        codec.manifest_key(manifest.partition),
        body,
        manifest_mod.manifest_metadata(manifest),
        content_md5=content_md5_b64(codec.hashes(body)[1]),
    )


def _shard_row_count(shard: AssetShard) -> int | None:
    """表分片的行数诊断（legacy 分片返回 None）。"""
    try:
        payload = json.loads(shard.content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(payload, dict) and "table" in payload:
        rows = payload.get("rows")
        if isinstance(rows, list):
            return len(rows)
    return None


def _excluded_periods(partitions, date_from, date_to) -> list[dict]:
    """显式给了 ``--from/--to`` 时，被区间排除的 period 分区（供人读提示）。"""
    if not date_from and not date_to:
        return []
    out = []
    for partition in partitions:
        try:
            info = codec.parse_partition(partition)
        except ValueError:
            continue
        if info.kind not in ("period", "period_raw"):
            continue
        if info.sort_date and not codec.partition_in_range(partition, date_from, date_to):
            out.append({"partition": partition, "sort_date": info.sort_date})
    return sorted(out, key=lambda item: (item["sort_date"], item["partition"]))


def _source_fresh(state: dict, source, partition: str, remote, watermarks: dict,
                  volatile: dict) -> bool:
    """按 (分区, source) 判新鲜：volatile 永不短路，且要求远端条目集合 ⊇ 上次集合。"""
    if partition in volatile.get(source.name, set()):
        return False
    if remote is None:
        return False
    entry = (state.get("partitions") or {}).get(partition)
    if not entry or entry.get("codec") != CODEC_VERSION:
        return False
    source_state = (entry.get("sources") or {}).get(source.name)
    if not source_state:
        return False
    token = (watermarks.get(source.name) or {}).get(partition)
    if token is None or source_state.get("watermark") != token:
        return False
    recorded = set(source_state.get("entries") or [])
    remote_keys = {key for key in remote.assets if key.startswith(source.name + ":")}
    return recorded <= remote_keys


def _record_partition_state(state: dict, partition: str, manifest,
                            watermarks: dict, timestamp: str) -> None:
    sources_state = {}
    for name, wm in watermarks.items():
        token = wm.get(partition)
        if token is None:
            continue
        sources_state[name] = {
            "watermark": token,
            "max_created_at": token.split("|", 1)[0],
            "entries": sorted(key for key in manifest.assets
                              if key.startswith(name + ":")),
        }
    kind = codec.partition_kind(partition)
    state.setdefault("partitions", {})[partition] = {
        "kind": kind,
        "manifest_id": manifest.manifest_id,
        "codec": CODEC_VERSION,
        "pushed_at": timestamp,
        "sources": sources_state,
    }
    if kind == "date":
        last = state.get("last_success_trade_date") or ""
        if partition > last:
            state["last_success_trade_date"] = partition
    state["updated_at"] = timestamp


# ─── push ─────────────────────────────────────────────────────────────────


def push(backend, sources, *, date_from=None, date_to=None, all_dates=False,
         dry_run=False, allow_shrink=False, paths=None, now=None) -> dict:
    paths = paths or default_paths()
    timestamp = now_iso(now)
    lock = None
    if not dry_run:
        # 先加锁再读 state：避免「读-改-写」快照被并发进程覆盖。
        lock = ProcessLock(paths.lock_path)
        lock.acquire()
    try:
        state = load_state(paths.state_path)
        return _push(
            backend, sources, state=state, paths=paths, timestamp=timestamp,
            date_from=date_from, date_to=date_to, all_dates=all_dates,
            dry_run=dry_run, allow_shrink=allow_shrink,
        )
    finally:
        if lock is not None:
            lock.release()


def _push(backend, sources, *, state, paths, timestamp, date_from, date_to,
          all_dates, dry_run, allow_shrink) -> dict:
    report = SyncReport(dry_run=dry_run)
    watermarks: dict[str, dict[str, str]] = {}
    volatile: dict[str, set] = {}
    local_by_source: dict[str, set] = {}
    try:
        for source in sources:
            watermarks[source.name] = source.watermarks()
            volatile[source.name] = source.volatile_partitions()
            local_by_source[source.name] = set(source.partitions())
    except Exception as exc:   # 源级失败：该次 push 无法判断水位 / 分区
        report.failed.append({
            "kind": "source", "source": getattr(source, "name", ""),
            "reason": f"{type(exc).__name__}: {exc}",
        })
        return _result(False, True, report.failed, dataclasses.asdict(report))

    local_partitions: set[str] = set()
    for value in local_by_source.values():
        local_partitions |= value

    partitions = sorted(
        (p for p in local_partitions
         if codec.partition_in_range(p, date_from, date_to)),
        key=codec.partition_sort_key,
    )
    summary_extra = {"period_excluded": _excluded_periods(
        local_partitions, date_from, date_to)}

    fatal = False
    if all_dates:
        recorded = set((state.get("partitions") or {}).keys())
        for lost in sorted(recorded - local_partitions, key=codec.partition_sort_key):
            if not codec.partition_in_range(lost, date_from, date_to):
                continue
            report.local_empty_remote_present.append(lost)
            report.failed.append({
                "partition": lost, "kind": "local_empty",
                "reason": "state 记录过该分区但本地已无资产",
            })

    for partition in partitions:
        try:
            fatal = _push_partition(
                backend, sources, state=state, watermarks=watermarks,
                volatile=volatile, local_by_source=local_by_source,
                paths=paths, timestamp=timestamp,
                partition=partition, all_dates=all_dates,
                dry_run=dry_run, allow_shrink=allow_shrink, report=report,
            ) or fatal
        except StorageError as exc:
            report.failed.append({
                "partition": partition, "kind": "transport", "reason": str(exc)})
            fatal = True
        except IntegrityError as exc:
            report.failed.append({
                "partition": partition, "kind": "manifest_unreadable",
                "reason": str(exc)})
            fatal = True

    if not dry_run:
        state["updated_at"] = timestamp
        save_state(paths.state_path, state)

    ok = not report.failed and not report.shrunk
    summary = dataclasses.asdict(report)
    summary.update(summary_extra)
    return _result(ok, fatal, report.failed, summary)


def _push_partition(backend, sources, *, state, watermarks, volatile,
                    local_by_source, paths, timestamp, partition, all_dates,
                    dry_run, allow_shrink, report) -> bool:
    remote = None
    if not dry_run:
        remote = manifest_mod.read_remote_manifest(backend, partition)

    entries: dict = {}
    uploads: list[tuple[AssetShard, bytes]] = []
    reused_sources: list[str] = []
    for source in sources:
        prefix = source.name + ":"
        source_local = partition in local_by_source.get(source.name, set())
        remote_prefix = (remote is not None
                         and any(key.startswith(prefix) for key in remote.assets))
        if not source_local and not remote_prefix:
            # 该源在此分区既无本地行、远端也无历史条目：无需读取。
            continue
        if not source_local:
            # 远端曾有该源条目、本地已无行 → 读取空结果，交给收缩保护判定
            # （绝不静默保留已消失的数据，也绝不静默删除）。
            batch = source.read_partition(partition)
            if batch.failed:
                report.failed.extend(batch.failed)
                report.dates.append({"partition": partition,
                                     "action": "failed", "failed": batch.failed})
                return False
            continue
        if (not dry_run and not all_dates
                and _source_fresh(state, source, partition, remote,
                                  watermarks, volatile)):
            prefix = source.name + ":"
            entries.update({key: entry for key, entry in remote.assets.items()
                            if key.startswith(prefix)})
            reused_sources.append(source.name)
            continue
        try:
            batch = source.read_partition(partition)
        except Exception as exc:
            report.failed.append({
                "partition": partition, "kind": "source", "source": source.name,
                "reason": f"{type(exc).__name__}: {exc}",
            })
            return True
        if batch.failed:
            report.failed.extend(batch.failed)
            report.dates.append({"partition": partition,
                                 "trade_date": codec.partition_sort_date(partition)
                                 if codec.partition_kind(partition) == "date" else "",
                                 "action": "failed", "failed": batch.failed})
            return False
        for shard in batch.shards:
            if shard.partition != partition:
                report.failed.append({
                    "partition": partition, "kind": "shard",
                    "asset_type": shard.asset_type,
                    "reason": "分片分区与请求分区不一致",
                })
                report.dates.append({"partition": partition, "action": "failed"})
                return False
            size = len(shard.content)
            if size > MAX_SHARD_BYTES:
                report.failed.append({
                    "partition": partition, "kind": "shard_too_large",
                    "asset_type": shard.asset_type,
                    "reason": f"分片 {size} 字节超过上限 {MAX_SHARD_BYTES}",
                })
                report.dates.append({"partition": partition, "action": "failed"})
                return False
            if size > WARN_SHARD_BYTES:
                report.dates.append({
                    "partition": partition, "action": "warning",
                    "warning": "shard_large", "asset_type": shard.asset_type,
                    "size": size})
            blob = codec.compress(shard.content)
            entries[f"{shard.source}:{shard.asset_type}"] = codec.make_entry(
                shard, blob, rows=_shard_row_count(shard))
            uploads.append((shard, blob))

    if not entries:
        report.dates.append({"partition": partition, "action": "empty"})
        return False

    built = manifest_mod.manifest_from_entries(
        partition, entries, generated_at=timestamp,
        generator_version=__version__)
    base_entry = {
        "partition": partition,
        "manifest_id": built.manifest_id,
        "shards": len(entries),
    }
    if reused_sources:
        base_entry["reused"] = sorted(reused_sources)

    if dry_run:
        report.dates.append({**base_entry, "action": "push"})
        return False

    remote_id, supported = manifest_mod.remote_manifest_id(backend, partition)
    report.metadata_supported = report.metadata_supported and supported

    if remote_id == built.manifest_id:
        remote = remote or manifest_mod.read_remote_manifest(backend, partition)
        if remote is not None and _created_signature(remote) == _created_signature(built):
            report.skipped += 1
            report.dates.append({**base_entry, "action": "skip"})
        else:
            # 仅采集时间变化；或 HEAD 与 GET 之间对象被删除/替换 → 重写 body（0 blob PUT）。
            _put_manifest(backend, built)
            report.manifests_uploaded += 1
            report.dates.append({**base_entry, "action": "manifest_refresh"})
        _record_partition_state(state, partition, built, watermarks, timestamp)
        save_state(paths.state_path, state)
        return False

    if remote is not None and not allow_shrink:
        old_keys = set(remote.assets)
        new_keys = set(built.assets)
        if new_keys < old_keys:
            removed = sorted(old_keys - new_keys)
            report.shrunk.append({"partition": partition, "removed": removed})
            report.failed.append({
                "partition": partition, "kind": "shrink",
                "reason": "新 manifest 是远端严格子集，需 --allow-shrink",
                "removed": removed,
            })
            report.dates.append({**base_entry, "action": "shrink_rejected"})
            return False

    for shard, blob in uploads:
        key = codec.blob_key(shard)
        meta = backend.head(key)
        if meta is not None and (meta.metadata or {}).get("sha256") == shard.sha256:
            continue
        backend.put(key, blob, codec.blob_metadata(shard, blob),
                    content_md5=content_md5_b64(codec.hashes(blob)[1]))
        report.blobs_uploaded += 1
        report.bytes_uploaded += len(blob)

    _put_manifest(backend, built)
    report.manifests_uploaded += 1
    report.pushed += 1
    _record_partition_state(state, partition, built, watermarks, timestamp)
    save_state(paths.state_path, state)
    report.dates.append({**base_entry, "action": "push"})
    return False


# ─── pull ─────────────────────────────────────────────────────────────────


def pull(backend, sources, *, date_from, date_to, apply=False, force=False,
         pick_latest=False, paths=None, now=None) -> dict:
    paths = paths or default_paths()
    lock = None
    if apply:
        lock = ProcessLock(paths.lock_path)
        lock.acquire()
    try:
        return _pull(
            backend, sources, date_from=date_from, date_to=date_to, apply=apply,
            force=force, pick_latest=pick_latest, paths=paths, now=now,
        )
    finally:
        if lock is not None:
            lock.release()


def _blob_partitions(backend, *, date_from=None, date_to=None,
                     tolerate_errors=False) -> set[str]:
    """从 `blobs/` 前缀一次性求出出现过的分区（避免按自然日空转）。

    ``tolerate_errors=True`` 时 LIST 失败（如超过 10 万 key）降级为空集合，
    不阻断按 manifest/本地分区进行的恢复。
    """
    partitions = set()
    try:
        objects = backend.list("blobs/")
    except StorageError:
        if tolerate_errors:
            return partitions
        raise
    for meta in objects:
        parts = meta.key.split("/")
        if len(parts) < 3 or parts[0] != "blobs":
            continue
        if not codec.is_partition(parts[1]):
            continue
        if codec.partition_in_range(parts[1], date_from, date_to):
            partitions.add(parts[1])
    return partitions


def _partition_label(partition: str) -> dict:
    kind = codec.partition_kind(partition)
    return {
        "partition": partition,
        "trade_date": partition if kind == "date" else "",
    }


def _pull(backend, sources, *, date_from, date_to, apply, force, pick_latest,
          paths, now) -> dict:
    report = RestoreReport()
    by_name = sources_by_name(sources)
    fatal = False

    try:
        known: set[str] = set()
        for source in sources:
            known.update(source.partitions())
        known |= _remote_manifest_partitions(backend)
        known |= _blob_partitions(
            backend, date_from=date_from, date_to=date_to, tolerate_errors=True)
    except Exception as exc:
        return _result(False, True, [{
            "kind": "source", "reason": f"{type(exc).__name__}: {exc}"}],
            dataclasses.asdict(report))

    report.period_excluded = _excluded_periods(known, date_from, date_to)

    selected = sorted(
        (p for p in known if codec.partition_in_range(p, date_from, date_to)),
        key=codec.partition_sort_key,
    )
    for partition in selected:
        try:
            manifest = manifest_mod.read_remote_manifest(backend, partition)
            manifest_source = "remote"
            degraded = False
            if manifest is None:
                manifest, degraded = manifest_mod.rebuild_manifest(
                    backend, partition, pick_latest=pick_latest)
                manifest_source = "rebuilt"
        except AmbiguousError as exc:
            report.failed.append({
                **_partition_label(partition), "kind": "ambiguous", "reason": str(exc)})
            continue
        except IntegrityError as exc:
            report.failed.append({
                **_partition_label(partition), "kind": "manifest_unreadable",
                "reason": str(exc)})
            fatal = True
            continue
        except StorageError as exc:
            report.failed.append({
                **_partition_label(partition), "kind": "transport", "reason": str(exc)})
            fatal = True
            continue

        if manifest is None or not manifest.assets:
            report.dates.append({**_partition_label(partition), "action": "no_remote"})
            continue
        if manifest_source == "rebuilt":
            report.manifest_source = "rebuilt"
        if degraded:
            report.degraded = True

        local: dict[str, str] | None = {}
        for source in sources:
            try:
                index, _counts, _shards = source.local_snapshot(partition)
                local.update(index)
            except IntegrityError as exc:
                report.failed.append({
                    **_partition_label(partition), "kind": "integrity",
                    "source": source.name, "reason": str(exc),
                })
                local = None
                break
            except Exception as exc:
                report.failed.append({
                    **_partition_label(partition), "kind": "source",
                    "source": source.name, "reason": f"{type(exc).__name__}: {exc}",
                })
                fatal = True
                local = None
                break
        if local is None:
            continue

        needed = []
        conflicts = []
        pending = 0
        for key, entry in sorted(manifest.assets.items()):
            if entry.source not in by_name:
                report.unsupported.append({
                    **_partition_label(partition), "key": key,
                    "source": entry.source})
                continue
            local_sha = local.get(key)
            if local_sha is None:
                needed.append(entry)
            elif local_sha == entry.sha256:
                report.skipped += 1
            else:
                granularity = by_name[entry.source].merge_granularity_for(
                    entry.asset_type)
                if granularity == "shard" and not force:
                    conflicts.append({
                        **_partition_label(partition), "key": key,
                        "local_sha256": local_sha, "remote_sha256": entry.sha256,
                    })
                else:
                    needed.append(entry)
                    if granularity == "row":
                        pending += 1
        report.conflicts.extend(conflicts)
        report.merge_pending += pending

        if not apply:
            report.dates.append({
                **_partition_label(partition), "action": "plan",
                "download": len(needed), "conflicts": len(conflicts),
                "merge_pending": pending,
                "manifest_source": manifest_source,
            })
            continue
        if not needed:
            report.dates.append({
                **_partition_label(partition), "action": "nothing",
                "conflicts": len(conflicts)})
            continue

        total = sum(int(entry.blob_size or 0) for entry in needed)
        tmp_partition = paths.tmp_dir / partition
        tmp_partition.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(str(tmp_partition)).free
        if free < total * 2 + _MIN_FREE_BYTES:
            report.failed.append({
                **_partition_label(partition), "kind": "config",
                "reason": f"临时区磁盘不足: 需要 {total * 2 + _MIN_FREE_BYTES}，剩余 {free}",
            })
            fatal = True
            continue

        shards: list[AssetShard] = []
        integrity_failed = False
        for entry in needed:
            try:
                blob = backend.get(entry.blob)
                content = _load_blob(entry, blob)
            except (IntegrityError, NotFoundError, OSError) as exc:
                report.failed.append({
                    **_partition_label(partition), "key": entry.blob,
                    "kind": "integrity", "reason": str(exc)})
                integrity_failed = True
                break
            except StorageError as exc:
                report.failed.append({
                    **_partition_label(partition), "key": entry.blob,
                    "kind": "transport", "reason": str(exc)})
                fatal = True
                integrity_failed = True
                break
            report.bytes_downloaded += len(blob)
            shards.append(AssetShard(
                partition=partition,
                asset_type=entry.asset_type,
                source=entry.source,
                content=content,
                sha256=entry.sha256,
                md5=entry.md5,
                schema=entry.schema,
                created_at=entry.created_at,
                created_at_map=dict(entry.created_at_map),
            ))
        if integrity_failed:
            continue

        grouped: dict[str, list[AssetShard]] = {}
        for shard in shards:
            grouped.setdefault(shard.source, []).append(shard)

        partition_failed = False
        for name, group in grouped.items():
            try:
                restored = by_name[name].restore(partition, group, force=force)
            except Exception as exc:
                report.failed.append({
                    **_partition_label(partition), "source": name, "kind": "db",
                    "reason": f"{type(exc).__name__}: {exc}",
                })
                partition_failed = True
                continue
            report.restored += restored.restored
            report.skipped += restored.skipped
            report.rows_inserted += restored.rows_inserted
            report.rows_skipped += restored.rows_skipped
            report.rows_filled += restored.rows_filled
            report.conflicts.extend(
                {**item, **_partition_label(partition), "source": name}
                for item in restored.conflicts)
            report.deferred.extend(
                {**item, **_partition_label(partition), "source": name}
                for item in restored.deferred)
            report.failed.extend(
                {**item, **_partition_label(partition), "source": name}
                for item in restored.failed)

        report.dates.append({
            **_partition_label(partition), "action": "applied",
            "shards": len(shards), "conflicts": len(conflicts),
        })
        if not partition_failed:
            shutil.rmtree(tmp_partition, ignore_errors=True)

    ok = not report.failed and not report.conflicts
    return _result(ok, fatal, report.failed, dataclasses.asdict(report))


# ─── status ───────────────────────────────────────────────────────────────


def _select_partitions(partitions, all_dates: bool) -> list[str]:
    """默认窗口：date 取最近 30 个，period/period_raw/static 全取。

    不能沿用 ``sorted(...)[-30:]``——``fin-`` / ``static-`` 在字符串序里落在
    ``2026-`` 之后，直接取尾会丢掉最近日期分区。
    """
    ordered = sorted(partitions, key=codec.partition_sort_key)
    if all_dates:
        return ordered
    dates = [p for p in ordered if codec.partition_kind(p) == "date"]
    others = [p for p in ordered if codec.partition_kind(p) != "date"]
    return sorted(dates[-DEFAULT_DATE_LIMIT:] + others, key=codec.partition_sort_key)


def _drift_or_mismatch(diff_keys, local_index, local_rows, remote) -> tuple[list, list]:
    drift, mismatched = [], []
    for key in diff_keys:
        entry = remote.assets.get(key)
        if entry is None:
            mismatched.append(key)
            continue
        try:
            spec = tables.spec_for(entry.source, entry.asset_type)
        except KeyError:
            spec = None
        local_sha = local_index.get(key)
        local_count = int(local_rows.get(key) or 0)
        if (spec is not None and spec.drift_ok and entry.rows is not None
                and local_sha is not None and local_sha != entry.sha256
                and local_count > int(entry.rows)):
            drift.append(key)
        else:
            mismatched.append(key)
    return drift, mismatched


def _tables_coverage(remote_counts: dict, local_counts: dict) -> dict:
    coverage = {}
    for spec in tables.TABLE_SPECS:
        coverage[spec.unit_key] = {
            "table": spec.table,
            "partition_kind": spec.partition_kind,
            "merge": spec.merge_granularity,
            "volatile": spec.volatile,
            "local_entries": int(local_counts.get(spec.unit_key, 0)),
            "remote_entries": int(remote_counts.get(spec.unit_key, 0)),
        }
    coverage["prep:*"] = {"table": "prep_assets", "partition_kind": "date",
                          "merge": "shard", "volatile": False,
                          "local_entries": int(local_counts.get("prep", 0)),
                          "remote_entries": int(remote_counts.get("prep", 0))}
    coverage["auction:*"] = {"table": "auction_assets", "partition_kind": "date",
                             "merge": "shard", "volatile": False,
                             "local_entries": int(local_counts.get("auction", 0)),
                             "remote_entries": int(remote_counts.get("auction", 0))}
    return coverage


def status(backend, sources, *, all_dates=False, orphans=False, paths=None,
           now=None) -> dict:
    paths = paths or default_paths()
    try:
        local_partitions: set[str] = set()
        for source in sources:
            local_partitions.update(source.partitions())
        remote_partitions = _remote_manifest_partitions(backend)
    except Exception as exc:
        return _result(False, True, [{
            "kind": "transport", "reason": f"{type(exc).__name__}: {exc}"}],
            {"dates": [], "dates_missing": [], "dates_mismatched": [],
             "dates_drift": []})

    selected = _select_partitions(local_partitions | remote_partitions, all_dates)
    summary = {
        "dates": [],
        "dates_missing": [],
        "dates_mismatched": [],
        "dates_drift": [],
        "bytes_local": 0,
        "bytes_remote": 0,
        "last_state": _state_summary(paths),
        "metadata_supported": True,
        "orphan_blobs": [] if orphans else None,
        # M6：factor_* / fin_* 已纳入同步（值保持为 {}，见方案 §10 breaking 说明）
        "not_synced": {},
        "tables": {},
    }
    fatal = False
    orphan_unknown: list[str] = []
    remote_counts: dict[str, int] = {}
    local_counts: dict[str, int] = {}

    for partition in selected:
        local: dict[str, str] | None = None
        local_rows: dict[str, int] = {}
        local_shards: list = []
        if partition in local_partitions:
            local = {}
            try:
                for source in sources:
                    index, counts, shards = source.local_snapshot(partition)
                    local.update(index)
                    local_rows.update(counts)
                    local_shards.extend(shards)
                    summary["bytes_local"] += sum(len(s.content) for s in shards)
            except IntegrityError as exc:
                summary["dates"].append({
                    **_partition_label(partition), "status": "local_error",
                    "reason": str(exc)})
                continue
            except Exception as exc:
                summary["dates"].append({
                    **_partition_label(partition), "status": "local_error",
                    "reason": f"{type(exc).__name__}: {exc}"})
                fatal = True
                continue

        try:
            remote_id, supported = manifest_mod.remote_manifest_id(backend, partition)
            summary["metadata_supported"] = summary["metadata_supported"] and supported
            remote = manifest_mod.read_remote_manifest(backend, partition)
        except IntegrityError as exc:
            summary["dates"].append({
                **_partition_label(partition), "status": "unreadable",
                "reason": str(exc)})
            fatal = True
            continue
        except StorageError as exc:
            summary["dates"].append({
                **_partition_label(partition), "status": "transport_error",
                "reason": str(exc)})
            fatal = True
            continue

        if local is None:
            summary["dates_missing"].append({
                **_partition_label(partition), "reason": "local_missing"})
            summary["dates"].append({
                **_partition_label(partition), "status": "local_missing"})
            continue
        if remote is None:
            summary["dates_missing"].append({
                **_partition_label(partition), "reason": "remote_missing"})
            entry = {**_partition_label(partition), "status": "remote_missing"}
            if orphans:
                orphan_unknown.append(partition)
                entry["orphans"] = None
                entry["orphan_reason"] = "manifest_missing"
            summary["dates"].append(entry)
            continue

        for key in local:
            local_counts[key.split(":", 1)[0]] = local_counts.get(
                key.split(":", 1)[0], 0) + 1
            if tables.has_spec(*key.split(":", 1)):
                local_counts[key] = local_counts.get(key, 0) + 1
        for key in remote.assets:
            remote_counts[key.split(":", 1)[0]] = remote_counts.get(
                key.split(":", 1)[0], 0) + 1
            if tables.has_spec(*key.split(":", 1)):
                remote_counts[key] = remote_counts.get(key, 0) + 1

        diff = _diff_keys(local, remote)
        drift_keys, mismatch_keys = _drift_or_mismatch(
            diff, local, local_rows, remote)
        local_manifest_id = codec.manifest_id_for_shards(local_shards)
        id_mismatch = bool(remote_id) and local_manifest_id != remote_id
        # 追加式单元的「本地行更多」会让 manifest_id 必然不同；此时按 drift 处理，
        # 不让 id 差异把它升级成 mismatched（定性交给 verify）。
        if drift_keys and not mismatch_keys:
            id_mismatch = False
        if mismatch_keys or id_mismatch:
            summary["dates_mismatched"].append({
                **_partition_label(partition),
                "keys": mismatch_keys,
                "local_manifest_id": local_manifest_id,
                "remote_manifest_id": remote_id,
            })
        if drift_keys:
            summary["dates_drift"].append({
                **_partition_label(partition),
                "keys": drift_keys, "reason": "row_count_only",
            })
        summary["bytes_remote"] += sum(
            int(entry.blob_size or 0) for entry in remote.assets.values())
        if mismatch_keys or id_mismatch:
            state = "mismatched"
        elif drift_keys:
            state = "drift"
        else:
            state = "ok"
        summary["dates"].append({
            **_partition_label(partition), "status": state,
            "keys": len(remote.assets),
        })
        if orphans:
            listed = backend.list(f"blobs/{partition}/")
            expected = {entry.blob for entry in remote.assets.values()}
            summary["orphan_blobs"].extend(
                meta.key for meta in listed if meta.key not in expected)

    if orphans and orphan_unknown:
        # manifest 缺失时无法判定孤儿集合：整体置 null，绝不伪装成「无孤儿」。
        summary["orphan_blobs"] = None
        summary["orphan_reason"] = {
            "reason": "manifest_missing", "dates": orphan_unknown}

    summary["tables"] = _tables_coverage(remote_counts, local_counts)
    ok = not summary["dates_missing"] and not summary["dates_mismatched"] and not fatal
    return _result(ok, fatal, summary["dates_missing"] + summary["dates_mismatched"],
                   summary)


def _state_summary(paths) -> dict:
    state = load_state(paths.state_path) if paths.state_path else {}
    partitions = state.get("partitions") or {}
    return {
        "hostname": state.get("hostname", ""),
        "updated_at": state.get("updated_at", ""),
        "last_success_trade_date": state.get("last_success_trade_date", ""),
        "partitions": len(partitions),
        "dates": len(partitions),
    }


# ─── verify ───────────────────────────────────────────────────────────────


def verify(backend, sources, *, date_from=None, date_to=None, samples=None,
           deep=False, show_md5=False, paths=None, now=None) -> dict:
    if bool(date_from) != bool(date_to):
        raise ValueError("--from 与 --to 必须成对指定")
    paths = paths or default_paths()
    try:
        local_partitions: set[str] = set()
        for source in sources:
            local_partitions.update(source.partitions())
        remote_partitions = _remote_manifest_partitions(backend)
    except Exception as exc:
        return _result(False, True, [{
            "kind": "transport", "reason": f"{type(exc).__name__}: {exc}"}],
            {"checked_dates": 0, "checked_blobs": 0, "mismatched": []})

    known = local_partitions | remote_partitions
    if date_from and date_to:
        selected = set(_calendar_dates(date_from, date_to))
        selected |= {p for p in known
                     if codec.partition_kind(p) in ("period", "period_raw")
                     and codec.partition_in_range(p, date_from, date_to)}
        selected |= {p for p in known if codec.partition_kind(p) == "static"}
        partitions = sorted(selected, key=codec.partition_sort_key)
    else:
        partitions = _select_partitions(known, False)
    excluded = _excluded_periods(known, date_from, date_to)

    mismatched: list[dict] = []
    entries: list[tuple[str, str, object]] = []
    local_absent: list[str] = []
    checked_dates = 0
    metadata_supported = True

    for partition in partitions:
        try:
            metadata_id, supported = manifest_mod.remote_manifest_id(backend, partition)
            metadata_supported = metadata_supported and supported
            remote = manifest_mod.read_remote_manifest(backend, partition)
        except (IntegrityError, StorageError) as exc:
            mismatched.append({**_partition_label(partition), "reason": str(exc)})
            continue
        if remote is None:
            mismatched.append({**_partition_label(partition),
                               "reason": "manifest_missing"})
            continue
        checked_dates += 1
        if metadata_id and metadata_id != remote.manifest_id:
            mismatched.append({
                **_partition_label(partition), "reason": "metadata_id_mismatch",
                "metadata_id": metadata_id, "body_id": remote.manifest_id,
            })
        local: dict[str, str] | None = None
        if partition in local_partitions:
            local = {}
            try:
                for source in sources:
                    index, _counts, _shards = source.local_snapshot(partition)
                    local.update(index)
            except Exception as exc:
                mismatched.append({
                    **_partition_label(partition),
                    "reason": f"local_read_error: {type(exc).__name__}: {exc}",
                })
                local = None
        else:
            local_absent.append(partition)
        if local:
            diff = _diff_keys(local, remote)
            if diff:
                mismatched.append({
                    **_partition_label(partition), "reason": "local_diff",
                    "keys": diff})
        entries.extend((partition, key, entry)
                       for key, entry in sorted(remote.assets.items()))

    checked_blobs = 0
    md5_details: list[dict] = []
    if deep:
        chosen = entries if samples is None else _sample(entries, samples)
        for partition, key, entry in chosen:
            try:
                blob = backend.get(entry.blob)
                content = _load_blob(entry, blob)
            except (IntegrityError, NotFoundError, StorageError, OSError) as exc:
                mismatched.append({
                    **_partition_label(partition), "key": key,
                    "reason": f"blob_invalid: {exc}"})
                continue
            checked_blobs += 1
            if show_md5:
                md5_details.append({
                    **_partition_label(partition), "key": key,
                    "md5": codec.hashes(content)[1],
                })

    summary = {
        "checked_dates": checked_dates,
        "checked_blobs": checked_blobs,
        "mismatched": mismatched,
        "metadata_supported": metadata_supported,
        "local_absent": local_absent,
        "deep": bool(deep),
        "samples": samples,
        "period_excluded": excluded,
    }
    if show_md5:
        summary["md5"] = md5_details

    return _result(not mismatched, False, mismatched, summary)
