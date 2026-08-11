"""tests for account-level review simulation."""

import unittest
from unittest.mock import MagicMock, patch

from dragon_quant.models.types import KBar
from dragon_quant.review_account.indicators import enrich_daily_klines
from dragon_quant.review_account.models import Position, StrategyConfig
from dragon_quant.review_account.simulator import AccountSimulator
from dragon_quant.review_account.strategy import evaluate_buy, evaluate_sell


def _kbar(date: str, open_: float, close: float, high: float, low: float,
          pct: float = 0.0, turnover: float = 10.0, amount: float = 1_000_000_000) -> KBar:
    import datetime as dt
    ts = int(dt.datetime.strptime(date, "%Y-%m-%d").timestamp() * 1000)
    return KBar(
        timestamp=ts,
        volume=1_000_000,
        open=open_,
        high=high,
        low=low,
        close=close,
        chg=close - open_,
        pct=pct,
        turnover=turnover,
        amount=amount,
    )


class TestAccountIndicators(unittest.TestCase):
    def test_enrich_daily_klines_computes_ma5_touch(self):
        rows = enrich_daily_klines([
            _kbar("2026-05-18", 10, 10, 10.2, 9.8),
            _kbar("2026-05-19", 10, 11, 11.2, 9.9),
            _kbar("2026-05-20", 11, 12, 12.2, 10.8),
            _kbar("2026-05-21", 12, 13, 13.2, 11.8),
            _kbar("2026-05-22", 13, 14, 14.2, 12.0),
        ])

        self.assertAlmostEqual(rows[-1]["ma5"], 12.0)
        self.assertTrue(rows[-1]["low_touch_ma5"])


class TestAccountStrategy(unittest.TestCase):
    def test_buy_ma5_pullback_signal(self):
        cfg = StrategyConfig()
        row = {
            "date": "2026-05-22", "open": 12.2, "high": 13.5, "low": 11.9,
            "close": 12.8, "pct": 4.0, "ma5": 12.0, "ma10": None, "ma20": None,
            "open_gap_pct": 2.0, "close_to_ma5_pct": 6.7, "low_touch_ma5": True,
            "prev_high": 12.5, "return_3d": 10.0, "return_5d": None,
            "max_drawdown_5d": -3.0, "avg_amplitude_5": 7.0,
            "is_one_word_board": False,
        }
        cand = {
            "rank": 1, "composite_score": 82.0, "turnover_rate": 18.0,
            "amount": 1_200_000_000, "board_count": 3,
        }

        signal = evaluate_buy(cand, row, cfg)

        self.assertIsNotNone(signal)
        self.assertEqual(signal["code"], "buy_open_ma5_pullback")
        self.assertIn("开盘贴近MA5", signal["reason_text"])

    def test_buy_amount_accepts_ten_thousand_yuan_unit(self):
        cfg = StrategyConfig(min_amount=500_000_000)
        row = {
            "date": "2026-05-22", "open": 12.2, "high": 13.5, "low": 11.9,
            "close": 12.8, "pct": 4.0, "ma5": 12.0, "ma10": None, "ma20": None,
            "open_gap_pct": 2.0, "close_to_ma5_pct": 6.7, "low_touch_ma5": True,
            "prev_high": 12.5, "return_3d": 10.0, "return_5d": None,
            "max_drawdown_5d": -3.0, "avg_amplitude_5": 7.0,
            "is_one_word_board": False,
        }
        cand = {
            "rank": 1, "composite_score": 82.0, "turnover_rate": 18.0,
            "amount": 74_080.0, "board_count": 3,
        }

        signal = evaluate_buy(cand, row, cfg)

        self.assertIsNotNone(signal)
        self.assertGreaterEqual(signal["signal"]["amount_yuan"], 740_800_000)

    def test_buy_open_decision_does_not_peek_close(self):
        cfg = StrategyConfig()
        row = {
            "date": "2026-07-06", "open": 56.93, "high": 59.58, "low": 54.23,
            "close": 54.57, "pct": -4.2, "ma5": 51.2, "ma10": 50.0, "ma20": 49.0,
            "open_gap_pct": -0.05, "close_to_ma5_pct": 6.58,
            "is_one_word_board": False,
        }
        prev_row = {"date": "2026-07-05", "close": 56.96, "high": 56.96, "ma5": 55.5}
        cand = {
            "trade_date": "2026-07-05", "rank": 1, "composite_score": 71.0,
            "turnover_rate": 6.17, "amount": 39575.0, "board_count": 1,
        }

        signal = evaluate_buy(cand, row, cfg, prev_row=prev_row)

        self.assertIsNotNone(signal)
        self.assertEqual(signal["signal"]["decision_timing"], "open")

    def test_buy_does_not_require_composite_score_floor(self):
        cfg = StrategyConfig(min_score=90)
        row = {
            "date": "2026-05-22", "open": 12.2, "high": 13.5, "low": 11.9,
            "close": 12.8, "pct": 4.0, "ma5": 12.0, "ma10": None, "ma20": None,
            "open_gap_pct": 2.0, "close_to_ma5_pct": 6.7, "low_touch_ma5": True,
            "prev_high": 12.5, "return_3d": 10.0, "return_5d": None,
            "max_drawdown_5d": -3.0, "avg_amplitude_5": 7.0,
            "is_one_word_board": False,
        }
        cand = {
            "trade_date": "2026-05-21", "rank": 1, "composite_score": 60.0,
            "turnover_rate": 18.0, "amount": 1_200_000_000, "board_count": 3,
        }

        signal = evaluate_buy(cand, row, cfg)

        self.assertIsNotNone(signal)
        self.assertEqual(signal["signal"]["candidate_trade_date"], "2026-05-21")

    def test_buy_requires_rank_one(self):
        cfg = StrategyConfig()
        row = {
            "date": "2026-05-22", "open": 12.2, "high": 13.5, "low": 11.9,
            "close": 12.8, "pct": 4.0, "ma5": 12.0, "open_gap_pct": 2.0,
            "close_to_ma5_pct": 6.7, "prev_high": 12.5, "is_one_word_board": False,
        }
        cand = {
            "rank": 2, "composite_score": 99.0, "turnover_rate": 18.0,
            "amount": 1_200_000_000, "board_count": 3,
        }

        self.assertIsNone(evaluate_buy(cand, row, cfg))

    def test_high_open_gap_blocks_buy(self):
        cfg = StrategyConfig(max_open_gap=7.0)
        row = {
            "open": 12.0, "high": 12.5, "low": 11.8, "close": 12.2, "pct": 5.0,
            "ma5": 11.8, "open_gap_pct": 9.0, "close_to_ma5_pct": 3.4,
            "is_one_word_board": False,
        }
        cand = {"rank": 1, "composite_score": 90, "turnover_rate": 12, "amount": 800_000_000}

        self.assertIsNone(evaluate_buy(cand, row, cfg))

    def test_sell_hard_stop_loss_has_priority(self):
        cfg = StrategyConfig(stop_loss_pct=-5.0, take_profit_pct=10.0)
        pos = Position(
            code="000001", name="样本", qty=100, entry_date="2026-05-20",
            entry_price=10.0, cost=1000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
        )
        row = {
            "date": "2026-05-21", "open": 10.0, "high": 12.0,
            "low": 9.4, "close": 11.5, "ma5": 10.0,
        }

        signal = evaluate_sell(pos, row, 1, cfg)

        self.assertEqual(signal["code"], "hard_stop_loss")


