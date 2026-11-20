"""bar 版本的 PIT 查询、区间状态与聚合（只读）。"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from .schema import ObservationStoreError
from .store import _iso_now, _open_read


def select_bars_asof(cutoff: str, *, codes: Optional[list[str]] = None,
                     periods: Optional[list[str]] = None,
                     trade_dates: Optional[list[str]] = None,
                     limit: Optional[int] = 20000, db_path=None,
                     strict: bool = True) -> list[dict]:
    """PIT 选择：每个 bar 身份在 ``cutoff`` 时刻最后可用的 revision。"""
    _iso_now(cutoff)
    conn = _open_read(db_path, strict=strict)
    if conn is None:
        return []
    try:
        where, params = _bar_where(None, None, codes, periods)
        clause = (" AND " if where else " WHERE ")
        date_clause = ""
        date_params: list = []
        if trade_dates:
            date_clause = ("AND b.trade_date IN ("
                           + ", ".join("?" * len(trade_dates)) + ") ")
            date_params = list(trade_dates)
        sql = (
            f"SELECT {_BAR_COLUMNS} FROM market_bar_versions AS b"
            f"{where}{clause}b.available_at <= ? "
            f"{date_clause}"
            "AND b.revision = (SELECT MAX(b2.revision) "
            "                  FROM market_bar_versions AS b2 "
            "                  WHERE b2.market = b.market AND b2.code = b.code "
            "                    AND b2.period = b.period "
            "                    AND b2.adjustment = b.adjustment "
            "                    AND b2.interval_end = b.interval_end "
            "                    AND b2.available_at <= ?) "
            "ORDER BY b.trade_date, b.code, b.period, b.interval_end, b.revision"
        )
        params = list(params) + [cutoff] + date_params + [cutoff]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [_bar_row(row) for row in conn.execute(sql, tuple(params))]
    except sqlite3.Error as exc:
        raise ObservationStoreError(
            f"PIT bar 选择失败: {type(exc).__name__}: {exc}",
            reason="read_error") from exc
    finally:
        conn.close()



_BAR_COLUMNS = (
    "bar_id, trade_date, market, code, period, interval_start, interval_end, "
    "open, high, low, close, volume, amount, adjustment, source, observed_at, "
    "available_at, revision, content_hash, completeness, time_semantics, "
    "unit_semantics, derived, schema_version, run_id, calibration_json"
)


def _bar_row(row) -> dict:
    return {
        "bar_id": row[0], "trade_date": row[1], "market": row[2],
        "code": row[3], "period": row[4], "interval_start": row[5],
        "interval_end": row[6], "open": row[7], "high": row[8], "low": row[9],
        "close": row[10], "volume": row[11], "amount": row[12],
        "adjustment": row[13], "source": row[14], "observed_at": row[15],
        "available_at": row[16], "revision": row[17],
        "content_hash": row[18], "completeness": row[19],
        "time_semantics": row[20], "unit_semantics": row[21],
        "derived": bool(row[22]), "schema_version": row[23], "run_id": row[24],
        "calibration": _loads_calibration(row[25] if len(row) > 25 else None),
    }


def _loads_calibration(text):
    if text is None or text == "":
        return {}
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _bar_where(date_from, date_to, codes, periods) -> tuple[str, list]:
    clauses, params = [], []
    if date_from:
        clauses.append("trade_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("trade_date <= ?")
        params.append(date_to)
    if codes:
        clauses.append(f"code IN ({', '.join('?' * len(codes))})")
        params.extend(codes)
    if periods:
        clauses.append(f"period IN ({', '.join('?' * len(periods))})")
        params.extend(periods)
    return ((" WHERE " + " AND ".join(clauses)) if clauses else ""), params


def query_bars(date_from: Optional[str] = None, date_to: Optional[str] = None,
               *, codes: Optional[list[str]] = None,
               periods: Optional[list[str]] = None,
               limit: Optional[int] = None, offset: int = 0,
               db_path=None, strict: bool = True) -> list[dict]:
    conn = _open_read(db_path, strict=strict)
    if conn is None:
        return []
    try:
        where, params = _bar_where(date_from, date_to, codes, periods)
        sql = (f"SELECT {_BAR_COLUMNS} FROM market_bar_versions{where} "
               "ORDER BY trade_date, code, period, interval_end, revision")
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend((int(limit), int(offset)))
        return [_bar_row(row) for row in conn.execute(sql, tuple(params))]
    except sqlite3.Error as exc:
        raise ObservationStoreError(
            f"读取 market_bar_versions 失败: {type(exc).__name__}: {exc}",
            reason="read_error") from exc
    finally:
        conn.close()


def bar_groups(date_from: Optional[str] = None,
               date_to: Optional[str] = None, *,
               codes: Optional[list[str]] = None,
               periods: Optional[list[str]] = None,
               limit: int = 1000, offset: int = 0,
               db_path=None, strict: bool = True) -> tuple[list[dict], int]:
    """SQL 聚合：``(groups, total_groups)``，按区间去重统计。"""
    conn = _open_read(db_path, strict=strict)
    if conn is None:
        return [], 0
    try:
        where, params = _bar_where(date_from, date_to, codes, periods)
        total = conn.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM market_bar_versions"
            f"{where} GROUP BY trade_date, code, period, adjustment)", tuple(params),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT trade_date, code, period, adjustment, COUNT(*) AS versions, "
            "COUNT(DISTINCT interval_end) AS distinct_intervals, "
            "SUM(CASE WHEN revision > 1 THEN 1 ELSE 0 END) AS revised_versions, "
            "SUM(CASE WHEN time_semantics != 'verified' OR "
            "completeness != 'final' THEN 1 ELSE 0 END) AS unusable_versions, "
            "MIN(interval_end), MAX(interval_end), MAX(revision) "
            f"FROM market_bar_versions{where} "
            "GROUP BY trade_date, code, period, adjustment "
            "ORDER BY trade_date, code, period, adjustment LIMIT ? OFFSET ?",
            tuple(params) + (int(limit), int(offset)),
        ).fetchall()
        return [
            {"trade_date": row[0], "code": row[1], "period": row[2],
             "adjustment": row[3], "versions": row[4],
             "distinct_intervals": row[5], "revised_versions": row[6],
             "unusable_versions": row[7], "first_interval_end": row[8],
             "last_interval_end": row[9], "max_revision": row[10]}
            for row in rows
        ], int(total)
    except sqlite3.Error as exc:
        raise ObservationStoreError(
            f"聚合 market_bar_versions 失败: {type(exc).__name__}: {exc}",
            reason="read_error") from exc
    finally:
        conn.close()


def bar_interval_states(trade_date: str, code: str, period: str, *,
                        adjustment: str = "none", limit: int = 500,
                        db_path=None, strict: bool = True) -> list[dict]:
    """单 (日期, 代码, 周期) 的逐区间最新版本状态（有界）。"""
    conn = _open_read(db_path, strict=strict)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT interval_end, revision, completeness, time_semantics, "
            "unit_semantics, content_hash, available_at FROM market_bar_versions "
            "WHERE trade_date = ? AND code = ? AND period = ? AND adjustment = ? "
            "ORDER BY interval_end, revision DESC LIMIT ?",
            (trade_date, code, period, adjustment, int(limit) * 20),
        ).fetchall()
        latest: dict[str, dict] = {}
        for end, revision, completeness, time_sem, unit_sem, digest, available in rows:
            current = latest.get(end)
            if current is None or revision > current["revision"]:
                latest[end] = {
                    "interval_end": end, "revision": revision,
                    "completeness": completeness,
                    "time_semantics": time_sem, "unit_semantics": unit_sem,
                    "content_hash": digest, "available_at": available,
                    "revisions": (current["revisions"] + 1) if current else 1,
                }
            elif current is not None:
                current["revisions"] += 1
        return [latest[key] for key in sorted(latest)]
    except sqlite3.Error as exc:
        raise ObservationStoreError(
            f"读取区间状态失败: {type(exc).__name__}: {exc}",
            reason="read_error") from exc
    finally:
        conn.close()