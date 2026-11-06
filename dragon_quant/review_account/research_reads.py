"""研究查询用的账户/信号只读仓储（``mode=ro``，零 DDL，不触发回测）。"""

from __future__ import annotations

import json
from typing import Optional

from dragon_quant.infra.storage import paths
from dragon_quant.infra.storage.readonly import open_readonly


def _loads_checked(text, default):
    if not text:
        return default, None
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return default, f"{type(exc).__name__}: {exc}"


def _value(text, default):
    return _loads_checked(text, default)[0]


def _bad(text):
    return _loads_checked(text, None)[1]


def _corrupt(**pairs):
    return [name for name, error in pairs.items() if error] or None


def _clamp(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(int(limit), 1001)), max(0, int(offset))


def _rows(conn, sql: str, params: list) -> list[tuple]:
    return conn.execute(sql, params).fetchall()


def account_runs(*, run_id: Optional[int] = None, date_from: Optional[str] = None,
                 date_to: Optional[str] = None, limit: int = 100,
                 offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        sql = ("SELECT id, source, display_name, strategy_name, strategy_params_json, "
               "date_from, date_to, initial_cash, base_equity, final_equity, "
               "total_return, max_drawdown, trade_count, win_rate, created_at, "
               "base_run_id, start_equity, start_positions_json, start_realized_pnl, "
               "start_cash, start_peak_equity, start_consecutive_losses, "
               "start_marks_json, run_meta_json FROM review_account_runs WHERE source = 'v2'")
        params: list = []
        if run_id is not None:
            sql += " AND id = ?"
            params.append(int(run_id))
        if date_from:
            sql += " AND date_to >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND date_from <= ?"
            params.append(date_to)
        limit, offset = _clamp(limit, offset)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [
            {
                "run_id": r[0], "source": r[1], "display_name": r[2],
                "strategy_name": r[3], "strategy_params": _value(r[4], {}),
                "date_from": r[5], "date_to": r[6], "initial_cash": r[7],
                "base_equity": r[8], "final_equity": r[9],
                "total_return": r[10], "max_drawdown": r[11],
                "trade_count": r[12], "win_rate": r[13], "created_at": r[14],
                "base_run_id": r[15], "start_equity": r[16],
                "start_positions": _value(r[17], []),
                "start_state_recorded": r[17] is not None,
                "start_realized_pnl": r[18], "start_cash": r[19],
                "start_peak_equity": r[20],
                "start_consecutive_losses": r[21],
                "start_marks": _value(r[22], {}),
                "run_meta": _value(r[23], {}),
                "_corrupt": _corrupt(strategy_params=_bad(r[4]),
                                     start_positions=_bad(r[17]),
                                     start_marks=_bad(r[22]),
                                     run_meta=_bad(r[23])),
            }
            for r in _rows(conn, sql, params)
        ]
    finally:
        conn.close()


def account_snapshots(*, run_id: Optional[int] = None,
                      date_from: Optional[str] = None,
                      date_to: Optional[str] = None, code: Optional[str] = None,
                      limit: int = 100, offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        sql = ("SELECT run_id, trade_date, cash, market_value, total_equity, "
               "daily_return, cumulative_return, drawdown, position_code, "
               "position_name, position_qty, position_cost, position_market_price, "
               "position_unrealized_return, positions_json "
               "FROM review_account_snapshots WHERE run_id IN (SELECT id FROM review_account_runs WHERE source = 'v2')")
        params: list = []
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(int(run_id))
        if date_from:
            sql += " AND trade_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND trade_date <= ?"
            params.append(date_to)
        if code:
            sql += " AND position_code = ?"
            params.append(code)
        limit, offset = _clamp(limit, offset)
        sql += " ORDER BY trade_date ASC, id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [
            {
                "run_id": r[0], "trade_date": r[1], "cash": r[2],
                "market_value": r[3], "total_equity": r[4],
                "daily_return": r[5], "cumulative_return": r[6],
                "drawdown": r[7], "position_code": r[8] or "",
                "position_name": r[9] or "", "position_qty": r[10] or 0,
                "position_cost": r[11], "position_market_price": r[12],
                "position_unrealized_return": r[13],
                "positions": _value(r[14], []), "_corrupt": _corrupt(positions=_bad(r[14])),
            }
            for r in _rows(conn, sql, params)
        ]
    finally:
        conn.close()


def account_trades(*, run_id: Optional[int] = None,
                   date_from: Optional[str] = None, date_to: Optional[str] = None,
                   code: Optional[str] = None, limit: int = 100,
                   offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        sql = ("SELECT run_id, trade_date, code, name, side, price, qty, amount, "
               "fee, realized_pnl, cash_after, position_after, reason_code, "
               "reason_text, signal_json FROM review_account_trades "
               "WHERE run_id IN (SELECT id FROM review_account_runs WHERE source = 'v2')")
        params: list = []
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(int(run_id))
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
        sql += " ORDER BY trade_date ASC, id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [
            {
                "run_id": r[0], "trade_date": r[1], "code": r[2],
                "name": r[3] or "", "side": r[4], "price": r[5], "qty": r[6],
                "amount": r[7], "fee": r[8], "realized_pnl": r[9],
                "cash_after": r[10], "position_after": r[11],
                "reason_code": r[12] or "", "reason_text": r[13] or "",
                "signal": _value(r[14], {}), "_corrupt": _corrupt(signal=_bad(r[14])),
            }
            for r in _rows(conn, sql, params)
        ]
    finally:
        conn.close()


def account_positions(*, run_id: Optional[int] = None,
                      date_from: Optional[str] = None,
                      date_to: Optional[str] = None, code: Optional[str] = None,
                      limit: int = 100, offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        sql = ("SELECT run_id, code, name, entry_date, entry_price, qty, "
               "entry_reason_code, entry_signal_json, exit_date, exit_price, "
               "exit_reason_code, exit_signal_json, realized_return, hold_days, "
               "status, post_exit_max_return, post_exit_max_drawdown, "
               "post_exit_window_days, initial_cost, realized_pnl, period_pnl, "
               "period_start_value, inherited FROM review_account_positions "
               "WHERE run_id IN (SELECT id FROM review_account_runs WHERE source = 'v2')")
        params: list = []
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(int(run_id))
        if date_from:
            sql += " AND entry_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND entry_date <= ?"
            params.append(date_to)
        if code:
            sql += " AND code = ?"
            params.append(code)
        limit, offset = _clamp(limit, offset)
        sql += " ORDER BY entry_date ASC, id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [
            {
                "run_id": r[0], "code": r[1], "name": r[2] or "",
                "entry_date": r[3], "entry_price": r[4], "qty": r[5],
                "entry_reason_code": r[6] or "",
                "entry_signal": _value(r[7], {}), "exit_date": r[8],
                "exit_price": r[9], "exit_reason_code": r[10] or "",
                "exit_signal": _value(r[11], {}), "realized_return": r[12],
                "hold_days": r[13], "status": r[14],
                "post_exit_max_return": r[15],
                "post_exit_max_drawdown": r[16],
                "post_exit_window_days": r[17], "initial_cost": r[18],
                "realized_pnl": r[19], "period_pnl": r[20],
                "period_start_value": r[21],
                "inherited": bool(r[22]) if r[22] is not None else False,
                "_corrupt": _corrupt(entry_signal=_bad(r[7]),
                                     exit_signal=_bad(r[11])),
            }
            for r in _rows(conn, sql, params)
        ]
    finally:
        conn.close()


def account_events(*, run_id: Optional[int] = None,
                   date_from: Optional[str] = None, date_to: Optional[str] = None,
                   code: Optional[str] = None, limit: int = 100,
                   offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        sql = ("SELECT run_id, event_date, event_type, code, name, title, detail, "
               "reason_code, cash, total_equity, signal_json "
               "FROM review_account_events WHERE run_id IN (SELECT id FROM review_account_runs WHERE source = 'v2')")
        params: list = []
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(int(run_id))
        if date_from:
            sql += " AND event_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND event_date <= ?"
            params.append(date_to)
        if code:
            sql += " AND code = ?"
            params.append(code)
        limit, offset = _clamp(limit, offset)
        sql += " ORDER BY event_date ASC, id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [
            {
                "run_id": r[0], "event_date": r[1], "event_type": r[2],
                "code": r[3] or "", "name": r[4] or "", "title": r[5] or "",
                "detail": r[6] or "", "reason_code": r[7] or "",
                "cash": r[8], "total_equity": r[9],
                "signal": _value(r[10], {}), "_corrupt": _corrupt(signal=_bad(r[10])),
            }
            for r in _rows(conn, sql, params)
        ]
    finally:
        conn.close()


def signals(*, date_from: Optional[str] = None, date_to: Optional[str] = None,
            code: Optional[str] = None, status: str = "all", limit: int = 100,
            offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        sql = ("SELECT id, code, name, source, entry_date, entry_price, "
               "entry_reason_code, entry_reason_text, entry_signal_json, "
               "highest_return, highest_price, took_profit_half, status, "
               "exit_date, exit_price, exit_reason_code, exit_reason_text, "
               "exit_signal_json, hold_days, qty, half_exit_date, half_exit_price, "
               "half_exit_qty, half_exit_reason_code, half_exit_reason_text, "
               "half_exit_signal_json, created_at, updated_at "
               "FROM buy_sell_signals WHERE source = 'v2'")
        params: list = []
        if date_from:
            sql += " AND entry_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND entry_date <= ?"
            params.append(date_to)
        if code:
            sql += " AND code = ?"
            params.append(code)
        if status == "open":
            sql += " AND status = 'open'"
        elif status == "closed":
            sql += " AND status != 'open'"
        elif status != "all":
            raise ValueError(f"未知信号状态：{status}")
        limit, offset = _clamp(limit, offset)
        sql += " ORDER BY entry_date DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [
            {
                "signal_id": r[0], "code": r[1], "name": r[2] or "",
                "source": r[3], "entry_date": r[4], "entry_price": r[5],
                "entry_reason_code": r[6] or "",
                "entry_reason_text": r[7] or "",
                "entry_signal": _value(r[8], {}), "highest_return": r[9],
                "highest_price": r[10],
                "took_profit_half": bool(r[11]) if r[11] is not None else False,
                "status": r[12], "exit_date": r[13], "exit_price": r[14],
                "exit_reason_code": r[15] or "",
                "exit_reason_text": r[16] or "",
                "exit_signal": _value(r[17], {}), "hold_days": r[18],
                "qty": r[19], "half_exit_date": r[20],
                "half_exit_price": r[21], "half_exit_qty": r[22],
                "half_exit_reason_code": r[23] or "",
                "half_exit_reason_text": r[24] or "",
                "half_exit_signal": _value(r[25], {}),
                "created_at": r[26], "updated_at": r[27],
                "_corrupt": _corrupt(entry_signal=_bad(r[8]),
                                     exit_signal=_bad(r[17]),
                                     half_exit_signal=_bad(r[25])),
            }
            for r in _rows(conn, sql, params)
        ]
    finally:
        conn.close()


def experiments(*, run_id: Optional[int] = None, experiment_id: Optional[int] = None,
                limit: int = 100, offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        sql = ("SELECT id, base_run_id, created_at, experiment_version, status, "
               "request_json, report_json FROM review_account_experiments "
               "WHERE base_run_id IN ("
               "SELECT id FROM review_account_runs WHERE source = 'v2')")
        params: list = []
        if run_id is not None:
            sql += " AND base_run_id = ?"
            params.append(int(run_id))
        if experiment_id is not None:
            sql += " AND id = ?"
            params.append(int(experiment_id))
        limit, offset = _clamp(limit, offset)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [
            {
                "experiment_id": r[0], "run_id": r[1], "created_at": r[2],
                "experiment_version": r[3], "status": r[4],
                "request": _value(r[5], {}), "report": _value(r[6], {}),
                "_corrupt": _corrupt(request=_bad(r[5]), report=_bad(r[6])),
            }
            for r in _rows(conn, sql, params)
        ]
    finally:
        conn.close()


def table_present(table: str) -> Optional[bool]:
    conn = open_readonly(paths.DB_PATH)
    if conn is None:
        return None
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone() is not None
    finally:
        conn.close()
