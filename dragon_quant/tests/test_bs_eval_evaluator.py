"""``BsEvaluator``：四类买点、双口径、卖出/半仓腿、状态机与无前视。"""

from __future__ import annotations

from datetime import date, timedelta

from dragon_quant.bs_eval.evaluator import BsEvaluator
from dragon_quant.bs_eval.main import _engine_config
from dragon_quant.infra.models import KBar
from dragon_quant.review_account.data import DataCoverageError
from dragon_quant.review_account.market import at, bar_times

TRADE_DATE = "2026-09-01"
BUY_DAY = "2026-09-02"
CALENDAR_START = "2026-08-03"
CALENDAR_END = "2026-09-11"


def weekdays(start: str, end: str) -> list[str]:
    current = date.fromisoformat(start)
    last = date.fromisoformat(end)
    out: list[str] = []
    while current <= last:
        if current.weekday() < 5:
            out.append(current.isoformat())
        current += timedelta(days=1)
    return out


CALENDAR = weekdays(CALENDAR_START, CALENDAR_END)


def buy_trigger_day() -> str:
    future = [day for day in CALENDAR if day > TRADE_DATE]
    return future[0]


def row_for_day(trade_date: str = TRADE_DATE) -> dict:
    return {
        "trade_date": trade_date, "code": "600001", "name": "样本",
        "rank": 1, "composite_score": None, "is_true_dragon": False,
    }


def _daily_bar(day: str, *, open_=10.0, high=10.2, low=9.8, close=10.0,
               volume=1_000_000.0, turnover=12.0,
               amount=1_000_000_000.0) -> KBar:
    return KBar(at(day, "15:00"), volume, open_, high, low, close, 0.0,
                0.0, turnover, amount)


def _intraday(day: str, *, price=10.0, open_price=None,
              overrides=None) -> list[KBar]:
    bars = []
    opening = price if open_price is None else open_price
    for timestamp in bar_times(day):
        o = opening if timestamp == bar_times(day)[0] else price
        h, low, c = price + 0.05, price - 0.05, price
        override = (overrides or {}).get(timestamp)
        if override:
            o = override.get("open", o)
            h = override.get("high", h)
            low = override.get("low", low)
            c = override.get("close", c)
        bars.append(KBar(timestamp, 10_000.0, o, h, low, c, 0.0, 0.0, 1.0,
                         100_000.0))
    return bars


class FakeData:
    def __init__(self, calendar=None, *, daily_overrides=None,
                 intraday_overrides=None, no_intraday=(), missing_days=(),
                 missing_codes=(), short_history_codes=(), price=10.0,
                 buy_window_open=None):
        self.calendar_days = list(calendar or CALENDAR)
        self.daily_overrides = daily_overrides or {}
        self.intraday_overrides = intraday_overrides or {}
        self.no_intraday = set(no_intraday)
        self.missing_days = set(missing_days)
        self.missing_codes = set(missing_codes)
        self.short_history_codes = set(short_history_codes)
        self.price = price
        self.buy_window_open = buy_window_open

    def calendar(self, start, end, include_today=False):
        return [day for day in self.calendar_days if start <= day <= end]

    def daily(self, code, start, refresh=False):
        if code in self.missing_codes:
            raise DataCoverageError(f"{code} 缺少日K")
        if code == "SH000001":
            return [_daily_bar(day, low=2990.0, high=3010.0, close=3000.0)
                    for day in self.calendar_days]
        days = self.calendar_days
        if code in self.short_history_codes:
            days = days[-5:]
        bars = []
        for day in days:
            if day in self.missing_days:
                continue
            values = {"open_": self.price, "high": self.price + 0.2,
                      "low": self.price - 0.2, "close": self.price}
            if day > TRADE_DATE and self.buy_window_open is not None:
                values["open_"] = self.buy_window_open
                values["close"] = self.buy_window_open
            values.update(self.daily_overrides.get(day, {}))
            bars.append(_daily_bar(day, **values))
        return bars

    def try_intraday(self, code, day, until=None):
        if code in self.missing_codes or day in self.no_intraday:
            return None
        open_price = (self.buy_window_open
                      if day > TRADE_DATE and self.buy_window_open is not None
                      else None)
        bars = _intraday(day, price=self.price, open_price=open_price,
                         overrides=self.intraday_overrides.get(day))
        return [bar for bar in bars if until is None or bar.timestamp <= until]

    def intraday(self, code, day, until=None):
        bars = self.try_intraday(code, day, until)
        if bars is None:
            raise DataCoverageError(f"{day} 无完整5分钟K")
        return bars


def run_row(row: dict, data: FakeData, calendar=None) -> dict:
    days = list(calendar or data.calendar_days)
    evaluator = BsEvaluator(_engine_config(), data, days, days[-1])
    return evaluator.evaluate(row)


# ─── 买入 ──────────────────────────────────────────────────────────────────


def test_buy_signal_dual_prices_and_metrics():
    data = FakeData()
    result = run_row(row_for_day(), data)
    assert result["eval_status"] == "open"
    assert result["buy_date"] == BUY_DAY
    assert result["buy_rule_code"] == "buy_open_ma5_pullback"
    assert result["buy_loose_price"] == 9.8
    assert result["buy_strict_price"] == 10.02
    assert result["buy_strict_max_return_5d"] is not None
    assert result["buy_loose_max_return_5d"] is not None
    assert result["buy_window_partial"] == 0
    assert result["buy_data_quality"] == "strict_5min"


def test_gate_neutral_candidate_still_triggers_all_four_pattern_family():
    # composite_score=None / is_true_dragon=False 仍进入买点判定（A 口径）。
    data = FakeData()
    result = run_row(row_for_day(), data)
    assert result["buy_rule_code"] in {
        "buy_open_ma5_pullback", "buy_open_turn_strong",
        "buy_divergence_first_break", "buy_dragon_comeback",
    }


