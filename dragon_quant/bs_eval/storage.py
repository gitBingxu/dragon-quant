"""``bs_runs`` / ``bs_signals`` 的唯一读写入口（Repository）。

与实盘 ``buy_sell_signals`` 完全隔离；只写 ``bs_*`` 两表。写入持
``infra.storage._base._lock``，每次操作独立连接（沿用 ``live_trade/storage.py``）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from dragon_quant.infra import clock
from dragon_quant.infra.storage._base import (
    _connect,
    _ensure_schema,
    _lock,
)

SHANGHAI = "Asia/Shanghai"

_COLUMNS = (
    "trade_date", "code", "name", "source", "strategy_hash",
    "evaluator_version", "eval_status", "eval_note", "is_terminal",
    "data_quality", "replay_days", "fallback_days", "suspended_days",
    "fault_days",
    "buy_date", "buy_rule_code", "buy_rule_text", "buy_loose_price",
    "buy_strict_price", "buy_data_quality", "buy_loose_max_return_5d",
    "buy_loose_max_drawdown_5d", "buy_strict_max_return_5d",
    "buy_strict_max_drawdown_5d", "buy_max_return_hold_days",
    "buy_window_partial", "sell_date", "sell_rule_code", "sell_rule_text",
    "sell_price", "sell_hold_days", "sell_data_quality", "sell_half_date",
    "sell_half_price", "sell_half_qty", "sell_half_rule_code",
    "sell_half_max_return_5d", "sell_half_max_drawdown_5d",
    "sell_half_window_days",
    "sell_max_return_5d", "sell_max_drawdown_5d", "sell_max_return_hold_days",
    "sell_post_exit_window_days",
    "sell_window_partial",
)

_HASH_EXCLUDED = {
    "run_id", "content_hash", "last_attempt_at", "created_at", "updated_at",
}

_RUN_COLUMNS = (
    "started_at", "finished_at", "range_from", "range_to", "limit_n", "force",
    "evaluator_version", "strategy_name", "strategy_params_json",
    "strategy_hash", "rows_scanned", "rows_skipped_terminal", "rows_evaluated",
    "rows_no_signal", "rows_bought", "rows_sold", "rows_partial", "rows_error",
    "note",
)

_COUNTER_KEYS = (
    "rows_scanned", "rows_skipped_terminal", "rows_evaluated",
    "rows_no_signal", "rows_bought", "rows_sold", "rows_partial", "rows_error",
)

#: 公开别名：run 计数器键的唯一来源。
COUNTER_KEYS = _COUNTER_KEYS

_BUY_SORT = {
    "trade_date": "trade_date",
    "buy_date": "buy_date",
    "code": "code",
    "strict_max_return_5d": "buy_strict_max_return_5d",
    "loose_max_return_5d": "buy_loose_max_return_5d",
    "strict_max_drawdown_5d": "buy_strict_max_drawdown_5d",
    "loose_max_drawdown_5d": "buy_loose_max_drawdown_5d",
}

_SELL_SORT = {
    "trade_date": "trade_date",
    "sell_date": "sell_date",
    "code": "code",
    "sell_max_return_5d": "sell_max_return_5d",
    "sell_max_drawdown_5d": "sell_max_drawdown_5d",
}


def _local_now() -> str:
    from zoneinfo import ZoneInfo

    return clock.now(ZoneInfo(SHANGHAI)).strftime("%Y-%m-%d %H:%M:%S")


def content_hash(payload: dict) -> str:
    """稳定业务字段哈希（排除运行/时间等易变列）。"""

    stable = {
        key: value for key, value in payload.items()
        if key in _COLUMNS and key not in _HASH_EXCLUDED
    }
    blob = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def start_run(*, range_from: Optional[str], range_to: Optional[str],
              limit_n: Optional[int], force: bool, evaluator_version: str,
              strategy_name: str, strategy_params: dict,
              strategy_hash: str) -> int:
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            cursor = conn.execute(
                "INSERT INTO bs_runs(range_from, range_to, limit_n, force, "
                "evaluator_version, strategy_name, strategy_params_json, "
                "strategy_hash, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (range_from, range_to, limit_n, 1 if force else 0,
                 evaluator_version, strategy_name,
                 json.dumps(strategy_params, ensure_ascii=False, sort_keys=True),
                 strategy_hash, _local_now()),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)
        finally:
            conn.close()


def finish_run(run_id: int, counters: dict, note: Optional[str] = None) -> None:
    values = {key: int(counters.get(key) or 0) for key in _COUNTER_KEYS}
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute(
                "UPDATE bs_runs SET finished_at = ?, rows_scanned = ?, "
                "rows_skipped_terminal = ?, rows_evaluated = ?, rows_no_signal = ?, "
                "rows_bought = ?, rows_sold = ?, rows_partial = ?, rows_error = ?, "
                "note = ? WHERE id = ?",
                (_local_now(), values["rows_scanned"],
                 values["rows_skipped_terminal"], values["rows_evaluated"],
                 values["rows_no_signal"], values["rows_bought"],
                 values["rows_sold"], values["rows_partial"],
                 values["rows_error"], note, run_id),
            )
            conn.commit()
        finally:
            conn.close()


def _run_dict(row) -> dict:
    data = dict(zip(("id",) + _RUN_COLUMNS, row))
    data["force"] = bool(data.get("force"))
    params = data.pop("strategy_params_json", None)
    try:
        data["strategy_params"] = json.loads(params) if params else {}
    except (TypeError, ValueError):
        data["strategy_params"] = {}
    data["counters"] = {key: int(data.get(key) or 0) for key in _COUNTER_KEYS}
    return data


def latest_run() -> Optional[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            f"SELECT id, {', '.join(_RUN_COLUMNS)} FROM bs_runs "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _run_dict(row) if row else None
    finally:
        conn.close()


def list_existing(date_from: Optional[str] = None,
                  date_to: Optional[str] = None) -> dict[tuple[str, str], dict]:
    conditions: list[str] = []
    params: list = []
    if date_from:
        conditions.append("trade_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("trade_date <= ?")
        params.append(date_to)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            f"SELECT {', '.join(_COLUMNS)}, content_hash, last_attempt_at, "
            f"created_at, updated_at FROM bs_signals{where}",
            params,
        ).fetchall()
        result: dict[tuple[str, str], dict] = {}
        for row in rows:
            data = dict(zip(
                _COLUMNS + ("content_hash", "last_attempt_at", "created_at",
                            "updated_at"), row))
            result[(data["trade_date"], data["code"])] = data
        return result
    finally:
        conn.close()


def upsert_signal(payload: dict, *, force: bool = False) -> bool:
    """写入一行；返回是否写入业务字段（False = 哈希相同，仅轻量写尝试时间）。"""

    data = dict(payload)
    # NOT NULL 计数列的稳定默认值：缺键按 0，不写 NULL 触发约束错误。
    for key in ("is_terminal", "replay_days", "fallback_days", "suspended_days",
                "fault_days", "buy_window_partial", "sell_window_partial"):
        data.setdefault(key, 0)
    data["content_hash"] = content_hash(data)
    now = _local_now()
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT content_hash FROM bs_signals WHERE trade_date = ? AND code = ?",
                (data["trade_date"], data["code"]),
            ).fetchone()
            if existing and not force and existing[0] == data["content_hash"]:
                conn.execute(
                    "UPDATE bs_signals SET last_attempt_at = ? "
                    "WHERE trade_date = ? AND code = ?",
                    (now, data["trade_date"], data["code"]),
                )
                conn.commit()
                return False

            columns = list(_COLUMNS) + ["run_id", "content_hash", "last_attempt_at"]
            values = [data.get(key) for key in _COLUMNS]
            values.extend([data.get("run_id"), data["content_hash"], now])
            if existing:
                assignments = ", ".join(f"{key} = ?" for key in columns)
                conn.execute(
                    f"UPDATE bs_signals SET {assignments}, "
                    "updated_at = datetime('now', 'localtime') "
                    "WHERE trade_date = ? AND code = ?",
                    values + [data["trade_date"], data["code"]],
                )
            else:
                placeholders = ", ".join("?" for _ in columns)
                conn.execute(
                    f"INSERT INTO bs_signals({', '.join(columns)}) "
                    f"VALUES ({placeholders})",
                    values,
                )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _filtered(side: str, filters: dict, strategy_hash: Optional[str],
              evaluator_version: Optional[str]) -> tuple[list[str], list]:
    conditions: list[str] = []
    params: list = []
    side_col = "buy_date" if side == "buy" else "sell_date"
    partial_col = "buy_window_partial" if side == "buy" else "sell_window_partial"
    conditions.append(f"{side_col} IS NOT NULL")
    if filters.get("date_from"):
        conditions.append("trade_date >= ?")
        params.append(filters["date_from"])
    if filters.get("date_to"):
        conditions.append("trade_date <= ?")
        params.append(filters["date_to"])
    if filters.get("code_like"):
        conditions.append("code LIKE ?")
        params.append(f"%{filters['code_like']}%")
    if filters.get("name_like"):
        conditions.append("name LIKE ?")
        params.append(f"%{filters['name_like']}%")
    rules = list(filters.get("rule_codes") or [])
    if rules:
        placeholders = ", ".join("?" for _ in rules)
        rule_col = "buy_rule_code" if side == "buy" else "sell_rule_code"
        conditions.append(f"{rule_col} IN ({placeholders})")
        params.extend(rules)
    if filters.get("exclude_partial"):
        conditions.append(f"{partial_col} = 0")
    if strategy_hash:
        conditions.append("strategy_hash = ?")
        params.append(strategy_hash)
    if evaluator_version:
        conditions.append("evaluator_version = ?")
        params.append(evaluator_version)
    return conditions, params


def query_signals(*, side: str, filters: Optional[dict] = None,
                  sort_by: str = "", sort_dir: str = "desc",
                  strategy_hash: Optional[str] = None,
                  evaluator_version: Optional[str] = None) -> list[dict]:
    filters = filters or {}
    mapping = _BUY_SORT if side == "buy" else _SELL_SORT
    default = "buy_date" if side == "buy" else "sell_date"
    column = mapping.get(sort_by, mapping[default])
    direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
    conditions, params = _filtered(side, filters, strategy_hash,
                                   evaluator_version)
    conn = _connect()
    try:
        _ensure_schema(conn)
        sql = (
            f"SELECT {', '.join(_COLUMNS)} FROM bs_signals "
            f"WHERE {' AND '.join(conditions)} "
            f"ORDER BY {column} {direction} NULLS LAST, trade_date DESC, code ASC"
        )
        rows = conn.execute(sql, params).fetchall()
        return [dict(zip(_COLUMNS, row)) for row in rows]
    finally:
        conn.close()


def list_current(strategy_hash: str, evaluator_version: str) -> list[dict]:
    """当前策略/评测器版本下的全部行（overview 汇总口径）。"""

    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM bs_signals "
            "WHERE strategy_hash = ? AND evaluator_version = ? "
            "ORDER BY trade_date ASC, code ASC",
            (strategy_hash, evaluator_version),
        ).fetchall()
        return [dict(zip(_COLUMNS, row)) for row in rows]
    finally:
        conn.close()


def count_stale(strategy_hash: str, evaluator_version: str) -> int:
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) FROM bs_signals "
            "WHERE strategy_hash != ? OR evaluator_version != ?",
            (strategy_hash, evaluator_version),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


__all__ = [
    "COUNTER_KEYS",
    "content_hash",
    "count_stale",
    "finish_run",
    "latest_run",
    "list_current",
    "list_existing",
    "query_signals",
    "start_run",
    "upsert_signal",
]
