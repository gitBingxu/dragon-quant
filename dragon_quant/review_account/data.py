from __future__ import annotations

from datetime import datetime

from dragon_quant.cache.data_cache import DataCache
from dragon_quant.providers.xueqiu import XueqiuProvider
from dragon_quant.review_account.market import (
    SHANGHAI, DataCoverageError, at, build_daily_row, build_row, daily_bar, event_date,
    is_tradable_daily_bar, last_close, validate_bars,
)
from dragon_quant.review_account.models import (
    MarketEvent, UNAVAILABLE_DAILY_MISSING, UNAVAILABLE_SUSPENDED,
)


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
        closed = (day < now.strftime("%Y-%m-%d")
                  or (day == now.strftime("%Y-%m-%d") and now.strftime("%H:%M") >= "15:05"))
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

    def try_intraday(self, code: str, day: str, until: int | None = None) -> list | None:
        """取当日5分钟K（until 非空时只取到该时点）；缺失/不完整则返回 None（供日K兜底），不抛错。"""
        try:
            return self.intraday(code, day, until)
        except DataCoverageError:
            return None


def historical_day(day: str, now: datetime | None = None) -> bool:
    """day 是否早于当前自然日（历史交易日）。

    停牌分类只对历史交易日启用：当日日K可能尚未生成，且 `MarketData.daily` 的缓存
    按写入当日命名空间存放、不校验新鲜度，同日盘中写入的缓存会缺少当日行；此时
    「日K缺行」不能等价为停牌。当日一律沿用旧契约（缺当日日K仍硬报错）。
    """
    now = now or datetime.now(SHANGHAI)
    return day < now.strftime("%Y-%m-%d")


def historical_events(day: str, candidates: list[dict], codes: set[str], data: MarketData,
                      until: int | None = None):
    """生成当日事件流（按 code 三态可用性分类）。

    - 历史交易日：`intraday`（有完整5分钟K）/ `daily`（无分时但当日日K有效）/
      `suspended`（两者皆无）。剔除停牌 code 后再判断「是否全员有完整分时」：
      剩余可用 code 都有分时 → 严格盘中撮合；否则整日降级日K兜底（只为日K有效的
      code 建行，分时可用但日K缺行者记为 `daily_unavailable`）。
    - 当日（盘中/收盘后）：不做停牌判定，保持旧契约——全员有分时走严格撮合，
      否则整日兜底，当日日K未生成时由 `build_daily_row` 抛 `DataCoverageError`。
    - 停牌/不可用的 code 不进 rows，由 `MarketEvent.unavailable` 显式标记；它们的
      估值价放入 `last_closes`（仅用于 marks，不参与买卖评估）。

    `until` 用于盘中（实时今日）只取到最新已完成的5分钟K。买卖决策始终复用同一
    evaluate_buy/evaluate_sell，本函数只决定数据可用性与成交口径。
    """
    codes = sorted(codes)
    if not codes:
        yield from _daily_events(day, candidates, [], {}, {})
        return
    # 无日K历史仍抛错（数据源故障，不是停牌）；有历史但当日缺行由可用性判定处理。
    histories = {code: data.daily(code, day) for code in codes}
    intraday = {code: data.try_intraday(code, day, until) for code in codes}

    if not historical_day(day):
        if all(intraday[code] is not None for code in codes):
            yield from _intraday_events(day, candidates, codes, histories, intraday, {})
        else:
            yield from _daily_events(day, candidates, codes, histories, {})
        return

    has_daily = {code: is_tradable_daily_bar(daily_bar(histories[code], day)) for code in codes}
    unavailable = {code: UNAVAILABLE_SUSPENDED for code in codes
                   if intraday[code] is None and not has_daily[code]}
    active = [code for code in codes if code not in unavailable]

    if not active:  # 全部停牌：一条空白占位事件，净值不变、推进引擎换日
        yield MarketEvent(at(day, "15:00"), "late", {}, candidates,
                          unavailable=dict(unavailable),
                          last_closes=_valuations(day, unavailable, histories, intraday))
        return

    if any(intraday[code] is None for code in active):  # 混合日 → 整日日K兜底
        usable = [code for code in active if has_daily[code]]
        if not usable:  # 不可达：无分时且无日K者已被判停牌
            raise DataCoverageError(f"{day} 无可建行标的，数据可用性判定异常")
        unavailable |= {code: UNAVAILABLE_DAILY_MISSING
                        for code in active if not has_daily[code]}
        yield from _daily_events(day, candidates, usable, histories, unavailable,
                                 _valuations(day, unavailable, histories, intraday))
        return

    yield from _intraday_events(day, candidates, active, histories, intraday, unavailable,
                                _valuations(day, unavailable, histories, intraday))


