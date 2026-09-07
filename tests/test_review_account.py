"""tests for account-level review simulation."""

import unittest
from unittest.mock import MagicMock, patch

from dragon_quant.models.types import KBar
from dragon_quant.review_account.indicators import enrich_daily_klines
from dragon_quant.review_account.models import Position, StrategyConfig
from dragon_quant.review_account.simulator import AccountSimulator
from dragon_quant.review_account.strategy import (
    evaluate_buy,
    evaluate_divergence_buy,
    evaluate_sell,
    explain_buy_candidate,
)
from dragon_quant.utils.trading import build_trade_calendar


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


def _minute_bar(date_time: str, open_: float, close: float, high: float, low: float) -> KBar:
    import datetime as dt
    ts = int(dt.datetime.strptime(date_time, "%Y-%m-%d %H:%M").timestamp() * 1000)
    return KBar(
        timestamp=ts,
        volume=10_000,
        open=open_,
        high=high,
        low=low,
        close=close,
        chg=close - open_,
        pct=0.0,
        turnover=0.0,
        amount=1_000_000,
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


class TestAccountDataBoundaries(unittest.TestCase):
    def test_xueqiu_daily_kline_window_scales_with_requested_days(self):
        from dragon_quant.providers.xueqiu import XueqiuProvider

        captured = {}
        now_ms = 1_800_000_000_000
        payload = {"data": {"item": []}}
        with patch("dragon_quant.providers.xueqiu.time.time", return_value=now_ms / 1000), \
             patch("dragon_quant.providers.xueqiu._fetch", side_effect=lambda path, **_: captured.setdefault("path", path) and payload):
            XueqiuProvider().get_kline("000001", days=260, fq_type="normal")

        begin = int(captured["path"].split("begin=")[1])
        self.assertEqual(begin, now_ms - 520 * 86400 * 1000)

    def test_trade_calendar_excludes_unclosed_today(self):
        bars = [
            _kbar("2026-08-14", 10, 10, 10, 10),
            _kbar("2026-08-17", 10, 10, 10, 10),
        ]
        provider = MagicMock()
        provider.get_kline.return_value = bars
        with patch("dragon_quant.providers.xueqiu.XueqiuProvider", return_value=provider), \
             patch("dragon_quant.utils.trading.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = __import__("datetime").datetime(2026, 8, 17, 14, 30)
            mocked_datetime.fromtimestamp.side_effect = __import__("datetime").datetime.fromtimestamp
            mocked_datetime.strptime.side_effect = __import__("datetime").datetime.strptime
            days = build_trade_calendar("2026-08-14", "2026-08-17")

        self.assertEqual(days, {"2026-08-14"})


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

    def test_default_buy_amount_threshold_is_two_hundred_million(self):
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
            "amount": 210_000_000, "board_count": 3,
        }

        self.assertIsNotNone(evaluate_buy(cand, row, cfg))
        cand["amount"] = 199_000_000
        self.assertIsNone(evaluate_buy(cand, row, cfg))

    def test_buy_requires_min_turnover_floor(self):
        cfg = StrategyConfig(min_turnover=5.0)
        row = {
            "date": "2026-05-22", "open": 12.2, "high": 13.5, "low": 11.9,
            "close": 12.8, "pct": 4.0, "ma5": 12.0, "ma10": None, "ma20": None,
            "open_gap_pct": 2.0, "close_to_ma5_pct": 6.7, "low_touch_ma5": True,
            "prev_high": 12.5, "return_3d": 10.0, "return_5d": None,
            "max_drawdown_5d": -3.0, "avg_amplitude_5": 7.0,
            "is_one_word_board": False,
        }
        cand = {
            "rank": 1, "composite_score": 82.0, "turnover_rate": 2.0,
            "amount": 1_200_000_000, "board_count": 3,
        }

        signal = evaluate_buy(cand, row, cfg)

        self.assertIsNone(signal)

    def test_buy_open_decision_does_not_peek_close(self):
        cfg = StrategyConfig()
        row = {
            "date": "2026-07-06", "open": 55.8, "high": 59.58, "low": 54.23,
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

    def test_buy_requires_composite_score_floor(self):
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

        self.assertIsNone(signal)

    def test_buy_allows_non_rank_one_true_dragon(self):
        cfg = StrategyConfig()
        row = {
            "date": "2026-05-22", "open": 12.2, "high": 13.5, "low": 11.9,
            "close": 12.8, "pct": 4.0, "ma5": 12.0, "open_gap_pct": 2.0,
            "close_to_ma5_pct": 6.7, "prev_high": 12.5, "is_one_word_board": False,
        }
        cand = {
            "rank": 2, "composite_score": 99.0, "turnover_rate": 18.0,
            "amount": 1_200_000_000, "board_count": 3, "is_true_dragon": True,
        }

        signal = evaluate_buy(cand, row, cfg)

        self.assertIsNotNone(signal)
        self.assertIn("上日真龙池排名2", signal["reason_text"])

    def test_buy_blocks_non_true_dragon_candidate(self):
        cfg = StrategyConfig()
        row = {
            "date": "2026-05-22", "open": 12.2, "high": 13.5, "low": 11.9,
            "close": 12.8, "pct": 4.0, "ma5": 12.0, "open_gap_pct": 2.0,
            "close_to_ma5_pct": 6.7, "prev_high": 12.5, "is_one_word_board": False,
        }
        cand = {
            "rank": 1, "composite_score": 99.0, "turnover_rate": 18.0,
            "amount": 1_200_000_000, "board_count": 3, "is_true_dragon": False,
        }

        self.assertIsNone(evaluate_buy(cand, row, cfg))

    def test_high_open_gap_blocks_ma5_pullback_buy(self):
        cfg = StrategyConfig(max_open_gap=7.0)
        row = {
            "open": 12.0, "high": 12.5, "low": 11.8, "close": 12.2, "pct": 5.0,
            "ma5": 11.8, "open_gap_pct": 9.0, "close_to_ma5_pct": 3.4,
            "is_one_word_board": False,
        }
        cand = {"rank": 1, "composite_score": 90, "turnover_rate": 12, "amount": 800_000_000}

        signal = evaluate_buy(cand, row, cfg)

        self.assertIsNone(signal)

    def test_divergence_buy_hits_after_first_break_with_support(self):
        cfg = StrategyConfig()
        hist = [
            {"date": "2026-05-20", "is_one_word_board": True, "pct": 10.0, "volume": 2_000_000},
            {"date": "2026-05-21", "is_one_word_board": True, "pct": 10.0, "volume": 1_500_000},
        ]
        row = {
            "date": "2026-05-22", "open": 11.5, "high": 11.9, "low": 11.3,
            "close": 11.7, "pct": 6.4, "prev_close": 11.0, "open_gap_pct": 4.5,
            "is_one_word_board": False,
        }
        bars = [
            _minute_bar("2026-05-22 09:35", 11.5, 11.6, 11.7, 11.2),
            _minute_bar("2026-05-22 09:40", 11.6, 11.8, 11.9, 11.4),
        ]
        cand = {
            "code": "000001", "name": "样本", "rank": 1, "composite_score": 70.0,
            "turnover_rate": 3.0, "amount": 50_000_000, "is_true_dragon": True,
        }

        signal = evaluate_buy(cand, row, cfg, hist_rows=hist, intraday_bars=bars)

        self.assertIsNotNone(signal)
        self.assertEqual(signal["code"], "buy_divergence_first_break")
        self.assertEqual(signal["priority"], 400)
        self.assertAlmostEqual(signal["signal"]["execution_price"], 11.8)
        self.assertEqual(signal["signal"]["divergence_support"], "hold_prev_close")

    def test_divergence_buy_needs_min_boards(self):
        cfg = StrategyConfig(divergence_min_boards=2)
        hist = [
            {"date": "2026-05-21", "is_one_word_board": True, "pct": 10.0, "volume": 1_500_000},
        ]
        row = {
            "date": "2026-05-22", "open": 11.5, "high": 11.9, "low": 11.3,
            "close": 11.7, "prev_close": 11.0, "open_gap_pct": 4.5,
        }
        bars = [_minute_bar("2026-05-22 09:35", 11.5, 11.8, 11.9, 11.4)]
        cand = {"composite_score": 70.0, "is_true_dragon": True, "rank": 1}

        self.assertIsNone(
            evaluate_divergence_buy(cand, row, cfg, hist_rows=hist, intraday_bars=bars)
        )

    def test_divergence_buy_requires_shrinking_volume(self):
        cfg = StrategyConfig(divergence_min_boards=2)
        hist = [
            {"date": "2026-05-20", "is_one_word_board": True, "pct": 10.0, "volume": 1_000_000},
            {"date": "2026-05-21", "is_one_word_board": True, "pct": 10.0, "volume": 2_000_000},
        ]
        row = {
            "date": "2026-05-22", "open": 11.5, "high": 11.9, "low": 11.3,
            "close": 11.7, "prev_close": 11.0, "open_gap_pct": 4.5,
        }
        bars = [_minute_bar("2026-05-22 09:35", 11.5, 11.8, 11.9, 11.4)]
        cand = {"composite_score": 70.0, "is_true_dragon": True, "rank": 1}

        self.assertIsNone(
            evaluate_divergence_buy(cand, row, cfg, hist_rows=hist, intraday_bars=bars)
        )

    def test_divergence_buy_needs_actual_break(self):
        cfg = StrategyConfig(divergence_min_boards=2)
        hist = [
            {"date": "2026-05-20", "is_one_word_board": True, "pct": 10.0, "volume": 2_000_000},
            {"date": "2026-05-21", "is_one_word_board": True, "pct": 10.0, "volume": 1_500_000},
        ]
        # 开盘仍贴近涨停价（12.1×0.998=12.08），未断板
        row = {
            "date": "2026-05-22", "open": 12.1, "high": 12.1, "low": 12.1,
            "close": 12.1, "prev_close": 11.0, "open_gap_pct": 10.0,
        }
        bars = [_minute_bar("2026-05-22 09:35", 12.1, 12.1, 12.1, 12.1)]
        cand = {"composite_score": 70.0, "is_true_dragon": True, "rank": 1}

        self.assertIsNone(
            evaluate_divergence_buy(cand, row, cfg, hist_rows=hist, intraday_bars=bars)
        )

    def test_divergence_buy_rejects_when_support_breaks_prev_close(self):
        cfg = StrategyConfig(divergence_min_boards=2)
        hist = [
            {"date": "2026-05-20", "is_one_word_board": True, "pct": 10.0, "volume": 2_000_000},
            {"date": "2026-05-21", "is_one_word_board": True, "pct": 10.0, "volume": 1_500_000},
        ]
        row = {
            "date": "2026-05-22", "open": 11.5, "high": 11.9, "low": 10.5,
            "close": 10.8, "prev_close": 11.0, "open_gap_pct": 4.5,
        }
        # 窗口内最低 10.8 < 昨收 11.0，破位无承接
        bars = [
            _minute_bar("2026-05-22 09:35", 11.5, 11.0, 11.6, 10.8),
            _minute_bar("2026-05-22 09:40", 11.0, 10.9, 11.1, 10.85),
        ]
        cand = {"composite_score": 70.0, "is_true_dragon": True, "rank": 1}

        self.assertIsNone(
            evaluate_divergence_buy(cand, row, cfg, hist_rows=hist, intraday_bars=bars)
        )

    def test_divergence_buy_skips_when_5min_missing(self):
        cfg = StrategyConfig(divergence_min_boards=2)
        hist = [
            {"date": "2026-05-20", "is_one_word_board": True, "pct": 10.0, "volume": 2_000_000},
            {"date": "2026-05-21", "is_one_word_board": True, "pct": 10.0, "volume": 1_500_000},
        ]
        row = {
            "date": "2026-05-22", "open": 11.5, "high": 11.9, "low": 11.3,
            "close": 11.7, "prev_close": 11.0, "open_gap_pct": 4.5,
        }
        cand = {"composite_score": 70.0, "is_true_dragon": True, "rank": 1}

        self.assertIsNone(
            evaluate_divergence_buy(cand, row, cfg, hist_rows=hist, intraday_bars=[])
        )

    def test_divergence_buy_reseals_at_limit_up_price(self):
        cfg = StrategyConfig(divergence_min_boards=2)
        hist = [
            {"date": "2026-05-20", "is_one_word_board": True, "pct": 10.0, "volume": 2_000_000},
            {"date": "2026-05-21", "is_one_word_board": True, "pct": 10.0, "volume": 1_500_000},
        ]
        row = {
            "date": "2026-05-22", "open": 11.5, "high": 12.1, "low": 11.3,
            "close": 12.1, "prev_close": 11.0, "open_gap_pct": 4.5,
        }
        # 窗口内触及涨停 12.1，视为回封
        bars = [_minute_bar("2026-05-22 09:35", 11.5, 12.1, 12.1, 11.4)]
        cand = {"composite_score": 70.0, "is_true_dragon": True, "rank": 1}

        signal = evaluate_divergence_buy(cand, row, cfg, hist_rows=hist, intraday_bars=bars)

        self.assertIsNotNone(signal)
        self.assertEqual(signal["signal"]["divergence_support"], "limit_up_reseal")
        self.assertAlmostEqual(signal["signal"]["execution_price"], 12.1)

    def test_divergence_buy_outranks_ma5_pullback(self):
        cfg = StrategyConfig()
        hist = [
            {"date": "2026-05-20", "is_one_word_board": True, "pct": 10.0, "volume": 2_000_000},
            {"date": "2026-05-21", "is_one_word_board": True, "pct": 10.0, "volume": 1_500_000},
        ]
        # 该 row 同时满足贴近 MA5（open<=ma5*1.03）与分歧断板承接
        row = {
            "date": "2026-05-22", "open": 11.5, "high": 11.9, "low": 11.3,
            "close": 11.7, "pct": 6.4, "ma5": 11.4, "prev_close": 11.0,
            "open_gap_pct": 4.5, "close_to_ma5_pct": 2.6, "is_one_word_board": False,
        }
        bars = [
            _minute_bar("2026-05-22 09:35", 11.5, 11.6, 11.7, 11.2),
            _minute_bar("2026-05-22 09:40", 11.6, 11.8, 11.9, 11.4),
        ]
        cand = {
            "code": "000001", "rank": 1, "composite_score": 70.0,
            "turnover_rate": 12.0, "amount": 800_000_000, "is_true_dragon": True,
        }

        signal = evaluate_buy(cand, row, cfg, hist_rows=hist, intraday_bars=bars)

        self.assertEqual(signal["code"], "buy_divergence_first_break")
        self.assertEqual(signal["priority"], 400)

    def test_explain_buy_candidate_reports_pattern_reject_reason(self):
        cfg = StrategyConfig(max_open_gap=7.0)
        row = {
            "date": "2026-05-22", "open": 12.8, "high": 13.1, "low": 12.5,
            "close": 12.9, "pct": 5.0, "ma5": 11.8, "open_gap_pct": 8.0,
            "close_to_ma5_pct": 3.4, "is_one_word_board": False,
            "prev_high": 13.2,
        }
        cand = {
            "code": "000001", "name": "样本", "rank": 1,
            "composite_score": 90, "turnover_rate": 12,
            "amount": 800_000_000, "is_true_dragon": True,
        }

        explain = explain_buy_candidate(cand, row, cfg)

        self.assertFalse(explain["passed"])
        self.assertEqual(explain["reason_code"], "no_buy_pattern")
        self.assertIn("未触发回踩MA5或弱转强买点", explain["reason_text"])

    def test_hard_stop_has_priority_over_cost_line_protection(self):
        cfg = StrategyConfig(stop_loss_pct=-5.0, take_profit_pct=10.0, breakeven_activate_pct=6.0)
        pos = Position(
            code="000001", name="样本", qty=100, entry_date="2026-05-20",
            entry_price=10.0, cost=1000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
            highest_return=6.5,
        )
        row = {
            "date": "2026-05-21", "open": 10.0, "high": 12.0,
            "low": 9.4, "close": 11.5, "ma5": 10.0,
        }

        signals = evaluate_sell(pos, row, 2, cfg)

        self.assertEqual(signals[0]["code"], "hard_stop_loss")

    def test_sell_profit_back_to_cost_requires_prior_profit_activation(self):
        cfg = StrategyConfig(breakeven_activate_pct=6.0)
        pos = Position(
            code="000001", name="样本", qty=100, entry_date="2026-05-20",
            entry_price=10.0, cost=1000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
            highest_return=0.0,
        )
        row = {
            "date": "2026-05-22", "open": 10.0, "high": 10.3,
            "low": 9.98, "close": 10.1, "ma5": 9.8,
        }

        signals = evaluate_sell(pos, row, 2, cfg)

        self.assertEqual(signals, [])

    def test_sell_profit_back_to_cost_take_profit_after_activation(self):
        cfg = StrategyConfig(breakeven_activate_pct=6.0)
        pos = Position(
            code="000001", name="样本", qty=100, entry_date="2026-05-20",
            entry_price=10.0, cost=1000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
            highest_return=6.2,
        )
        row = {
            "date": "2026-05-22", "open": 10.2, "high": 10.4,
            "low": 9.98, "close": 10.1, "ma5": 9.8,
        }

        signals = evaluate_sell(pos, row, 2, cfg)

        self.assertEqual(signals[0]["code"], "profit_back_to_cost_take_profit")
        self.assertGreater(signals[0]["signal"]["break_even_price"], pos.entry_price)
        self.assertEqual(
            signals[0]["signal"]["execution_price"],
            signals[0]["signal"]["break_even_price"],
        )

    def test_same_day_profit_and_retrace_requires_ordered_intraday_bars(self):
        cfg = StrategyConfig(breakeven_activate_pct=6.0)
        pos = Position(
            code="000001", name="样本", qty=100, entry_date="2026-05-20",
            entry_price=10.0, cost=1000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
        )
        row = {
            "date": "2026-05-22", "open": 10.0, "high": 10.8,
            "low": 9.95, "close": 10.2, "ma5": 9.8,
        }

        no_intraday = evaluate_sell(pos, row, 2, cfg)
        self.assertEqual(no_intraday, [])

        pos = Position(
            code="000001", name="样本", qty=100, entry_date="2026-05-20",
            entry_price=10.0, cost=1000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
        )
        bars = [
            _minute_bar("2026-05-22 09:35", 10.0, 10.7, 10.8, 10.0),
            _minute_bar("2026-05-22 09:40", 10.7, 10.0, 10.7, 9.95),
        ]
        signals = evaluate_sell(pos, row, 2, cfg, intraday_bars=bars)

        self.assertEqual(signals[0]["code"], "profit_back_to_cost_take_profit")

    def test_next_day_limit_up_sells_half_position(self):
        cfg = StrategyConfig(take_profit_pct=12.0)
        pos = Position(
            code="000001", name="样本", qty=800, entry_date="2026-05-20",
            entry_price=10.0, cost=8000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
        )
        row = {
            "date": "2026-05-21", "open": 10.5, "high": 11.0,
            "low": 10.2, "close": 11.0, "ma5": 9.8,
            "is_limit_up_close": True,
        }

        signals = evaluate_sell(pos, row, 1, cfg)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["code"], "next_day_limit_up_half")

    def test_next_day_limit_up_then_weak_clear_returns_two_signals(self):
        cfg = StrategyConfig()
        pos = Position(
            code="000001", name="样本", qty=800, entry_date="2026-05-20",
            entry_price=10.0, cost=8000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
        )
        row = {
            "date": "2026-05-21", "open": 10.5, "high": 11.0,
            "low": 10.2, "close": 11.0, "ma5": 11.2,
            "is_limit_up_close": True,
        }

        signals = evaluate_sell(pos, row, 1, cfg)

        self.assertEqual([s["code"] for s in signals], [
            "next_day_limit_up_half",
            "next_day_limit_up_clear",
        ])

    def test_next_day_close_below_open_clears_position(self):
        cfg = StrategyConfig()
        pos = Position(
            code="000001", name="样本", qty=400, entry_date="2026-05-20",
            entry_price=10.0, cost=4000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
        )
        row = {
            "date": "2026-05-21", "open": 10.8, "high": 10.9,
            "low": 10.2, "close": 10.4, "ma5": 9.8,
        }

        signals = evaluate_sell(pos, row, 1, cfg)

        self.assertEqual(signals[0]["code"], "next_day_close_below_open")

    def test_close_below_ma5_stops_out(self):
        cfg = StrategyConfig()
        pos = Position(
            code="000001", name="样本", qty=400, entry_date="2026-05-20",
            entry_price=10.0, cost=4000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
        )
        row = {
            "date": "2026-05-22", "open": 10.0, "high": 10.4,
            "low": 10.05, "close": 10.1, "ma5": 10.3,
        }

        signals = evaluate_sell(pos, row, 2, cfg)

        self.assertEqual(signals[0]["code"], "break_intraday_ma_stop")

    def test_close_above_ma5_no_longer_takes_profit(self):
        cfg = StrategyConfig()
        pos = Position(
            code="000001", name="样本", qty=400, entry_date="2026-05-20",
            entry_price=10.0, cost=4000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
        )
        row = {
            "date": "2026-05-22", "open": 10.2, "high": 10.8,
            "low": 10.1, "close": 10.7, "ma5": 10.3,
        }

        signals = evaluate_sell(pos, row, 2, cfg)

        self.assertEqual(signals, [])

    def test_volume_spike_takes_profit_only_when_not_limit_up(self):
        cfg = StrategyConfig(volume_spike_pct=30.0)
        pos = Position(
            code="000001", name="样本", qty=400, entry_date="2026-05-20",
            entry_price=10.0, cost=4000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
        )
        row = {
            "date": "2026-05-22", "open": 10.2, "high": 10.4,
            "low": 10.1, "close": 10.2, "ma5": 10.2,
            "volume": 1300, "prev_volume": 1000,
        }

        signals = evaluate_sell(pos, row, 2, cfg)

        self.assertEqual(signals[0]["code"], "volume_spike_take_profit")

        row["is_limit_up_close"] = True
        self.assertEqual(evaluate_sell(pos, row, 2, cfg), [])

    def test_high_open_7pct_without_limit_up_in_5m_clears(self):
        cfg = StrategyConfig()
        pos = Position(
            code="000001", name="样本", qty=400, entry_date="2026-05-20",
            entry_price=10.0, cost=4000.0, entry_reason_code="buy",
            entry_reason_text="buy",
        )
        row = {
            "date": "2026-05-22", "open": 10.8, "high": 10.95,
            "low": 10.4, "close": 10.6, "ma5": 10.3,
            "prev_close": 10.0, "open_gap_pct": 8.0,
        }
        bars = [_minute_bar("2026-05-22 09:30", 10.8, 10.72, 10.88, 10.7)]

        signals = evaluate_sell(pos, row, 2, cfg, intraday_bars=bars)

        self.assertEqual(signals[0]["code"], "high_open_7pct_no_limit_5m_clear")
        self.assertEqual(signals[0]["signal"]["execution_price"], 10.72)

    def test_high_open_5pct_without_limit_up_in_30m_clears(self):
        cfg = StrategyConfig()
        pos = Position(
            code="000001", name="样本", qty=400, entry_date="2026-05-20",
            entry_price=10.0, cost=4000.0, entry_reason_code="buy",
            entry_reason_text="buy",
        )
        row = {
            "date": "2026-05-22", "open": 10.55, "high": 10.96,
            "low": 10.4, "close": 10.7, "ma5": 10.3,
            "prev_close": 10.0, "open_gap_pct": 5.5,
        }
        bars = [
            _minute_bar(f"2026-05-22 09:{30 + i * 5:02d}", 10.55, 10.6 + i * 0.01, 10.8, 10.5)
            for i in range(6)
        ]

        signals = evaluate_sell(pos, row, 2, cfg, intraday_bars=bars)

        self.assertEqual(signals[0]["code"], "high_open_5pct_no_limit_30m_clear")
        self.assertEqual(signals[0]["signal"]["execution_price"], bars[-1].close)

    def test_high_open_window_holds_when_limit_up_touched(self):
        cfg = StrategyConfig()
        pos = Position(
            code="000001", name="样本", qty=400, entry_date="2026-05-20",
            entry_price=10.0, cost=4000.0, entry_reason_code="buy",
            entry_reason_text="buy",
        )
        row = {
            "date": "2026-05-22", "open": 10.8, "high": 11.0,
            "low": 10.4, "close": 10.8, "ma5": 10.3,
            "prev_close": 10.0, "open_gap_pct": 8.0,
        }
        bars = [_minute_bar("2026-05-22 09:30", 10.8, 11.0, 11.0, 10.7)]

        signals = evaluate_sell(pos, row, 2, cfg, intraday_bars=bars)

        self.assertEqual(signals, [])

    def test_no_max_hold_days_forced_exit(self):
        cfg = StrategyConfig(max_hold_days=5)
        pos = Position(
            code="000001", name="样本", qty=400, entry_date="2026-05-20",
            entry_price=10.0, cost=4000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
        )
        row = {
            "date": "2026-05-29", "open": 10.6, "high": 10.8,
            "low": 10.3, "close": 10.6, "ma5": 10.6,
        }

        signals = evaluate_sell(pos, row, 6, cfg)

        self.assertEqual(signals, [])


class TestAccountSellOptimizations(unittest.TestCase):
    """卖出策略优化：首日紧止损 / 弱势容忍带 / 移动止盈。"""

    def _pos(self, **kw) -> Position:
        base = dict(
            code="000001", name="样本", qty=400, entry_date="2026-05-20",
            entry_price=10.0, cost=4000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
        )
        base.update(kw)
        return Position(**base)

    def test_first_day_uses_tighter_stop_loss(self):
        cfg = StrategyConfig(stop_loss_pct=-5.0, first_day_stop_loss_pct=-3.5)
        pos = self._pos()
        # 最低 -4%，触及首日 -3.5% 但未及常规 -5%
        row = {
            "date": "2026-05-21", "open": 10.0, "high": 10.1,
            "low": 9.6, "close": 9.7, "ma5": 9.5,
        }

        signals = evaluate_sell(pos, row, 1, cfg)

        self.assertEqual(signals[0]["code"], "first_day_stop_loss")
        self.assertEqual(signals[0]["signal"]["applied_stop_pct"], -3.5)

    def test_regular_day_keeps_wider_stop_loss(self):
        cfg = StrategyConfig(stop_loss_pct=-5.0, first_day_stop_loss_pct=-3.5)
        pos = self._pos()
        # 最低 -4%，第2个可卖日不触发常规 -5%
        row = {
            "date": "2026-05-22", "open": 10.0, "high": 10.3,
            "low": 9.6, "close": 10.2, "ma5": 9.5,
        }

        signals = evaluate_sell(pos, row, 2, cfg)

        codes = [s["code"] for s in signals]
        self.assertNotIn("hard_stop_loss", codes)
        self.assertNotIn("first_day_stop_loss", codes)

    def test_weak_close_within_tolerance_above_ma5_holds(self):
        cfg = StrategyConfig(weak_close_tolerance_pct=1.0)
        pos = self._pos()
        # 收盘较开盘仅低 0.5%，仍站上 MA5 与昨收 → 洗盘，继续持有
        row = {
            "date": "2026-05-22", "open": 10.0, "high": 10.2,
            "low": 9.9, "close": 9.95, "ma5": 9.8, "prev_close": 9.9,
        }

        signals = evaluate_sell(pos, row, 2, cfg)

        self.assertEqual(signals, [])

    def test_weak_close_beyond_tolerance_clears(self):
        cfg = StrategyConfig(weak_close_tolerance_pct=1.0)
        pos = self._pos()
        # 收盘较开盘低 2% > 容忍带 → 清仓
        row = {
            "date": "2026-05-22", "open": 10.0, "high": 10.2,
            "low": 9.7, "close": 9.8, "ma5": 9.5, "prev_close": 9.6,
        }

        signals = evaluate_sell(pos, row, 2, cfg)

        self.assertEqual(signals[0]["code"], "close_below_open_stop")

    def test_weak_close_within_tolerance_but_below_support_clears(self):
        cfg = StrategyConfig(weak_close_tolerance_pct=1.0)
        pos = self._pos()
        # 收盘较开盘仅低 0.5%，但同时跌破 MA5 → 支撑失守，清仓
        row = {
            "date": "2026-05-22", "open": 10.0, "high": 10.2,
            "low": 9.9, "close": 9.95, "ma5": 10.1, "prev_close": 9.9,
        }

        signals = evaluate_sell(pos, row, 2, cfg)

        self.assertEqual(signals[0]["code"], "close_below_open_stop")

    def test_first_day_weak_close_uses_tolerance_band(self):
        cfg = StrategyConfig(weak_close_tolerance_pct=1.0)
        pos = self._pos()
        # 买入次日：收盘较开盘仅低 0.5% 且站上 MA5/昨收 → 不清仓（T+1 首个可卖日）
        row = {
            "date": "2026-05-21", "open": 10.0, "high": 10.2,
            "low": 9.9, "close": 9.95, "ma5": 9.8, "prev_close": 9.9,
        }

        signals = evaluate_sell(pos, row, 1, cfg)

        self.assertEqual(signals, [])

    def test_trailing_take_profit_on_retrace(self):
        cfg = StrategyConfig(trailing_activate_pct=8.0, trailing_drawdown_pct=3.5)
        pos = self._pos(highest_return=9.0, highest_price=10.9)
        # 上一日峰值 9% 已达标；今日收盘自最高价 10.9 回撤约 4.6% (>3.5%)
        row = {
            "date": "2026-05-25", "open": 10.8, "high": 10.85,
            "low": 10.3, "close": 10.4, "ma5": 10.2,
        }

        signals = evaluate_sell(pos, row, 4, cfg)

        self.assertEqual(signals[0]["code"], "trailing_take_profit")

    def test_trailing_take_profit_on_ma5_break(self):
        cfg = StrategyConfig(trailing_activate_pct=8.0, trailing_drawdown_pct=3.5)
        pos = self._pos(highest_return=9.0, highest_price=10.9)
        # 回撤不足 3.5%，但收盘跌破 MA5 → 移动止盈
        row = {
            "date": "2026-05-25", "open": 10.8, "high": 10.88,
            "low": 10.6, "close": 10.7, "ma5": 10.8,
        }

        signals = evaluate_sell(pos, row, 4, cfg)

        self.assertEqual(signals[0]["code"], "trailing_take_profit")

    def test_breakeven_protection_not_preempted_below_trailing_activate(self):
        cfg = StrategyConfig(
            breakeven_activate_pct=6.0, trailing_activate_pct=8.0,
            trailing_drawdown_pct=3.5,
        )
        pos = self._pos(highest_return=6.5, highest_price=10.65)
        # 峰值 6.5% ∈ [6,8)：移动止盈不接管，回落成本线仍走保本保护
        row = {
            "date": "2026-05-25", "open": 10.2, "high": 10.3,
            "low": 9.98, "close": 10.0, "ma5": 9.9,
        }

        signals = evaluate_sell(pos, row, 4, cfg)

        self.assertEqual(signals[0]["code"], "profit_back_to_cost_take_profit")


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
            _kbar("2026-05-15", 10, 10, 10.2, 9.8, amount=100_000_000),
            _kbar("2026-05-18", 10, 10, 10.2, 9.8, amount=100_000_000),
            _kbar("2026-05-19", 10, 11, 11.2, 9.9, amount=100_000_000),
            _kbar("2026-05-20", 11, 12, 12.2, 10.8, amount=100_000_000),
            _kbar("2026-05-21", 11.5, 11.5, 12.0, 10.8, amount=100_000_000),
            _kbar("2026-05-22", 11.2, 13.2, 13.4, 11.0, pct=4.0, amount=100_000_000),
        ]
        candidate = {
            "trade_date": "2026-05-21", "code": "000001", "name": "样本", "rank": 1,
            "composite_score": 80.0, "turnover_rate": 12.0,
            "amount": 100_000.0, "board_count": 1,
        }

        with patch("dragon_quant.review_account.simulator.build_trade_calendar",
                   return_value=["2026-05-21", "2026-05-22"]), \
             patch("dragon_quant.review_account.simulator.db.get_dragons_by_date",
                   return_value=[candidate]):
            result = AccountSimulator(cfg, provider=provider).run(
                "2026-05-21", "2026-05-22"
            )

        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0].side, "BUY")
        self.assertAlmostEqual(result["trades"][0].price, 11.2 * 1.002)
        self.assertAlmostEqual(result["trades"][0].realized_pnl, -result["trades"][0].fee)
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["snapshots"][-1].position_code, "000001")
        self.assertGreater(result["snapshots"][-1].market_value, 0)

    def test_buy_selection_prefers_better_rank_when_signal_ties(self):
        cfg = StrategyConfig(
            initial_cash=100000,
            min_amount=500_000_000,
            min_turnover=5,
        )
        provider = MagicMock()
        kline_by_code = {
            "000001": [
                _kbar("2026-05-15", 10, 10, 10.2, 9.8, amount=100_000_000),
                _kbar("2026-05-18", 10, 10, 10.2, 9.8, amount=100_000_000),
                _kbar("2026-05-19", 10, 10, 10.2, 9.8, amount=100_000_000),
                _kbar("2026-05-20", 10, 10, 10.2, 9.8, amount=100_000_000),
                _kbar("2026-05-21", 10.5, 10.5, 11.0, 10.0, amount=100_000_000),
                _kbar("2026-05-22", 10.3, 10.8, 11.2, 10.1, amount=100_000_000),
            ],
            "000002": [
                _kbar("2026-05-15", 20, 20, 20.2, 19.8, amount=100_000_000),
                _kbar("2026-05-18", 20, 20, 20.2, 19.8, amount=100_000_000),
                _kbar("2026-05-19", 20, 20, 20.2, 19.8, amount=100_000_000),
                _kbar("2026-05-20", 20, 20, 20.2, 19.8, amount=100_000_000),
                _kbar("2026-05-21", 21, 21, 21.5, 20.5, amount=100_000_000),
                _kbar("2026-05-22", 20.5, 21.4, 22.0, 20.2, amount=100_000_000),
            ],
        }
        provider.get_kline.side_effect = lambda code, days=260, fq_type="normal": kline_by_code[code]
        candidates = [
            {
                "trade_date": "2026-05-21", "code": "000002", "name": "高分低排名",
                "rank": 2, "composite_score": 99.0, "turnover_rate": 12.0,
                "amount": 100_000.0, "board_count": 1, "is_true_dragon": True,
            },
            {
                "trade_date": "2026-05-21", "code": "000001", "name": "低分高排名",
                "rank": 1, "composite_score": 70.0, "turnover_rate": 12.0,
                "amount": 100_000.0, "board_count": 1, "is_true_dragon": True,
            },
        ]

        with patch("dragon_quant.review_account.simulator.build_trade_calendar",
                   return_value=["2026-05-21", "2026-05-22"]), \
             patch("dragon_quant.review_account.simulator.db.get_dragons_by_date",
                   return_value=candidates):
            result = AccountSimulator(cfg, provider=provider).run(
                "2026-05-21", "2026-05-22"
            )

        self.assertEqual(result["trades"][0].code, "000001")
        self.assertIn("上日真龙池排名1", result["trades"][0].reason_text)

    def test_idle_event_explains_no_candidate_passed(self):
        cfg = StrategyConfig(
            initial_cash=100000,
            min_amount=500_000_000,
            min_turnover=5,
            max_open_gap=7.0,
        )
        provider = MagicMock()
        provider.get_kline.return_value = [
            _kbar("2026-05-15", 10, 10, 10.2, 9.8, amount=100_000_000),
            _kbar("2026-05-18", 10, 10, 10.2, 9.8, amount=100_000_000),
            _kbar("2026-05-19", 10, 10, 10.2, 9.8, amount=100_000_000),
            _kbar("2026-05-20", 10, 10, 10.2, 9.8, amount=100_000_000),
            _kbar("2026-05-21", 10, 10, 10.2, 9.8, amount=100_000_000),
            _kbar("2026-05-22", 11.0, 11.2, 11.5, 10.8, amount=100_000_000),
        ]
        candidate = {
            "trade_date": "2026-05-21", "code": "000001", "name": "样本", "rank": 1,
            "composite_score": 80.0, "turnover_rate": 12.0,
            "amount": 100_000.0, "board_count": 1, "is_true_dragon": True,
        }

        with patch("dragon_quant.review_account.simulator.build_trade_calendar",
                   return_value=["2026-05-21", "2026-05-22"]), \
             patch("dragon_quant.review_account.simulator.db.get_dragons_by_date",
                   return_value=[candidate]):
            result = AccountSimulator(cfg, provider=provider).run(
                "2026-05-21", "2026-05-22"
            )

        self.assertEqual(result["trades"], [])
        self.assertEqual(result["events"][-1].event_type, "IDLE")
        self.assertEqual(result["events"][-1].reason_code, "no_candidate_passed")
        self.assertIn("没有候选触发买入条件", result["events"][-1].detail)
        self.assertEqual(result["events"][-1].signal["details"][0]["reason_code"], "no_buy_pattern")

    def test_candidate_pool_unions_recent_lookback_days(self):
        """近 N 日票池并集：上一交易日池为空时，仍可纳入更早（窗口内）交易日的候选。"""
        cfg = StrategyConfig(initial_cash=100000, candidate_lookback_days=3)
        provider = MagicMock()
        provider.get_kline.return_value = [
            _kbar("2026-07-06", 10, 10, 10.2, 9.8, amount=100_000_000),
            _kbar("2026-07-07", 10, 10, 10.2, 9.8, amount=100_000_000),
            _kbar("2026-07-08", 10, 10, 10.2, 9.8, amount=100_000_000),
            _kbar("2026-07-09", 10.1, 10.5, 10.8, 10.0, amount=100_000_000),
        ]
        older_candidate = {
            "trade_date": "2026-07-07", "code": "000001", "name": "有研新材",
            "rank": 1, "composite_score": 80.0, "turnover_rate": 12.0,
            "amount": 300_000_000, "board_count": 1, "is_true_dragon": True,
        }

        with patch("dragon_quant.review_account.simulator.build_trade_calendar",
                   return_value=["2026-07-07", "2026-07-08", "2026-07-09"]), \
             patch("dragon_quant.review_account.simulator.db.get_dragons_by_date",
                   side_effect=lambda day, top_n=5, source="v2": [older_candidate] if day == "2026-07-07" else []):
            result = AccountSimulator(cfg, provider=provider).run(
                "2026-07-07", "2026-07-09"
            )

        # 07-09 的上一交易日 07-08 池为空，但 07-07 在 3 日窗口内仍被纳入，
        # 因此不是 empty_previous_pool，而是池内候选未触发买点。
        last_event = result["events"][-1]
        self.assertEqual(last_event.reason_code, "no_candidate_passed")
        self.assertEqual(
            last_event.signal["details"][0]["code"], "000001"
        )

    def test_simulator_executes_divergence_buy_end_to_end(self):
        cfg = StrategyConfig(initial_cash=100000)
        provider = MagicMock()
        # 连续两个一字板（05-20、05-21）后 05-22 开盘断板并盘中承接
        provider.get_kline.return_value = [
            _kbar("2026-05-19", 9.0, 9.0, 9.0, 9.0, pct=10.0),
            _kbar("2026-05-20", 9.9, 9.9, 9.9, 9.9, pct=10.0),
            _kbar("2026-05-21", 10.89, 10.89, 10.89, 10.89, pct=10.0),
            _kbar("2026-05-22", 11.4, 11.7, 11.9, 11.3, pct=7.4),
        ]
        provider.get_5min_kline_for.return_value = [
            _minute_bar("2026-05-22 09:35", 11.4, 11.6, 11.7, 11.2),
            _minute_bar("2026-05-22 09:40", 11.6, 11.8, 11.9, 11.4),
        ]
        candidate = {
            "trade_date": "2026-05-21", "code": "000001", "name": "分歧龙", "rank": 1,
            "composite_score": 70.0, "turnover_rate": 3.0,
            "amount": 50_000_000, "board_count": 3, "is_true_dragon": True,
        }

        with patch("dragon_quant.review_account.simulator.build_trade_calendar",
                   return_value=["2026-05-21", "2026-05-22"]), \
             patch("dragon_quant.review_account.simulator.db.get_dragons_by_date",
                   return_value=[candidate]):
            result = AccountSimulator(cfg, provider=provider).run(
                "2026-05-21", "2026-05-22"
            )

        buys = [t for t in result["trades"] if t.side == "BUY"]
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0].reason_code, "buy_divergence_first_break")
        # 成交价 = 承接窗口末根收盘 11.8 × (1 + buy_slippage)
        self.assertAlmostEqual(buys[0].price, 11.8 * (1 + cfg.buy_slippage), places=4)

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

    def test_snapshot_uses_last_close_when_current_day_kline_is_missing(self):
        cfg = StrategyConfig()
        provider = MagicMock()
        provider.get_kline.return_value = [
            _kbar("2026-05-20", 10, 10, 10.2, 9.8),
            _kbar("2026-05-21", 10, 12, 12.1, 9.9),
        ]
        sim = AccountSimulator(cfg, provider=provider)
        sim.position = Position(
            code="000001", name="样本", qty=100, entry_date="2026-05-20",
            entry_price=10.0, cost=1000.0, entry_reason_code="buy",
            entry_reason_text="buy",
        )
        sim.cash = 0

        snapshot = sim._snapshot("2026-05-22")

        self.assertEqual(snapshot.market_value, 1200.0)
        self.assertEqual(snapshot.total_equity, 1200.0)

    def test_hold_event_marks_missing_intraday_data_for_high_open_rule(self):
        cfg = StrategyConfig()
        sim = AccountSimulator(cfg, provider=MagicMock())
        pos = Position(
            code="000001", name="样本", qty=100, entry_date="2026-05-20",
            entry_price=10.0, cost=1000.0, entry_reason_code="buy",
            entry_reason_text="buy",
        )
        row = {
            "date": "2026-05-21", "open": 10.6, "high": 10.8,
            "low": 10.4, "close": 10.7, "ma5": 10.2, "open_gap_pct": 6.0,
        }

        event = sim._hold_event(pos, "2026-05-21", row, 1, intraday_bars=[])

        self.assertTrue(event.signal["high_open_intraday_missing"])
        self.assertIn("高开未封板规则未执行", event.detail)

    def test_sell_day_blocks_rebuy_until_next_day(self):
        cfg = StrategyConfig()
        sim = AccountSimulator(cfg, provider=MagicMock())
        sim.position = Position(
            code="000001", name="样本", qty=100, entry_date="2026-05-20",
            entry_price=10.0, cost=1000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
        )
        sim.cash = 10000.0
        rows = {
            "2026-05-21": {
                "date": "2026-05-21", "open": 10.0, "high": 10.2,
                "low": 9.0, "close": 9.3, "ma5": 9.8,
            },
            "2026-05-22": {
                "date": "2026-05-22", "open": 9.4, "high": 9.8,
                "low": 9.2, "close": 9.6, "ma5": 9.7,
            },
        }

        with patch.object(sim, "_row_for", side_effect=lambda code, day: rows[day]), \
             patch.object(sim, "_try_buy", return_value={
                 "reason_code": "no_candidate_passed",
                 "reason_text": "无买点",
                 "details": [],
             }) as buy:
            sim._process_day("2026-05-21")
            self.assertEqual(buy.call_count, 0)
            sim._process_day("2026-05-22")

        self.assertEqual(buy.call_count, 1)

    def test_next_day_limit_up_half_cash_can_buy_new_position_next_day(self):
        cfg = StrategyConfig(
            initial_cash=100000,
            min_amount=500_000_000,
            min_turnover=5,
            take_profit_pct=12.0,
            sell_slippage=0.0,
        )
        provider = MagicMock()
        kline_by_code = {
            "000001": [
                _kbar("2026-05-15", 10, 10, 10.2, 9.8, amount=100_000_000),
                _kbar("2026-05-18", 10, 10, 10.2, 9.8, amount=100_000_000),
                _kbar("2026-05-19", 10, 10, 10.2, 9.8, amount=100_000_000),
                _kbar("2026-05-20", 10, 10, 10.2, 9.8, amount=100_000_000),
            _kbar("2026-05-21", 10, 10, 10.2, 9.8, amount=100_000_000),
            _kbar("2026-05-22", 10.0, 11.0, 11.0, 10.0, pct=10.0, amount=100_000_000),
                _kbar("2026-05-25", 10.0, 11.0, 11.0, 10.0, pct=10.0, amount=100_000_000),
            ],
            "000002": [
                _kbar("2026-05-15", 20, 20, 20.2, 19.8, amount=100_000_000),
                _kbar("2026-05-18", 20, 20, 20.2, 19.8, amount=100_000_000),
                _kbar("2026-05-19", 20, 20, 20.2, 19.8, amount=100_000_000),
                _kbar("2026-05-20", 20, 20, 20.2, 19.8, amount=100_000_000),
                _kbar("2026-05-21", 20, 20, 20.2, 19.8, amount=100_000_000),
                _kbar("2026-05-22", 20.3, 20.5, 20.8, 20.1, amount=100_000_000),
                _kbar("2026-05-25", 20.2, 20.4, 20.8, 20.0, amount=100_000_000),
                _kbar("2026-05-26", 20.2, 20.4, 20.8, 20.0, amount=100_000_000),
            ],
        }
        provider.get_kline.side_effect = lambda code, days=260, fq_type="normal": kline_by_code[code]
        day_candidates = {
            "2026-05-21": [{
                "trade_date": "2026-05-21", "code": "000001", "name": "先买",
                "rank": 1, "composite_score": 80.0, "turnover_rate": 12.0,
                "amount": 100_000.0, "board_count": 1, "is_true_dragon": True,
            }],
            "2026-05-22": [{
                "trade_date": "2026-05-22", "code": "000002", "name": "后买",
                "rank": 1, "composite_score": 82.0, "turnover_rate": 12.0,
                "amount": 100_000.0, "board_count": 1, "is_true_dragon": True,
            }],
            "2026-05-25": [{
                "trade_date": "2026-05-25", "code": "000002", "name": "后买",
                "rank": 1, "composite_score": 82.0, "turnover_rate": 12.0,
                "amount": 100_000.0, "board_count": 1, "is_true_dragon": True,
            }],
        }

        with patch("dragon_quant.review_account.simulator.build_trade_calendar",
                   return_value=["2026-05-21", "2026-05-22", "2026-05-25", "2026-05-26"]), \
             patch("dragon_quant.review_account.simulator.db.get_dragons_by_date",
                   side_effect=lambda day, top_n=5, source="v2": day_candidates.get(day, [])):
            result = AccountSimulator(cfg, provider=provider).run(
                "2026-05-21", "2026-05-26"
            )

        self.assertEqual([t.side for t in result["trades"]], ["BUY", "SELL", "BUY"])
        self.assertEqual(result["trades"][2].trade_date, "2026-05-26")
        self.assertEqual(result["trades"][2].code, "000002")
        self.assertEqual(result["trades"][1].reason_code, "next_day_limit_up_half")
        self.assertEqual(result["snapshots"][-1].position_code, "MULTI")

    def test_hard_stop_sells_at_stop_price_not_low(self):
        cfg = StrategyConfig(stop_loss_pct=-5.0)
        sim = AccountSimulator(cfg, provider=MagicMock())
        sim.position = Position(
            code="000001", name="样本", qty=100, entry_date="2026-05-20",
            entry_price=10.0, cost=1000.0, entry_reason_code="buy",
            entry_reason_text="buy",
        )

        price = sim._sell_execution_price(
            sim.position, {"open": 9.8, "low": 9.1, "close": 9.3}, "hard_stop_loss"
        )

        self.assertEqual(price, 9.5)

    def test_next_day_limit_up_half_keeps_remaining_position(self):
        cfg = StrategyConfig(take_profit_pct=12.0, sell_slippage=0.0)
        sim = AccountSimulator(cfg, provider=MagicMock())
        sim.position = Position(
            code="000001", name="样本", qty=800, entry_date="2026-05-20",
            entry_price=10.0, cost=8000.0, entry_reason_code="buy",
            entry_reason_text="buy",
        )
        sim.cash = 1000.0

        sim._sell(
            sim.position, "2026-05-21", 11.2, "next_day_limit_up_half",
            "买入次日收盘涨停，按涨停价卖出半仓",
            {"intraday_mode": "daily_k_approx"},
        )

        self.assertIsNotNone(sim.position)
        self.assertEqual(sim.position.qty, 400)
        self.assertAlmostEqual(sim.position.cost, 4000.0)
        self.assertTrue(sim.position.took_profit_half)
        self.assertEqual(sim.trades[-1].qty, 400)
        self.assertEqual(sim.trades[-1].position_after, 400)
        self.assertAlmostEqual(
            sim.trades[-1].realized_pnl,
            sim.trades[-1].amount - sim.trades[-1].fee - 4000.0,
        )
        self.assertEqual(sim.closed_positions, [])

    def test_close_below_open_closes_remaining_after_half_sell(self):
        cfg = StrategyConfig(take_profit_pct=12.0, sell_slippage=0.0)
        sim = AccountSimulator(cfg, provider=MagicMock())
        sim.position = Position(
            code="000001", name="样本", qty=800, entry_date="2026-05-20",
            entry_price=10.0, cost=8000.0, entry_reason_code="buy",
            entry_reason_text="buy",
        )
        sim.cash = 1000.0
        with patch.object(sim, "_hold_days", return_value=1):
            sim._sell(
                sim.position, "2026-05-21", 11.2, "next_day_limit_up_half",
                "买入次日收盘涨停，按涨停价卖出半仓",
                {"intraday_mode": "daily_k_approx"},
            )
        row = {
            "date": "2026-05-22", "open": 10.9, "high": 11.0,
            "low": 10.2, "close": 10.4, "ma5": 10.5,
        }

        with patch.object(sim, "_row_for", return_value=row), \
             patch.object(sim, "_try_buy"), \
             patch.object(sim, "_hold_days", return_value=2):
            sim._process_day("2026-05-22")

        self.assertIsNone(sim.position)
        self.assertEqual(len(sim.trades), 2)
        self.assertEqual(sim.trades[-1].reason_code, "close_below_open_stop")
        self.assertEqual(sim.trades[-1].qty, 400)
        self.assertEqual(len(sim.closed_positions), 1)
        self.assertEqual(sim.closed_positions[0].qty, 800)
        self.assertGreater(sim.closed_positions[0].realized_return, 0)

    def test_sell_execution_price_for_new_reason_codes(self):
        cfg = StrategyConfig(first_day_stop_loss_pct=-3.5, sell_slippage=0.0)
        sim = AccountSimulator(cfg, provider=MagicMock())
        pos = Position(
            code="000001", name="样本", qty=400, entry_date="2026-05-20",
            entry_price=10.0, cost=4000.0, entry_reason_code="buy",
            entry_reason_text="buy", entry_day_low=9.8,
        )
        # 首日紧止损：开盘未破止损价，按 -3.5% 止损价成交
        first_day_row = {"open": 9.9, "high": 9.95, "low": 9.5, "close": 9.6,
                         "applied_stop_pct": -3.5}
        self.assertAlmostEqual(
            sim._sell_execution_price(pos, first_day_row, "first_day_stop_loss"),
            10.0 * (1 - 3.5 / 100),
        )
        # 移动止盈：按当日收盘价成交
        trailing_row = {"open": 10.8, "high": 10.85, "low": 10.3, "close": 10.4}
        self.assertEqual(
            sim._sell_execution_price(pos, trailing_row, "trailing_take_profit"),
            10.4,
        )


if __name__ == "__main__":
    unittest.main()
