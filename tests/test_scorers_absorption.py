"""
tests for dragon_quant.scorers.absorption — 资金承接评分器
"""
import unittest
from dragon_quant.scorers.absorption import _window_return
from dragon_quant.models.types import KBar


class TestWindowReturn(unittest.TestCase):

    def test_positive_return(self):
        bars = [
            KBar(timestamp=i * 1000, volume=100, open=10.0, high=11, low=9,
                 close=10 + i * 0.1, chg=0, pct=0, turnover=0, amount=1000)
            for i in range(6)
        ]
        ret = _window_return(bars, 0, 5)
        self.assertGreater(ret, 0)

    def test_negative_return(self):
        bars = [
            KBar(timestamp=i * 1000, volume=100, open=10.0, high=11, low=9,
                 close=10 - i * 0.1, chg=0, pct=0, turnover=0, amount=1000)
            for i in range(6)
        ]
        ret = _window_return(bars, 0, 5)
        self.assertLess(ret, 0)

    def test_zero_open_guard(self):
        bars = [
            KBar(timestamp=0, volume=100, open=0.0, high=11, low=9,
                 close=10.0, chg=0, pct=0, turnover=0, amount=1000),
            KBar(timestamp=1000, volume=100, open=10.0, high=11, low=9,
                 close=12.0, chg=0, pct=0, turnover=0, amount=1000),
        ]
        ret = _window_return(bars, 0, 1)
        self.assertEqual(ret, 0.0)

    def test_flat(self):
        bars = [
            KBar(timestamp=i * 1000, volume=100, open=10.0, high=11, low=9,
                 close=10.0, chg=0, pct=0, turnover=0, amount=1000)
            for i in range(6)
        ]
        ret = _window_return(bars, 0, 5)
        self.assertAlmostEqual(ret, 0.0)


if __name__ == "__main__":
    unittest.main()
