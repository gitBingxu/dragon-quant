"""``dragons_v2`` 只读数据源（唯一读取入口）。

只调用既有只读 API，不写 scan 表；``top_n=None`` 关闭 LIMIT 取全量。
"""

from __future__ import annotations

from typing import Optional


def list_dragon_dates(date_from: Optional[str] = None,
                      date_to: Optional[str] = None) -> list[str]:
    from dragon_quant.scan.storage import dragons as dragon_store

    return dragon_store.list_dragon_trade_dates(date_from, date_to, source="v2")


def list_dragons_for_date(day: str) -> list[dict]:
    from dragon_quant.scan.storage import dragons as dragon_store

    return dragon_store.get_dragons_by_date(day, top_n=None, source="v2")


def list_dragons(date_from: Optional[str] = None,
                 date_to: Optional[str] = None) -> list[dict]:
    """区间内全部 dragons_v2 行（按 trade_date / code 稳定排序）。"""

    rows: list[dict] = []
    for day in list_dragon_dates(date_from, date_to):
        rows.extend(list_dragons_for_date(day))
    rows.sort(key=lambda item: (item.get("trade_date") or "", item.get("code") or ""))
    return rows


__all__ = ["list_dragon_dates", "list_dragons", "list_dragons_for_date"]
