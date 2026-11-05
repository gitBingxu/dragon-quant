"""账户对照实验的**本地只读**行情适配（Adapter，不触网）。

与 ``LocalStockDailyMarket`` 不同，这里必须满足 ``AccountSimulator`` /
``historical_events`` 的真实 K 线契约：

* ``daily()`` 返回 ``KBar`` 列表（epoch 毫秒、Asia/Shanghai 15:00），不是 dict；
* 缺日 K → ``DataCoverageError``（数据故障，不是停牌）；
* 无本地分时 → ``try_intraday`` 返回 ``None``，由既有 ``historical_events`` 走
  日 K 兜底；``intraday`` 直接抛错，绝不触网；
* 交易日历来自调用方注入的已确认 bar 日期并集。

实验报告必须把「本地日 K 兜底」作为口径标签，不冒充实时盘中撮合。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Mapping
from zoneinfo import ZoneInfo

from dragon_quant.infra.models import KBar
from dragon_quant.review_account.market import DataCoverageError, at

__all__ = ["LocalAccountMarket"]

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _to_kbar(bar: Mapping) -> KBar | None:
    day = bar.get("date")
    if not isinstance(day, str) or len(day) < 10:
        return None
    open_px = _number(bar.get("open"))
    high = _number(bar.get("high"))
    low = _number(bar.get("low"))
    close = _number(bar.get("close"))
    if None in (open_px, high, low, close):
        return None
    volume = _number(bar.get("volume"))
    amount = _number(bar.get("amount"))
    pct = _number(bar.get("pct"))
    turnover = _number(bar.get("turnover"))
    return KBar(
        timestamp=at(day[:10], "15:00"),
        volume=volume if volume is not None else 0.0,
        open=open_px,
        high=high,
        low=low,
        close=close,
        chg=0.0,
        pct=pct if pct is not None else 0.0,
        turnover=turnover if turnover is not None else 0.0,
        amount=amount if amount is not None else 0.0,
    )


def _kbar_day(bar: KBar) -> str:
    return datetime.fromtimestamp(bar.timestamp / 1000,
                                  SHANGHAI).strftime("%Y-%m-%d")


class LocalAccountMarket:
    def __init__(self, rows: Mapping[str, list] | None = None,
                 calendar_days: list[str] | None = None) -> None:
        self._rows: dict[str, list[KBar]] = {}
        invalid: list[str] = []
        for code, bars in (rows or {}).items():
            by_day: dict[str, KBar] = {}
            for bar in bars or []:
                if not isinstance(bar, Mapping):
                    continue
                kbar = _to_kbar(bar)
                if kbar is None:
                    invalid.append(str(code))
                    continue
                by_day[_kbar_day(kbar)] = kbar
            if by_day:
                self._rows[str(code)] = [by_day[day]
                                         for day in sorted(by_day)]
        self._invalid_codes = sorted(set(invalid))
        self._calendar = sorted({str(day) for day in (calendar_days or [])
                                 if day})

    @property
    def invalid_codes(self) -> list[str]:
        return list(self._invalid_codes)

    def daily(self, code: str, start: str | None = None,
              refresh: bool = False) -> list[KBar]:
        bars = self._rows.get(str(code))
        if not bars:
            raise DataCoverageError(f"{code} 缺少本地日K，无法验证实验")
        # 与 ``MarketData.daily`` 同语义：``start`` 只是缓存/取数起点提示，
        # 返回完整历史（含 20 日预热），否则 historical_events 无法建行。
        return list(bars)

    def calendar(self, start: str, end: str,
                 include_today: bool = False) -> list[str]:
        return [day for day in self._calendar if start <= day <= end]

    def try_intraday(self, code: str, day: str, until=None):
        return None

    def intraday(self, code: str, day: str, until=None):
        raise DataCoverageError("本地快照无分时数据（实验不允许触网）")

    def codes(self) -> list[str]:
        return sorted(self._rows)