def test_high_price_stock_still_fills_at_least_one_lot():
    data = FakeData(price=1700.0)
    result = run_row(row_for_day(), data)
    assert result["buy_date"] == BUY_DAY
    assert result["buy_strict_price"] is not None
    assert result["buy_strict_price"] > 1700.0


def test_no_lookahead_future_bars_do_not_change_buy_decision():
    baseline = run_row(row_for_day(), FakeData())
    extreme = FakeData(daily_overrides={
        "2026-09-03": {"high": 99.0, "low": 1.0, "close": 50.0},
        "2026-09-04": {"high": 99.0, "low": 1.0, "close": 50.0},
    })
    changed = run_row(row_for_day(), extreme)
    assert changed["buy_date"] == baseline["buy_date"] == BUY_DAY
    assert changed["buy_rule_code"] == baseline["buy_rule_code"]
    assert changed["buy_strict_price"] == baseline["buy_strict_price"]


# ─── 卖出 ──────────────────────────────────────────────────────────────────


def test_half_leg_then_final_clear_and_sell_window():
    late_ts = at("2026-09-03", "14:55")
    data = FakeData(
        daily_overrides={
            "2026-09-03": {"high": 11.0, "low": 10.0, "close": 11.0},
            "2026-09-04": {"high": 10.0, "low": 9.49, "close": 9.5},
        },
        intraday_overrides={
            "2026-09-03": {late_ts: {"open": 11.0, "high": 11.0,
                                    "low": 11.0, "close": 11.0}},
            "2026-09-04": {at("2026-09-04", "09:35"):
                           {"open": 9.7, "high": 9.8, "low": 9.49,
                            "close": 9.6}},
        },
    )
    result = run_row(row_for_day(), data)
    assert result["eval_status"] == "closed"
    assert result["is_terminal"] == 1
    assert result["sell_half_date"] == "2026-09-03"
    assert result["sell_half_rule_code"] == "next_day_limit_up_half"
    assert result["sell_date"] == "2026-09-04"
    # 半仓后峰值已到 11，剩余半仓由移动止盈链先触发（规则顺序的真实语义）。
    assert result["sell_rule_code"] == "trailing_take_profit"
    assert result["sell_window_partial"] == 0
    # 卖后窗口锚定最终全清日/价。
    assert result["sell_price"] is not None


def test_sell_window_partial_is_not_terminal():
    data = FakeData(
        daily_overrides={"2026-09-10": {"low": 9.4, "close": 9.4}},
        intraday_overrides={"2026-09-10": {at("2026-09-10", "10:00"):
                                           {"open": 9.6, "high": 9.7,
                                            "low": 9.4, "close": 9.5}}},
    )
    result = run_row(row_for_day(), data)
    assert result["eval_status"] == "closed"
    assert result["sell_date"] == "2026-09-10"
    assert result["sell_window_partial"] == 1
    assert result["is_terminal"] == 0


# ─── 状态机 ────────────────────────────────────────────────────────────────


def test_pending_when_buy_window_not_mature():
    result = run_row(row_for_day("2026-09-09"), FakeData())
    assert result["eval_status"] == "pending"
    assert result["eval_note"] == "buy_window_not_ready"
    assert result["is_terminal"] == 0
    assert result["buy_date"] is None


def test_no_signal_when_open_far_from_ma5():
    data = FakeData(buy_window_open=10.9)
    result = run_row(row_for_day(), data)
    assert result["eval_status"] == "no_signal"
    assert result["is_terminal"] == 1
    assert result["buy_date"] is None


def test_cannot_evaluate_for_missing_daily_and_short_warmup():
    missing = run_row(row_for_day(), FakeData(missing_codes={"600001"}))
    assert missing["eval_status"] == "cannot_evaluate"
    assert missing["eval_note"] == "daily_missing"

    short = run_row(row_for_day(), FakeData(short_history_codes={"600001"}))
    assert short["eval_status"] == "cannot_evaluate"
    assert short["eval_note"] == "warmup_insufficient"
    assert short["is_terminal"] == 0


def test_hold_cap_reached_is_terminal_open():
    calendar = weekdays(CALENDAR_START, "2026-12-11")
    data = FakeData(calendar=calendar)
    result = run_row(row_for_day(), data, calendar=calendar)
    assert result["eval_status"] == "open"
    assert result["eval_note"] == "hold_cap_reached"
    assert result["is_terminal"] == 1
    # 买入日 + 买入后 60 个交易日。
    assert result["replay_days"] == 61


# ─── 数据质量与停牌 ────────────────────────────────────────────────────────


def test_daily_fallback_quality_is_recorded_not_faked():
    future = [day for day in CALENDAR if day > TRADE_DATE]
    data = FakeData(no_intraday=set(future))
    result = run_row(row_for_day(), data)
    assert result["buy_date"] == BUY_DAY
    assert result["buy_data_quality"] == "daily_fallback"
    assert result["data_quality"] == "daily_fallback"
    assert result["fallback_days"] == len(future)


def test_mixed_quality_is_marked_not_collapsed_to_strict():
    data = FakeData(no_intraday={BUY_DAY})
    result = run_row(row_for_day(), data)
    assert result["buy_data_quality"] == "daily_fallback"
    assert result["data_quality"] == "mixed"


def test_suspended_day_is_counted_without_faking_quality():
    data = FakeData(no_intraday={"2026-09-07"}, missing_days={"2026-09-07"})
    result = run_row(row_for_day(), data)
    assert result["suspended_days"] == 1
    # 停牌日不改变其它交易日的 quality。
    assert result["data_quality"] == "strict_5min"
