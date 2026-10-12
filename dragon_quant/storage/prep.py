"""盘后复盘快照（prep_snapshots）持久化。

快照以单列 ``result_json`` 存完整复盘结果 JSON（sentiment / main_boards / candidates），
后续新增字段 = 往 JSON 加 key，无需改表 / 迁移 schema。
"""

import json
from typing import Optional

from dragon_quant.storage._base import _connect, _ensure_schema, _lock


def save_prep_snapshot(trade_date: str, result: dict, source: str = "v2") -> None:
    """UPSERT 复盘快照（trade_date 唯一）。result 为完整复盘结果 dict。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO prep_snapshots(trade_date, source, result_json) VALUES (?, ?, ?) "
                "ON CONFLICT(trade_date, source) DO UPDATE SET "
                "result_json=excluded.result_json, "
                "created_at=datetime('now','localtime')",
                (trade_date, source, json.dumps(result, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()


def get_prep_snapshot(trade_date: str, source: str = "v2") -> Optional[dict]:
    """读取某复盘日的快照，无记录返回 None。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT result_json FROM prep_snapshots WHERE trade_date = ? AND source = ?",
            (trade_date, source),
        ).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


def has_prep_snapshot(trade_date: str, source: str = "v2") -> bool:
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT 1 FROM prep_snapshots WHERE trade_date = ? AND source = ?",
            (trade_date, source),
        ).fetchone()
        return row is not None
    finally:
        conn.close()
