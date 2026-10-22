"""push / pull / status / verify 编排（普通函数 + 共享 helper）。

返回统一 dict：``{"ok", "fatal", "failed", "summary"}``：
  - ``fatal=True`` 表示配置/传输/正式 manifest 不可读等阻断级错误 → CLI 退出 2；
  - ``ok=False`` 且非 fatal（部分分片失败、收缩被拒、pull 冲突）→ CLI 退出 1。
"""

from __future__ import annotations

import dataclasses
import shutil
from datetime import date, timedelta

from dragon_quant._version import __version__
from dragon_quant.assets_sync import codec, manifest as manifest_mod
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

CODEC_VERSION = 1
DEFAULT_DATE_LIMIT = 30
_MIN_FREE_BYTES = 16 * 1024 * 1024

def _result(ok: bool, fatal: bool, failed: list, summary: dict) -> dict:
    summary = dict(summary)
    summary["fatal"] = bool(fatal)
    return {"ok": bool(ok), "fatal": bool(fatal), "failed": list(failed),
            "summary": summary}


def _in_range(value: str, date_from: str | None, date_to: str | None) -> bool:
    if date_from and value < date_from:
        return False
    if date_to and value > date_to:
        return False
    return True


def _calendar_dates(date_from: str, date_to: str) -> list[str]:
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    out = []
    while start <= end:
        out.append(start.isoformat())
        start += timedelta(days=1)
    return out


def _remote_manifest_dates(backend) -> set[str]:
    dates = set()
    for meta in backend.list("manifests/"):
        name = meta.key
        if not name.startswith("manifests/") or not name.endswith(".json"):
            continue
        value = name[len("manifests/"): -len(".json")]
        try:
            date.fromisoformat(value)
        except ValueError:
            continue
        dates.add(value)
    return dates


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
        codec.manifest_key(manifest.trade_date),
        body,
        manifest_mod.manifest_metadata(manifest),
        content_md5=content_md5_b64(codec.hashes(body)[1]),
    )


def _state_fresh(state: dict, trade_date: str, watermarks: dict) -> bool:
    entry = (state.get("dates") or {}).get(trade_date)
    if not entry or entry.get("codec") != CODEC_VERSION:
        return False
    current = {name: wm[trade_date] for name, wm in watermarks.items()
               if trade_date in wm}
    previous = entry.get("sources") or {}
    if set(current) != set(previous):
        return False
    return all((previous[name].get("watermark") == token)
               for name, token in current.items())


def _record_state(state: dict, trade_date: str, manifest, watermarks: dict,
                  timestamp: str) -> None:
    sources_state = {}
    for name, wm in watermarks.items():
        token = wm.get(trade_date)
        if token is None:
            continue
        sources_state[name] = {
            "watermark": token,
            "max_created_at": token.split("|", 1)[0],
        }
    state.setdefault("dates", {})[trade_date] = {
        "manifest_id": manifest.manifest_id,
        "codec": CODEC_VERSION,
        "pushed_at": timestamp,
        "sources": sources_state,
    }
    last = state.get("last_success_trade_date") or ""
    if trade_date > last:
        state["last_success_trade_date"] = trade_date
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
    for source in sources:
        try:
            watermarks[source.name] = source.watermarks()
        except Exception as exc:   # 源级失败：该次 push 无法判断水位
            report.failed.append({
                "kind": "source", "source": source.name,
                "reason": f"{type(exc).__name__}: {exc}",
            })
            return _result(False, True, report.failed, dataclasses.asdict(report))

    local_dates: set[str] = set()
    for source in sources:
        try:
            local_dates.update(source.list_dates())
        except Exception as exc:
            report.failed.append({
                "kind": "source", "source": source.name,
                "reason": f"{type(exc).__name__}: {exc}",
            })
            return _result(False, True, report.failed, dataclasses.asdict(report))

    dates = sorted(d for d in local_dates if _in_range(d, date_from, date_to))
    fatal = False

    if all_dates:
        recorded = set((state.get("dates") or {}).keys())
        for lost in sorted(recorded - local_dates):
            if not _in_range(lost, date_from, date_to):
                continue
            report.local_empty_remote_present.append(lost)
            report.failed.append({
                "trade_date": lost, "kind": "local_empty",
                "reason": "state 记录过该日但本地已无资产",
            })

    for trade_date in dates:
        try:
            fatal = _push_date(
                backend, sources, state=state, watermarks=watermarks, paths=paths,
                timestamp=timestamp, trade_date=trade_date, all_dates=all_dates,
                dry_run=dry_run, allow_shrink=allow_shrink, report=report,
            ) or fatal
        except StorageError as exc:
            report.failed.append({
                "trade_date": trade_date, "kind": "transport", "reason": str(exc)})
            fatal = True
        except IntegrityError as exc:
            report.failed.append({
                "trade_date": trade_date, "kind": "manifest_unreadable",
                "reason": str(exc)})
            fatal = True

    if not dry_run:
        state["updated_at"] = timestamp
        save_state(paths.state_path, state)

    ok = not report.failed and not report.shrunk
    return _result(ok, fatal, report.failed, dataclasses.asdict(report))


