"""账户级回测（review_account_*）持久化。"""

import json
from typing import Optional

from dragon_quant.storage._base import _connect, _ensure_schema, _lock, _normalize_source


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
                              display_name: Optional[str] = None) -> int:
    """创建一条账户级 review run，返回 run_id。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            source = _normalize_source(source)
            cur = conn.execute(
                "INSERT INTO review_account_runs("
                "source, display_name, strategy_name, strategy_params_json, date_from, date_to, "
                "initial_cash, final_equity, total_return, max_drawdown, trade_count, win_rate"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source, display_name, strategy_name, strategy_params_json, date_from, date_to,
                 initial_cash, final_equity, total_return, max_drawdown, trade_count, win_rate),
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
                "realized_return, hold_days, status"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id, p.get("code"), p.get("name"), p.get("entry_date"),
                        p.get("entry_price"), p.get("qty"), p.get("entry_reason_code"),
                        p.get("entry_signal_json"), p.get("exit_date"), p.get("exit_price"),
                        p.get("exit_reason_code"), p.get("exit_signal_json"),
                        p.get("realized_return"), p.get("hold_days"), p.get("status"),
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
            "initial_cash, final_equity, total_return, max_drawdown, trade_count, win_rate, created_at "
            "FROM review_account_runs WHERE source = ? ORDER BY id DESC LIMIT ?",
            (source, limit),
        ).fetchall()
        return [
            {
                "id": r[0], "source": r[1], "display_name": r[2],
                "strategy_name": r[3],
                "strategy_params": json.loads(r[4]) if r[4] else {},
                "date_from": r[5], "date_to": r[6],
                "initial_cash": r[7], "final_equity": r[8],
                "total_return": r[9], "max_drawdown": r[10],
                "trade_count": r[11], "win_rate": r[12],
                "created_at": r[13],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_review_account_run(run_id: int) -> Optional[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT id, source, display_name, strategy_name, strategy_params_json, date_from, date_to, "
            "initial_cash, final_equity, total_return, max_drawdown, trade_count, win_rate, created_at "
            "FROM review_account_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "source": row[1], "display_name": row[2],
            "strategy_name": row[3],
            "strategy_params": json.loads(row[4]) if row[4] else {},
            "date_from": row[5], "date_to": row[6],
            "initial_cash": row[7], "final_equity": row[8],
            "total_return": row[9], "max_drawdown": row[10],
            "trade_count": row[11], "win_rate": row[12],
            "created_at": row[13],
        }
    finally:
        conn.close()


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
            "exit_date, exit_price, exit_reason_code, exit_signal_json, realized_return, hold_days, status "
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
            }
            for r in rows
        ]
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

