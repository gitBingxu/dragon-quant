"""本地日 K 共享适配（竞价成效 / 复盘计划后续收益共用，方案 §2.3）。

边界：只读调用方注入的 ``rows``（来自 ``deps.local_bars_loader`` 的
``prep_assets.stock_daily``），不打开数据库、不触网、不补行情。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping

LOCAL_CAVEATS = (
    "行情只读本地日 K；打开页面不联网，缺数据一律「待评估」",
    "本地日 K 缺涨停标记，一字板可能未被剔除，收益可能高估",
    "成交额低于 2 亿的样本按既有规则剔除，小盘股样本可能偏少",
    "交易日按本地日 K 推断，非交易所日历；周末/节假日缺口不是异常",
)


def shift_day(day: str, days: int) -> str:
    """``YYYY-MM-DD`` 日期平移（自然日，仅用于裁剪窗口，不判定交易日）。"""

    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=days)).strftime(
        "%Y-%m-%d")


class LocalStockDailyMarket:
    """预加载日 K 的只读行情适配器（Adapter）。

    刻意**不提供** ``calendar()``：``decision_review`` 会退化为
    ``calendar_basis="bars_union"`` 并给出 ``calendar_fallback`` 告警。
    """

    def __init__(self, rows: Mapping[str, list] | None = None) -> None:
        self._rows = {str(code): list(bars or [])
                      for code, bars in (rows or {}).items()}

    def daily(self, code: str, start: str | None = None) -> list:
        return list(self._rows.get(str(code), []))

    def codes(self) -> list[str]:
        return sorted(self._rows)


__all__ = ["LOCAL_CAVEATS", "LocalStockDailyMarket", "shift_day"]