class TestAccountSimulator(unittest.TestCase):
    def test_period_end_keeps_open_position(self):
        cfg = StrategyConfig(
            initial_cash=100000,
            min_score=70,
            min_amount=500_000_000,
            min_turnover=5,
        )
        provider = MagicMock()
        provider.get_kline.return_value = [
            _kbar("2026-05-18", 10, 10, 10.2, 9.8, amount=100_000_000),
            _kbar("2026-05-19", 10, 11, 11.2, 9.9, amount=100_000_000),
            _kbar("2026-05-20", 11, 12, 12.2, 10.8, amount=100_000_000),
            _kbar("2026-05-21", 12, 13, 13.2, 11.8, amount=100_000_000),
            _kbar("2026-05-22", 12.8, 13.2, 13.4, 11.9, pct=4.0, amount=100_000_000),
        ]
        candidate = {
            "trade_date": "2026-05-21", "code": "000001", "name": "样本", "rank": 1,
            "composite_score": 80.0, "turnover_rate": 12.0,
            "amount": 100_000.0, "board_count": 1,
        }

        with patch("dragon_quant.review_account.simulator.db.list_dragon_trade_dates",
                   return_value=["2026-05-21", "2026-05-22"]), \
             patch("dragon_quant.review_account.simulator.db.get_dragons_by_date",
                   return_value=[candidate]):
            result = AccountSimulator(cfg, provider=provider).run(
                "2026-05-21", "2026-05-22"
            )

        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0].side, "BUY")
        self.assertAlmostEqual(result["trades"][0].price, 12.8 * 1.002)
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["snapshots"][-1].position_code, "000001")
        self.assertGreater(result["snapshots"][-1].market_value, 0)

    def test_t_plus_one_blocks_same_day_sell(self):
        cfg = StrategyConfig()
        sim = AccountSimulator(cfg, provider=MagicMock())
        sim.position = Position(
            code="000001", name="样本", qty=100, entry_date="2026-05-22",
            entry_price=10.0, cost=1000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
        )
        sim.cash = 0

        with patch.object(sim, "_row_for", return_value={
            "date": "2026-05-22", "open": 10.0, "high": 10.2,
            "low": 9.0, "close": 9.2, "ma5": 9.8,
        }), patch.object(sim, "_try_buy"):
            sim._process_day("2026-05-22")

        self.assertEqual(sim.trades, [])
        self.assertIsNotNone(sim.position)


if __name__ == "__main__":
    unittest.main()
