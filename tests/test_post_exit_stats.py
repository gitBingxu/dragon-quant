"""清仓后窗口指标与买点统计（纯函数）单测。"""

import math
import unittest

from dragon_quant.infra.models import KBar
from dragon_quant.review_account.evaluation import TOTAL_ROW_CODE, entry_reason_stats
from dragon_quant.review_account.indicators import is_tradable_daily_bar, post_exit_stats
from dragon_quant.review_account.market import at
from dragon_quant.review_account.models import BUY_SIGNAL_CODES, ClosedPosition
from dragon_quant.review_account.simulator import annotate_post_exit
from dragon_quant.review_account.strategy.main import BUY_CHECK_LABELS

EXIT = "2026-09-04"   # 周五
DAYS = ["2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]


def bar(day, high=10.5, low=9.5, volume=10000, amount=1_000_000):
    return KBar(at(day, "15:00"), volume, 10.0, high, low, 10.0, 0, 0, 1.0, amount)


class TestPostExitStats(unittest.TestCase):
    def test_exactly_five_bars(self):
        history = [bar(d, high=10 + i, low=9 - i) for i, d in enumerate(DAYS)]
        stats = post_exit_stats(history, EXIT, 10.0)
        self.assertEqual(stats["post_exit_window_days"], 5)
        self.assertAlmostEqual(stats["post_exit_max_return"], 40.0)      # 14 / 10
        self.assertAlmostEqual(stats["post_exit_max_drawdown"], -50.0)   # 5 / 10

    def test_more_than_five_uses_first_five_only(self):
        history = [bar(d) for d in DAYS] + [bar("2026-09-14", high=99, low=1)]
        stats = post_exit_stats(history, EXIT, 10.0)
        self.assertEqual(stats["post_exit_window_days"], 5)
        self.assertAlmostEqual(stats["post_exit_max_return"], 5.0)
        self.assertAlmostEqual(stats["post_exit_max_drawdown"], -5.0)

    def test_suspended_missing_day_is_skipped_and_window_extends(self):
        history = [bar("2026-09-07"), bar("2026-09-09"), bar("2026-09-10"),
                   bar("2026-09-11"), bar("2026-09-14")]
        stats = post_exit_stats(history, EXIT, 10.0)
        self.assertEqual(stats["post_exit_window_days"], 5)

    def test_zero_volume_placeholder_does_not_take_a_slot(self):
        placeholder = bar("2026-09-08", high=10.0, low=10.0, volume=0, amount=0)
        history = [bar("2026-09-07"), placeholder, bar("2026-09-09"), bar("2026-09-10"),
                   bar("2026-09-11"), bar("2026-09-14", high=20, low=9)]
        self.assertFalse(is_tradable_daily_bar(placeholder))
        stats = post_exit_stats(history, EXIT, 10.0)
        # 占位行不占名额：窗口 = 07/09/10/11/14，最高价取 14 日的 20。
        self.assertEqual(stats["post_exit_window_days"], 5)
        self.assertAlmostEqual(stats["post_exit_max_return"], 100.0)

    def test_one_and_three_bars_are_partial(self):
        one = post_exit_stats([bar("2026-09-07", high=11, low=9)], EXIT, 10.0)
        self.assertEqual(one["post_exit_window_days"], 1)
        self.assertAlmostEqual(one["post_exit_max_return"], 10.0)
        three = post_exit_stats([bar(d) for d in DAYS[:3]], EXIT, 10.0)
        self.assertEqual(three["post_exit_window_days"], 3)
        self.assertAlmostEqual(three["post_exit_max_return"], 5.0)

    def test_empty_history_and_exit_day_not_counted(self):
        self.assertEqual(post_exit_stats([], EXIT, 10.0),
                         {"post_exit_max_return": None, "post_exit_max_drawdown": None,
                          "post_exit_window_days": 0})
        stats = post_exit_stats([bar(EXIT, high=12, low=8)], EXIT, 10.0)
        self.assertEqual(stats["post_exit_window_days"], 0)
        self.assertIsNone(stats["post_exit_max_return"])

    def test_invalid_ohlc_nan_and_inf_are_skipped(self):
        bad_zero = bar("2026-09-07", high=0, low=0)
        bad_nan = bar("2026-09-08", high=math.nan, low=9.5)
        bad_inf = bar("2026-09-09", high=math.inf, low=9.5)
        good = bar("2026-09-10", high=11, low=9)
        stats = post_exit_stats([bad_zero, bad_nan, bad_inf, good], EXIT, 10.0)
        self.assertEqual(stats["post_exit_window_days"], 1)
        self.assertAlmostEqual(stats["post_exit_max_return"], 10.0)

    def test_never_below_exit_price_drawdown_is_zero(self):
        history = [bar(d, high=12, low=10.5) for d in DAYS]
        stats = post_exit_stats(history, EXIT, 10.0)
        self.assertAlmostEqual(stats["post_exit_max_return"], 20.0)
        self.assertEqual(stats["post_exit_max_drawdown"], 0.0)

    def test_negative_max_return_when_always_below_exit_price(self):
        history = [bar(d, high=9, low=8) for d in DAYS]
        stats = post_exit_stats(history, EXIT, 10.0)
        self.assertAlmostEqual(stats["post_exit_max_return"], -10.0)
        self.assertAlmostEqual(stats["post_exit_max_drawdown"], -20.0)

    def test_invalid_exit_price_and_zero_window(self):
        for price in (0, -1, None, float("nan")):
            stats = post_exit_stats([bar(d) for d in DAYS], EXIT, price)
            self.assertIsNone(stats["post_exit_max_return"])
            self.assertEqual(stats["post_exit_window_days"], 0)
        self.assertEqual(post_exit_stats([bar(d) for d in DAYS], EXIT, 10.0, window=0)
                         ["post_exit_window_days"], 0)


def pos(code, ret, price=10.0, qty=1000, status="closed"):
    return {"entry_reason_code": code, "realized_return": ret,
            "entry_price": price, "qty": qty, "status": status}


class TestEntryReasonStats(unittest.TestCase):
    def test_four_rows_plus_unknown_plus_total(self):
        positions = [
            pos(BUY_SIGNAL_CODES[0], 10.0),
            pos(BUY_SIGNAL_CODES[0], -5.0),
            pos(BUY_SIGNAL_CODES[3], 3.0),
            pos("legacy_buy", 1.0),
            pos("", 2.0),
        ]
        stats = entry_reason_stats(positions, 100000)
        codes = [r["reason_code"] for r in stats["rows"]]
        self.assertEqual(codes[:4], list(BUY_SIGNAL_CODES))
        self.assertEqual(codes[4], "legacy_buy")
        self.assertEqual(codes[5], "")
        self.assertEqual(codes[6], TOTAL_ROW_CODE)
        rows = {r["reason_code"]: r for r in stats["rows"]}
        self.assertEqual(rows[BUY_SIGNAL_CODES[0]]["label"], BUY_CHECK_LABELS[BUY_SIGNAL_CODES[0]])
        self.assertEqual(rows[BUY_SIGNAL_CODES[0]]["trades"], 2)
        self.assertEqual(rows[BUY_SIGNAL_CODES[0]]["wins"], 1)
        self.assertAlmostEqual(rows[BUY_SIGNAL_CODES[0]]["win_rate"], 50.0)
        self.assertAlmostEqual(rows[BUY_SIGNAL_CODES[0]]["avg_return"], 2.5)
        self.assertEqual(rows[""]["label"], "未知")
        self.assertEqual(rows[TOTAL_ROW_CODE]["label"], "合计")
        self.assertEqual(stats["total_trades"], 5)
        # 贡献近似：Σ(return/100 × price × qty) / 100000 × 100
        expected = ((10 - 5 + 3 + 1 + 2) / 100 * 10 * 1000) / 100000 * 100
        self.assertAlmostEqual(rows[TOTAL_ROW_CODE]["contribution"], expected)

    def test_zero_trades_rows_have_none_ratios_and_zero_contribution(self):
        stats = entry_reason_stats([], 100000)
        self.assertEqual([r["reason_code"] for r in stats["rows"]],
                         [*BUY_SIGNAL_CODES, TOTAL_ROW_CODE])
        for row in stats["rows"]:
            self.assertEqual(row["trades"], 0)
            self.assertIsNone(row["win_rate"])
            self.assertIsNone(row["avg_return"])
            self.assertEqual(row["contribution"], 0.0)
        self.assertEqual(stats["total_trades"], 0)

    def test_initial_cash_non_positive_makes_contribution_none(self):
        for cash in (0, -100, None):
            stats = entry_reason_stats([pos(BUY_SIGNAL_CODES[0], 10.0)], cash)
            self.assertIsNone(stats["rows"][0]["contribution"])
            self.assertIsNone(stats["rows"][-1]["contribution"])

    def test_open_positions_are_excluded(self):
        stats = entry_reason_stats(
            [pos(BUY_SIGNAL_CODES[0], 10.0), pos(BUY_SIGNAL_CODES[0], 5.0, status="open")],
            100000)
        self.assertEqual(stats["total_trades"], 1)
        self.assertEqual(stats["rows"][0]["trades"], 1)

    def test_total_win_rate_matches_result_formula(self):
        returns = [10.0, -5.0, 3.0, 0.0, 2.0]
        positions = [pos(BUY_SIGNAL_CODES[i % 4], r) for i, r in enumerate(returns)]
        stats = entry_reason_stats(positions, 100000)
        expected = sum(r > 0 for r in returns) / len(returns) * 100
        self.assertAlmostEqual(stats["rows"][-1]["win_rate"], expected)


class _BrokenData:
    def daily(self, code, start, refresh=False):
        raise RuntimeError("provider down")


class _OneCallData:
    def __init__(self, bars):
        self.bars = bars
        self.calls = []

    def daily(self, code, start, refresh=False):
        self.calls.append((code, start))
        return self.bars


def closed(exit_date=EXIT, exit_price=10.0, code="600001"):
    return ClosedPosition(code, "样本", "2026-08-28", 10.0, 1000,
                          "buy_open_ma5_pullback", {}, exit_date, exit_price,
                          "trailing_take_profit", {}, 1.0, 1)


class TestAnnotatePostExit(unittest.TestCase):
    def test_fills_fields_from_daily_history(self):
        p = closed()
        annotate_post_exit([p], _OneCallData([bar(d) for d in DAYS]))
        self.assertAlmostEqual(p.post_exit_max_return, 5.0)
        self.assertAlmostEqual(p.post_exit_max_drawdown, -5.0)
        self.assertEqual(p.post_exit_window_days, 5)

    def test_daily_failure_degrades_without_raising(self):
        p = closed()
        annotate_post_exit([p], _BrokenData())
        self.assertIsNone(p.post_exit_max_return)
        self.assertIsNone(p.post_exit_max_drawdown)
        self.assertEqual(p.post_exit_window_days, 0)

    def test_one_daily_call_per_code(self):
        data = _OneCallData([bar(d) for d in DAYS])
        annotate_post_exit([closed(), closed(exit_date="2026-09-07")], data)
        self.assertEqual(len(data.calls), 1)

    def test_same_code_positions_use_their_own_window(self):
        data = _OneCallData([bar(d) for d in DAYS])
        first, second = closed(), closed(exit_date="2026-09-07")
        annotate_post_exit([first, second], data)
        self.assertEqual(len(data.calls), 1)
        self.assertEqual(first.post_exit_window_days, 5)
        self.assertEqual(second.post_exit_window_days, 4)
        self.assertAlmostEqual(second.post_exit_max_return, 5.0)


if __name__ == "__main__":
    unittest.main()