import copy
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from dragon_quant.models.types import KBar
from dragon_quant.review_account.data import MarketData, historical_events
from dragon_quant.review_account.engine import TradingEngine
from dragon_quant.review_account.execution import break_even_price, buy_quantity, fee, fill_price
from dragon_quant.review_account.market import DataCoverageError, at, bar_times, build_row, collect_candidates, validate_bars
from dragon_quant.review_account.models import AccountState, MarketEvent, Position, StrategyConfig
from dragon_quant.review_account.simulator import AccountSimulator
from dragon_quant.review_account.strategy import evaluate_buy, evaluate_sell

DAY = "2026-09-07"
CAND = {"code": "600001", "name": "样本", "rank": 1, "composite_score": 80,
        "is_true_dragon": True, "trade_date": "2026-09-04"}


def history(day=DAY, price=10):
    end = date.fromisoformat(day)
    days = [end - timedelta(days=n) for n in range(50, 0, -1) if (end - timedelta(days=n)).weekday() < 5]
    return [KBar(at(d.isoformat(), "15:00"), 1_000_000, price, price + .2, price - .2,
                 price, 0, 0, 12, 1_000_000_000) for d in days]


def bars(day=DAY, price=10, turnover=1.0):
    return [KBar(t, 10000, price, price + .05, price - .05, price, 0, 0, turnover, 100000)
            for t in bar_times(day)]


def row(day=DAY, phase="open", price=10, timestamp=None, known=None):
    ts = timestamp or at(day, "09:30")
    result = build_row(history(day), day, price, known or [], ts, execution_price=price)
    result["phase"] = phase
    return result


def position(entry="2026-09-04", **kw):
    return Position("600001", "样本", 1000, entry, 10, 10005, "buy", "buy", highest_price=10, **kw)


class FakeData:
    def __init__(self, days=None, intraday_missing=None):
        self.days = days or ["2026-09-04", DAY, "2026-09-08"]
        self.intraday_missing = set(intraday_missing or [])
    def calendar(self, start, end, include_today=False):
        return [d for d in self.days if start <= d <= end]
    def daily(self, code, start, refresh=False):
        return history("2026-09-09")
    def intraday(self, code, day, until=None):
        if day in self.intraday_missing:
            raise DataCoverageError(f"{day} 无完整5分钟K")
        return validate_bars(bars(day), day, until)
    def try_intraday(self, code, day):
        try:
            return self.intraday(code, day)
        except DataCoverageError:
            return None