def _push_date(backend, sources, *, state, watermarks, paths, timestamp,
               trade_date, all_dates, dry_run, allow_shrink, report) -> bool:
    entry = (state.get("dates") or {}).get(trade_date)
    if not all_dates and not dry_run and _state_fresh(state, trade_date, watermarks):
        remote_id, supported = manifest_mod.remote_manifest_id(backend, trade_date)
        report.metadata_supported = report.metadata_supported and supported
        if remote_id == entry.get("manifest_id"):
            report.skipped += 1
            report.dates.append({"trade_date": trade_date, "action": "skip"})
            return False

    shards = []
    failures = []
    for source in sources:
        try:
            batch = source.read_date(trade_date)
        except Exception as exc:
            report.failed.append({
                "trade_date": trade_date, "kind": "source", "source": source.name,
                "reason": f"{type(exc).__name__}: {exc}",
            })
            return True
        shards.extend(batch.shards)
        failures.extend(batch.failed)

    if failures:
        report.failed.extend(failures)
        report.dates.append({"trade_date": trade_date, "action": "failed",
                             "failed": failures})
        return False

    if not shards:
        report.dates.append({"trade_date": trade_date, "action": "empty"})
        return False

    if dry_run:
        built = manifest_mod.build_manifest(
            trade_date, shards, generated_at=timestamp,
            generator_version=__version__)
        report.dates.append({
            "trade_date": trade_date, "action": "push",
            "manifest_id": built.manifest_id, "shards": len(shards),
        })
        return False

    return _push_manifest(
        backend, state=state, watermarks=watermarks, paths=paths,
        timestamp=timestamp, trade_date=trade_date, shards=shards,
        allow_shrink=allow_shrink, report=report)