def _valuations(day, unavailable, histories, intraday) -> dict[str, float]:
    """不可用 code 的估值价：停牌用前收；日K缺行（有分时）用当日末根5分钟K收盘。"""
    values = {}
    for code, reason in unavailable.items():
        price = None
        if reason == UNAVAILABLE_DAILY_MISSING and intraday.get(code):
            price = intraday[code][-1].close
        if price is None:
            price = last_close(histories.get(code, []), day)
        if price is not None:
            values[code] = price
    return values


def _intraday_events(day, candidates, codes, histories, intraday, unavailable,
                     last_closes=None):
    # 以实际取得的5分钟K时点为准（实时今日为部分当日），而非固定48根 bar_times。
    times = sorted({b.timestamp for b in intraday[codes[0]]}) if codes else []
    marked = dict(unavailable)
    valuations = dict(last_closes or {})

    def make(ts, phase, prices=None):
        rows = {}
        for code in codes:
            row = build_row(histories[code], day, intraday[code][0].open, intraday[code], ts,
                            execution_price=(prices or {}).get(code))
            row["phase"] = phase
            rows[code] = row
        return MarketEvent(ts, phase, rows, candidates,
                           unavailable=dict(marked), last_closes=dict(valuations))
    yield make(at(day, "09:30"), "open")
    for i, timestamp in enumerate(times):
        opening_ts = at(day, "09:30") if i == 0 else timestamp - 300_000
        yield make(opening_ts + 1, "fill", {c: intraday[c][i].open for c in codes})
        phase = "late" if timestamp == at(day, "14:55") else "close" if timestamp == at(day, "15:00") else "bar"
        yield make(timestamp, phase)


def _daily_events(day, candidates, codes, histories, unavailable, last_closes=None):
    marked = dict(unavailable)
    valuations = dict(last_closes or {})

    def row(code, phase, ts, price=None):
        return build_daily_row(histories[code], day, phase, ts, execution_price=price)

    def event(ts, phase, rows):
        return MarketEvent(ts, phase, rows, candidates,
                           unavailable=dict(marked), last_closes=dict(valuations))

    open_ts, close_ts = at(day, "09:30"), at(day, "15:00")
    # 开盘：判定开盘买点（不成交）→ 开盘价成交
    yield event(open_ts, "open", {c: row(c, "open", open_ts) for c in codes})
    yield event(open_ts + 1, "fill",
                {c: row(c, "open", open_ts + 1, histories_open(histories[c], day)) for c in codes})
    # 收盘：按当日OHLC判定卖出（不成交）→ 收盘价成交（卖价按原因在引擎侧近似）
    yield event(close_ts, "late", {c: row(c, "late", close_ts) for c in codes})
    yield event(close_ts + 1, "fill",
                {c: row(c, "late", close_ts + 1, histories_close(histories[c], day)) for c in codes})


def histories_open(history, day):
    bar = daily_bar(history, day)
    return bar.open if bar else None


def histories_close(history, day):
    bar = daily_bar(history, day)
    return bar.close if bar else None
