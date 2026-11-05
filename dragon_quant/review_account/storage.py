"""账户级回测（``review_account_*`` 五表）持久化（Task 12 canonical）。

表归属：dragon.db 的 ``review_account_runs`` / ``_snapshots`` / ``_trades`` /
``_positions`` / ``_events`` 由本模块独占写入。
"""

import json
from typing import Optional

from dragon_quant.infra.storage._base import _connect, _ensure_schema, _lock, _normalize_source


def create_review_account_run(source: str,
                              strategy_name: str,
                              strategy_params_json: str,
                              date_from: str,
                              date_to: str,
                              initial_cash: float,
                              final_equity: float,
                              total_return: float,
                              max_drawdown: float,
                              trade_count: int,
                              win_rate: Optional[float],
                              display_name: Optional[str] = None,
                              base_equity: Optional[float] = None,
                              base_run_id: Optional[int] = None,
                              start_equity: Optional[float] = None,
                              start_positions_json: Optional[str] = None,
                              start_realized_pnl: Optional[float] = None,
                              start_cash: Optional[float] = None,
                              start_peak_equity: Optional[float] = None,
                              start_consecutive_losses: Optional[int] = None,
                              start_marks_json: Optional[str] = None,
                              run_meta_json: Optional[str] = None) -> int:
    """创建一条账户级 review run，返回 run_id。

    ``base_equity`` 仅增量 run 写入（基准最后收盘权益，作为收益率与指数曲线
    起点）；全量 run 保持 NULL，读取侧 ``base_equity or initial_cash`` 逐位不变。
    """
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            source = _normalize_source(source)
            cur = conn.execute(
                "INSERT INTO review_account_runs("
                "source, display_name, strategy_name, strategy_params_json, date_from, date_to, "
                "initial_cash, base_equity, final_equity, total_return, max_drawdown, trade_count, win_rate, "
                "base_run_id, start_equity, start_positions_json, start_realized_pnl, "
                "start_cash, start_peak_equity, start_consecutive_losses, "
                "start_marks_json, run_meta_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source, display_name, strategy_name, strategy_params_json, date_from, date_to,
                 initial_cash, base_equity, final_equity, total_return, max_drawdown,
                 trade_count, win_rate, base_run_id, start_equity,
                 start_positions_json, start_realized_pnl, start_cash,
                 start_peak_equity, start_consecutive_losses,
                 start_marks_json, run_meta_json),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        finally:
            conn.close()


