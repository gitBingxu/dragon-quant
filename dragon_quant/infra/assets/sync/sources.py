"""资产源 Adapter：把 ``assets.db`` 的原始资产统一为 ``AssetShard``。

设计要点：
  - ``build_sources(db_path)`` 是唯一构造入口；实例持有 ``db_path``，
    路径在调用期解析（支持 ``assets pull --target-dir`` 与测试隔离）。
  - ``read_date`` 返回 ``ShardBatch``：逐分片问题进 ``failed``；
    DB 打不开/表不可读等**源级异常直接抛**，由 service 让该日整体失败。
  - 采集时间只作旁路字段：prep 用行级 ``created_at``；auction 的逐 code 时间
    放 ``created_at_map``，**不进入 payload / 内容哈希**。
"""

from __future__ import annotations

import json
import re

from dragon_quant.infra.assets.sync import codec
from dragon_quant.infra.assets.sync.errors import IntegrityError
from dragon_quant.infra.assets.sync.models import AssetShard, RestoreReport, ShardBatch
from dragon_quant.infra.assets.sync.tables import (
    AuctionsnapAssetSource,
    DragonsAssetSource,
    FactorAssetSource,
    FinAssetSource,
    PrepsnapAssetSource,
)
from dragon_quant.infra.assets import db as assets_db

_ASSET_TYPE_RE = re.compile(r"^[a-z0-9_]+$")


def _check_asset_type(source: str, asset_type: str) -> None:
    if not isinstance(asset_type, str) or not _ASSET_TYPE_RE.match(asset_type):
        raise ValueError(f"非法 asset_type: {source}:{asset_type}")


class PrepAssetSource:
    """``assets.db / prep_assets``：每个 (trade_date, asset_type) 一个分片。"""

    name = "prep"
    merge_granularity = "shard"

    def __init__(self, db_path=None):
        self.db_path = db_path

    # ── 分区协议（M6）：legacy 源只参与 date 分区，行为零变化 ──────────────
    def partitions(self) -> list[str]:
        return self.list_dates()

    def partition_kind(self, partition: str) -> str:
        return codec.partition_kind(partition)

    def read_partition(self, partition: str) -> ShardBatch:
        return self.read_date(partition)

    def volatile_partitions(self) -> set[str]:
        return set()

    def merge_granularity_for(self, asset_type: str) -> str:
        return "shard"

    def local_snapshot(self, partition: str):
        batch = self.read_date(partition)
        if batch.failed:
            raise IntegrityError(
                f"本地 {self.name} 资产损坏: {batch.failed[0].get('reason')}")
        return ({f"{shard.source}:{shard.asset_type}": shard.sha256
                 for shard in batch.shards}, {}, list(batch.shards))

    def list_dates(self) -> list[str]:
        return assets_db.list_prep_asset_dates(db_path=self.db_path)

    def read_date(self, trade_date: str) -> ShardBatch:
        rows = assets_db.export_prep_assets(trade_date, db_path=self.db_path)
        shards: list[AssetShard] = []
        failed: list[dict] = []
        for row in rows:
            asset_type = row.get("asset_type")
            if row.get("payload_error"):
                failed.append({
                    "source": self.name,
                    "trade_date": trade_date,
                    "asset_type": asset_type,
                    "reason": f"payload_json 损坏: {row['payload_error']}",
                })
                continue
            try:
                _check_asset_type(self.name, asset_type)
                shards.append(codec.build_shard(
                    trade_date, asset_type, self.name, row.get("payload"),
                    created_at=row.get("created_at") or "",
                ))
            except (TypeError, ValueError) as exc:
                failed.append({
                    "source": self.name,
                    "trade_date": trade_date,
                    "asset_type": asset_type,
                    "reason": f"{type(exc).__name__}: {exc}",
                })
        return ShardBatch(shards=shards, failed=failed)

    def local_index(self, trade_date: str) -> dict[str, str]:
        batch = self.read_date(trade_date)
        if batch.failed:
            raise IntegrityError(
                f"本地 {self.name} 资产损坏: {batch.failed[0].get('reason')}")
        return {f"{self.name}:{shard.asset_type}": shard.sha256 for shard in batch.shards}

    def watermarks(self) -> dict[str, str]:
        return assets_db.prep_watermarks(db_path=self.db_path)

    def restore(self, trade_date: str, shards: list[AssetShard], *,
                force: bool = False) -> RestoreReport:
        rows = []
        for shard in shards:
            if shard.source != self.name:
                continue
            rows.append({
                "asset_type": shard.asset_type,
                "payload": json.loads(shard.content.decode("utf-8")),
                "created_at": shard.created_at or "",
            })
        result = assets_db.restore_prep_assets(
            trade_date, rows, force=force, db_path=self.db_path)
        return RestoreReport(**result)


