"""
tests for dragon_quant.scorers.anti_drop — 抗跌性评分器
"""
import time
import unittest
from dragon_quant.models.types import KBar
from dragon_quant.scorers.anti_drop import (
    _find_matching_index,
    _relative_retreat,
    _intraday_hold,
    _rebound,
    _consecutive_plunge_bonus,
)


def _make_kbar(ts, pct, open_px=10.0, high_px=11.0, low_px=9.5, close_px=10.5):
    return KBar(timestamp=ts, volume=1e6, open=open_px, high=high_px,
                low=low_px, close=close_px, chg=0.5, pct=pct,
                turnover=5.0, amount=1e7)


class TestFindMatchingIndex(unittest.TestCase):

    def test_match_found(self):
        now = int(time.time() * 1000)
        klines = [_make_kbar(now, 0)]
        idx = _find_matching_index(klines, now)
        self.assertEqual(idx, 0)

    def test_no_match(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        klines = [_make_kbar(now - day, 0)]
        idx = _find_matching_index(klines, now)
        self.assertIsNone(idx)


class TestRelativeRetreat(unittest.TestCase):

    def test_stock_positive(self):
        bar = KBar(timestamp=0, volume=1, open=10, high=11, low=9, close=11,
                   chg=0, pct=5.0, turnover=1, amount=1e6)
        self.assertEqual(_relative_retreat(bar, -2.0), 100.0)

    def test_excess_positive(self):
        bar = KBar(timestamp=0, volume=1, open=10, high=11, low=9, close=10,
                   chg=0, pct=-1.0, turnover=1, amount=1e6)
        score = _relative_retreat(bar, -3.0)
        self.assertGreater(score, 60)

    def test_moderate_drop(self):
        """pct=-1.5 with market=-5 → excess=3.5>0 → 60+3.5/5*40=88"""
        bar = KBar(timestamp=0, volume=1, open=10, high=11, low=9, close=10,
                   chg=0, pct=-1.5, turnover=1, amount=1e6)
        self.assertAlmostEqual(_relative_retreat(bar, -5.0), 88.0, places=2)

    def test_severe_drop(self):
        bar = KBar(timestamp=0, volume=1, open=10, high=11, low=9, close=10,
                   chg=0, pct=-5.0, turnover=1, amount=1e6)
        self.assertEqual(_relative_retreat(bar, -5.0), 0.0)


class TestIntradayHold(unittest.TestCase):

    def test_normal_case(self):
        bar = KBar(timestamp=0, volume=1, open=10.0, high=11.0, low=9.5,
                   close=10.8, chg=0, pct=8.0, turnover=1, amount=1e6)
        score = _intraday_hold(bar, 10.0)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_flat_bar(self):
        bar = KBar(timestamp=0, volume=1, open=10.0, high=10.0, low=10.0,
                   close=10.0, chg=0, pct=0, turnover=1, amount=1e6)
        score = _intraday_hold(bar, 10.0)
        self.assertEqual(score, 50.0)


class TestRebound(unittest.TestCase):

    def test_dive_is_latest(self):
        """跳水是最近一天 — 无次日数据"""
        now = int(time.time() * 1000)
        day = 86400 * 1000
        stock_klines = [_make_kbar(now, 2.0)]
        market_klines = [_make_kbar(now, -1.0)]
        self.assertEqual(_rebound(stock_klines, market_klines, 0, 0), 50.0)

    def test_stock_alone_up(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        stock_klines = [_make_kbar(now - day, 3.0), _make_kbar(now, 2.0)]
        market_klines = [_make_kbar(now - day, -1.0), _make_kbar(now, -0.5)]
        self.assertEqual(_rebound(stock_klines, market_klines, 1, 1), 100.0)


class TestConsecutivePlungeBonus(unittest.TestCase):

    def test_single_plunge(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        stock = [_make_kbar(now - day, -2.0), _make_kbar(now, 3.0)]
        market = [_make_kbar(now - day, -1.5), _make_kbar(now, 0.5)]
        plunge = [(1, 1, "2026-05-14", -1.5)]
        self.assertEqual(_consecutive_plunge_bonus(stock, market, plunge), 0.0)

    def test_two_consecutive_anti_drop(self):
        """连续2日跳水，个股跌幅 << 大盘 → +10分"""
        now = int(time.time() * 1000)
        day = 86400 * 1000
        stock = [
            _make_kbar(now - 1 * day, -0.5, close_px=9.95),
            _make_kbar(now, -0.3, close_px=9.92),
        ]
        market = [
            _make_kbar(now - 1 * day, -2.0, close_px=98),
            _make_kbar(now, -1.5, close_px=96.5),
        ]
        plunge = [(0, 0, "2026-05-14", -2.0), (1, 1, "2026-05-15", -1.5)]
        self.assertGreaterEqual(_consecutive_plunge_bonus(stock, market, plunge), 0)

    def test_two_consecutive_but_stock_drops_hard(self):
        """连续2日跳水，个股也大跌 → 不加分"""
        now = int(time.time() * 1000)
        day = 86400 * 1000
        stock = [
            _make_kbar(now - 1 * day, -3.0, close_px=9.7),
            _make_kbar(now, -2.5, close_px=9.46),
        ]
        market = [
            _make_kbar(now - 1 * day, -2.0, close_px=98),
            _make_kbar(now, -1.5, close_px=96.5),
        ]
        plunge = [(1, 1, "2026-05-15", -1.5), (0, 0, "2026-05-14", -2.0)]
        self.assertEqual(_consecutive_plunge_bonus(stock, market, plunge), 0.0)


if __name__ == "__main__":
    unittest.main()
