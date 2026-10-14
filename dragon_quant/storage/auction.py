"""集合竞价盘口快照（auction_snapshots）持久化。

快照以单列 ``items_json`` 存完整竞价盘口明细（time/price/matched/unmatched 数组），
键为 (trade_date, code)，重复采集同一交易日幂等覆盖（UPSERT）。
"""

import json
from typing import Optional

from dragon_quant.storage._base import _connect, _ensure_schema, _lock


def save_auction_snapshot(trade_date: str, code: str, name: str,
                          items: list[dict]) -> None:
    """UPSERT 竞价盘口快照。items 为 [{'time','price','matched','unmatched'}, ...]。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO auction_snapshots(trade_date, code, name, items_json) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(trade_date, code) DO UPDATE SET "
                "name=excluded.name, items_json=excluded.items_json, "
                "created_at=datetime('now','localtime')",
                (trade_date, code, name, json.dumps(items, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()


def get_auction_snapshot(trade_date: str, code: str) -> Optional[dict]:
    """读取某日某股竞价快照，无记录返回 None。返回 {trade_date, code, name, items}。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT trade_date, code, name, items_json FROM auction_snapshots "
            "WHERE trade_date = ? AND code = ?",
            (trade_date, code),
        ).fetchone()
        if not row:
            return None
        return {
            "trade_date": row[0],
            "code": row[1],
            "name": row[2],
            "items": json.loads(row[3]) if row[3] else [],
        }
    finally:
        conn.close()


def list_auction_snapshots(trade_date: str) -> list[dict]:
    """列出某交易日全部竞价快照，按 code 排序。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT trade_date, code, name, items_json FROM auction_snapshots "
            "WHERE trade_date = ? ORDER BY code",
            (trade_date,),
        ).fetchall()
        return [
            {
                "trade_date": r[0],
                "code": r[1],
                "name": r[2],
                "items": json.loads(r[3]) if r[3] else [],
            }
            for r in rows
        ]
    finally:
        conn.close()