def _push_manifest(backend, *, state, watermarks, paths, timestamp, trade_date,
                   shards, allow_shrink, report) -> bool:
    built = manifest_mod.build_manifest(
        trade_date, shards, generated_at=timestamp, generator_version=__version__)
    remote_id, supported = manifest_mod.remote_manifest_id(backend, trade_date)
    report.metadata_supported = report.metadata_supported and supported

    if remote_id == built.manifest_id:
        remote = manifest_mod.read_remote_manifest(backend, trade_date)
        if remote is not None and _created_signature(remote) == _created_signature(built):
            report.skipped += 1
            action = "skip"
        else:
            # 仅采集时间变化；或 HEAD 与 GET 之间对象被删除/替换 → 重写 body（0 blob PUT）。
            _put_manifest(backend, built)
            report.manifests_uploaded += 1
            action = "manifest_refresh"
        _record_state(state, trade_date, built, watermarks, timestamp)
        save_state(paths.state_path, state)
        report.dates.append({"trade_date": trade_date, "action": action})
        return False

    remote = manifest_mod.read_remote_manifest(backend, trade_date)
    if remote is not None and not allow_shrink:
        old_keys = set(remote.assets)
        new_keys = set(built.assets)
        if new_keys < old_keys:
            removed = sorted(old_keys - new_keys)
            report.shrunk.append({"trade_date": trade_date, "removed": removed})
            report.failed.append({
                "trade_date": trade_date, "kind": "shrink",
                "reason": "新 manifest 是远端严格子集，需 --allow-shrink",
                "removed": removed,
            })
            report.dates.append({"trade_date": trade_date, "action": "shrink_rejected"})
            return False

    for shard in shards:
        key = codec.blob_key(shard)
        meta = backend.head(key)
        if meta is not None and (meta.metadata or {}).get("sha256") == shard.sha256:
            continue
        blob = codec.compress(shard.content)
        backend.put(key, blob, codec.blob_metadata(shard, blob),
                    content_md5=content_md5_b64(codec.hashes(blob)[1]))
        report.blobs_uploaded += 1
        report.bytes_uploaded += len(blob)

    _put_manifest(backend, built)
    report.manifests_uploaded += 1
    report.pushed += 1
    _record_state(state, trade_date, built, watermarks, timestamp)
    save_state(paths.state_path, state)
    report.dates.append({
        "trade_date": trade_date, "action": "push",
        "manifest_id": built.manifest_id, "shards": len(shards),
    })
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


def _blob_dates(backend, *, date_from=None, date_to=None,
                tolerate_errors=False) -> set[str]:
    """从 `blobs/` 前缀一次性求出现过的日期（避免按自然日空转）。

    ``tolerate_errors=True`` 时 LIST 失败（如超过 10 万 key）降级为空集合，
    不阻断按 manifest/本地日期进行的恢复。
    """
    dates = set()
    try:
        objects = backend.list("blobs/")
    except StorageError:
        if tolerate_errors:
            return dates
        raise
    for meta in objects:
        parts = meta.key.split("/")
        if len(parts) < 3 or parts[0] != "blobs":
            continue
        try:
            date.fromisoformat(parts[1])
        except ValueError:
            continue
        if _in_range(parts[1], date_from, date_to):
            dates.add(parts[1])
    return dates


