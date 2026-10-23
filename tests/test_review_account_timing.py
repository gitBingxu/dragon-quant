"""择时与仓位改造的单元测试：否决/禁买、卖出规则开关、时间止损、分级止盈、龙回头、组合风控、验证基建、首日止损改造。"""

import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from tests.test_review_account import CAND, DAY, FakeData, row, position

from dragon_quant.models.types import KBar
from dragon_quant.review_account.data import historical_events
from dragon_quant.review_account.engine import TradingEngine
from dragon_quant.review_account.execution import (
    apply_sell_fill, buy_quantity, daily_fallback_sell_price, sell_quantity,
)
from dragon_quant.review_account.market import at, load_market_sentiment
from dragon_quant.review_account.models import AccountState, MarketEvent, StrategyConfig, Trade
from dragon_quant.review_account.risk import risk_governor
from dragon_quant.review_account.strategy import (
    BuyRejected, entry_veto, evaluate_buy, evaluate_sell, explain_buy_candidate,
)


class TestVetoAndSentiment(unittest.TestCase):
    def test_entry_veto_default_passthrough(self):
        self.assertIsNone(entry_veto(StrategyConfig(), CAND, row()))

    def test_entry_veto_mounted_raises_buyrejected(self):
        with patch("dragon_quant.review_account.strategy.entry_veto", return_value="竞价弱"):
            with self.assertRaises(BuyRejected) as cm:
                evaluate_buy(CAND, row(), StrategyConfig(), row()["prev_row"])
            self.assertEqual(cm.exception.code, "vetoed")

    def test_sentiment_ban(self):
        cfg = StrategyConfig(sentiment_ban_enabled=True)
        with self.assertRaises(BuyRejected) as cm:
            evaluate_buy(CAND, row(), cfg, row()["prev_row"], sentiment_pct=0.0)
        self.assertEqual(cm.exception.code, "sentiment_ban")
        # 未开启禁买时，sentiment_pct=0 不生效
        self.assertIsNotNone(evaluate_buy(CAND, row(), StrategyConfig(), row()["prev_row"], sentiment_pct=0.0))
        # None 不生效
        self.assertIsNotNone(evaluate_buy(CAND, row(), cfg, row()["prev_row"], sentiment_pct=None))

    def test_explain_surfaces_reject_reason_and_full_signal(self):
        cfg = StrategyConfig(sentiment_ban_enabled=True)
        d = explain_buy_candidate(CAND, row(), cfg, row()["prev_row"], sentiment_pct=0.0)
        self.assertFalse(d["passed"])
        self.assertEqual(d["reason_code"], "sentiment_ban")
        self.assertIsNone(d["full_signal"])
        # 正常买点返回 full_signal
        d2 = explain_buy_candidate(CAND, row(), StrategyConfig(), row()["prev_row"])
        self.assertTrue(d2["passed"])
        self.assertEqual(d2["full_signal"]["code"], "buy_open_ma5_pullback")


class TestBuyQuantityScale(unittest.TestCase):
    def test_budget_scale(self):
        cfg = StrategyConfig()
        full = buy_quantity(100000, 100000, 10, cfg)
        self.assertEqual(buy_quantity(100000, 100000, 10, cfg, budget_scale=1.0), full)
        self.assertLess(buy_quantity(100000, 100000, 10, cfg, budget_scale=0.5), full)
        self.assertEqual(buy_quantity(100000, 100000, 10, cfg, budget_scale=0.0), 0)


class TestSellRuleTogglesAndTimeStop(unittest.TestCase):
    def test_hard_time_stop_default_off(self):
        # 默认关：hold_days=99 不退出（与既有 test_half_only_once_and_no_max_hold_exit 一致）
        self.assertEqual(evaluate_sell(position(), row(phase="late"), 99, StrategyConfig()), [])

    def test_hard_time_stop_when_enabled(self):
        cfg = StrategyConfig(sell_rule_toggles={"hard_time_stop": True})
        r = row(phase="late")
        r["limit_up"] = 11.0
        r["bar_close"] = 10.0
        self.assertEqual(evaluate_sell(position(), r, 5, cfg)[0]["code"], "hard_time_stop")
        # 封板豁免
        r["bar_close"] = 11.0
        self.assertEqual(evaluate_sell(position(), r, 5, cfg), [])

    def test_soft_time_stop(self):
        cfg = StrategyConfig(soft_time_stop_days=3, sell_rule_toggles={"soft_time_stop": True})
        p = position(highest_return=0)  # trailing 未激活
        r = row(phase="late")
        r["bar_close"] = 10.0  # 未脱离成本区
        self.assertEqual(evaluate_sell(p, r, 3, cfg)[0]["code"], "soft_time_stop")
        self.assertEqual(evaluate_sell(p, r, 2, cfg), [])  # 天数不足
        r["bar_close"] = 11.0  # 已脱离成本区
        self.assertEqual(evaluate_sell(p, r, 3, cfg), [])

    def test_toggle_off_existing_rule(self):
        cfg = StrategyConfig(sell_rule_toggles={"hard_stop": False})
        r = row()
        r["bar_low"] = 9.6  # 否则触发 first_day_stop_loss
        self.assertEqual(evaluate_sell(position(), r, 1, cfg), [])


