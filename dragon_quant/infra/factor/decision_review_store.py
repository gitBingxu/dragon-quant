"""``factor decision-review`` 的唯一只读存储适配（技术方案 §3.1 / §3.3）。

边界：
  - 只允许读 ``infra.assets.db`` 的 ``prep_snapshots`` / ``auction_snapshots``；
  - ``infra.assets`` 必须**函数内 lazy import**（``infra`` 是叶子层，避免 import 期
    建立数据库/循环依赖）；
  - 零 DDL、零写路径：读接口一律 ``create=False``，库文件不存在返回空；
  - 显式传 ``source="v2"``（与 ``engine._load_prep_source`` / ``_load_auction_source``
    一致）。

纯映射（payload → ``DecisionObservation``）在 ``decision_review.py``，本模块只负责
「拿原始行」，两者通过 ``SnapshotStore`` 协议（鸭子类型）解耦。
"""

from __future__ import annotations


def _assets_db():
    from dragon_quant.infra.assets import db as assets_db

    return assets_db


class PrepSnapshotStore:
    """``prep_snapshots`` 只读适配。"""

    source = "prep"

    def snapshot_dates(self, date_from: str, date_to: str) -> list[str]:
        return _assets_db().list_prep_snapshot_dates(
            date_from=date_from, date_to=date_to, source="v2")

    def load(self, snapshot_date: str):
        return _assets_db().get_prep_snapshot(
            snapshot_date, source="v2", with_meta=True)


class AuctionSnapshotStore:
    """``auction_snapshots`` 只读适配。"""

    source = "auction"

    def snapshot_dates(self, date_from: str, date_to: str) -> list[str]:
        return _assets_db().list_auction_snapshot_dates(
            date_from=date_from, date_to=date_to, source="v2")

    def load(self, snapshot_date: str) -> list[dict]:
        return _assets_db().list_auction_snapshots(
            snapshot_date, source="v2", with_meta=True)


def build_store(source: str):
    if source == "prep":
        return PrepSnapshotStore()
    if source == "auction":
        return AuctionSnapshotStore()
    raise ValueError(f"未知决策回测来源: {source!r}（合法值 prep / auction）")


__all__ = ["AuctionSnapshotStore", "PrepSnapshotStore", "build_store"]
