"""规范化 JSON、gzip、内容/传输哈希与分区键 / manifest schema。

哈希口径（见技术方案 §5.4 / §6.5）：
  - 内容哈希：对未压缩的规范化 JSON 计算 sha256 / md5，作为资产身份。
  - 传输哈希：对 gzip 字节计算 sha256，只用于传输完整性。

分区键（M6，方案 §6.1）分四类：
  - ``date``       ``YYYY-MM-DD``（交易日，与旧版完全一致）
  - ``period``     ``fin-YYYY-MM-DD``（报告期）
  - ``period_raw`` ``finraw-YYYY-MM-DD``（报告期原始 blob）
  - ``static``     ``static-<domain>``（维度表）
date 分区的 manifest 继续写 schema 1（旧客户端可读），其余写 schema 2。
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
from dataclasses import dataclass

from dragon_quant.assets_sync.errors import IntegrityError
from dragon_quant.assets_sync.models import AssetShard, Manifest, ManifestEntry

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PERIOD_RE = re.compile(r"^fin-(\d{4}-\d{2}-\d{2})$")
_PERIOD_RAW_RE = re.compile(r"^finraw-(\d{4}-\d{2}-\d{2})$")
_STATIC_RE = re.compile(r"^static-[a-z]+$")

PARTITION_KINDS = ("date", "period", "period_raw", "static")
KIND_RANK = {"date": 0, "period": 1, "period_raw": 2, "static": 3}
MANIFEST_SCHEMA_DATE = 1
MANIFEST_SCHEMA_PARTITIONED = 2


@dataclass(frozen=True)
class PartitionInfo:
    key: str
    kind: str
    sort_date: str


def parse_partition(partition: str) -> PartitionInfo:
    """解析分区键；非法键抛 ``ValueError``。"""
    if not isinstance(partition, str) or not partition:
        raise ValueError(f"非法分区键: {partition!r}")
    if _DATE_RE.match(partition):
        return PartitionInfo(partition, "date", partition)
    match = _PERIOD_RE.match(partition)
    if match:
        return PartitionInfo(partition, "period", match.group(1))
    match = _PERIOD_RAW_RE.match(partition)
    if match:
        return PartitionInfo(partition, "period_raw", match.group(1))
    if _STATIC_RE.match(partition):
        return PartitionInfo(partition, "static", "")
    raise ValueError(f"非法分区键: {partition}")


def is_partition(partition) -> bool:
    try:
        parse_partition(partition)
    except ValueError:
        return False
    return True


def partition_kind(partition: str) -> str:
    return parse_partition(partition).kind


def partition_sort_date(partition: str) -> str:
    return parse_partition(partition).sort_date


def partition_sort_key(partition: str) -> tuple:
    info = parse_partition(partition)
    return (KIND_RANK[info.kind], info.sort_date, info.key)


def partition_in_range(partition: str, date_from: str | None,
                       date_to: str | None) -> bool:
    """static 分区不受日期区间过滤（方案 §6.1 / §13.3）。"""
    info = parse_partition(partition)
    if info.kind == "static":
        return True
    if date_from and info.sort_date < date_from:
        return False
    if date_to and info.sort_date > date_to:
        return False
    return True


def manifest_schema_for(partition: str) -> int:
    return MANIFEST_SCHEMA_DATE if partition_kind(partition) == "date" \
        else MANIFEST_SCHEMA_PARTITIONED


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
    partition: str,
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
        partition=partition,
        asset_type=asset_type,
        source=source,
        content=content,
        sha256=sha256,
        md5=md5,
        schema=schema,
        created_at=created_at,
        created_at_map=dict(created_at_map or {}),
    )


def blob_key_parts(partition: str, source: str, asset_type: str, sha256: str) -> str:
    """自描述 blob 键的唯一构造口径（manifest 解析时用它做指针校验）。"""
    return f"blobs/{partition}/{source}_{asset_type}.{sha256}.json.gz"


def blob_key(shard: AssetShard) -> str:
    """自描述 blob 键：分区/来源/类型/内容哈希。"""
    return blob_key_parts(shard.partition, shard.source, shard.asset_type, shard.sha256)


def blob_metadata(shard: AssetShard, blob: bytes) -> dict[str, str]:
    """写入对象头的自定义元数据（HEAD 可回传）。

    键名统一用连字符：HTTP 头对下划线支持不一致，部分代理会丢弃。
    ``partition`` 是权威键；``trade-date`` 仍在 date 分区双写，兼容旧读取端。
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
        "partition": shard.partition,
    }
    if shard.trade_date:
        metadata["trade-date"] = shard.trade_date
    if shard.created_at:
        metadata["created-at"] = shard.created_at
    return metadata


def make_entry(shard: AssetShard, blob: bytes, *, rows: int | None = None) -> ManifestEntry:
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
        rows=rows,
    )