class TestTieredTrailing(unittest.TestCase):
    def test_tiered_drawdown_thresholds(self):
        from dragon_quant.review_account.strategy import _tiered_drawdown
        cfg = StrategyConfig()
        self.assertEqual(_tiered_drawdown(10, cfg, 3.5), 3.5)  # tier1 基准
        self.assertEqual(_tiered_drawdown(16, cfg, 3.5), 5.0)  # tier2
        self.assertEqual(_tiered_drawdown(26, cfg, 3.5), 8.0)  # tier3

    def test_tiered_widens_tolerance(self):
        p = position(highest_return=20)
        p.highest_price = 12
        r = row()
        r["bar_close"] = 11.52  # 回撤 4%
        r["bar_low"] = 11.5     # 高于成本线，避免保本规则干扰
        # 单值模式：回撤 4% >= 3.5% 触发
        self.assertEqual(evaluate_sell(p, r, 2, StrategyConfig())[0]["code"], "trailing_take_profit")
        # 分级模式：20% 浮盈 → tier2 drawdown=5%，回撤 4% 不触发
        cfg = StrategyConfig(tiered_trailing_enabled=True)
        self.assertEqual(evaluate_sell(p, r, 2, cfg), [])


def _make_hist(rows):
    return [{"date": f"d{i}", "close": c, "open": c, "low": lo, "volume": v, "pct": p,
             "is_limit_up_close": lu, "ma20": None, "ma10": None}
            for i, (c, lo, v, p, lu) in enumerate(rows)]


def _dragon_hist():
    rows = [(7.0, 6.9, 200, 0.0, False)] * 14
    rows += [(8.0, 7.3, 250, 10.0, True), (9.0, 8.0, 280, 11.1, True), (10.0, 9.0, 300, 10.0, True),
             (9.5, 9.2, 50, -5.0, False), (8.5, 8.0, 40, -5.6, False), (8.9, 8.4, 90, 4.7, False)]
    hist = _make_hist(rows)
    hist[-1]["open"] = 8.6  # T-1 阳线（收 8.9 > 开 8.6）
    return hist


class TestDragonComeback(unittest.TestCase):
    def _buy(self, cfg, hist):
        r = row(price=10.9)  # 远离 MA5，MA5 回踩不触发，龙回头独立判定
        return evaluate_buy(CAND, r, cfg, r["prev_row"], hist)

    def test_dragon_comeback_fires(self):
        cfg = StrategyConfig(dragon_comeback_enabled=True)
        sig = self._buy(cfg, _dragon_hist())
        self.assertIsNotNone(sig)
        self.assertEqual(sig["code"], "buy_dragon_comeback")

    def test_dragon_comeback_enabled_by_default(self):
        self.assertTrue(StrategyConfig().dragon_comeback_enabled)
        sig = self._buy(StrategyConfig(), _dragon_hist())
        self.assertIsNotNone(sig)
        self.assertEqual(sig["code"], "buy_dragon_comeback")

    def test_dragon_comeback_can_be_disabled(self):
        self.assertIsNone(self._buy(StrategyConfig(dragon_comeback_enabled=False), _dragon_hist()))

    def test_dragon_comeback_rejects_unshrinking_volume(self):
        hist = _dragon_hist()
        hist[17]["volume"] = 150  # 回调未缩量
        cfg = StrategyConfig(dragon_comeback_enabled=True)
        self.assertIsNone(self._buy(cfg, hist))

    def test_dragon_comeback_rejects_break_ma20(self):
        hist = _dragon_hist()
        for r in hist[17:]:  # 回调段跌破 MA20
            r["ma20"] = 8.5
        cfg = StrategyConfig(dragon_comeback_enabled=True)
        self.assertIsNone(self._buy(cfg, hist))

    def test_dragon_comeback_rejects_wave_too_big(self):
        hist = _dragon_hist()
        hist[16]["close"] = 14.0  # 一波涨幅 ~103% ≥ 100，动能耗尽
        cfg = StrategyConfig(dragon_comeback_enabled=True)
        self.assertIsNone(self._buy(cfg, hist))

    def test_dragon_comeback_rejects_pullback_rebound(self):
        hist = _dragon_hist()
        hist[17]["pct"] = 4.0  # 回调期出现大阳反抽
        cfg = StrategyConfig(dragon_comeback_enabled=True)
        self.assertIsNone(self._buy(cfg, hist))


