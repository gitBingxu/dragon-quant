"""``vpa_analysis`` 持久化（量价分析结果读写，Task 9 从 storage/dragons 分出）。

表归属：dragon.db ``vpa_analysis`` 由本模块独占写入；``scan.storage.dragons``
不再承载 vpa 函数。因子层经函数内 lazy import 只读 ``list_vpa_analysis``。
"""

from __future__ import annotations

import json
from typing import Optional

from dragon_quant.infra.storage._base import _connect, _ensure_schema, _lock


def upsert_vpa(trade_date: str, code: str,
               name: str = "",
               source: str = "",
               health_score: Optional[float] = None,
               signal: str = "",
               summary: str = "",
               factors_json: Optional[str] = None,
               version: str = ""):
    """写入/更新单条量价分析结果（按 trade_date + code 去重覆盖）。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO vpa_analysis "
                "(trade_date, code, name, source, health_score, signal, "
                " summary, factors_json, version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(trade_date, code) DO UPDATE SET "
                "name=excluded.name, source=excluded.source, "
                "health_score=excluded.health_score, signal=excluded.signal, "
                "summary=excluded.summary, factors_json=excluded.factors_json, "
                "version=excluded.version, "
                "created_at=datetime('now','localtime')",
                (trade_date, code, name, source, health_score, signal,
                 summary, factors_json, version),
            )
            conn.commit()
        finally:
            conn.close()


def list_vpa_analysis(trade_date: str) -> list[dict]:
    """读取某日的量价分析结果（因子层 vpa 迁移的只读源）。

    ``factors_json`` 逐行解析为 list；损坏时返回空列表并带 ``factors_error``，
    不中断整批读取（因子层按「name 缺失」处理，不静默取第一个）。
    """
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT trade_date, code, name, source, health_score, signal, summary, "
            "factors_json, version, created_at FROM vpa_analysis "
            "WHERE trade_date = ? ORDER BY code",
            (trade_date,),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for r in rows:
        raw = r[7]
        factors, error = [], None
        if raw:
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            else:
                if isinstance(parsed, list):
                    factors = parsed
                else:
                    error = "factors_json 不是数组"
        result.append({
            "trade_date": r[0], "code": r[1], "name": r[2], "source": r[3],
            "health_score": r[4], "signal": r[5], "summary": r[6],
            "factors": factors, "factors_error": error,
            "version": r[8] or "", "created_at": r[9],
        })
    return result