class TestStrategy(unittest.TestCase):
    def test_open_uses_previous_day_amount_not_candidate_or_future(self):
        r = row()
        cfg = StrategyConfig()
        self.assertEqual(evaluate_buy(CAND, r, cfg, r["prev_row"])["code"], "buy_open_ma5_pullback")
        r["prev_row"]["amount"] = 1
        self.assertIsNone(evaluate_buy({**CAND, "amount": 9e9}, {**r, "amount": 9e9}, cfg, r["prev_row"]))

    def test_not_true_dragon_and_score_floor(self):
        r = row()
        for cand in ({**CAND, "is_true_dragon": False}, {**CAND, "composite_score": 49}):
            self.assertIsNone(evaluate_buy(cand, r, StrategyConfig(), r["prev_row"]))

    def test_risk_profile_rejects_deep_ma5_and_falling_trend(self):
        r = row(price=9.8)
        self.assertIsNone(evaluate_buy(CAND, r, StrategyConfig(ma5_min_distance_pct=-1), r["prev_row"]))
        self.assertIsNone(evaluate_buy(CAND, row(), StrategyConfig(require_rising_ma5=True), row()["prev_row"]))

    def test_turn_strong_needs_first_bar_volume(self):
        cfg = StrategyConfig()
        # 高开突破前高发生在首根5分钟K(phase=bar, 1根)；带量则触发，无量则不触发
        strong = bars(price=10.4, turnover=0.6)[:1]
        weak = bars(price=10.4, turnover=0.3)[:1]
        r_strong = row(phase="bar", price=10.4, timestamp=at(DAY, "09:35"), known=strong)
        r_weak = row(phase="bar", price=10.4, timestamp=at(DAY, "09:35"), known=weak)
        self.assertEqual(evaluate_buy(CAND, r_strong, cfg, r_strong["prev_row"], None, strong)["code"],
                         "buy_open_turn_strong")
        self.assertIsNone(evaluate_buy(CAND, r_weak, cfg, r_weak["prev_row"], None, weak))
        # 开盘事件(phase=open)不再直接给突破买点
        self.assertIsNone(evaluate_buy(CAND, row(price=10.4), cfg, row(price=10.4)["prev_row"]))

    def test_no_open_signal_after_open(self):
        r = row(phase="bar")
        self.assertIsNone(evaluate_buy(CAND, r, StrategyConfig(), r["prev_row"]))

    def test_divergence_requires_full_contiguous_window(self):
        cfg = StrategyConfig()
        hist = [{"is_one_word_board": True, "pct": 10, "volume": 200},
                {"is_one_word_board": True, "pct": 10, "volume": 100}]
        b = bars(price=10.2)[:6]
        r = row(phase="bar", price=10.2, timestamp=at(DAY, "10:00"), known=b)
        self.assertIsNone(evaluate_buy(CAND, r, cfg, r["prev_row"], hist, b[:2]))
        self.assertEqual(evaluate_buy(CAND, r, cfg, r["prev_row"], hist, b)["code"], "buy_divergence_first_break")
        b[0].low = 9.9
        self.assertIsNone(evaluate_buy(CAND, r, cfg, r["prev_row"], hist, b))

    def test_t_plus_one(self):
        r = row()
        r["bar_low"] = 8
        self.assertEqual(evaluate_sell(position(entry=DAY), r, 0, StrategyConfig()), [])

    def test_first_and_regular_stop(self):
        r = row()
        r["bar_low"] = 9.6
        self.assertEqual(evaluate_sell(position(), r, 1, StrategyConfig())[0]["code"], "first_day_stop_loss")
        self.assertEqual(evaluate_sell(position(), r, 2, StrategyConfig()), [])
        r["bar_low"] = 9.4
        self.assertEqual(evaluate_sell(position(), r, 2, StrategyConfig())[0]["code"], "hard_stop_loss")

    def test_profit_protection_and_trailing(self):
        p = position(highest_return=7)
        r = row()
        self.assertEqual(evaluate_sell(p, r, 2, StrategyConfig())[0]["code"], "profit_back_to_cost_take_profit")
        p.highest_return, p.highest_price = 10, 11
        self.assertEqual(evaluate_sell(p, r, 2, StrategyConfig())[0]["code"], "trailing_take_profit")

    def test_high_open_windows(self):
        for gap, count in ((7.1, 1), (5.1, 6)):
            b = bars(price=10.5)[:count]
            r = row(phase="bar", price=10.5, timestamp=b[-1].timestamp, known=b)
            r["open_gap_pct"] = gap
            self.assertIn("no_limit", evaluate_sell(position(), r, 2, StrategyConfig(), b)[0]["code"])
            b[-1].high = 11
            self.assertEqual(evaluate_sell(position(), r, 2, StrategyConfig(), b), [])

    def test_weak_close_is_late_only_with_tolerance(self):
        r = row(phase="bar")
        r.update(close=9.99, bar_close=9.99, prev_close=9.98, ma5=9.98)
        self.assertEqual(evaluate_sell(position(), r, 2, StrategyConfig()), [])
        r["phase"] = "late"
        self.assertEqual(evaluate_sell(position(), r, 2, StrategyConfig()), [])
        r.update(close=9.8, bar_close=9.8)
        self.assertEqual(evaluate_sell(position(), r, 2, StrategyConfig())[0]["code"], "close_below_open_stop")

    def test_half_only_once_and_no_max_hold_exit(self):
        r = row(phase="late", price=11)
        r.update(limit_up=11, prev_close=10)
        self.assertEqual(evaluate_sell(position(), r, 1, StrategyConfig())[0]["code"], "next_day_limit_up_half")
        p = position(took_profit_half=True)
        self.assertEqual(evaluate_sell(p, r, 1, StrategyConfig()), [])
        self.assertEqual(evaluate_sell(position(), row(), 99, StrategyConfig()), [])