class TestRiskGovernor(unittest.TestCase):
    def test_default_off(self):
        delevel, allow, fired = risk_governor(StrategyConfig(), AccountState(100000), 100000)
        self.assertEqual(delevel, 1.0)
        self.assertTrue(allow)
        self.assertEqual(fired, [])

    def test_daily_loss_circuit_breaker(self):
        cfg = StrategyConfig(max_daily_loss_pct=2)
        delevel, allow, fired = risk_governor(cfg, AccountState(100000, realized_today=-3000), 100000)
        self.assertFalse(allow)
        self.assertIn("daily_loss_circuit_breaker", fired)
        self.assertEqual(delevel, 1.0)

    def test_drawdown_delevel(self):
        cfg = StrategyConfig(drawdown_delevel_pct=1)
        s = AccountState(88000)
        s.peak_equity = 100000
        delevel, _, fired = risk_governor(cfg, s, 88000)  # -12% → ×0.5
        self.assertEqual(delevel, 0.5)
        self.assertIn("drawdown_delevel", fired)

    def test_streak_delevel(self):
        cfg = StrategyConfig(streak_delevel_enabled=True)
        delevel, _, _ = risk_governor(cfg, AccountState(100000, consecutive_losses=4), 100000)
        self.assertEqual(delevel, 0.5)
        _, allow, _ = risk_governor(cfg, AccountState(100000, consecutive_losses=6), 100000)
        self.assertFalse(allow)


class TestEvaluationInfra(unittest.TestCase):
    def test_drawdown_decomposition(self):
        from dragon_quant.review_account.evaluation import drawdown_decomposition
        trades = [
            Trade("d", "c", "n", "SELL", 10, 100, 1000, 1, -50, 900, 0, "hard_stop_loss", "x"),
            Trade("d", "c", "n", "SELL", 10, 100, 1000, 1, 30, 900, 0, "trailing_take_profit", "x"),
            Trade("d", "c", "n", "BUY", 10, 100, 1000, 1, 0, 900, 100, "buy_open_ma5_pullback", "x"),
        ]
        b = drawdown_decomposition({"trades": trades})
        self.assertEqual(b["stop_loss"], -50)
        self.assertEqual(b["take_profit"], 30)
        self.assertEqual(b["other"], 0)

    def test_plateau_scan_structure(self):
        from dragon_quant.review_account.evaluation import plateau_scan
        data = FakeData()
        with patch("dragon_quant.review_account.simulator.candidate_loader", return_value=MagicMock(return_value=[CAND])):
            r = plateau_scan(DAY, DAY, data, StrategyConfig(), "max_position_pct", [25, 50, 100])
        self.assertIn("grid", r)
        self.assertEqual(r["trial_count"], 3)

    def test_ablation_structure(self):
        from dragon_quant.review_account.evaluation import ablation
        data = FakeData()
        with patch("dragon_quant.review_account.simulator.candidate_loader", return_value=MagicMock(return_value=[CAND])):
            r = ablation(DAY, DAY, data, StrategyConfig(), ["hard_stop", "trailing"])
        self.assertIn("baseline", r)
        self.assertEqual(set(r["marginal"]), {"hard_stop", "trailing"})


