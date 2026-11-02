"""共享严格指标核 ``review.strict_metrics`` + 与 ``review.main`` parity（方案 §4）。

覆盖 ``is_valid_bar`` / ``window_metrics`` / ``evaluate_strict``，并断言抽取后
``review.main`` 的私有别名输出逐字段一致，确保 review CLI 数值零漂移。
"""

from __future__ import annotations

import unittest

from dragon_quant.review import main as review_main
from dragon_quant.review import strict_metrics


CALENDAR = {"2026-09-10", "2026-09-11", "2026-09-14",
            "2026-09-15", "2026-09-16", "2026-09-17"}


def _bar(day: str, *, open_px=10.0, high=10.5, low=9.5, close=10.2,
         volume=1000.0) -> dict:
    return {"date": day, "open": open_px, "high": high, "low": low,
            "close": close, "volume": volume}


def _klines() -> list[dict]:
    return [
        _bar("2026-09-10", high=10.2, low=9.8, close=10.0),
        _bar("2026-09-11", open_px=10.0, high=10.8, low=9.5, close=10.6),
        _bar("2026-09-14", open_px=10.6, high=11.0, low=9.8, close=10.5),
        _bar("2026-09-15", open_px=10.5, high=11.2, low=9.9, close=11.0),
        _bar("2026-09-16", open_px=11.0, high=12.0, low=10.5, close=11.8),
        _bar("2026-09-17", open_px=11.8, high=11.9, low=10.0, close=10.4),
    ]


class TestIsValidBar(unittest.TestCase):
    def test_accepts_normal_and_boundary_bars(self):
        self.assertTrue(strict_metrics.is_valid_bar(_bar("2026-09-11")))
        # 一字板有成交 → 合法。
        self.assertTrue(strict_metrics.is_valid_bar(
            _bar("2026-09-11", open_px=10.0, high=10.0, low=10.0,
                 close=10.0, volume=500.0)))
        # 缺 volume 仍是合法 bar（旧契约只按 OHLC 判定）。
        bar = _bar("2026-09-11")
        bar.pop("volume")
        self.assertTrue(strict_metrics.is_valid_bar(bar))

    def test_rejects_missing_or_invalid_ohlc(self):
        cases = [
            None,
            "not-a-bar",
            {"date": "2026-09-11"},
            _bar("2026-09-11", open_px=None),
            _bar("2026-09-11", low=0),
            _bar("2026-09-11", high=9.0, low=9.5),
            _bar("2026-09-11", open_px=11.0),
            _bar("2026-09-11", close=9.0),
        ]
        for bar in cases:
            self.assertFalse(strict_metrics.is_valid_bar(bar), bar)

    def test_rejects_zero_volume_flat_placeholder(self):
        self.assertFalse(strict_metrics.is_valid_bar(
            _bar("2026-09-11", open_px=10.0, high=10.0, low=10.0,
                 close=10.0, volume=0.0)))


class TestWindowMetrics(unittest.TestCase):
    def test_computes_peak_return_and_drawdown(self):
        metrics = strict_metrics.window_metrics(
            _klines(), CALENDAR, "2026-09-11", 10.0)
        self.assertIsNotNone(metrics)
        # 买入后第 2~5 个交易日 = 09-15..09-17（09-14 被 skip），峰值 12.0。
        self.assertEqual(metrics["max_return_5d"], 20.0)
        # 回撤窗口 = 买入日 09-11 → 峰值日 09-16，最低 9.5。
        self.assertEqual(metrics["max_drawdown_5d"], -5.0)
        self.assertEqual(metrics["max_return_hold_days"], 3)
        self.assertTrue(metrics["partial"])   # 只有 3 个未来日

    def test_returns_none_for_empty_window(self):
        self.assertIsNone(strict_metrics.window_metrics(
            _klines(), {"2026-09-10", "2026-09-11"}, "2026-09-11", 10.0))
        # 有日历日但完全没有对应 bar → None。
        self.assertIsNone(strict_metrics.window_metrics(
            [], CALENDAR, "2026-09-11", 10.0))


class TestEvaluateStrict(unittest.TestCase):
    def test_completed_partial_window(self):
        result = strict_metrics.evaluate_strict(
            _klines(), CALENDAR, "2026-09-10")
        self.assertEqual(result["review_status"], "completed")
        self.assertEqual(result["reason"], strict_metrics.PARTIAL_WINDOW_REASON)
        self.assertEqual(result["buy_date"], "2026-09-11")
        self.assertEqual(result["buy_price"], 10.0)
        self.assertEqual(result["max_return_5d"], 20.0)
        self.assertEqual(result["max_drawdown_5d"], -5.0)
        self.assertEqual(result["max_return_hold_days"], 3)

    def test_no_next_trading_day(self):
        result = strict_metrics.evaluate_strict(
            _klines(), {"2026-09-10"}, "2026-09-10")
        self.assertEqual(result["review_status"], "cannot_evaluate")
        self.assertEqual(result["reason"], strict_metrics.CANNOT_EVALUATE_REASON)
        self.assertIsNone(result["buy_date"])

    def test_missing_or_invalid_buy_bar(self):
        no_bar = [bar for bar in _klines() if bar["date"] != "2026-09-11"]
        for klines in (no_bar, [
                _bar("2026-09-11", open_px=None)]):
            result = strict_metrics.evaluate_strict(
                klines, CALENDAR, "2026-09-10")
            self.assertEqual(result["review_status"], "cannot_evaluate")
            self.assertEqual(result["reason"],
                             strict_metrics.CANNOT_EVALUATE_REASON)


class TestReviewMainParity(unittest.TestCase):
    def test_is_valid_bar_alias_matches(self):
        bars = [
            _bar("2026-09-11"),
            None,
            _bar("2026-09-11", low=0),
            _bar("2026-09-11", open_px=10.0, high=10.0, low=10.0,
                 close=10.0, volume=0.0),
        ]
        for bar in bars:
            self.assertEqual(review_main._is_valid_bar(bar),
                             strict_metrics.is_valid_bar(bar))

    def test_strict_review_matches_shared_core(self):
        for trade_date in ("2026-09-10", "2026-09-17"):
            self.assertEqual(
                review_main._strict_review(_klines(), CALENDAR, trade_date),
                strict_metrics.evaluate_strict(_klines(), CALENDAR, trade_date))


if __name__ == "__main__":
    unittest.main()
