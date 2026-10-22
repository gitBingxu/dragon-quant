"""规范化 JSON、gzip 与内容/传输哈希。

哈希口径（见技术方案 §5.4）：
  - 内容哈希：对未压缩的规范化 JSON 计算 sha256 / md5，作为资产身份。
  - 传输哈希：对 gzip 字节计算 sha256，只用于传输完整性。
这样 Python/zlib 升级导致压缩输出变化时，同一资产不会被误判为新内容。
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json

from dragon_quant.assets_sync.errors import IntegrityError
from dragon_quant.assets_sync.models import AssetShard, Manifest, ManifestEntry


def canonical_json(payload) -> bytes:
    """确定性序列化：键排序、紧凑分隔符、拒绝 NaN。"""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def hashes(data: bytes) -> tuple[str, str]:
    """返回 (sha256, md5) 十六进制摘要。"""
    return hashlib.sha256(data).hexdigest(), hashlib.md5(data).hexdigest()


def compress(data: bytes) -> bytes:
    """确定性 gzip（mtime=0 → 不写入当前时间）。"""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as handle:
        handle.write(data)
    return buf.getvalue()


def decompress(data: bytes) -> bytes:
    return gzip.decompress(data)


def build_shard(
    trade_date: str,
    asset_type: str,
    source: str,
    payload,
    schema: int = 1,
    created_at: str = "",
    created_at_map: dict | None = None,
) -> AssetShard:
    """把 payload 转为规范化内容的 AssetShard。

    ``created_at`` / ``created_at_map`` 是旁路字段：只进 manifest 与 blob 元数据，
    不参与内容 sha256，保证「只重采、内容未变」不会触发重传。
    """
    content = canonical_json(payload)
    sha256, md5 = hashes(content)
    return AssetShard(
        trade_date=trade_date,
        asset_type=asset_type,
        source=source,
        content=content,
        sha256=sha256,
        md5=md5,
        schema=schema,
        created_at=created_at,
        created_at_map=dict(created_at_map or {}),
    )


def blob_key_parts(trade_date: str, source: str, asset_type: str, sha256: str) -> str:
    """自描述 blob 键的唯一构造口径（manifest 解析时用它做指针校验）。"""
    return f"blobs/{trade_date}/{source}_{asset_type}.{sha256}.json.gz"


def blob_key(shard: AssetShard) -> str:
    """自描述 blob 键：日期/来源/类型/内容哈希。"""
    return blob_key_parts(shard.trade_date, shard.source, shard.asset_type, shard.sha256)


def blob_metadata(shard: AssetShard, blob: bytes) -> dict[str, str]:
    """写入对象头的自定义元数据（HEAD 可回传）。

    键名统一用连字符：HTTP 头对下划线支持不一致，部分代理会丢弃。
    这与方案 §5.3 的契约一致（asset-type / trade-date）。
    """
    blob_sha256, _ = hashes(blob)
    metadata = {
        "sha256": shard.sha256,
        "md5": shard.md5,
        "size": str(len(shard.content)),
        "schema": str(shard.schema),
        "blob-sha256": blob_sha256,
        "source": shard.source,
        "asset-type": shard.asset_type,
        "trade-date": shard.trade_date,
    }
    if shard.created_at:
        metadata["created-at"] = shard.created_at
    return metadata


def make_entry(shard: AssetShard, blob: bytes) -> ManifestEntry:
    blob_sha256, _ = hashes(blob)
    return ManifestEntry(
        source=shard.source,
        asset_type=shard.asset_type,
        sha256=shard.sha256,
        md5=shard.md5,
        size=len(shard.content),
        blob=blob_key(shard),
        blob_sha256=blob_sha256,
        blob_size=len(blob),
        schema=shard.schema,
        created_at=shard.created_at,
        created_at_map=dict(shard.created_at_map),
    )


def manifest_from_entries(
    trade_date: str,
    entries: dict[str, ManifestEntry],
    *,
    schema: int = 1,
    generated_at: str = "",
    generator_version: str = "",
    manifest_source: str = "push",
) -> Manifest:
    """构建 manifest，并只对逻辑身份字段计算 manifest_id。"""
    assets = dict(sorted(entries.items()))
    identity = {key: entry.identity() for key, entry in assets.items()}
    manifest_id = hashlib.sha256(canonical_json(identity)).hexdigest()
    return Manifest(
        trade_date=trade_date,
        manifest_id=manifest_id,
        assets=assets,
        schema=schema,
        generated_at=generated_at,
        generator_version=generator_version,
        manifest_source=manifest_source,
    )


def manifest_id_for_shards(shards) -> str:
    """只按逻辑身份字段计算 manifest_id（不压缩、不构造 blob）。

    供 status 等只关心身份比较的路径使用，避免无谓 gzip。
    """
    identity = {}
    for shard in shards:
        identity[f"{shard.source}:{shard.asset_type}"] = {
            "source": shard.source,
            "asset_type": shard.asset_type,
            "sha256": shard.sha256,
            "md5": shard.md5,
            "size": len(shard.content),
            "schema": shard.schema,
        }
    return hashlib.sha256(canonical_json(identity)).hexdigest()


def manifest_key(trade_date: str) -> str:
    return f"manifests/{trade_date}.json"


def _entry_body(entry: ManifestEntry) -> dict:
    body = entry.identity() | {
        "blob": entry.blob,
        "blob_sha256": entry.blob_sha256,
        "blob_size": entry.blob_size,
    }
    if entry.created_at:
        body["created_at"] = entry.created_at
    if entry.created_at_map:
        body["created_at_map"] = dict(sorted(entry.created_at_map.items()))
    return body


def manifest_to_bytes(manifest: Manifest) -> bytes:
    return canonical_json({
        "schema": manifest.schema,
        "trade_date": manifest.trade_date,
        "manifest_id": manifest.manifest_id,
        "generated_at": manifest.generated_at,
        "generator_version": manifest.generator_version,
        "manifest_source": manifest.manifest_source,
        "assets": {key: _entry_body(entry) for key, entry in manifest.assets.items()},
    })


def recompute_manifest_id(manifest: Manifest) -> str:
    """按逻辑身份字段重算 manifest_id（用于校验/重建）。"""
    return hashlib.sha256(canonical_json(manifest.identity_map())).hexdigest()


def manifest_from_bytes(data: bytes, *, expected_trade_date: str | None = None) -> Manifest:
    """解析 manifest；损坏/未知 schema/指针或自洽性失败统一抛 IntegrityError。

    ``expected_trade_date`` 用于校验 body 自带的 ``trade_date`` 与请求键一致，
    防止把别的日期的 manifest body 误拷/篡改到当前键下。
    """
    try:
        raw = json.loads(data)
        if not isinstance(raw, dict):
            raise ValueError("manifest 顶层必须是对象")
        schema = raw.get("schema", 1)
        if schema != 1:
            raise ValueError(f"不支持的 manifest schema: {schema}")
        trade_date = raw["trade_date"]
        if expected_trade_date is not None and trade_date != expected_trade_date:
            raise ValueError(
                f"manifest trade_date 与请求日期不一致: {trade_date} != {expected_trade_date}")
        assets = raw.get("assets", {})
        if not isinstance(assets, dict):
            raise ValueError("manifest.assets 必须是对象")
        entries = {}
        for key, item in assets.items():
            if not isinstance(item, dict):
                raise ValueError(f"manifest.assets[{key}] 必须是对象")
            entry = ManifestEntry(
                source=item["source"],
                asset_type=item["asset_type"],
                sha256=item["sha256"],
                md5=item["md5"],
                size=item["size"],
                blob=item["blob"],
                blob_sha256=item.get("blob_sha256", ""),
                blob_size=item.get("blob_size", 0),
                schema=item.get("schema", 1),
                created_at=item.get("created_at", "") or "",
                created_at_map=item.get("created_at_map", {}) or {},
            )
            # 零 I/O 指针校验：键与 blob 路径都必须由 (date, source, type, sha) 唯一决定。
            if key != f"{entry.source}:{entry.asset_type}":
                raise ValueError(f"manifest 键与条目不一致: {key}")
            expected_blob = blob_key_parts(
                trade_date, entry.source, entry.asset_type, entry.sha256)
            if entry.blob != expected_blob:
                raise ValueError(f"manifest blob 指针不一致: {key}")
            if not isinstance(entry.created_at_map, dict):
                raise ValueError(f"manifest created_at_map 必须是对象: {key}")
            entries[key] = entry
        manifest = Manifest(
            trade_date=trade_date,
            manifest_id=raw["manifest_id"],
            assets=entries,
            schema=schema,
            generated_at=raw.get("generated_at", ""),
            generator_version=raw.get("generator_version", ""),
            manifest_source=raw.get("manifest_source", "push"),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise IntegrityError(f"manifest 解析失败: {exc}") from exc

    if recompute_manifest_id(manifest) != manifest.manifest_id:
        raise IntegrityError("manifest_id 与 assets 内容不一致")
    return manifest
