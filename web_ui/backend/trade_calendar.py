"""交易日历探测：TDX 指数日K + 进程内按自然日缓存（标准库 only）。

复用 ``dragon_quant.infra.market_calendar.tdx_trade_dates``（prep / auction 同源），
不在 Web 进程里另造日历口径。构造本对象**无 I/O**，首次 ``lookup`` 才联网；
每天只探一次，失败 5 分钟后重试，且失败一律降级为工作日口径、绝不阻塞页面。

当天的确认规则与 ``build_trade_calendar`` 一致：收盘后 15:05 起才承认「今天
不在日历里 = 非交易日」；此前（含 09:25 竞价窗口）按工作日口径假设为交易日，
避免盘中拿不到当日日K时误禁用。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable, Sequence

#: 收盘后 5 分钟才可确认「今天不是交易日」。
CONFIRM_AFTER = (15, 5)
SUCCESS_TTL_S = 6 * 3600
FAILURE_TTL_S = 300


def _default_loader() -> list[str]:
    """默认探测：TDX 上证指数日K（惰性 import，构造本模块不触网）。"""
    from dragon_quant.infra.market_calendar import tdx_trade_dates
    from dragon_quant.infra.provider.tdx import TdxProvider

    return tdx_trade_dates(TdxProvider())


class TradeCalendarProbe:
    """进程内交易日历缓存；``loader`` / ``mono`` 可注入（测试）。"""

    def __init__(
        self,
        loader: Callable[[], Sequence[str]] | None = None,
        *,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loader = loader or _default_loader
        self._mono = mono
        self._lock = threading.Lock()
        self._dates: frozenset[str] | None = None
        self._cached_day: str | None = None
        self._fetched_at: float | None = None
        self._ttl = SUCCESS_TTL_S

    def lookup(self, now: datetime) -> tuple[bool, str]:
        """返回 ``(是否交易日, 依据)``。

        依据取值：``weekend`` / ``tdx_calendar`` / ``weekday_assumed`` /
        ``weekday_fallback``（日历不可用）。
        """
        # 周末不必联网：先按自然日给结论。
        if now.weekday() >= 5:
            return False, "weekend"
        today = now.strftime("%Y-%m-%d")
        dates = self._dates_for(today, now)
        if dates is None:
            return True, "weekday_fallback"
        if today in dates:
            return True, "tdx_calendar"
        if (now.hour, now.minute) < CONFIRM_AFTER:
            return True, "weekday_assumed"
        return False, "tdx_calendar"

    def _dates_for(self, day: str, now: datetime) -> frozenset[str] | None:
        with self._lock:
            if (self._cached_day == day and self._fetched_at is not None
                    and self._mono() - self._fetched_at < self._ttl):
                return self._dates
            try:
                loaded = frozenset(
                    str(item) for item in (self._loader() or ())
                )
            except Exception:  # noqa: BLE001 - 日历不可用只降级，不影响页面
                self._dates = None
                self._ttl = FAILURE_TTL_S
            else:
                self._dates = loaded
                self._ttl = self._ttl_for_loaded(loaded, day, now)
            self._cached_day = day
            self._fetched_at = self._mono()
            return self._dates

    @staticmethod
    def _ttl_for_loaded(loaded: frozenset[str], day: str,
                        now: datetime) -> int:
        """今天缺席时的缓存时长。

        15:05 前拿不到当日日K属正常（盘前/盘中）：缓存只保留到 15:05 之后
        几十秒，届时重新探测——否则收盘前缓存的「今天缺席」会被误当成
        「非交易日」。
        """
        if day in loaded:
            return SUCCESS_TTL_S
        if (now.hour, now.minute) >= CONFIRM_AFTER:
            return SUCCESS_TTL_S
        boundary = now.replace(hour=CONFIRM_AFTER[0], minute=CONFIRM_AFTER[1],
                               second=0, microsecond=0)
        return max(int((boundary - now).total_seconds()), 0) + 60


__all__ = ["CONFIRM_AFTER", "FAILURE_TTL_S", "SUCCESS_TTL_S", "TradeCalendarProbe"]