def _pull(backend, sources, *, date_from, date_to, apply, force, pick_latest,
          paths, now) -> dict:
    report = RestoreReport()
    by_name = sources_by_name(sources)
    fatal = False

    try:
        known: set[str] = set()
        for source in sources:
            known.update(source.list_dates())
        known |= _remote_manifest_dates(backend)
        known |= _blob_dates(
            backend, date_from=date_from, date_to=date_to, tolerate_errors=True)
    except Exception as exc:
        return _result(False, True, [{
            "kind": "source", "reason": f"{type(exc).__name__}: {exc}"}],
            dataclasses.asdict(report))

    for trade_date in sorted(d for d in known if _in_range(d, date_from, date_to)):
        try:
            manifest = manifest_mod.read_remote_manifest(backend, trade_date)
            manifest_source = "remote"
            degraded = False
            if manifest is None:
                manifest, degraded = manifest_mod.rebuild_manifest(
                    backend, trade_date, pick_latest=pick_latest)
                manifest_source = "rebuilt"
        except AmbiguousError as exc:
            report.failed.append({
                "trade_date": trade_date, "kind": "ambiguous", "reason": str(exc)})
            continue
        except IntegrityError as exc:
            report.failed.append({
                "trade_date": trade_date, "kind": "manifest_unreadable",
                "reason": str(exc)})
            fatal = True
            continue
        except StorageError as exc:
            report.failed.append({
                "trade_date": trade_date, "kind": "transport", "reason": str(exc)})
            fatal = True
            continue
        if manifest is not None and manifest.trade_date != trade_date:
            report.failed.append({
                "trade_date": trade_date, "kind": "manifest_unreadable",
                "reason": f"manifest trade_date 与请求日期不一致: {manifest.trade_date}"})
            fatal = True
            continue

        if manifest is None or not manifest.assets:
            report.dates.append({"trade_date": trade_date, "action": "no_remote"})
            continue
        if manifest_source == "rebuilt":
            report.manifest_source = "rebuilt"
        if degraded:
            report.degraded = True

        local: dict[str, str] = {}
        for source in sources:
            try:
                local.update(source.local_index(trade_date))
            except IntegrityError as exc:
                report.failed.append({
                    "trade_date": trade_date, "kind": "integrity",
                    "source": source.name, "reason": str(exc),
                })
                local = None
                break
            except Exception as exc:
                report.failed.append({
                    "trade_date": trade_date, "kind": "source",
                    "source": source.name, "reason": f"{type(exc).__name__}: {exc}",
                })
                fatal = True
                local = None
                break
        if local is None:
            continue

        needed = []
        conflicts = []
        for key, entry in sorted(manifest.assets.items()):
            if entry.source not in by_name:
                report.unsupported.append({
                    "trade_date": trade_date, "key": key, "source": entry.source})
                continue
            local_sha = local.get(key)
            if local_sha is None:
                needed.append(entry)
            elif local_sha == entry.sha256:
                report.skipped += 1
            elif not force:
                conflicts.append({
                    "trade_date": trade_date, "key": key,
                    "local_sha256": local_sha, "remote_sha256": entry.sha256,
                })
            else:
                needed.append(entry)
        report.conflicts.extend(conflicts)

        if not apply:
            report.dates.append({
                "trade_date": trade_date, "action": "plan",
                "download": len(needed), "conflicts": len(conflicts),
                "manifest_source": manifest_source,
            })
            continue
        if not needed:
            report.dates.append({
                "trade_date": trade_date, "action": "nothing",
                "conflicts": len(conflicts)})
            continue

        total = sum(int(entry.blob_size or 0) for entry in needed)
        tmp_date = paths.tmp_dir / trade_date
        tmp_date.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(str(tmp_date)).free
        if free < total * 2 + _MIN_FREE_BYTES:
            report.failed.append({
                "trade_date": trade_date, "kind": "config",
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
                    "trade_date": trade_date, "key": entry.blob,
                    "kind": "integrity", "reason": str(exc)})
                integrity_failed = True
                break
            except StorageError as exc:
                report.failed.append({
                    "trade_date": trade_date, "key": entry.blob,
                    "kind": "transport", "reason": str(exc)})
                fatal = True
                integrity_failed = True
                break
            report.bytes_downloaded += len(blob)
            shards.append(AssetShard(
                trade_date=trade_date,
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

        date_failed = False
        for name, group in grouped.items():
            try:
                restored = by_name[name].restore(trade_date, group, force=force)
            except Exception as exc:
                report.failed.append({
                    "trade_date": trade_date, "source": name, "kind": "db",
                    "reason": f"{type(exc).__name__}: {exc}",
                })
                date_failed = True
                continue
            report.restored += restored.restored
            report.skipped += restored.skipped
            report.conflicts.extend(
                {**item, "trade_date": trade_date, "source": name}
                for item in restored.conflicts)
            report.failed.extend(
                {**item, "trade_date": trade_date, "source": name}
                for item in restored.failed)

        report.dates.append({
            "trade_date": trade_date, "action": "applied",
            "shards": len(shards), "conflicts": len(conflicts),
        })
        if not date_failed:
            shutil.rmtree(tmp_date, ignore_errors=True)

    ok = not report.failed and not report.conflicts
    return _result(ok, fatal, report.failed, dataclasses.asdict(report))


# ─── status ───────────────────────────────────────────────────────────────


def status(backend, sources, *, all_dates=False, orphans=False, paths=None,
           now=None) -> dict:
    paths = paths or default_paths()
    try:
        local_dates = set()
        for source in sources:
            local_dates.update(source.list_dates())
        remote_dates = _remote_manifest_dates(backend)
    except Exception as exc:
        return _result(False, True, [{
            "kind": "transport", "reason": f"{type(exc).__name__}: {exc}"}],
            {"dates": [], "dates_missing": [], "dates_mismatched": []})

    all_known = sorted(local_dates | remote_dates)
    dates = all_known if all_dates else all_known[-DEFAULT_DATE_LIMIT:]
    summary = {
        "dates": [],
        "dates_missing": [],
        "dates_mismatched": [],
        "bytes_local": 0,
        "bytes_remote": 0,
        "last_state": _state_summary(paths),
        "metadata_supported": True,
        "orphan_blobs": [] if orphans else None,
    }
    fatal = False
    orphan_unknown: list[str] = []

    for trade_date in dates:
        local: dict[str, str] | None = None
        local_shards: list = []
        if trade_date in local_dates:
            local = {}
            try:
                for source in sources:
                    batch = source.read_date(trade_date)
                    if batch.failed:
                        raise IntegrityError(
                            f"本地 {source.name} 资产损坏: {batch.failed[0].get('reason')}")
                    local_shards.extend(batch.shards)
                    local.update({f"{s.source}:{s.asset_type}": s.sha256
                                  for s in batch.shards})
                    summary["bytes_local"] += sum(len(s.content) for s in batch.shards)
            except IntegrityError as exc:
                summary["dates"].append({
                    "trade_date": trade_date, "status": "local_error",
                    "reason": str(exc)})
                continue
            except Exception as exc:
                summary["dates"].append({
                    "trade_date": trade_date, "status": "local_error",
                    "reason": f"{type(exc).__name__}: {exc}"})
                fatal = True
                continue

        try:
            remote_id, supported = manifest_mod.remote_manifest_id(backend, trade_date)
            summary["metadata_supported"] = summary["metadata_supported"] and supported
            remote = manifest_mod.read_remote_manifest(backend, trade_date)
        except IntegrityError as exc:
            summary["dates"].append({
                "trade_date": trade_date, "status": "unreadable", "reason": str(exc)})
            fatal = True
            continue
        except StorageError as exc:
            summary["dates"].append({
                "trade_date": trade_date, "status": "transport_error", "reason": str(exc)})
            fatal = True
            continue

        if local is None:
            summary["dates_missing"].append({
                "trade_date": trade_date, "reason": "local_missing"})
            summary["dates"].append({"trade_date": trade_date, "status": "local_missing"})
            continue
        if remote is None:
            summary["dates_missing"].append({
                "trade_date": trade_date, "reason": "remote_missing"})
            entry = {"trade_date": trade_date, "status": "remote_missing"}
            if orphans:
                orphan_unknown.append(trade_date)
                entry["orphans"] = None
                entry["orphan_reason"] = "manifest_missing"
            summary["dates"].append(entry)
            continue

        diff = _diff_keys(local, remote)
        local_manifest_id = codec.manifest_id_for_shards(local_shards)
        id_mismatch = bool(remote_id) and local_manifest_id != remote_id
        if diff or id_mismatch:
            summary["dates_mismatched"].append({
                "trade_date": trade_date,
                "keys": diff,
                "local_manifest_id": local_manifest_id,
                "remote_manifest_id": remote_id,
            })
        summary["bytes_remote"] += sum(
            int(entry.blob_size or 0) for entry in remote.assets.values())
        summary["dates"].append({
            "trade_date": trade_date,
            "status": "mismatched" if (diff or id_mismatch) else "ok",
            "keys": len(remote.assets),
        })
        if orphans:
            listed = backend.list(f"blobs/{trade_date}/")
            expected = {entry.blob for entry in remote.assets.values()}
            summary["orphan_blobs"].extend(
                meta.key for meta in listed if meta.key not in expected)

    if orphans and orphan_unknown:
        # manifest 缺失时无法判定孤儿集合：整体置 null，绝不伪装成「无孤儿」。
        summary["orphan_blobs"] = None
        summary["orphan_reason"] = {
            "reason": "manifest_missing", "dates": orphan_unknown}

    ok = not summary["dates_missing"] and not summary["dates_mismatched"] and not fatal
    return _result(ok, fatal, summary["dates_missing"] + summary["dates_mismatched"],
                   summary)


def _state_summary(paths) -> dict:
    state = load_state(paths.state_path) if paths.state_path else {}
    return {
        "hostname": state.get("hostname", ""),
        "updated_at": state.get("updated_at", ""),
        "last_success_trade_date": state.get("last_success_trade_date", ""),
        "dates": len(state.get("dates") or {}),
    }


# ─── verify ───────────────────────────────────────────────────────────────


def verify(backend, sources, *, date_from=None, date_to=None, samples=None,
           deep=False, show_md5=False, paths=None, now=None) -> dict:
    if bool(date_from) != bool(date_to):
        raise ValueError("--from 与 --to 必须成对指定")
    paths = paths or default_paths()
    try:
        local_dates = set()
        for source in sources:
            local_dates.update(source.list_dates())
        remote_dates = _remote_manifest_dates(backend)
    except Exception as exc:
        return _result(False, True, [{
            "kind": "transport", "reason": f"{type(exc).__name__}: {exc}"}],
            {"checked_dates": 0, "checked_blobs": 0, "mismatched": []})

    if date_from and date_to:
        dates = _calendar_dates(date_from, date_to)
    else:
        dates = sorted(local_dates | remote_dates)[-DEFAULT_DATE_LIMIT:]

    mismatched: list[dict] = []
    entries: list[tuple[str, str, object]] = []
    local_absent: list[str] = []
    checked_dates = 0
    metadata_supported = True

    for trade_date in dates:
        try:
            metadata_id, supported = manifest_mod.remote_manifest_id(backend, trade_date)
            metadata_supported = metadata_supported and supported
            remote = manifest_mod.read_remote_manifest(backend, trade_date)
        except (IntegrityError, StorageError) as exc:
            mismatched.append({"trade_date": trade_date, "reason": str(exc)})
            continue
        if remote is None:
            mismatched.append({"trade_date": trade_date, "reason": "manifest_missing"})
            continue
        checked_dates += 1
        if metadata_id and metadata_id != remote.manifest_id:
            mismatched.append({
                "trade_date": trade_date, "reason": "metadata_id_mismatch",
                "metadata_id": metadata_id, "body_id": remote.manifest_id,
            })
        local: dict[str, str] | None = None
        if trade_date in local_dates:
            local = {}
            try:
                for source in sources:
                    local.update(source.local_index(trade_date))
            except Exception as exc:
                mismatched.append({
                    "trade_date": trade_date,
                    "reason": f"local_read_error: {type(exc).__name__}: {exc}",
                })
                local = None
        else:
            local_absent.append(trade_date)
        if local:
            diff = _diff_keys(local, remote)
            if diff:
                mismatched.append({
                    "trade_date": trade_date, "reason": "local_diff", "keys": diff})
        entries.extend((trade_date, key, entry)
                       for key, entry in sorted(remote.assets.items()))

    checked_blobs = 0
    md5_details: list[dict] = []
    if deep:
        chosen = entries if samples is None else _sample(entries, samples)
        for trade_date, key, entry in chosen:
            try:
                blob = backend.get(entry.blob)
                content = _load_blob(entry, blob)
            except (IntegrityError, NotFoundError, StorageError, OSError) as exc:
                mismatched.append({
                    "trade_date": trade_date, "key": key,
                    "reason": f"blob_invalid: {exc}"})
                continue
            checked_blobs += 1
            if show_md5:
                md5_details.append({
                    "trade_date": trade_date, "key": key,
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
    }
    if show_md5:
        summary["md5"] = md5_details

    return _result(not mismatched, False, mismatched, summary)
