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
) -> AssetShard:
    """把 payload 转为规范化内容的 AssetShard。"""
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
    )


def blob_key(shard: AssetShard) -> str:
    """自描述 blob 键：日期/来源/类型/内容哈希。"""
    return f"blobs/{shard.trade_date}/{shard.key_token}.{shard.sha256}.json.gz"


def blob_metadata(shard: AssetShard, blob: bytes) -> dict[str, str]:
    """写入对象头的自定义元数据（HEAD 可回传）。

    键名统一用连字符：HTTP 头对下划线支持不一致，部分代理会丢弃。
    这与方案 §5.3 的契约一致（asset-type / trade-date）。
    """
    blob_sha256, _ = hashes(blob)
    return {
        "sha256": shard.sha256,
        "md5": shard.md5,
        "size": str(len(shard.content)),
        "schema": str(shard.schema),
        "blob-sha256": blob_sha256,
        "source": shard.source,
        "asset-type": shard.asset_type,
        "trade-date": shard.trade_date,
    }


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


def manifest_key(trade_date: str) -> str:
    return f"manifests/{trade_date}.json"


def manifest_to_bytes(manifest: Manifest) -> bytes:
    return canonical_json({
        "schema": manifest.schema,
        "trade_date": manifest.trade_date,
        "manifest_id": manifest.manifest_id,
        "generated_at": manifest.generated_at,
        "generator_version": manifest.generator_version,
        "manifest_source": manifest.manifest_source,
        "assets": {key: entry.identity() | {
            "blob": entry.blob,
            "blob_sha256": entry.blob_sha256,
            "blob_size": entry.blob_size,
        } for key, entry in manifest.assets.items()},
    })


def recompute_manifest_id(manifest: Manifest) -> str:
    """按逻辑身份字段重算 manifest_id（用于校验/重建）。"""
    return hashlib.sha256(canonical_json(manifest.identity_map())).hexdigest()


def manifest_from_bytes(data: bytes) -> Manifest:
    """解析 manifest；损坏/未知 schema/自洽性失败统一抛 IntegrityError。"""
    try:
        raw = json.loads(data)
        if not isinstance(raw, dict):
            raise ValueError("manifest 顶层必须是对象")
        schema = raw.get("schema", 1)
        if schema != 1:
            raise ValueError(f"不支持的 manifest schema: {schema}")
        assets = raw.get("assets", {})
        if not isinstance(assets, dict):
            raise ValueError("manifest.assets 必须是对象")
        entries = {}
        for key, item in assets.items():
            if not isinstance(item, dict):
                raise ValueError(f"manifest.assets[{key}] 必须是对象")
            entries[key] = ManifestEntry(
                source=item["source"],
                asset_type=item["asset_type"],
                sha256=item["sha256"],
                md5=item["md5"],
                size=item["size"],
                blob=item["blob"],
                blob_sha256=item.get("blob_sha256", ""),
                blob_size=item.get("blob_size", 0),
                schema=item.get("schema", 1),
            )
        manifest = Manifest(
            trade_date=raw["trade_date"],
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