class TestEngine(unittest.TestCase):
    def test_buy_selection_prefers_priority_then_composite(self):
        # 同为开盘MA5承接(priority300)时，综合分更高者优先，不再看rank
        cfg = StrategyConfig()
        state = AccountState(100000)
        low = {**CAND, "code": "600001", "composite_score": 60, "rank": 1}
        high = {**CAND, "code": "600002", "composite_score": 95, "rank": 4}
        rows = {"600001": row(), "600002": row()}
        result = TradingEngine(cfg, state).step(MarketEvent(at(DAY, "09:30"), "open", rows, [low, high]))
        self.assertEqual(state.pending[0]["stock_code"], "600002")

    def test_signal_then_fill_and_duplicate(self):
        state = AccountState(100000)
        engine = TradingEngine(StrategyConfig(), state)
        event = MarketEvent(at(DAY, "09:30"), "open", {"600001": row()}, [CAND])
        self.assertEqual(engine.step(event)["trades"], [])
        self.assertEqual(len(state.pending), 1)
        self.assertEqual(engine.step(event)["reason_code"], "already_processed")
        r = row(timestamp=event.timestamp + 1)
        result = engine.step(MarketEvent(event.timestamp + 1, "fill", {"600001": r}, [CAND]))
        self.assertEqual(len(result["trades"]), 1)
        self.assertGreater(result["trades"][0].price, 10)
        self.assertGreaterEqual(state.cash, 0)

    def test_sell_blocks_rebuy_and_gap_does_not_fill_at_stop(self):
        state = AccountState(1000, [position()])
        engine = TradingEngine(StrategyConfig(), state)
        r = row()
        r["bar_low"] = 9.5
        engine.step(MarketEvent(at(DAY, "09:30"), "open", {"600001": r}, [CAND]))
        r = row(timestamp=at(DAY, "09:30") + 1, price=9.3)
        result = engine.step(MarketEvent(r["observed_at"], "fill", {"600001": r}, [CAND]))
        self.assertLess(result["trades"][0].price, 9.3)
        self.assertTrue(state.sold_today)
        self.assertFalse(state.positions)

    def test_same_bar_profit_does_not_retroactively_activate(self):
        p = position()
        state = AccountState(0, [p])
        r = row(phase="bar", timestamp=at(DAY, "09:35"), known=bars()[:1])
        r.update(bar_high=10.7, bar_low=10, bar_close=10.4)
        engine = TradingEngine(StrategyConfig(), state)
        self.assertEqual(engine.step(MarketEvent(r["observed_at"], "bar", {"600001": r}))["pending"], [])
        self.assertGreater(p.highest_return, 6)
        duplicate = {**r, "observed_at": r["observed_at"] + 1}
        engine.step(MarketEvent(duplicate["observed_at"], "bar", {"600001": duplicate}))
        self.assertEqual(state.pending, [])

    def test_t_plus_one_still_tracks_profit(self):
        p = position(entry=DAY, entry_signal={"fill_timestamp": at(DAY, "09:30") + 1})
        state = AccountState(0, [p])
        r = row(phase="bar", timestamp=at(DAY, "09:35"), known=bars()[:1])
        r.update(bar_high=10.8, bar_low=9, bar_close=10.8)
        TradingEngine(StrategyConfig(), state).step(MarketEvent(r["observed_at"], "bar", {"600001": r}))
        self.assertEqual(state.pending, [])
        self.assertGreater(p.highest_return, 7)

    def test_stale_execution_quote_does_not_fill(self):
        state = AccountState(100000)
        engine = TradingEngine(StrategyConfig(), state)
        r = row()
        engine.step(MarketEvent(r["observed_at"], "open", {"600001": r}, [CAND]))
        r["observed_at"] += 1
        self.assertEqual(engine.step(MarketEvent(r["observed_at"], "fill", {"600001": r}))["trades"], [])

    def test_risk_budget_and_limit_price(self):
        cfg = StrategyConfig(max_position_pct=25, risk_per_trade_pct=1)
        qty = buy_quantity(100000, 100000, 10, cfg)
        self.assertLessEqual(qty * 10 + fee(qty * 10, "BUY", cfg), 25000)
        self.assertEqual(qty % 100, 0)
        self.assertIsNone(fill_price({"execution_price": 11, "limit_up": 11}, "BUY", cfg))
        self.assertIsNone(fill_price({"execution_price": 9, "limit_down": 9}, "SELL", cfg))
        p = position()
        gross = break_even_price(p, cfg) * (1 - cfg.sell_slippage) * p.qty
        self.assertGreaterEqual(gross - fee(gross, "SELL", cfg) + 1e-8, p.cost)


