"""``stock_daily`` 资产行转换 / 合并（C 扩池与 B 回填共用的叶子模块）。

职责只有两件事，不含 I/O、不 import 上层：

* :func:`daily_row` —— KBar → 日 K 行（时间戳按 ``Asia/Shanghai`` 取日期）；
* :func:`convert_daily_bars` / :func:`merge_daily_rows` —— 过滤未来 bar、升序、
  按 code 覆盖并裁到上限。

依赖方向：``review_account/prep/collectors`` 与
``infra/assets/stock_daily_backfill`` 都从本模块取实现，规则只保留一份。
本模块只依赖标准库（``infra.clock`` / ``infra.models`` 均无需 import）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping, Optional
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

#: 详细池 code 的日 K 回看根数（D5 60 日高点 + D6 20 日股性需要完整回看）。
STOCK_DAILY_DAYS = 120
#: 池 code 的存量上限（= STOCK_DAILY_DAYS）。
POOL_MAX_BARS = STOCK_DAILY_DAYS
#: 扩池独有 code 只服务 dashboard 前向收益评估，存最近 20 根即可。
PLAN_CANDIDATE_MAX_BARS = 20

_ROW_FIELDS = ("open", "high", "low", "close", "volume", "amount", "turnover")


def daily_row(bar) -> Optional[dict]:
    """KBar → 日 K 行（date/open/high/low/close/volume/amount/turnover）。"""

    timestamp = getattr(bar, "timestamp", 0) or 0
    if not timestamp:
        return None
    try:
        day = datetime.fromtimestamp(timestamp / 1000.0, SHANGHAI).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None
    row = {"date": day}
    row.update({key: getattr(bar, key, None) for key in _ROW_FIELDS})
    return row


def convert_daily_bars(bars, trade_date: str) -> list[dict]:
    """bars → 行列表：丢弃无效/未来 bar，按 date 升序。"""

    if not bars:
        return []
    converted = [row for row in (daily_row(bar) for bar in bars) if row]
    converted = [row for row in converted if row["date"] <= trade_date]
    converted.sort(key=lambda row: row["date"])
    return converted


def merge_daily_rows(
    rows: Mapping[str, list] | None,
    fetched: Mapping[str, list] | None,
    trade_date: str,
    *,
    limits: Mapping[str, int] | None = None,
    default_limit: int = STOCK_DAILY_DAYS,
) -> dict[str, list]:
    """按 code 合并日 K：未涉及的 code 原样保留，空取回不覆盖。

    ``limits`` 为每 code 的保留根数（缺省 ``default_limit``）；仅覆盖本次
    ``fetched`` 中能转换出有效 bar 的 code。
    """

    merged = dict(rows or {})
    per_code = limits or {}
    for code, bars in (fetched or {}).items():
        converted = convert_daily_bars(bars, trade_date)
        if not converted:
            continue
        limit = int(per_code.get(str(code), default_limit) or 0)
        if limit <= 0:
            continue
        merged[str(code)] = converted[-limit:]
    return merged


__all__ = [
    "SHANGHAI",
    "STOCK_DAILY_DAYS",
    "POOL_MAX_BARS",
    "PLAN_CANDIDATE_MAX_BARS",
    "daily_row",
    "convert_daily_bars",
    "merge_daily_rows",
]
