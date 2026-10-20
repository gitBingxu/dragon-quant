"""实盘辅助买卖信号（buy_sell_signals，纯信号记账）持久化。"""

import json
from typing import Optional

from dragon_quant.storage._base import _connect, _ensure_schema, _lock, _normalize_source


_SIGNAL_COLS = (
    "id, code, name, source, entry_date, entry_price, entry_reason_code, "
    "entry_reason_text, entry_signal_json, highest_return, highest_price, "
    "took_profit_half, status, exit_date, exit_price, exit_reason_code, "
    "exit_reason_text, exit_signal_json, hold_days, "
    "qty, half_exit_date, half_exit_price, half_exit_qty, "
    "half_exit_reason_code, half_exit_reason_text, half_exit_signal_json"
)


def _signal_row_to_dict(r) -> dict:
    return {
        "id": r[0], "code": r[1], "name": r[2] or "", "source": r[3] or "v2",
        "entry_date": r[4], "entry_price": r[5],
        "entry_reason_code": r[6] or "", "entry_reason_text": r[7] or "",
        "entry_signal": json.loads(r[8]) if r[8] else {},
        "highest_return": r[9] or 0.0, "highest_price": r[10] or 0.0,
        "took_profit_half": bool(r[11]), "status": r[12] or "open",
        "exit_date": r[13], "exit_price": r[14],
        "exit_reason_code": r[15] or "", "exit_reason_text": r[16] or "",
        "exit_signal": json.loads(r[17]) if r[17] else {}, "hold_days": r[18],
        "qty": r[19],
        "half_exit_date": r[20], "half_exit_price": r[21], "half_exit_qty": r[22],
        "half_exit_reason_code": r[23] or "", "half_exit_reason_text": r[24] or "",
        "half_exit_signal": json.loads(r[25]) if r[25] else {},
    }


def list_open_signals(before_date: Optional[str] = None, source: str = "v2") -> list[dict]:
    """已买入未卖出的信号；before_date 非空时只取 entry_date < before_date 的持仓。"""
    source = _normalize_source(source)
    conn = _connect()
    try:
        _ensure_schema(conn)
        sql = f"SELECT {_SIGNAL_COLS} FROM buy_sell_signals WHERE status = 'open' AND source = ?"
        params: list = [source]
        if before_date:
            sql += " AND entry_date < ?"
            params.append(before_date)
        sql += " ORDER BY entry_date ASC, id ASC"
        rows = conn.execute(sql, params).fetchall()
        return [_signal_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def list_all_signals(source: str = "v2") -> list[dict]:
    """全量信号（open+closed）按 id 升序，供影子状态 as-of 重建。"""
    source = _normalize_source(source)
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            f"SELECT {_SIGNAL_COLS} FROM buy_sell_signals WHERE source = ? ORDER BY id ASC",
            (source,),
        ).fetchall()
        return [_signal_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def insert_signal(code: str, name: str, entry_date: str, entry_price: float,
                  entry_reason_code: str = "", entry_reason_text: str = "",
                  entry_signal: Optional[dict] = None, source: str = "v2",
                  *, qty: Optional[int] = None) -> tuple[int, bool]:
    """记录一条买入信号；同一 code 已存在未平仓信号时幂等返回既有 id。

    返回 (id, created)：created=False 表示命中既有未平仓行（未重复写入）。
    qty 为买入日按回测同口径推算的虚拟数量（notional_quantity），落库冻结。
    """
    source = _normalize_source(source)
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id FROM buy_sell_signals WHERE code = ? AND status = 'open'",
                (code,),
            ).fetchone()
            if existing:
                conn.commit()
                return int(existing[0]), False
            cur = conn.execute(
                "INSERT INTO buy_sell_signals(code, name, source, entry_date, entry_price, "
                "entry_reason_code, entry_reason_text, entry_signal_json, status, qty) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                (code, name, source, entry_date, entry_price, entry_reason_code,
                 entry_reason_text, json.dumps(entry_signal or {}, ensure_ascii=False), qty),
            )
            conn.commit()
            return int(cur.lastrowid or 0), True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def close_signal(code: str, exit_date: str, exit_price: float,
                 exit_reason_code: str = "", exit_reason_text: str = "",
                 exit_signal: Optional[dict] = None, hold_days: Optional[int] = None) -> bool:
    """平掉一条未平仓信号；返回是否命中。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            cur = conn.execute(
                "UPDATE buy_sell_signals SET status = 'closed', exit_date = ?, exit_price = ?, "
                "exit_reason_code = ?, exit_reason_text = ?, exit_signal_json = ?, hold_days = ?, "
                "updated_at = datetime('now', 'localtime') WHERE code = ? AND status = 'open'",
                (exit_date, exit_price, exit_reason_code, exit_reason_text,
                 json.dumps(exit_signal or {}, ensure_ascii=False), hold_days, code),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def half_close_signal(code: str, exit_date: str, exit_price: float, exit_qty: int,
                      exit_reason_code: str = "", exit_reason_text: str = "",
                      exit_signal: Optional[dict] = None) -> bool:
    """记录半仓卖出腿：冻结 half_exit_*（含腿 qty/原因），保持 status='open' 供剩余半仓继续跟踪。

    幂等：`took_profit_half=0` 守卫保证同一信号只记一次半仓；返回是否命中。
    """
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            cur = conn.execute(
                "UPDATE buy_sell_signals SET took_profit_half = 1, half_exit_date = ?, "
                "half_exit_price = ?, half_exit_qty = ?, half_exit_reason_code = ?, "
                "half_exit_reason_text = ?, half_exit_signal_json = ?, "
                "updated_at = datetime('now', 'localtime') "
                "WHERE code = ? AND status = 'open' AND took_profit_half = 0",
                (exit_date, exit_price, exit_qty, exit_reason_code, exit_reason_text,
                 json.dumps(exit_signal or {}, ensure_ascii=False), code),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def update_signal_peaks(code: str, highest_return: float, highest_price: float) -> bool:
    """写回未平仓信号的最新峰值（供跨日移动止盈/保本判定）。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            cur = conn.execute(
                "UPDATE buy_sell_signals SET highest_return = ?, highest_price = ?, "
                "updated_at = datetime('now', 'localtime') WHERE code = ? AND status = 'open'",
                (highest_return, highest_price, code),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