class TestLoadMarketSentiment(unittest.TestCase):
    def test_non_v2_returns_none(self):
        self.assertIsNone(load_market_sentiment(["2026-09-04", DAY], DAY, StrategyConfig(source="v1")))

    def test_missing_snapshot_returns_none(self):
        with patch("dragon_quant.storage.assets_db.get_prep_snapshot", return_value=None):
            self.assertIsNone(load_market_sentiment(["2026-09-04", DAY], DAY, StrategyConfig()))

    def test_hit_returns_clamped_pct(self):
        with patch("dragon_quant.storage.assets_db.get_prep_snapshot",
                   return_value={"sentiment": {"position_pct": 0.8}}):
            self.assertEqual(load_market_sentiment(["2026-09-04", DAY], DAY, StrategyConfig()), 0.8)
        with patch("dragon_quant.storage.assets_db.get_prep_snapshot",
                   return_value={"sentiment": {"position_pct": 1.5}}):
            self.assertEqual(load_market_sentiment(["2026-09-04", DAY], DAY, StrategyConfig()), 1.0)

    def test_invalid_pct_returns_none(self):
        with patch("dragon_quant.storage.assets_db.get_prep_snapshot",
                   return_value={"sentiment": {"position_pct": "not-a-number"}}):
            self.assertIsNone(load_market_sentiment(["2026-09-04", DAY], DAY, StrategyConfig()))


class TestEngineFiredGate(unittest.TestCase):
    def test_engine_surfaces_fired_gate(self):
        cfg = StrategyConfig(max_daily_loss_pct=2)
        state = AccountState(100000, realized_today=-3000, trade_date=DAY)  # trade_date=当日，避免日切换归零
        result = TradingEngine(cfg, state).step(MarketEvent(at(DAY, "09:30"), "open", {"600001": row()}, [CAND]))
        self.assertIn("fired_gate", result)
        self.assertIn("daily_loss_circuit_breaker", result["fired_gate"])


class TestWalkForward(unittest.TestCase):
    def test_insufficient_data(self):
        from dragon_quant.review_account.evaluation import walk_forward
        r = walk_forward(DAY, "2026-09-11", FakeData(), StrategyConfig())
        self.assertEqual(r["status"], "insufficient_data")

    def test_split_windows(self):
        from dragon_quant.review_account.evaluation import _split_windows
        days = [f"d{i}" for i in range(10)]
        segs = _split_windows(days, 4)
        self.assertEqual(len(segs), 4)
        self.assertEqual(segs[0], ("d0", "d1"))
        self.assertEqual(segs[-1], ("d6", "d9"))


class _FallbackDaily:
    """日K兜底测试数据：按 day → (open, high, low, close) 提供日K，无分时（走兜底路径）。"""

    def __init__(self, ohlc_by_day: dict):
        skip = set(ohlc_by_day)
        end = date.fromisoformat(max(ohlc_by_day))
        series = [KBar(at(d.isoformat(), "15:00"), 1_000_000, 10.0, 10.2, 9.8, 10.0, 0, 0, 12, 1e9)
                  for d in (end - timedelta(days=n) for n in range(50, 0, -1))
                  if d.weekday() < 5 and d.isoformat() not in skip]
        series += [KBar(at(day, "15:00"), 1_000_000, o, h, l, c, 0, 0, 12, 1e9)
                   for day, (o, h, l, c) in ohlc_by_day.items()]
        self._bars = sorted(series, key=lambda b: b.timestamp)

    def daily(self, code, start, refresh=False):
        return self._bars

    def try_intraday(self, code, day, until=None):
        return None

    def intraday(self, code, day, until=None):
        raise AssertionError("兜底用例不应读取分时")


