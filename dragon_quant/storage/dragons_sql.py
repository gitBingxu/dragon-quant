"""dragons 表 SQL 唯一规则源（``dragon.db.dragons_*`` 与 ``assets.db.dragons`` 共用）。

两张表的列 schema 完全一致（见 ``storage/assets_db.py`` 的 ``dragons`` DDL），
因此 UPSERT / UPDATE / DELETE 只需参数化**表名**即可在两侧复用，
从结构上杜绝「两表列语义漂移」。

语义与 ``storage/dragons.py`` 的历史实现逐字对齐：
  - UPSERT 不覆盖 review 字段（``buy_date`` / ``max_return_*`` / ``review_status``
    由 ``update_review`` 独占写）；
  - 价格与量额列 ``COALESCE``（新值缺失时保留存量，避免重扫把已有行情清空）。

事务由调用方负责（本模块只执行语句，不 commit），以便回填按日期成批提交。
"""

from __future__ import annotations

import json

# 语义列（不含本机自增 id 与本地写入时间 created_at）；一致性判定与回填比对共用。
BUSINESS_COLUMNS: tuple[str, ...] = (
    "trade_date", "code", "name", "scan_id", "rank", "composite_score",
    "board_count", "open_px", "close_px", "high_px", "low_px", "pct",
    "turnover_rate", "amount", "market_cap", "concepts_json", "report_text",
    "is_true_dragon", "version", "buy_date", "buy_price", "max_return_5d",
    "max_drawdown_5d", "max_return_hold_days", "review_status", "source",
)


def build_rows(trade_date: str, dragons: list[dict], version: str,
               source: str) -> list[tuple]:
    """``dragons`` dict 列表 → UPSERT 参数行（与历史 ``save_dragons`` 完全一致）。"""
    rows: list[tuple] = []
    for i, s in enumerate(dragons):
        concepts = s.get("concepts", [])
        rows.append((
            trade_date,
            s.get("code", ""),
            s.get("name", ""),
            s.get("scan_id", ""),
            s.get("rank", i + 1),
            s.get("composite_score", 0),
            s.get("board_count", 0),
            s.get("open_px"),
            s.get("close_px"),
            s.get("high_px"),
            s.get("low_px"),
            s.get("pct"),
            s.get("turnover_rate"),
            s.get("amount"),
            s.get("market_cap"),
            json.dumps(concepts, ensure_ascii=False),
            s.get("report_text", ""),
            1 if s.get("is_true_dragon") else 0 if "is_true_dragon" in s else None,
            version,
            source,
        ))
    return rows


def upsert_dragons(conn, table: str, trade_date: str, dragons: list[dict],
                   version: str, source: str) -> None:
    """按 ``(trade_date, code)`` UPSERT，不覆盖 review 字段，价格列 COALESCE。"""
    rows = build_rows(trade_date, dragons, version, source)
    if not rows:
        return
    conn.executemany(
        f"INSERT INTO {table}("
        "trade_date, code, name, scan_id, rank, composite_score, board_count, "
        "open_px, close_px, high_px, low_px, pct, turnover_rate, amount, market_cap, "
        "concepts_json, report_text, is_true_dragon, version, source"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(trade_date, code) DO UPDATE SET "
        "name=excluded.name, "
        "scan_id=excluded.scan_id, "
        "rank=excluded.rank, "
        "composite_score=excluded.composite_score, "
        "board_count=excluded.board_count, "
        f"open_px=COALESCE(excluded.open_px, {table}.open_px), "
        f"close_px=COALESCE(excluded.close_px, {table}.close_px), "
        f"high_px=COALESCE(excluded.high_px, {table}.high_px), "
        f"low_px=COALESCE(excluded.low_px, {table}.low_px), "
        f"pct=COALESCE(excluded.pct, {table}.pct), "
        f"turnover_rate=COALESCE(excluded.turnover_rate, {table}.turnover_rate), "
        f"amount=COALESCE(excluded.amount, {table}.amount), "
        f"market_cap=COALESCE(excluded.market_cap, {table}.market_cap), "
        "concepts_json=excluded.concepts_json, "
        "report_text=excluded.report_text, "
        "is_true_dragon=excluded.is_true_dragon, "
        "version=excluded.version, "
        "source=excluded.source",
        rows,
    )


def update_review(conn, table: str, trade_date: str, code: str, *,
                  buy_date=None, buy_price=None, max_return_5d=None,
                  max_drawdown_5d=None, max_return_hold_days=None,
                  review_status: str = "completed") -> int:
    """更新单条 review 字段，返回 rowcount（0 = 目标库缺行）。"""
    cur = conn.execute(
        f"UPDATE {table} SET "
        "buy_date = ?, "
        "buy_price = ?, "
        "max_return_5d = ?, "
        "max_drawdown_5d = ?, "
        "max_return_hold_days = ?, "
        "review_status = ? "
        "WHERE trade_date = ? AND code = ?",
        (buy_date, buy_price, max_return_5d, max_drawdown_5d,
         max_return_hold_days, review_status, trade_date, code),
    )
    return cur.rowcount


def delete_pending_not_in(conn, table: str, trade_date: str,
                          keep_codes: set[str]) -> int:
    """删除某日不在 ``keep_codes`` 内且 ``review_status='pending'`` 的记录。"""
    if not keep_codes:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE trade_date = ? AND review_status = 'pending'",
            (trade_date,),
        )
        return cur.rowcount

    placeholders = ",".join(["?"] * len(keep_codes))
    params = [trade_date] + sorted(keep_codes)
    cur = conn.execute(
        f"DELETE FROM {table} WHERE trade_date = ? "
        f"AND review_status = 'pending' AND code NOT IN ({placeholders})",
        params,
    )
    return cur.rowcount
