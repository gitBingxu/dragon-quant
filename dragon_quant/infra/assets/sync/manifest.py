"""manifest 构建、远端读取与从 blob 重建（分区化，M6）。

约定见技术方案 §6 / §8：
  - manifest 每分区一份，重采覆盖；``manifest_id`` 只对逻辑身份字段计算。
  - date 分区写 schema 1，period / period_raw / static 写 schema 2。
  - 重建优先使用 blob 的 ``x-amz-meta-*``；元数据缺失时 GET + gunzip + 重算哈希。
  - 同分区同 ``source:type`` 多候选（重采留下的旧 blob）默认判定歧义，
    只有显式 ``pick_latest`` 才按 ``last_modified`` 选择并标 degraded。
  - 重建出的 manifest 不用于 push 幂等 skip。
"""

from __future__ import annotations

from dragon_quant.infra.assets.sync import codec
from dragon_quant.infra.assets.sync.errors import AmbiguousError, IntegrityError
from dragon_quant.infra.assets.sync.models import Manifest, ManifestEntry, ObjectMeta

_HEX = set("0123456789abcdef")


def build_manifest(partition: str, shards, *, generated_at: str = "",
                   generator_version: str = "", rows=None) -> Manifest:
    """由分片构建 manifest；``rows`` 为 ``{key: row_count}`` 诊断映射。"""
    row_counts = dict(rows or {})
    entries: dict[str, ManifestEntry] = {}
    for shard in shards:
        key = f"{shard.source}:{shard.asset_type}"
        entries[key] = codec.make_entry(
            shard, codec.compress(shard.content), rows=row_counts.get(key))
    return codec.manifest_from_entries(
        partition, entries,
        generated_at=generated_at,
        generator_version=generator_version,
        manifest_source="push",
    )


def manifest_from_entries(partition: str, entries: dict[str, ManifestEntry], *,
                          generated_at: str = "",
                          generator_version: str = "") -> Manifest:
    return codec.manifest_from_entries(
        partition, entries,
        generated_at=generated_at,
        generator_version=generator_version,
        manifest_source="push",
    )


def manifest_metadata(manifest: Manifest) -> dict[str, str]:
    return {
        "manifest-id": manifest.manifest_id,
        "schema": str(manifest.schema),
        "partition": manifest.partition,
    }


def remote_manifest_id(backend, partition: str) -> tuple[str | None, bool]:
    """返回 ``(manifest_id, metadata_supported)``。

    优先 HEAD 的 ``x-amz-meta-manifest-id``；缺失时降级 GET body 重算。
    """
    meta = backend.head(codec.manifest_key(partition))
    if meta is None:
        return None, True
    manifest_id = (meta.metadata or {}).get("manifest-id")
    if manifest_id:
        return manifest_id, True
    manifest = codec.manifest_from_bytes(
        backend.get(codec.manifest_key(partition)),
        expected_partition=partition)
    return manifest.manifest_id, False


def read_remote_manifest(backend, partition: str) -> Manifest | None:
    """读取正式 manifest；不存在返回 None，损坏/分区错配抛 IntegrityError。"""
    key = codec.manifest_key(partition)
    if backend.head(key) is None:
        return None
    return codec.manifest_from_bytes(
        backend.get(key), expected_partition=partition)


def parse_blob_key(key: str, partition: str) -> tuple[str, str, str]:
    """解析 ``blobs/<partition>/<source>_<type>.<sha>.json.gz``。"""
    prefix = f"blobs/{partition}/"
    if not key.startswith(prefix):
        raise ValueError(f"blob 键不属于该分区: {key}")
    name = key[len(prefix):]
    if not name.endswith(".json.gz"):
        raise ValueError(f"blob 键后缀非法: {key}")
    token, dot, sha256 = name[: -len(".json.gz")].rpartition(".")
    if not dot or len(sha256) != 64 or any(ch not in _HEX for ch in sha256):
        raise ValueError(f"blob 键哈希非法: {key}")
    source, sep, asset_type = token.partition("_")
    if not sep or not source or "_" in source or not asset_type:
        raise ValueError(f"blob 键来源非法: {key}")
    return source, asset_type, sha256


def _entry_from_object(backend, meta: ObjectMeta, source: str,
                       asset_type: str, sha256: str) -> ManifestEntry:
    metadata = meta.metadata or {}
    if (metadata.get("sha256") == sha256 and metadata.get("md5")
            and metadata.get("size") is not None):
        return ManifestEntry(
            source=source,
            asset_type=asset_type,
            sha256=sha256,
            md5=metadata["md5"],
            size=int(metadata["size"]),
            blob=meta.key,
            blob_sha256=metadata.get("blob-sha256", ""),
            blob_size=meta.size,
            schema=int(metadata.get("schema", 1) or 1),
            created_at=metadata.get("created-at", "") or "",
        )

    # 元数据缺失/不完整：下载一次补算（罕见）。
    blob = backend.get(meta.key)
    try:
        content = codec.decompress(blob)
    except OSError as exc:
        raise IntegrityError(f"blob 解压失败: {meta.key}: {exc}") from exc
    content_sha, content_md5 = codec.hashes(content)
    if content_sha != sha256:
        raise IntegrityError(f"blob 内容哈希与键不一致: {meta.key}")
    blob_sha, _ = codec.hashes(blob)
    return ManifestEntry(
        source=source,
        asset_type=asset_type,
        sha256=content_sha,
        md5=content_md5,
        size=len(content),
        blob=meta.key,
        blob_sha256=blob_sha,
        blob_size=len(blob),
        schema=int(metadata.get("schema", 1) or 1),
        created_at=metadata.get("created-at", "") or "",
    )


def rebuild_manifest(backend, partition: str, *,
                     pick_latest: bool = False) -> tuple[Manifest | None, bool]:
    """用 ``LIST blobs/<partition>/`` + HEAD 重建 manifest。

    返回 ``(manifest, degraded)``；该分区没有 blob 时返回 ``(None, False)``。
    同分区同 ``source:type`` 多候选且未 ``pick_latest`` → ``AmbiguousError``。
    """
    objects = backend.list(f"blobs/{partition}/")
    grouped: dict[tuple[str, str], list[tuple[ObjectMeta, str]]] = {}
    for meta in objects:
        try:
            source, asset_type, sha256 = parse_blob_key(meta.key, partition)
        except ValueError:
            continue
        grouped.setdefault((source, asset_type), []).append((meta, sha256))

    entries: dict[str, ManifestEntry] = {}
    degraded = False
    for (source, asset_type), candidates in sorted(grouped.items()):
        if len(candidates) > 1:
            if not pick_latest:
                raise AmbiguousError(
                    "同分区存在多个候选 blob: "
                    + ", ".join(meta.key for meta, _ in candidates)
                )
            candidates.sort(key=lambda item: (item[0].last_modified or "", item[0].key))
            candidates = [candidates[-1]]
            degraded = True
        meta, sha256 = candidates[0]
        entry = _entry_from_object(backend, meta, source, asset_type, sha256)
        entries[f"{source}:{asset_type}"] = entry

    if not entries:
        return None, False
    manifest = codec.manifest_from_entries(
        partition, entries, manifest_source="rebuilt")
    return manifest, degraded
