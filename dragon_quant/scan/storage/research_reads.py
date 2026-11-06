"""研究查询用的扫描侧只读仓储（固定 v2，``mode=ro``，零 DDL）。

SQL 与字段映射留在 owner 层；research 只组合本模块的窄函数。缺库返回
``None`` / 空列表由调用方区分；缺表等 ``sqlite3.Error`` 交给 research 映射为
可解释降级。
"""

from __future__ import annotations

import json
from typing import Optional

from dragon_quant.infra.storage import paths
from dragon_quant.infra.storage.identifiers import _tables
from dragon_quant.infra.storage.readonly import open_readonly

SOURCE = "v2"


def _loads_checked(text, default):
    if text is None:
        return default, None
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return default, f"{type(exc).__name__}: {exc}"


def _loads(text):
    return _loads_checked(text, [])[0]


def _bad(text):
    return _loads_checked(text, None)[1]


def _clamp(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(int(limit), 1001)), max(0, int(offset))


def scan_runs(*, date_from: Optional[str] = None, date_to: Optional[str] = None,
              scan_id: Optional[str] = None, limit: int = 100,
              offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        t = _tables(SOURCE)
        sql = (f"SELECT id, scan_date, elapsed_s, top_n, candidates_n, workers, "
               f"created_at, source FROM {t['scans']} WHERE 1=1")
        params: list = []
        if date_from:
            sql += " AND scan_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND scan_date <= ?"
            params.append(date_to)
        if scan_id:
            sql += " AND id = ?"
            params.append(scan_id)
        limit, offset = _clamp(limit, offset)
        sql += " ORDER BY scan_date DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [
            {"scan_id": r[0], "scan_date": r[1], "elapsed_s": r[2], "top_n": r[3],
             "candidates_n": r[4], "workers": r[5], "created_at": r[6],
             "source": r[7] or SOURCE}
            for r in conn.execute(sql, params).fetchall()
        ]
    finally:
        conn.close()


def scan_candidates(*, date_from: Optional[str] = None,
                    date_to: Optional[str] = None, code: Optional[str] = None,
                    scan_id: Optional[str] = None, limit: int = 100,
                    offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        t = _tables(SOURCE)
        sql = (f"SELECT s.id, s.scan_date, s.top_n, s.created_at, ss.code, ss.name, "
               f"ss.rank, ss.composite_score, ss.board_count, ss.concepts_json, "
               f"ss.dim_drive, ss.dim_anti_drop, ss.dim_leadership, "
               f"ss.dim_absorption, ss.dim_liquidity, ss.is_true_dragon, "
               f"ss.reject_reason, ss.open_px, ss.close_px, ss.high_px, ss.low_px, "
               f"ss.pct, ss.turnover_rate, ss.amount, ss.market_cap "
               f"FROM {t['scans']} s JOIN {t['scan_stocks']} ss ON ss.scan_id = s.id "
               "WHERE 1=1")
        params: list = []
        if date_from:
            sql += " AND s.scan_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND s.scan_date <= ?"
            params.append(date_to)
        if code:
            sql += " AND ss.code = ?"
            params.append(code)
        if scan_id:
            sql += " AND s.id = ?"
            params.append(scan_id)
        limit, offset = _clamp(limit, offset)
        sql += (" ORDER BY s.scan_date DESC, s.id DESC, ss.rank ASC, ss.code ASC "
                "LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "scan_id": r[0], "scan_date": r[1], "scan_top_n": r[2],
                "scan_created_at": r[3], "code": r[4], "name": r[5],
                "rank": r[6], "composite_score": r[7], "board_count": r[8],
                "concepts": _loads(r[9]),
                "dim_drive": r[10], "dim_anti_drop": r[11],
                "dim_leadership": r[12], "dim_absorption": r[13],
                "dim_liquidity": r[14],
                "is_true_dragon": (bool(r[15]) if r[15] is not None else None),
                "reject_reason": r[16], "open_px": r[17], "close_px": r[18],
                "high_px": r[19], "low_px": r[20], "pct": r[21],
                "turnover_rate": r[22], "amount": r[23], "market_cap": r[24],
                "source": SOURCE,
                "_corrupt": _bad(r[9]),
            }
            for r in rows
        ]
    finally:
        conn.close()