class TestMarketAndSimulator(unittest.TestCase):
    def test_calendar_pool_does_not_skip_empty_dates(self):
        loader = MagicMock(return_value=[])
        collect_candidates(["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", DAY], DAY, StrategyConfig(), loader)
        self.assertEqual([c.args[0] for c in loader.call_args_list], ["2026-09-02", "2026-09-03", "2026-09-04"])

    def test_candidate_pool_is_all_true_dragons_filtered_and_score_sorted(self):
        # 前三日全部真龙并集，共享层过滤否决/低分，按综合分降序，不再截断前5
        pool = [{**CAND, "code": "A", "composite_score": 60, "rank": 3},
                {**CAND, "code": "B", "composite_score": 90, "rank": 2},
                {**CAND, "code": "C", "composite_score": 49},        # 低于门槛剔除
                {**CAND, "code": "D", "composite_score": 70, "is_true_dragon": False},  # 否决剔除
                {**CAND, "code": "E", "composite_score": 55, "rank": 1}]
        got = collect_candidates(["2026-09-04"], DAY, StrategyConfig(), lambda day, **kw: pool)
        self.assertEqual([c["code"] for c in got], ["B", "A", "E"])
        # loader 以 top_n=None 取全部真龙
        cap = MagicMock(return_value=[])
        collect_candidates(["2026-09-04"], DAY, StrategyConfig(), cap)
        self.assertIsNone(cap.call_args.kwargs["top_n"])

    def test_merge_dedup_keeps_higher_score(self):
        def loader(day, **kw):
            return ([{**CAND, "code": "X", "composite_score": 60}] if day == "2026-09-03"
                    else [{**CAND, "code": "X", "composite_score": 88}])
        got = collect_candidates(["2026-09-03", "2026-09-04"], DAY, StrategyConfig(), loader)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["composite_score"], 88)

    def test_validate_full_window_and_no_lunch_gap(self):
        self.assertEqual(len(validate_bars(bars(), DAY)), 48)
        with self.assertRaises(DataCoverageError):
            validate_bars(bars()[:2], DAY)
        broken = bars()
        broken[24].timestamp = at(DAY, "11:35")
        with self.assertRaises(DataCoverageError):
            validate_bars(broken, DAY)

    def test_history_and_current_bars_are_sliced(self):
        b = bars()
        b[-1].close = 50
        r = build_row(history(), DAY, 10, b, at(DAY, "09:35"))
        self.assertEqual(r["close"], 10)
        self.assertEqual(len(r["intraday_bars"]), 1)

    def test_simulator_can_buy_first_requested_day(self):
        with patch("dragon_quant.review_account.simulator.db.get_dragons_by_date", return_value=[CAND]):
            result = AccountSimulator(StrategyConfig(), data=FakeData()).run(DAY, DAY)
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0].trade_date, DAY)
        self.assertEqual(result["account_state"]["positions"][0]["code"], CAND["code"])

    def test_daily_fallback_runs_when_intraday_missing(self):
        data = FakeData(intraday_missing={DAY})
        with patch("dragon_quant.review_account.simulator.db.get_dragons_by_date", return_value=[CAND]):
            result = AccountSimulator(StrategyConfig(), data=data).run(DAY, DAY)
        self.assertEqual(result["data_quality"], "daily_fallback")
        self.assertEqual(result["fallback_days"], 1)
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0].signal.get("data_quality"), "daily_fallback")

    def test_missing_daily_still_fails(self):
        data = FakeData(intraday_missing={DAY})
        data.daily = MagicMock(side_effect=DataCoverageError("no daily"))
        with patch("dragon_quant.review_account.simulator.db.get_dragons_by_date", return_value=[CAND]), self.assertRaises(DataCoverageError):
            AccountSimulator(StrategyConfig(), data=data).run(DAY, DAY)

    def test_daily_fallback_stop_loss_price_by_reason(self):
        from dragon_quant.review_account.execution import daily_fallback_sell_price
        cfg = StrategyConfig()
        p = position()
        row_gap = {"open": 9.0, "bar_close": 9.5}
        # 跳空低开穿止损 → 按开盘价
        self.assertEqual(daily_fallback_sell_price(p, row_gap, "hard_stop_loss", cfg), 9.0)
        # 未跳空 → 按止损线
        self.assertAlmostEqual(daily_fallback_sell_price(p, {"open": 9.9, "bar_close": 9.4}, "hard_stop_loss", cfg),
                               10 * (1 + cfg.stop_loss_pct / 100))
        # 其它原因 → 收盘价
        self.assertEqual(daily_fallback_sell_price(p, {"open": 10, "bar_close": 10.6}, "trailing_take_profit", cfg), 10.6)

    def test_missing_intraday_fails_instead_of_faking_returns(self):
        data = FakeData()
        data.daily = MagicMock(side_effect=DataCoverageError("missing"))
        with patch("dragon_quant.review_account.simulator.db.get_dragons_by_date", return_value=[CAND]), self.assertRaises(DataCoverageError):
            AccountSimulator(StrategyConfig(), data=data).run(DAY, DAY)

    def test_historical_fill_is_after_signal(self):
        events = list(historical_events(DAY, [CAND], {CAND["code"]}, FakeData()))
        self.assertEqual(events[0].phase, "open")
        self.assertEqual(events[1].phase, "fill")
        self.assertGreater(events[1].timestamp, events[0].timestamp)
        late = next(i for i,e in enumerate(events) if e.phase == "late")
        self.assertEqual(events[late + 1].phase, "fill")

    def test_evaluation_does_not_promote_on_data_gaps(self):
        from dragon_quant.review_account.evaluation import compare_strategies
        data = FakeData()
        result = compare_strategies(DAY, "2026-09-11", data)
        self.assertEqual(result["status"], "insufficient_data")
        self.assertFalse(result["promoted"])

    def test_config_validation(self):
        for values in ({"initial_cash": 0}, {"buy_slippage": float("nan")}, {"max_positions": 2.5},
                       {"made_up": 2}, {"max_position_pct": 101}, {"stop_loss_pct": 1}):
            with self.assertRaises(ValueError):
                StrategyConfig.from_dict(values)

    def test_provider_intraday_unadjusted(self):
        from dragon_quant.providers.xueqiu import XueqiuProvider
        with patch("dragon_quant.providers.xueqiu._fetch", return_value={}) as fetch:
            XueqiuProvider().get_5min_kline_for("600001", at(DAY, "09:30"), fq_type="normal")
        self.assertIn("type=normal", fetch.call_args.args[0])

    def test_daily_lookback_scales(self):
        from dragon_quant.providers.xueqiu import XueqiuProvider
        with patch("dragon_quant.providers.xueqiu.time.time", return_value=1800000000), patch("dragon_quant.providers.xueqiu._fetch", return_value={}) as fetch:
            XueqiuProvider().get_kline("600001", days=500)
        self.assertIn(f"begin={1800000000000 - 1000 * 86400000}", fetch.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
