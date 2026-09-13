from __future__ import annotations

from datetime import datetime

from dragon_quant.cache.data_cache import DataCache
from dragon_quant.providers.xueqiu import XueqiuProvider
from dragon_quant.review_account.market import (
    SHANGHAI, DataCoverageError, at, bar_times, build_row, event_date, validate_bars,
)
from dragon_quant.review_account.models import MarketEvent


class MarketData:
    def __init__(self, provider=None, cache=None, offline=False):
        self.provider = provider or XueqiuProvider()
        self.cache = cache or DataCache()
        self.offline = offline
        self._daily = {}
        self._bars = {}

    def daily(self, code: str, start: str, refresh=False) -> list:
        days = max(260, (datetime.now(SHANGHAI).date() - datetime.fromisoformat(start).date()).days + 90)
        today = datetime.now(SHANGHAI).strftime("%Y-%m-%d")
        key = (code, days)
        if key not in self._daily or refresh:
            cache_key = f"kline:day:{code}:normal:{days}"
            bars = None if refresh else self.cache.load_for_trade_date(cache_key, today, namespace="account")
            if bars is None and not self.offline:
                bars = self.provider.get_kline(code, days=days, fq_type="normal")
                if bars:
                    self.cache.set_for_trade_date(cache_key, bars, today, namespace="account")
            if not bars:
                raise DataCoverageError(f"{code} 缺少日K，无法验证交易日历/指标")
            self._daily[key] = sorted(bars, key=lambda b: b.timestamp)
        return self._daily[key]

    def calendar(self, start: str, end: str, include_today=False) -> list[str]:
        now = datetime.now(SHANGHAI)
        today = now.strftime("%Y-%m-%d")
        dates = {event_date(b.timestamp) for b in self.daily("SH000001", start)}
        if include_today and end == today and today not in dates and not self.offline:
            dates = {event_date(b.timestamp) for b in self.daily("SH000001", start, refresh=True)}
        if min(dates) > start:
            from datetime import timedelta
            if datetime.fromisoformat(min(dates)) - datetime.fromisoformat(start) > timedelta(days=10):
                raise DataCoverageError("日K历史不足以覆盖请求区间，禁止截短回测")
        result = sorted(d for d in dates if start <= d <= end
                        and (d < today or (d == today and (include_today or now.strftime("%H:%M") >= "15:05"))))
        if not result:
            raise DataCoverageError("请求区间没有可验证的交易日历，不生成空回测")
        return result

    def intraday(self, code: str, day: str, until: int | None = None) -> list:
        cache_key = f"kline:5min:{code}:normal"
        key = (code, day)
        now = datetime.now(SHANGHAI)
        closed = day < now.strftime("%Y-%m-%d") or (day == now.strftime("%Y-%m-%d") and now.strftime("%H:%M") >= "15:05")
        if key not in self._bars or not closed:
            bars = self.cache.load_for_trade_date(cache_key, day, namespace="account") if closed else None
            if bars is None and not self.offline:
                fetched = self.provider.get_5min_kline_for(code, at(day, "09:30"), fq_type="normal") or []
                bars = [b for b in fetched if event_date(b.timestamp) == day]
            try:
                bars = validate_bars(bars or [], day, None if closed else until)
            except DataCoverageError as exc:
                raise DataCoverageError(f"{code}: {exc}") from exc
            if closed:
                self._bars[key] = bars
                if not self.offline:
                    self.cache.set_for_trade_date(cache_key, bars, day, namespace="account")
            else:
                return bars
        return validate_bars(self._bars[key], day, until)


def historical_events(day: str, candidates: list[dict], codes: set[str], data: MarketData):
    histories, intraday = {}, {}
    for code in sorted(codes):
        histories[code] = data.daily(code, day)
        intraday[code] = data.intraday(code, day)
        build_row(histories[code], day, intraday[code][0].open, [], at(day, "09:30"))
    def make(ts, phase, prices=None):
        rows = {}
        for code in sorted(codes):
            row = build_row(histories[code], day, intraday[code][0].open, intraday[code], ts,
                            execution_price=(prices or {}).get(code))
            row["phase"] = phase
            rows[code] = row
        return MarketEvent(ts, phase, rows, candidates)
    yield make(at(day, "09:30"), "open")
    for i, timestamp in enumerate(bar_times(day)):
        opening_ts = at(day, "09:30") if i == 0 else timestamp - 300_000
        yield make(opening_ts + 1, "fill", {c: intraday[c][i].open for c in codes})
        phase = "late" if timestamp == at(day, "14:55") else "close" if timestamp == at(day, "15:00") else "bar"
        yield make(timestamp, phase)
