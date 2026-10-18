"""择时与仓位改造的单元测试：否决/禁买、卖出规则开关、时间止损、分级止盈、龙回头、组合风控、验证基建。"""

import unittest
from unittest.mock import patch

from tests.test_review_account import CAND, DAY, FakeData, row, position

from dragon_quant.review_account.engine import TradingEngine
from dragon_quant.review_account.execution import buy_quantity
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

    def test_dragon_comeback_disabled_by_default(self):
        self.assertIsNone(self._buy(StrategyConfig(), _dragon_hist()))

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
        with patch("dragon_quant.review_account.simulator.db.get_dragons_by_date", return_value=[CAND]):
            r = plateau_scan(DAY, DAY, data, StrategyConfig(), "max_position_pct", [25, 50, 100])
        self.assertIn("grid", r)
        self.assertEqual(r["trial_count"], 3)

    def test_ablation_structure(self):
        from dragon_quant.review_account.evaluation import ablation
        data = FakeData()
        with patch("dragon_quant.review_account.simulator.db.get_dragons_by_date", return_value=[CAND]):
            r = ablation(DAY, DAY, data, StrategyConfig(), ["hard_stop", "trailing"])
        self.assertIn("baseline", r)
        self.assertEqual(set(r["marginal"]), {"hard_stop", "trailing"})


class TestLoadMarketSentiment(unittest.TestCase):
    def test_non_v2_returns_none(self):
        self.assertIsNone(load_market_sentiment(["2026-09-04", DAY], DAY, StrategyConfig(source="v1")))

    def test_missing_snapshot_returns_none(self):
        with patch("dragon_quant.storage.prep_db.get_prep_snapshot", return_value=None):
            self.assertIsNone(load_market_sentiment(["2026-09-04", DAY], DAY, StrategyConfig()))

    def test_hit_returns_clamped_pct(self):
        with patch("dragon_quant.storage.prep_db.get_prep_snapshot",
                   return_value={"sentiment": {"position_pct": 0.8}}):
            self.assertEqual(load_market_sentiment(["2026-09-04", DAY], DAY, StrategyConfig()), 0.8)
        with patch("dragon_quant.storage.prep_db.get_prep_snapshot",
                   return_value={"sentiment": {"position_pct": 1.5}}):
            self.assertEqual(load_market_sentiment(["2026-09-04", DAY], DAY, StrategyConfig()), 1.0)

    def test_invalid_pct_returns_none(self):
        with patch("dragon_quant.storage.prep_db.get_prep_snapshot",
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


if __name__ == "__main__":
    unittest.main()
