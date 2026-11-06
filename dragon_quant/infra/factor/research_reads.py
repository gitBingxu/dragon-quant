"""研究查询用的因子存储只读仓储（``mode=ro``，不建库/不建表）。"""

from __future__ import annotations

import json
from typing import Optional

from dragon_quant.infra.assets import db as _assets_db
from dragon_quant.infra.storage.readonly import open_readonly


def _loads(text, default):
    if text is None:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return {"_corrupt": f"{type(exc).__name__}: {exc}"}


def _nested_corrupt(*values) -> bool:
    return any(isinstance(value, dict) and "_corrupt" in value for value in values)


def _clamp(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(int(limit), 1001)), max(0, int(offset))


def factor_values(*, date_from: Optional[str] = None, date_to: Optional[str] = None,
                  code: Optional[str] = None, limit: int = 100,
                  offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(_assets_db.ASSETS_DB_PATH)
    if conn is None:
        return None
    try:
        sql = ("SELECT trade_date, code, factor_name, factor_version, value, "
               "available_at, source, source_scan_id, source_meta_json, computed_at "
               "FROM factor_values WHERE 1=1")
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
        sql += (" ORDER BY trade_date DESC, code ASC, factor_name ASC, "
                "factor_version ASC LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        return [
            {
                "trade_date": r[0], "code": r[1], "factor_name": r[2],
                "factor_version": r[3], "value": r[4], "available_at": r[5],
                "source": r[6], "source_scan_id": r[7],
                "source_meta": _loads(r[8], {}), "computed_at": r[9],
                "_corrupt": _nested_corrupt(_loads(r[8], {})),
            }
            for r in conn.execute(sql, params).fetchall()
        ]
    finally:
        conn.close()


def factor_registry(*, limit: int = 100, offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(_assets_db.ASSETS_DB_PATH)
    if conn is None:
        return None
    try:
        limit, offset = _clamp(limit, offset)
        rows = conn.execute(
            "SELECT factor_name, factor_version, label, frequency, source, "
            "depends_on_json, lookback, direction, unit, available_phase, "
            "pit_rule, scope_json, params_json, default_enabled, valid_from, "
            "deprecated_at, owner, description, spec_hash, created_at "
            "FROM factor_registry ORDER BY factor_name ASC, factor_version ASC "
            "LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [
            {
                "factor_name": r[0], "factor_version": r[1], "label": r[2],
                "frequency": r[3], "source": r[4],
                "depends_on": _loads(r[5], []), "lookback": r[6],
                "direction": r[7], "unit": r[8], "available_phase": r[9],
                "pit_rule": r[10], "scope": _loads(r[11], {}),
                "params": _loads(r[12], {}),
                "default_enabled": bool(r[13]) if r[13] is not None else False,
                "valid_from": r[14], "deprecated_at": r[15], "owner": r[16],
                "description": r[17], "spec_hash": r[18], "created_at": r[19],
                "_corrupt": _nested_corrupt(_loads(r[5], []), _loads(r[11], {}),
                                            _loads(r[12], {})),
            }
            for r in rows
        ]
    finally:
        conn.close()


def factor_build_runs(*, date_from: Optional[str] = None,
                      date_to: Optional[str] = None, include_report: bool = False,
                      limit: int = 100, offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(_assets_db.ASSETS_DB_PATH)
    if conn is None:
        return None
    try:
        sql = ("SELECT id, trade_date, started_at, finished_at, created_at, report_json "
               "FROM factor_build_runs WHERE 1=1")
        params: list = []
        if date_from:
            sql += " AND trade_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND trade_date <= ?"
            params.append(date_to)
        limit, offset = _clamp(limit, offset)
        sql += " ORDER BY trade_date DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        result = []
        for r in conn.execute(sql, params).fetchall():
            item = {"build_id": r[0], "trade_date": r[1], "started_at": r[2],
                    "finished_at": r[3], "created_at": r[4]}
            if include_report:
                item["report"] = _loads(r[5], {})
                item["_corrupt"] = _nested_corrupt(item["report"])
            result.append(item)
        return result
    finally:
        conn.close()


def factor_models(*, limit: int = 100, offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(_assets_db.ASSETS_DB_PATH)
    if conn is None:
        return None
    try:
        limit, offset = _clamp(limit, offset)
        rows = conn.execute(
            "SELECT model_name, model_version, params_json, factor_versions_json, "
            "created_at FROM factor_model_snapshots "
            "ORDER BY model_name ASC, model_version ASC LIMIT ? OFFSET ?",
            (limit, offset)).fetchall()
        return [
            {
                "model_name": r[0], "model_version": r[1],
                "params": _loads(r[2], {}),
                "factor_versions": _loads(r[3], {}),
                "created_at": r[4],
                "_corrupt": _nested_corrupt(_loads(r[2], {}), _loads(r[3], {})),
            }
            for r in rows
        ]
    finally:
        conn.close()


def table_present(table: str) -> Optional[bool]:
    conn = open_readonly(_assets_db.ASSETS_DB_PATH)
    if conn is None:
        return None
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone() is not None
    finally:
        conn.close()
