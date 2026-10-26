"""严格口径回测结果仓储（``dragon.db.dragon_review_strict``）。

设计要点（与 ``dragons_v2`` 的宽松字段完全隔离）：

* ``dragons_*`` 的 ``buy_date`` / ``max_return_5d`` 等字段保持**宽松口径**
  （入选后首个 ``high != low`` 日按最低价买入）语义与生命周期不变；
* 严格口径（入选后首个交易日按开盘价买入）单独落本表，唯一键
  ``(trade_date, code, source)``；**无行 = 尚未重算**，不写 0 / 中性值；
* 只提供消费者需要的 API：``upsert`` / ``get`` / ``list_keys``。写入函数接收
  调用方连接，便于 ``save_dual_review`` 在同一事务内同时写宽松 + 严格。

本模块不做连接 / 锁管理（由 ``scan.storage.dragons`` 统一负责），但读接口为了
调用方便自带连接。
"""

from __future__ import annotations

from typing import Iterable, Optional

from dragon_quant._version import __version__
from dragon_quant.infra.storage._base import _connect, _ensure_schema

COLUMNS = (
    "trade_date", "code", "source", "buy_date", "buy_price",
    "max_return_5d", "max_drawdown_5d", "max_return_hold_days",
    "review_status", "reason", "review_version", "computed_at",
)


def upsert(conn, *,
           trade_date: str,
           code: str,
           source: str,
           buy_date: Optional[str],
           buy_price: Optional[float],
           max_return_5d: Optional[float],
           max_drawdown_5d: Optional[float],
           max_return_hold_days: Optional[int],
           review_status: str,
           reason: Optional[str] = None,
           review_version: str = __version__) -> None:
    """按 ``(trade_date, code, source)`` UPSERT；调用方负责事务与提交。"""
    conn.execute(
        "INSERT INTO dragon_review_strict("
        "trade_date, code, source, buy_date, buy_price, max_return_5d, "
        "max_drawdown_5d, max_return_hold_days, review_status, reason, "
        "review_version"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(trade_date, code, source) DO UPDATE SET "
        "buy_date=excluded.buy_date, "
        "buy_price=excluded.buy_price, "
        "max_return_5d=excluded.max_return_5d, "
        "max_drawdown_5d=excluded.max_drawdown_5d, "
        "max_return_hold_days=excluded.max_return_hold_days, "
        "review_status=excluded.review_status, "
        "reason=excluded.reason, "
        "review_version=excluded.review_version, "
        "computed_at=datetime('now', 'localtime')",
        (trade_date, code, source, buy_date, buy_price, max_return_5d,
         max_drawdown_5d, max_return_hold_days, review_status, reason,
         review_version),
    )


def get(trade_date: str, code: str, source: str = "v2") -> Optional[dict]:
    """返回单条严格结果；无行返回 ``None``（= 尚未重算）。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT trade_date, code, source, buy_date, buy_price, "
            "max_return_5d, max_drawdown_5d, max_return_hold_days, "
            "review_status, reason, review_version, computed_at "
            "FROM dragon_review_strict "
            "WHERE trade_date = ? AND code = ? AND source = ?",
            (trade_date, code, source),
        ).fetchone()
        if row is None:
            return None
        return dict(zip(COLUMNS, row))
    finally:
        conn.close()


def list_keys(source: str = "v2") -> set[tuple[str, str]]:
    """返回已有严格结果的 ``(trade_date, code)`` 集合（重算跳过用）。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT trade_date, code FROM dragon_review_strict WHERE source = ?",
            (source,),
        ).fetchall()
        return {(row[0], row[1]) for row in rows}
    finally:
        conn.close()


def upsert_many(conn, rows: Iterable[dict], source: str = "v2") -> None:
    """批量写入（调用方持有事务）；便于测试与将来的回填脚本复用。"""
    for row in rows:
        upsert(conn, source=source, **row)