def manifest_from_entries(
    partition: str,
    entries: dict[str, ManifestEntry],
    *,
    schema: int | None = None,
    generated_at: str = "",
    generator_version: str = "",
    manifest_source: str = "push",
) -> Manifest:
    """构建 manifest，并只对逻辑身份字段计算 manifest_id。"""
    assets = dict(sorted(entries.items()))
    identity = {key: entry.identity() for key, entry in assets.items()}
    manifest_id = hashlib.sha256(canonical_json(identity)).hexdigest()
    if schema is None:
        schema = manifest_schema_for(partition)
    return Manifest(
        partition=partition,
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


def manifest_key(partition: str) -> str:
    return f"manifests/{partition}.json"


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
    if entry.rows is not None:
        body["rows"] = int(entry.rows)
    return body


def manifest_to_bytes(manifest: Manifest) -> bytes:
    body = {
        "schema": manifest.schema,
        "manifest_id": manifest.manifest_id,
        "generated_at": manifest.generated_at,
        "generator_version": manifest.generator_version,
        "manifest_source": manifest.manifest_source,
        "assets": {key: _entry_body(entry) for key, entry in manifest.assets.items()},
    }
    if manifest.schema == MANIFEST_SCHEMA_DATE:
        body["trade_date"] = manifest.partition
    else:
        info = parse_partition(manifest.partition)
        body["partition"] = manifest.partition
        body["partition_kind"] = info.kind
        body["sort_date"] = info.sort_date
    return canonical_json(body)


def recompute_manifest_id(manifest: Manifest) -> str:
    """按逻辑身份字段重算 manifest_id（用于校验/重建）。"""
    return hashlib.sha256(canonical_json(manifest.identity_map())).hexdigest()


def _validate_manifest_partition(raw: dict, schema: int,
                                 expected: str | None) -> str:
    if schema == MANIFEST_SCHEMA_DATE:
        partition = raw["trade_date"]
        info = parse_partition(partition)
        if info.kind != "date":
            raise ValueError(f"schema 1 manifest 的分区必须是 date: {partition}")
        if expected is not None and partition != expected:
            raise ValueError(
                f"manifest trade_date 与请求日期不一致: {partition} != {expected}")
        return partition
    partition = raw["partition"]
    info = parse_partition(partition)
    if info.kind == "date":
        raise ValueError(f"schema 2 manifest 不得用于 date 分区: {partition}")
    if raw.get("partition_kind") != info.kind:
        raise ValueError(
            f"manifest partition_kind 与分区键不一致: "
            f"{raw.get('partition_kind')} != {info.kind}")
    if raw.get("sort_date") != info.sort_date:
        raise ValueError(
            f"manifest sort_date 与分区键不一致: "
            f"{raw.get('sort_date')} != {info.sort_date}")
    if expected is not None and partition != expected:
        raise ValueError(
            f"manifest partition 与请求分区不一致: {partition} != {expected}")
    return partition


def manifest_from_bytes(data: bytes, *, expected_partition: str | None = None,
                        expected_trade_date: str | None = None) -> Manifest:
    """解析 manifest；损坏/未知 schema/指针或自洽性失败统一抛 IntegrityError。

    ``expected_partition``（兼容别名 ``expected_trade_date``）用于校验 body
    自带的分区键与请求键一致，防止把别的分区的 manifest body 误拷/篡改。
    """
    expected = expected_partition if expected_partition is not None \
        else expected_trade_date
    try:
        raw = json.loads(data)
        if not isinstance(raw, dict):
            raise ValueError("manifest 顶层必须是对象")
        schema = raw.get("schema", 1)
        if schema not in (MANIFEST_SCHEMA_DATE, MANIFEST_SCHEMA_PARTITIONED):
            raise ValueError(f"不支持的 manifest schema: {schema}")
        partition = _validate_manifest_partition(raw, schema, expected)
        assets = raw.get("assets", {})
        if not isinstance(assets, dict):
            raise ValueError("manifest.assets 必须是对象")
        entries = {}
        for key, item in assets.items():
            if not isinstance(item, dict):
                raise ValueError(f"manifest.assets[{key}] 必须是对象")
            rows = item.get("rows", None)
            if rows is not None and (not isinstance(rows, int) or rows < 0):
                raise ValueError(f"manifest.assets[{key}].rows 非法")
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
                rows=rows,
            )
            # 零 I/O 指针校验：键与 blob 路径都必须由 (分区, source, type, sha) 唯一决定。
            if key != f"{entry.source}:{entry.asset_type}":
                raise ValueError(f"manifest 键与条目不一致: {key}")
            expected_blob = blob_key_parts(
                partition, entry.source, entry.asset_type, entry.sha256)
            if entry.blob != expected_blob:
                raise ValueError(f"manifest blob 指针不一致: {key}")
            if not isinstance(entry.created_at_map, dict):
                raise ValueError(f"manifest created_at_map 必须是对象: {key}")
            entries[key] = entry
        manifest = Manifest(
            partition=partition,
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