def dragons(*, date_from: Optional[str] = None, date_to: Optional[str] = None,
            code: Optional[str] = None, limit: int = 100,
            offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        t = _tables(SOURCE)
        sql = (f"SELECT trade_date, code, name, scan_id, rank, composite_score, "
               f"board_count, open_px, close_px, high_px, low_px, pct, "
               f"turnover_rate, amount, market_cap, concepts_json, is_true_dragon, "
               f"review_status, version, buy_date, buy_price, max_return_5d, "
               f"max_drawdown_5d FROM {t['dragons']} WHERE 1=1")
        params: list = []
        if date_from:
            sql += " AND trade_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND trade_date <= ?"
            params.append(date_to)
        if code:
            sql += " AND code = ?"
            params.append(code)
        limit, offset = _clamp(limit, offset)
        sql += " ORDER BY trade_date DESC, code ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "trade_date": r[0], "code": r[1], "name": r[2], "scan_id": r[3],
                "rank": r[4], "composite_score": r[5], "board_count": r[6],
                "open_px": r[7], "close_px": r[8], "high_px": r[9],
                "low_px": r[10], "pct": r[11], "turnover_rate": r[12],
                "amount": r[13], "market_cap": r[14], "concepts": _loads(r[15]),
                "is_true_dragon": (bool(r[16]) if r[16] is not None else None),
                "review_status": r[17], "version": r[18], "buy_date": r[19],
                "buy_price": r[20], "max_return_5d": r[21],
                "max_drawdown_5d": r[22], "source": SOURCE,
                "_corrupt": _bad(r[15]),
            }
            for r in rows
        ]
    finally:
        conn.close()


def review_strict(*, date_from: Optional[str] = None,
                  date_to: Optional[str] = None, code: Optional[str] = None,
                  limit: int = 100, offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        sql = ("SELECT trade_date, code, source, buy_date, buy_price, "
               "max_return_5d, max_drawdown_5d, max_return_hold_days, "
               "review_status, reason, review_version, computed_at "
               "FROM dragon_review_strict WHERE source = ?")
        params: list = [SOURCE]
        if date_from:
            sql += " AND trade_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND trade_date <= ?"
            params.append(date_to)
        if code:
            sql += " AND code = ?"
            params.append(code)
        limit, offset = _clamp(limit, offset)
        sql += " ORDER BY trade_date DESC, code ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [dict(zip((
            "trade_date", "code", "source", "buy_date", "buy_price",
            "max_return_5d", "max_drawdown_5d", "max_return_hold_days",
            "review_status", "reason", "review_version", "computed_at"), r))
            for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def table_present(table: str) -> Optional[bool]:
    """缺库 → ``None``；否则该物理表是否存在（表名由数据集注册表给出）。"""
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()
        return row is not None
    finally:
        conn.close()


# ─── scan-quality 研究编排使用的只读适配（与 scans.py 同口径）──────────────


def scan_dates(date_from: Optional[str] = None,
               date_to: Optional[str] = None) -> Optional[list[str]]:
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        t = _tables(SOURCE)
        sql = f"SELECT DISTINCT scan_date FROM {t['scans']}"
        params: list = []
        conditions: list[str] = []
        if date_from:
            conditions.append("scan_date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("scan_date <= ?")
            params.append(date_to)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY scan_date ASC"
        return [r[0] for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def contributions_by_date(scan_date: str) -> Optional[list[dict]]:
    """某日所有扫描 run 的贡献明细（字段与 ``scans`` 版本一致）。"""
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        t = _tables(SOURCE)
        rows = conn.execute(
            "SELECT s.id, s.top_n, s.created_at, ss.code, ss.name, ss.rank, "
            "ss.composite_score, ss.board_count, ss.concepts_json, ss.report_text, "
            "ss.open_px, ss.close_px, ss.high_px, ss.low_px, ss.pct, "
            "ss.turnover_rate, ss.amount, ss.market_cap, ss.is_true_dragon, "
            "ss.reject_reason "
            f"FROM {t['scans']} s JOIN {t['scan_stocks']} ss ON ss.scan_id = s.id "
            "WHERE s.scan_date = ?", (scan_date,)).fetchall()
        return [
            {
                "scan_id": r[0], "scan_top_n": r[1], "scan_created_at": r[2],
                "code": r[3], "name": r[4], "rank": r[5],
                "composite_score": r[6], "board_count": r[7],
                "concepts": _loads(r[8]), "report_text": r[9] or "",
                "open_px": r[10], "close_px": r[11], "high_px": r[12],
                "low_px": r[13], "pct": r[14], "turnover_rate": r[15],
                "amount": r[16], "market_cap": r[17],
                "is_true_dragon": (bool(r[18]) if r[18] is not None else None),
                "reject_reason": r[19], "source": SOURCE,
            }
            for r in rows
        ]
    finally:
        conn.close()
