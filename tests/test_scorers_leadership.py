"""
tests for dragon_quant.scorers.leadership — 领涨性评分器
"""
import unittest
from dragon_quant.scorers.leadership import (
    _normal_cdf_approx,
    _aggregate_1min_to_5min,
    _bar_return,
)
from dragon_quant.models.types import KBar


class TestNormalCdfApprox(unittest.TestCase):

    def test_middle(self):
        """z=0 → Φ(0)=0.5, rank = 1-0.5 = 0.5"""
        self.assertAlmostEqual(_normal_cdf_approx(0.0), 0.50, places=2)

    def test_head(self):
        """z=2 → rank is small (head)"""
        val = _normal_cdf_approx(2.0)
        self.assertLess(val, 0.1)

    def test_tail(self):
        """z=-2 → rank is large (tail)"""
        val = _normal_cdf_approx(-2.0)
        self.assertGreater(val, 0.9)

    def test_extreme_positive(self):
        """z=5 → near 0"""
        self.assertAlmostEqual(_normal_cdf_approx(5.0), 0.01, places=2)

    def test_extreme_negative(self):
        """z=-5 → near 1"""
        self.assertAlmostEqual(_normal_cdf_approx(-5.0), 0.99, places=2)

    def test_z_one(self):
        """z=1 → Φ(1)≈0.84, rank≈0.16"""
        self.assertAlmostEqual(_normal_cdf_approx(1.0), 0.16, delta=0.01)

    def test_z_negative_one(self):
        self.assertAlmostEqual(_normal_cdf_approx(-1.0), 0.84, delta=0.01)


class TestAggregate1minTo5min(unittest.TestCase):

    def test_five_bars(self):
        bars = [
            KBar(timestamp=1000 * i, volume=100, open=10 + i * 0.1, high=11,
                 low=9, close=10 + i * 0.1 + 0.05, chg=0, pct=i * 0.5,
                 turnover=0, amount=1000)
            for i in range(5)
        ]
        result = _aggregate_1min_to_5min(bars)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].open, 10.0)
        self.assertAlmostEqual(result[0].close, 10.45, places=6)

    def test_less_than_3_skipped(self):
        bars = [
            KBar(timestamp=1000 * i, volume=100, open=10.0, high=11,
                 low=9, close=10.5, chg=0, pct=0, turnover=0, amount=1000)
            for i in range(2)
        ]
        result = _aggregate_1min_to_5min(bars)
        self.assertEqual(len(result), 0)


class TestBarReturn(unittest.TestCase):

    def test_normal(self):
        bar = KBar(timestamp=0, volume=1, open=10.0, high=11, low=9,
                   close=10.5, chg=0, pct=0, turnover=0, amount=0)
        expected = (10.5 - 10.0) / 10.0
        self.assertAlmostEqual(_bar_return([bar], 0), expected)

    def test_zero_open(self):
        bar = KBar(timestamp=0, volume=1, open=0.0, high=11, low=9,
                   close=10.5, chg=0, pct=0, turnover=0, amount=0)
        self.assertEqual(_bar_return([bar], 0), 0.0)


if __name__ == "__main__":
    unittest.main()