class AuctionAssetSource:
    """``assets.db / auction_assets``：每日聚合一个分片。

    payload = ``{"trade_date", "items": [{code, name, items, quote}]}``；
    payload **不含 created_at**，逐 code 时间走 ``created_at_map``（manifest 旁路字段）。
    """

    name = "auction"
    asset_type = "assets"
    merge_granularity = "shard"

    def __init__(self, db_path=None):
        self.db_path = db_path

    # ── 分区协议（M6）：legacy 源只参与 date 分区，行为零变化 ──────────────
    def partitions(self) -> list[str]:
        return self.list_dates()

    def partition_kind(self, partition: str) -> str:
        return codec.partition_kind(partition)

    def read_partition(self, partition: str) -> ShardBatch:
        return self.read_date(partition)

    def volatile_partitions(self) -> set[str]:
        return set()

    def merge_granularity_for(self, asset_type: str) -> str:
        return "shard"

    def local_snapshot(self, partition: str):
        batch = self.read_date(partition)
        if batch.failed:
            raise IntegrityError(
                f"本地 {self.name} 资产损坏: {batch.failed[0].get('reason')}")
        return ({f"{shard.source}:{shard.asset_type}": shard.sha256
                 for shard in batch.shards}, {}, list(batch.shards))

    def list_dates(self) -> list[str]:
        return assets_db.list_auction_asset_dates(db_path=self.db_path)

    def _build(self, trade_date: str) -> tuple[dict, dict, list[dict]]:
        rows = assets_db.export_auction_assets(trade_date, db_path=self.db_path)
        failures = [
            {
                "source": self.name,
                "trade_date": trade_date,
                "asset_type": self.asset_type,
                "reason": f"{row.get('code')} 的 items/quote JSON 损坏",
            }
            for row in rows
            if row.get("items_error") or row.get("quote_error")
        ]
        if failures:
            # 宁可整日失败，也不上传有损 payload（quote 不可再生）。
            return {}, {}, failures
        items = [
            {
                "code": row.get("code"),
                "name": row.get("name"),
                "items": row.get("items"),
                "quote": row.get("quote"),
            }
            for row in rows
        ]
        created_map = {row.get("code"): (row.get("created_at") or "") for row in rows}
        payload = {"trade_date": trade_date, "items": items}
        return payload, created_map, []

    def read_date(self, trade_date: str) -> ShardBatch:
        payload, created_map, failures = self._build(trade_date)
        if failures:
            return ShardBatch(shards=[], failed=failures)
        if not payload or not payload.get("items"):
            return ShardBatch(shards=[], failed=[])
        try:
            _check_asset_type(self.name, self.asset_type)
            shard = codec.build_shard(
                trade_date, self.asset_type, self.name, payload,
                created_at=max(created_map.values()) if created_map else "",
                created_at_map=created_map,
            )
        except (TypeError, ValueError) as exc:
            return ShardBatch(shards=[], failed=[{
                "source": self.name,
                "trade_date": trade_date,
                "asset_type": self.asset_type,
                "reason": f"{type(exc).__name__}: {exc}",
            }])
        return ShardBatch(shards=[shard], failed=[])

    def local_index(self, trade_date: str) -> dict[str, str]:
        batch = self.read_date(trade_date)
        if batch.failed:
            raise IntegrityError(
                f"本地 {self.name} 资产损坏: {batch.failed[0].get('reason')}")
        return {f"{self.name}:{shard.asset_type}": shard.sha256 for shard in batch.shards}

    def watermarks(self) -> dict[str, str]:
        return assets_db.auction_watermarks(db_path=self.db_path)

    def restore(self, trade_date: str, shards: list[AssetShard], *,
                force: bool = False) -> RestoreReport:
        rows = []
        for shard in shards:
            if shard.source != self.name:
                continue
            payload = json.loads(shard.content.decode("utf-8"))
            for item in payload.get("items", []):
                code = item.get("code")
                rows.append({
                    "code": code,
                    "name": item.get("name"),
                    "items": item.get("items") or [],
                    "quote": item.get("quote"),
                    "created_at": shard.created_at_map.get(code, ""),
                })
        result = assets_db.restore_auction_assets(
            trade_date, rows, force=force, db_path=self.db_path)
        return RestoreReport(**result)


SOURCE_CLASSES = (
    PrepAssetSource,
    AuctionAssetSource,
    PrepsnapAssetSource,
    AuctionsnapAssetSource,
    DragonsAssetSource,
    FactorAssetSource,
    FinAssetSource,
)


def _validate_registry() -> None:
    names = [cls.name for cls in SOURCE_CLASSES]
    if len(set(names)) != len(names):
        raise RuntimeError(f"资产源名称重复: {names}")
    for name in names:
        if not name or "_" in name:
            raise RuntimeError(f"资产源名称不得为空或包含 '_': {name}")


_validate_registry()


def build_sources(db_path=None) -> tuple:
    """按调用期路径构造全部资产源（注册表只声明类）。"""
    return tuple(cls(db_path=db_path) for cls in SOURCE_CLASSES)


def sources_by_name(sources) -> dict:
    return {source.name: source for source in sources}