def save_review_account_results(run_id: int,
                                snapshots: list[dict],
                                trades: list[dict],
                                positions: list[dict],
                                events: Optional[list[dict]] = None):
    """批量保存账户级 review 的快照、交割单和已平仓持仓。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.executemany(
                "INSERT INTO review_account_snapshots("
                "run_id, trade_date, cash, market_value, total_equity, daily_return, "
                "cumulative_return, drawdown, position_code, position_name, position_qty, "
                "position_cost, position_market_price, position_unrealized_return, positions_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id, s.get("trade_date"), s.get("cash"), s.get("market_value"),
                        s.get("total_equity"), s.get("daily_return"), s.get("cumulative_return"),
                        s.get("drawdown"), s.get("position_code"), s.get("position_name"),
                        s.get("position_qty"), s.get("position_cost"),
                        s.get("position_market_price"), s.get("position_unrealized_return"),
                        s.get("positions_json", "[]"),
                    )
                    for s in snapshots
                ],
            )
            conn.executemany(
                "INSERT INTO review_account_trades("
                "run_id, trade_date, code, name, side, price, qty, amount, fee, "
                "realized_pnl, cash_after, position_after, reason_code, reason_text, signal_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id, t.get("trade_date"), t.get("code"), t.get("name"),
                        t.get("side"), t.get("price"), t.get("qty"), t.get("amount"),
                        t.get("fee"), t.get("realized_pnl"), t.get("cash_after"),
                        t.get("position_after"), t.get("reason_code"), t.get("reason_text"),
                        t.get("signal_json"),
                    )
                    for t in trades
                ],
            )
            conn.executemany(
                "INSERT INTO review_account_positions("
                "run_id, code, name, entry_date, entry_price, qty, entry_reason_code, "
                "entry_signal_json, exit_date, exit_price, exit_reason_code, exit_signal_json, "
                "realized_return, hold_days, status, post_exit_max_return, "
                "post_exit_max_drawdown, post_exit_window_days, "
                "initial_cost, realized_pnl, period_pnl, period_start_value, inherited"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id, p.get("code"), p.get("name"), p.get("entry_date"),
                        p.get("entry_price"), p.get("qty"), p.get("entry_reason_code"),
                        p.get("entry_signal_json"), p.get("exit_date"), p.get("exit_price"),
                        p.get("exit_reason_code"), p.get("exit_signal_json"),
                        p.get("realized_return"), p.get("hold_days"), p.get("status"),
                        p.get("post_exit_max_return"), p.get("post_exit_max_drawdown"),
                        p.get("post_exit_window_days"),
                        p.get("initial_cost"), p.get("realized_pnl"),
                        p.get("period_pnl"), p.get("period_start_value"),
                        1 if p.get("inherited") else 0,
                    )
                    for p in positions
                ],
            )
            if events:
                conn.executemany(
                    "INSERT INTO review_account_events("
                    "run_id, event_date, event_type, code, name, title, detail, "
                    "reason_code, cash, total_equity, signal_json"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            run_id, e.get("event_date"), e.get("event_type"),
                            e.get("code"), e.get("name"), e.get("title"),
                            e.get("detail"), e.get("reason_code"), e.get("cash"),
                            e.get("total_equity"), e.get("signal_json"),
                        )
                        for e in events
                    ],
                )
            conn.commit()
        finally:
            conn.close()


def delete_review_account_run(run_id: int) -> bool:
    """删除一条账户级 review run 及其全部子表数据，返回是否删除成功。

    子表（snapshots / trades / positions / events）虽声明了 ON DELETE CASCADE，
    但外键级联仅在 `PRAGMA foreign_keys=ON` 的连接上生效；此处显式清理子表，
    保证在任何连接（含测试用裸连接）上都能彻底删除。
    """
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            for table in (
                "review_account_snapshots",
                "review_account_trades",
                "review_account_positions",
                "review_account_events",
            ):
                conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
            cur = conn.execute(
                "DELETE FROM review_account_runs WHERE id = ?", (run_id,)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def query_review_account_runs(limit: int = 20, source: str = "v2") -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        rows = conn.execute(
            "SELECT id, source, display_name, strategy_name, strategy_params_json, date_from, date_to, "
            "initial_cash, base_equity, final_equity, total_return, max_drawdown, trade_count, win_rate, "
            "created_at, base_run_id, start_equity, start_positions_json, start_realized_pnl, "
            "start_cash, start_peak_equity, start_consecutive_losses, start_marks_json, "
            "run_meta_json "
            "FROM review_account_runs WHERE source = ? ORDER BY id DESC LIMIT ?",
            (source, limit),
        ).fetchall()
        return [_run_row(r) for r in rows]
    finally:
        conn.close()


def get_review_account_run(run_id: int) -> Optional[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT id, source, display_name, strategy_name, strategy_params_json, date_from, date_to, "
            "initial_cash, base_equity, final_equity, total_return, max_drawdown, trade_count, win_rate, "
            "created_at, base_run_id, start_equity, start_positions_json, start_realized_pnl, "
            "start_cash, start_peak_equity, start_consecutive_losses, start_marks_json, "
            "run_meta_json "
            "FROM review_account_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            return None
        return _run_row(row)
    finally:
        conn.close()


def _run_row(row) -> dict:
    return {
        "id": row[0], "source": row[1], "display_name": row[2],
        "strategy_name": row[3],
        "strategy_params": json.loads(row[4]) if row[4] else {},
        "date_from": row[5], "date_to": row[6],
        "initial_cash": row[7], "base_equity": row[8], "final_equity": row[9],
        "total_return": row[10], "max_drawdown": row[11],
        "trade_count": row[12], "win_rate": row[13],
        "created_at": row[14],
        "base_run_id": row[15],
        "start_equity": row[16],
        "start_positions": json.loads(row[17]) if row[17] else [],
        "start_state_recorded": row[17] is not None,
        "start_realized_pnl": row[18],
        "start_cash": row[19],
        "start_peak_equity": row[20],
        "start_consecutive_losses": row[21],
        "start_marks": json.loads(row[22]) if row[22] else {},
        "run_meta": json.loads(row[23]) if row[23] else {},
    }


def query_review_account_snapshots(run_id: int) -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT trade_date, cash, market_value, total_equity, daily_return, cumulative_return, "
            "drawdown, position_code, position_name, position_qty, position_cost, "
            "position_market_price, position_unrealized_return, positions_json "
            "FROM review_account_snapshots WHERE run_id = ? ORDER BY trade_date ASC",
            (run_id,),
        ).fetchall()
        return [
            {
                "trade_date": r[0], "cash": r[1], "market_value": r[2],
                "total_equity": r[3], "daily_return": r[4], "cumulative_return": r[5],
                "drawdown": r[6], "position_code": r[7] or "", "position_name": r[8] or "",
                "position_qty": r[9] or 0, "position_cost": r[10],
                "position_market_price": r[11], "position_unrealized_return": r[12],
                "positions": json.loads(r[13]) if r[13] else [],
            }
            for r in rows
        ]
    finally:
        conn.close()


def query_review_account_trades(run_id: int) -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT trade_date, code, name, side, price, qty, amount, fee, realized_pnl, "
            "cash_after, position_after, reason_code, reason_text, signal_json "
            "FROM review_account_trades WHERE run_id = ? ORDER BY trade_date ASC, id ASC",
            (run_id,),
        ).fetchall()
        return [
            {
                "trade_date": r[0], "code": r[1], "name": r[2] or "",
                "side": r[3], "price": r[4], "qty": r[5], "amount": r[6],
                "fee": r[7], "realized_pnl": r[8], "cash_after": r[9],
                "position_after": r[10], "reason_code": r[11] or "",
                "reason_text": r[12] or "",
                "signal": json.loads(r[13]) if r[13] else {},
            }
            for r in rows
        ]
    finally:
        conn.close()


def query_review_account_positions(run_id: int) -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT code, name, entry_date, entry_price, qty, entry_reason_code, entry_signal_json, "
            "exit_date, exit_price, exit_reason_code, exit_signal_json, realized_return, hold_days, status, "
            "post_exit_max_return, post_exit_max_drawdown, post_exit_window_days, "
            "initial_cost, realized_pnl, period_pnl, period_start_value, inherited "
            "FROM review_account_positions WHERE run_id = ? ORDER BY entry_date ASC, id ASC",
            (run_id,),
        ).fetchall()
        return [
            {
                "code": r[0], "name": r[1] or "", "entry_date": r[2],
                "entry_price": r[3], "qty": r[4], "entry_reason_code": r[5] or "",
                "entry_signal": json.loads(r[6]) if r[6] else {},
                "exit_date": r[7], "exit_price": r[8], "exit_reason_code": r[9] or "",
                "exit_signal": json.loads(r[10]) if r[10] else {},
                "realized_return": r[11], "hold_days": r[12], "status": r[13],
                "post_exit_max_return": r[14], "post_exit_max_drawdown": r[15],
                "post_exit_window_days": r[16],
                "initial_cost": r[17], "realized_pnl": r[18],
                "period_pnl": r[19], "period_start_value": r[20],
                "inherited": bool(r[21]) if r[21] is not None else False,
            }
            for r in rows
        ]
    finally:
        conn.close()


def query_review_account_closed_positions(run_id: int) -> list[dict]:
    """基准 run 的已平仓记录（只读，按成交完成序）。

    供增量续算恢复 ``consecutive_losses``：``review_account_positions`` 只在
    整清仓时写入一行（半仓腿不单独成行），``id`` 自增序即成交完成序，与运行时
    ``TradingEngine._fill`` 的连亏记账口径一致。
    """
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, code, exit_date, realized_return, status "
            "FROM review_account_positions WHERE run_id = ? "
            "ORDER BY exit_date ASC, id ASC",
            (run_id,),
        ).fetchall()
        return [
            {
                "id": r[0], "code": r[1], "exit_date": r[2],
                "realized_return": r[3], "status": r[4],
            }
            for r in rows
        ]
    finally:
        conn.close()


def save_review_account_experiment(base_run_id: int, *,
                                   experiment_version: str,
                                   status: Optional[str],
                                   request: Optional[dict],
                                   report: dict) -> int:
    """保存用户显式触发的对照实验报告（可审计；GET 只读不重算）。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            cur = conn.execute(
                "INSERT INTO review_account_experiments("
                "base_run_id, experiment_version, status, request_json, report_json"
                ") VALUES (?, ?, ?, ?, ?)",
                (base_run_id, experiment_version, status,
                 json.dumps(request or {}, ensure_ascii=False),
                 json.dumps(report, ensure_ascii=False, default=str)),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        finally:
            conn.close()


def query_review_account_experiments(base_run_id: int,
                                     limit: int = 5) -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, base_run_id, created_at, experiment_version, status, "
            "request_json, report_json FROM review_account_experiments "
            "WHERE base_run_id = ? ORDER BY id DESC LIMIT ?",
            (base_run_id, limit),
        ).fetchall()
        result = []
        for row in rows:
            result.append({
                "id": row[0], "base_run_id": row[1], "created_at": row[2],
                "experiment_version": row[3], "status": row[4],
                "request": json.loads(row[5]) if row[5] else {},
                "report": json.loads(row[6]) if row[6] else {},
            })
        return result
    finally:
        conn.close()


def query_review_account_events(run_id: int) -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT event_date, event_type, code, name, title, detail, reason_code, "
            "cash, total_equity, signal_json "
            "FROM review_account_events WHERE run_id = ? ORDER BY event_date ASC, id ASC",
            (run_id,),
        ).fetchall()
        return [
            {
                "event_date": r[0], "event_type": r[1], "code": r[2] or "",
                "name": r[3] or "", "title": r[4] or "", "detail": r[5] or "",
                "reason_code": r[6] or "", "cash": r[7], "total_equity": r[8],
                "signal": json.loads(r[9]) if r[9] else {},
            }
            for r in rows
        ]
    finally:
        conn.close()