class TestFirstDayStopRedesign(unittest.TestCase):
    """首日止损改造（D3 首日浮亏减半）——方案 §7 测试清单。

    方案：archived_md/review_account/2026-10-22-first-day-stoploss-redesign.md
    """

    @staticmethod
    def _on(**kw) -> StrategyConfig:
        return StrategyConfig(sell_rule_toggles={"first_day_stop": True}, **kw)

    # ── 1. 默认关：旧基线逐笔不变 ──
    def test_default_off_keeps_legacy_first_day_stop_loss(self):
        r = row()
        r["bar_low"] = 9.6
        self.assertEqual(evaluate_sell(position(), r, 1, StrategyConfig())[0]["code"],
                         "first_day_stop_loss")
        explicit_off = StrategyConfig(sell_rule_toggles={"first_day_stop": False})
        self.assertEqual(evaluate_sell(position(), r, 1, explicit_off)[0]["code"],
                         "first_day_stop_loss")
        self.assertFalse(StrategyConfig().first_day_stop_enabled())

    # ── 2. 开启后盘中破线 → 减半 ──
    def test_enabled_triggers_half_on_intraday_breach(self):
        cfg = self._on()
        for phase in ("open", "bar"):
            r = row(phase=phase)
            r["bar_low"] = 9.6
            self.assertEqual(evaluate_sell(position(), r, 1, cfg)[0]["code"],
                             "first_day_stop_half", phase)
        self.assertEqual(sell_quantity(position(), "first_day_stop_half", cfg), 500)

    # ── 3. 半仓记账：复用 apply_sell_fill，仓位保留 ──
    def test_half_booking_keeps_position_and_marks_leg(self):
        cfg = self._on()
        p = position()
        state = AccountState(0.0, [p])
        fill = apply_sell_fill(p, 9.6, "first_day_stop_half", cfg)
        self.assertTrue(fill["partial"])
        self.assertEqual(fill["qty"], 500)
        self.assertEqual(p.qty, 500)
        self.assertAlmostEqual(p.cost, 10005 / 2, places=9)
        self.assertLess(fill["pnl"], 0)
        self.assertTrue(p.took_profit_half)
        self.assertIn(p, state.positions)  # 未产生 ClosedPosition（仓位仍在账上）

    # ── 4. 同一信号只允许一条半仓腿 ──
    def test_half_leg_is_unique(self):
        cfg = self._on()
        p = position()
        apply_sell_fill(p, 9.6, "first_day_stop_half", cfg)
        r = row(phase="bar")
        r["bar_low"] = 9.0
        self.assertEqual(evaluate_sell(p, r, 1, cfg), [])
        # 次日封板减半同样被守卫挡住（与 next_day_limit_up_half 共用 took_profit_half）
        limit_up = row(phase="late", price=11)
        limit_up.update(limit_up=11, prev_close=10)
        self.assertEqual(evaluate_sell(p, limit_up, 1, cfg), [])

    # ── 5. 灾难线：剩余半仓整清；关闭时不触发 ──
    def test_disaster_line_clears_remainder_only_when_enabled(self):
        cfg = self._on(first_day_disaster_stop_pct=-8)
        p = position()
        apply_sell_fill(p, 9.6, "first_day_stop_half", cfg)
        r = row(phase="bar")
        r["bar_low"] = 9.1  # -9%
        sig = evaluate_sell(p, r, 1, cfg)[0]
        self.assertEqual(sig["code"], "first_day_disaster_stop")
        self.assertEqual(sell_quantity(p, sig["code"], cfg), p.qty)
        self.assertEqual(evaluate_sell(p, r, 1, self._on()), [])  # disaster=0 → 不触发

    # ── 6. T+2 回归：走后既有 -5% 硬止损 ──
    def test_t_plus_two_uses_regular_hard_stop(self):
        cfg = self._on()
        r = row()
        r["bar_low"] = 9.4
        self.assertEqual(evaluate_sell(position(), r, 2, cfg)[0]["code"], "hard_stop_loss")

    # ── 7. 成交价：兜底日按「开盘价（跳空）或止损线」 ──
    def test_daily_fallback_prices_for_new_reasons(self):
        cfg = self._on(first_day_disaster_stop_pct=-8)
        p = position()
        stop = 10 * (1 + cfg.first_day_stop_loss_pct / 100)  # 9.65
        self.assertAlmostEqual(
            daily_fallback_sell_price(p, {"open": 10.0, "bar_close": 9.4}, "first_day_stop_half", cfg),
            stop, places=9)
        self.assertEqual(
            daily_fallback_sell_price(p, {"open": 9.5, "bar_close": 9.4}, "first_day_stop_half", cfg),
            9.5)
        disaster = 10 * (1 + cfg.first_day_disaster_stop_pct / 100)  # 9.2
        self.assertAlmostEqual(
            daily_fallback_sell_price(p, {"open": 10.0, "bar_close": 9.0}, "first_day_disaster_stop", cfg),
            disaster, places=9)
        self.assertEqual(
            daily_fallback_sell_price(p, {"open": 9.1, "bar_close": 9.0}, "first_day_disaster_stop", cfg),
            9.1)

    # ── 8. 奇数手：半仓为 0 → 退化为全卖 ──
    def test_odd_lot_half_falls_back_to_full(self):
        cfg = self._on()
        p = position()
        p.qty, p.initial_qty = 100, 100
        self.assertEqual(sell_quantity(p, "first_day_stop_half", cfg), 100)

    # ── 9. 归因：新原因码计入止损桶 ──
    def test_drawdown_decomposition_counts_new_codes_as_stop(self):
        from dragon_quant.review_account.evaluation import drawdown_decomposition
        trades = [
            Trade("d", "c", "n", "SELL", 9.6, 500, 4800, 5, -50, 900, 500,
                  "first_day_stop_half", "x"),
            Trade("d", "c", "n", "SELL", 9.2, 500, 4600, 5, -30, 900, 0,
                  "first_day_disaster_stop", "x"),
        ]
        self.assertEqual(drawdown_decomposition({"trades": trades})["stop_loss"], -80)

    # ── 10. 消融：首日规则与硬止损都关 → 首日无止损 ──
    def test_both_off_means_no_first_day_stop(self):
        cfg = StrategyConfig(sell_rule_toggles={"hard_stop": False})
        r = row()
        r["bar_low"] = 9.0
        self.assertEqual(evaluate_sell(position(), r, 1, cfg), [])

    # ── 12. 灾难段优先于减半（不带半仓守卫）──
    def test_disaster_takes_priority_over_half(self):
        cfg = self._on(first_day_disaster_stop_pct=-8)
        p = position()  # took_profit_half=False
        r = row(phase="bar")
        r["bar_low"] = 9.1  # 单根 K -9%
        sig = evaluate_sell(p, r, 1, cfg)
        self.assertEqual([s["code"] for s in sig], ["first_day_disaster_stop"])
        self.assertEqual(sell_quantity(p, sig[0]["code"], cfg), 1000)  # 不得先卖一半

    # ── 13. 同帧短路：late 帧减半后 weak_close 不再产出第二条信号 ──
    def test_late_frame_short_circuits_weak_close(self):
        cfg = self._on()
        r = row(phase="late")
        r.update(open=10.0, close=9.6, bar_close=9.6, bar_low=9.6, prev_close=9.9, ma5=9.9)
        sig = evaluate_sell(position(), r, 1, cfg)
        self.assertEqual([s["code"] for s in sig], ["first_day_stop_half"])

    # ── 14. _half_qty 回归：next_day_limit_up_half 逐分不变 ──
    def test_half_qty_regression_for_limit_up_rule(self):
        cfg = self._on()
        self.assertEqual(sell_quantity(position(), "next_day_limit_up_half", cfg), 500)
        p = position()
        p.qty = 300
        self.assertEqual(sell_quantity(p, "next_day_limit_up_half", cfg), 100)
        self.assertEqual(sell_quantity(p, "first_day_stop_half", cfg), 100)
        self.assertEqual(sell_quantity(p, "hard_stop_loss", cfg), 300)

    # ── 15. 消融语义：首日规则不接管后续交易日 ──
    def test_first_day_rule_does_not_handle_later_days(self):
        cfg = StrategyConfig(sell_rule_toggles={"first_day_stop": True, "hard_stop": False})
        r = row()
        r["bar_low"] = 9.4
        self.assertEqual(evaluate_sell(position(), r, 2, cfg), [])

    # ── 16. 风险口径：默认关逐分不变，开启后 = (half+tail)/2 ──
    def test_risk_distance_switch(self):
        base = StrategyConfig(max_position_pct=100, risk_per_trade_pct=1)
        self.assertEqual(base.first_day_risk_distance_pct(), base.first_day_stop_loss_pct)
        on = self._on(max_position_pct=100, risk_per_trade_pct=1)
        self.assertEqual(on.first_day_risk_distance_pct(), (-3.5 + -5.0) / 2)
        with_disaster = self._on(max_position_pct=100, risk_per_trade_pct=1,
                                 first_day_disaster_stop_pct=-8)
        self.assertEqual(with_disaster.first_day_risk_distance_pct(), (-3.5 + -8.0) / 2)
        self.assertEqual(buy_quantity(100000, 100000, 10, base),
                         buy_quantity(100000, 100000, 10, StrategyConfig(
                             max_position_pct=100, risk_per_trade_pct=1)))
        self.assertLess(buy_quantity(100000, 100000, 10, on),
                        buy_quantity(100000, 100000, 10, base))

    def test_disaster_line_validation(self):
        for bad in ({"first_day_disaster_stop_pct": 0.5},
                    {"first_day_disaster_stop_pct": -101},
                    {"first_day_disaster_stop_pct": -3.0}):
            with self.assertRaises(ValueError, msg=str(bad)):
                StrategyConfig.from_dict({**bad, "sell_rule_toggles": {"first_day_stop": True}})
        # 只配灾难线而不开规则 → 静默失效，必须报错（配置期拦截）
        with self.assertRaises(ValueError):
            StrategyConfig.from_dict({"first_day_disaster_stop_pct": -8})
        for ok in (0, -3.5, -8):
            cfg = StrategyConfig.from_dict({"first_day_disaster_stop_pct": ok,
                                            "sell_rule_toggles": {"first_day_stop": True}})
            self.assertEqual(cfg.first_day_disaster_stop_pct, ok)
            self.assertTrue(cfg.first_day_stop_enabled())

    # ── 引擎级兜底日集成：每帧最多一腿、无跨日残留 ──
    def _drive(self, data, days, cfg, p):
        state = AccountState(0.0, [p])
        engine = TradingEngine(cfg, state)
        trades, closed = [], []
        for day in days:
            for event in historical_events(day, [], {p.code}, data):
                result = engine.step(event)
                trades.extend(result["trades"])
                closed.extend(result["positions"])
        return state, trades, closed

    def test_engine_default_off_keeps_single_full_stop(self):
        p = position()
        state, trades, closed = self._drive(
            _FallbackDaily({DAY: (10.0, 10.2, 9.5, 9.6)}), [DAY], StrategyConfig(), p)
        self.assertEqual([t.reason_code for t in trades], ["first_day_stop_loss"])
        self.assertEqual(trades[0].qty, 1000)
        self.assertFalse(p.took_profit_half)
        self.assertEqual(state.pending, [])
        self.assertEqual(len(closed), 1)

    def test_engine_daily_fallback_late_half_leaves_no_cross_day_pending(self):
        cfg = self._on()
        p = position()
        state, trades, closed = self._drive(
            _FallbackDaily({DAY: (10.0, 10.2, 9.5, 9.6)}), [DAY], cfg, p)
        # late 帧才减半（开盘未破线）→ 当日收盘价帧按止损线成交
        self.assertEqual([t.reason_code for t in trades], ["first_day_stop_half"])
        self.assertEqual(trades[0].qty, 500)
        self.assertAlmostEqual(trades[0].price, 9.65 * (1 - cfg.sell_slippage), places=3)
        self.assertEqual(state.pending, [])       # 无跨日 pending SELL
        self.assertEqual(p.qty, 500)              # 剩余半仓留到 T+2
        self.assertTrue(p.took_profit_half)
        self.assertEqual(closed, [])

    def test_engine_daily_fallback_open_gap_gives_two_legs_same_day(self):
        cfg = self._on()
        p = position()
        state, trades, closed = self._drive(
            _FallbackDaily({DAY: (9.5, 9.6, 9.3, 9.4)}), [DAY], cfg, p)
        self.assertEqual([t.reason_code for t in trades],
                         ["first_day_stop_half", "next_day_close_below_open"])
        self.assertEqual([t.qty for t in trades], [500, 500])
        self.assertAlmostEqual(trades[0].price, 9.5 * (1 - cfg.sell_slippage), places=3)
        self.assertEqual(sum(t.qty for t in trades), 1000)
        self.assertEqual(state.pending, [])
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].exit_reason_code, "next_day_close_below_open")

    def test_engine_t_plus_two_hard_stop_uses_t2_row(self):
        cfg = self._on()
        p = position()
        # T+1 盘中破线减半；T+2 开盘已低于 -5% 止损线 → 按 T+2 自己的开盘价成交
        state, trades, closed = self._drive(
            _FallbackDaily({DAY: (10.0, 10.2, 9.5, 9.6), "2026-09-08": (9.4, 9.5, 9.3, 9.35)}),
            [DAY, "2026-09-08"], cfg, p)
        self.assertEqual([t.reason_code for t in trades], ["first_day_stop_half", "hard_stop_loss"])
        self.assertEqual([t.qty for t in trades], [500, 500])
        self.assertAlmostEqual(trades[1].price, 9.4 * (1 - cfg.sell_slippage), places=3)
        self.assertEqual(sum(t.qty for t in trades), 1000)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].hold_days, 2)
        self.assertLess(closed[0].realized_return, 0)

    def test_rule_registry_consistency(self):
        """结构性守护：_SELL_RULES 顺序 == SELL_RULE_NAMES，默认关清单与 models 同一真值来源。"""
        from dragon_quant.review_account.models import DEFAULT_OFF_SELL_RULES, SELL_RULE_NAMES
        from dragon_quant.review_account.strategy import _DEFAULT_OFF_RULES, _SELL_RULES
        self.assertEqual(tuple(rule.name for rule in _SELL_RULES), SELL_RULE_NAMES)
        self.assertEqual(_DEFAULT_OFF_RULES, set(DEFAULT_OFF_SELL_RULES))
        self.assertLessEqual(_DEFAULT_OFF_RULES, set(SELL_RULE_NAMES))
        self.assertIn("first_day_stop", _DEFAULT_OFF_RULES)

    def test_engine_disaster_open_frame_clears_full_position(self):
        """灾难线优先且不带半仓守卫：open 帧直接穿灾难线 → 整仓一次成交。"""
        cfg = self._on(first_day_disaster_stop_pct=-8)
        p = position()
        state, trades, closed = self._drive(
            _FallbackDaily({DAY: (9.1, 9.2, 9.0, 9.05)}), [DAY], cfg, p)
        self.assertEqual([t.reason_code for t in trades], ["first_day_disaster_stop"])
        self.assertEqual(trades[0].qty, 1000)                 # 不得先卖一半
        self.assertAlmostEqual(trades[0].price, 9.1 * (1 - cfg.sell_slippage), places=3)
        self.assertFalse(p.took_profit_half)
        self.assertEqual(state.pending, [])
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].exit_reason_code, "first_day_disaster_stop")

    def test_engine_open_half_then_late_disaster_same_day(self):
        """兜底日同日两腿：open 帧减半（-5%，未到灾难线）+ late 帧灾难（-9%）清剩余。"""
        cfg = self._on(first_day_disaster_stop_pct=-8)
        p = position()
        state, trades, closed = self._drive(
            _FallbackDaily({DAY: (9.5, 9.6, 9.1, 9.3)}), [DAY], cfg, p)
        self.assertEqual([t.reason_code for t in trades],
                         ["first_day_stop_half", "first_day_disaster_stop"])
        self.assertEqual([t.qty for t in trades], [500, 500])
        self.assertAlmostEqual(trades[0].price, 9.5 * (1 - cfg.sell_slippage), places=3)
        self.assertAlmostEqual(trades[1].price, 9.2 * (1 - cfg.sell_slippage), places=3)
        self.assertEqual(sum(t.qty for t in trades), 1000)
        self.assertEqual(state.pending, [])
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].exit_reason_code, "first_day_disaster_stop")

    def test_engine_limit_down_lock_keeps_pending_and_reprices_on_fill_day(self):
        """T+1 跌停封死 → 不减半成交，pending 跨日；T+2 按 T+2 自己的行重算价与量。"""
        cfg = self._on()
        p = position()
        state = AccountState(0.0, [p])
        engine = TradingEngine(cfg, state)
        data = _FallbackDaily({DAY: (9.0, 9.0, 9.0, 9.0), "2026-09-08": (8.9, 9.0, 8.8, 8.85)})
        t1 = [engine.step(e) for e in historical_events(DAY, [], {p.code}, data)]
        # T+1 全天跌停封死：信号已产生但不可成交，仓位未动、pending 保留
        self.assertEqual([t for r in t1 for t in r["trades"]], [])
        self.assertEqual([o["side"] for o in state.pending], ["SELL"])
        self.assertEqual(p.qty, 1000)
        self.assertFalse(p.took_profit_half)
        t2 = [engine.step(e) for e in historical_events("2026-09-08", [], {p.code}, data)]
        trades = [t for r in t2 for t in r["trades"]]
        self.assertEqual([t.reason_code for t in trades], ["first_day_stop_half", "hard_stop_loss"])
        # 半仓腿按 T+2 开盘价（8.9）成交，不是 T+1 的止损线 9.65
        self.assertAlmostEqual(trades[0].price, 8.9 * (1 - cfg.sell_slippage), places=3)
        self.assertEqual(trades[0].qty, 500)
        self.assertEqual(state.pending, [])


if __name__ == "__main__":
    unittest.main()
