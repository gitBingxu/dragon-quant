"""观测的 PIT 查询与聚合（只读）。"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from .schema import ObservationStoreError
from .store import _iso_now, _loads_strict, _open_read


def select_observations_asof(cutoff: str, *, codes: Optional[list[str]] = None,
                             topics: Optional[list[str]] = None,
                             trade_dates: Optional[list[str]] = None,
                             limit: Optional[int] = 5000, db_path=None,
                             strict: bool = True) -> list[dict]:
    """PIT 选择：每个语义键在 ``cutoff`` 时刻**最后实际可用**的 revision。

    只选 ``available_at <= cutoff`` 的版本；``valid_until`` 已过期的不选。绝不
    退化为「最新 revision」或在 ``first_received_at`` 上伪造可用时点。
    """
    _iso_now(cutoff)
    conn = _open_read(db_path, strict=strict)
    if conn is None:
        return []
    try:
        where, params = _obs_where(None, None, codes, topics)
        clause = (" AND " if where else " WHERE ")
        date_clause = ""
        date_params: list = []
        if trade_dates:
            date_clause = ("AND o.trade_date IN ("
                           + ", ".join("?" * len(trade_dates)) + ") ")
            date_params = list(trade_dates)
        sql = (
            f"SELECT {_OBS_COLUMNS} FROM market_observations AS o"
            f"{where}{clause}o.available_at <= ? "
            f"{date_clause}"
            "AND (o.valid_until IS NULL OR o.valid_until >= ?) "
            "AND o.revision = (SELECT MAX(o2.revision) "
            "                  FROM market_observations AS o2 "
            "                  WHERE o2.semantic_key = o.semantic_key "
            "                    AND o2.available_at <= ?) "
            "ORDER BY o.trade_date, o.obs_type, o.code, o.revision"
        )
        params = list(params) + [cutoff] + date_params + [cutoff, cutoff]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [_observation_row(row) for row in conn.execute(sql, tuple(params))]
    except sqlite3.Error as exc:
        raise ObservationStoreError(
            f"PIT 观测选择失败: {type(exc).__name__}: {exc}",
            reason="read_error") from exc
    finally:
        conn.close()



_OBS_COLUMNS = (
    "obs_id, trade_date, market, code, obs_type, source, source_event_time, "
    "received_at, first_received_at, available_at, valid_until, units_json, "
    "quality, schema_version, revision, content_hash, semantic_key, "
    "payload_json, run_id, calibration_json"
)


def _observation_row(row) -> dict:
    return {
        "obs_id": row[0], "trade_date": row[1], "market": row[2],
        "code": row[3], "obs_type": row[4], "source": row[5],
        "source_event_time": row[6], "received_at": row[7],
        "first_received_at": row[8], "available_at": row[9],
        "valid_until": row[10],
        "units": _loads_strict(row[11], column="market_observations.units_json",
                               default={}),
        "quality": row[12], "schema_version": row[13], "revision": row[14],
        "content_hash": row[15], "semantic_key": row[16],
        "payload": _loads_strict(row[17],
                                 column="market_observations.payload_json"),
        "run_id": row[18],
        "calibration": _loads_calibration(row[19] if len(row) > 19 else None),
    }


def _loads_calibration(text):
    if text is None or text == "":
        return {}
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _obs_where(date_from, date_to, codes, topics) -> tuple[str, list]:
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
    if topics:
        clauses.append(f"obs_type IN ({', '.join('?' * len(topics))})")
        params.extend(topics)
    return ((" WHERE " + " AND ".join(clauses)) if clauses else ""), params


def query_observations(date_from: Optional[str] = None,
                       date_to: Optional[str] = None,
                       *, codes: Optional[list[str]] = None,
                       topics: Optional[list[str]] = None,
                       limit: Optional[int] = None, offset: int = 0,
                       db_path=None, strict: bool = True) -> list[dict]:
    conn = _open_read(db_path, strict=strict)
    if conn is None:
        return []
    try:
        where, params = _obs_where(date_from, date_to, codes, topics)
        sql = (f"SELECT {_OBS_COLUMNS} FROM market_observations{where} "
               "ORDER BY trade_date, obs_type, code, revision")
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend((int(limit), int(offset)))
        return [_observation_row(row) for row in conn.execute(sql, tuple(params))]
    except sqlite3.Error as exc:
        raise ObservationStoreError(
            f"读取 market_observations 失败: {type(exc).__name__}: {exc}",
            reason="read_error") from exc
    finally:
        conn.close()


def observation_groups(date_from: Optional[str] = None,
                       date_to: Optional[str] = None, *,
                       codes: Optional[list[str]] = None,
                       topics: Optional[list[str]] = None,
                       limit: int = 1000, offset: int = 0,
                       db_path=None, strict: bool = True) -> tuple[list[dict], int]:
    """SQL 聚合：返回 ``(groups, total_groups)``，长历史下有界。"""
    conn = _open_read(db_path, strict=strict)
    if conn is None:
        return [], 0
    try:
        where, params = _obs_where(date_from, date_to, codes, topics)
        total = conn.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM market_observations"
            f"{where} GROUP BY trade_date, code, obs_type)", tuple(params),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT trade_date, code, obs_type, COUNT(*) AS versions, "
            "COUNT(DISTINCT content_hash) AS distinct_contents, "
            "MAX(revision) AS max_revision, "
            "MAX(received_at) AS latest_received_at, "
            "MIN(first_received_at) AS first_received_at, "
            "SUM(CASE WHEN quality != 'semantics_verified' THEN 1 ELSE 0 END) "
            "AS unverified_versions "
            f"FROM market_observations{where} "
            "GROUP BY trade_date, code, obs_type "
            "ORDER BY trade_date, obs_type, code LIMIT ? OFFSET ?",
            tuple(params) + (int(limit), int(offset)),
        ).fetchall()
        return [
            {"trade_date": row[0], "code": row[1], "obs_type": row[2],
             "versions": row[3], "distinct_contents": row[4],
             "max_revision": row[5], "latest_received_at": row[6],
             "first_received_at": row[7], "unverified_versions": row[8]}
            for row in rows
        ], int(total)
    except sqlite3.Error as exc:
        raise ObservationStoreError(
            f"聚合 market_observations 失败: {type(exc).__name__}: {exc}",
            reason="read_error") from exc
    finally:
        conn.close()
