"""
tests for dragon_quant.vpa — 量价分析模块
"""
import unittest

from dragon_quant.models.types import KBar
from dragon_quant.vpa.engine import analyze, _aggregate, DEFAULT_CTX
from dragon_quant.vpa.factors import FACTORS
from dragon_quant.vpa.factors import vol_amount, trend_verify, breakout, divergence
from dragon_quant.vpa.types import (
    FactorResult, SIGNAL_BULLISH, SIGNAL_BEARISH, SIGNAL_NEUTRAL,
)


def _bar(ts, close, vol, *, pct=0.0, open_px=None, high=None, low=None, amount=None):
    op = open_px if open_px is not None else close
    hi = high if high is not None else max(op, close)
    lo = low if low is not None else min(op, close)
    amt = amount if amount is not None else vol * close
    return KBar(timestamp=ts, volume=vol, open=op, high=hi, low=lo, close=close,
                chg=close - op, pct=pct, turnover=2.0, amount=amt)


def _uptrend_volup(n=40):
    """上升 + 放量趋势。"""
    bars = []
    price, vol = 10.0, 1_000_000.0
    for i in range(n):
        price *= 1.01
        vol *= 1.01
        bars.append(_bar(i * 86400000, price, vol, pct=1.0,
                         open_px=price / 1.005, high=price * 1.005, low=price / 1.005 * 0.998))
    return bars


class TestEngineAggregate(unittest.TestCase):

    def test_bullish_majority(self):
        results = [
            FactorResult("a", "A", SIGNAL_BULLISH, 80, ""),
            FactorResult("b", "B", SIGNAL_BULLISH, 70, ""),
            FactorResult("c", "C", SIGNAL_BEARISH, 40, ""),
        ]
        health, signal, _ = _aggregate(results)
        self.assertEqual(signal, SIGNAL_BULLISH)
        self.assertAlmostEqual(health, (80 + 70 + 40) / 3, places=1)

    def test_neutral_tie(self):
        results = [
            FactorResult("a", "A", SIGNAL_BULLISH, 60, ""),
            FactorResult("b", "B", SIGNAL_BEARISH, 40, ""),
        ]
        _, signal, _ = _aggregate(results)
        self.assertEqual(signal, SIGNAL_NEUTRAL)

    def test_empty(self):
        health, signal, _ = _aggregate([])
        self.assertEqual(signal, SIGNAL_NEUTRAL)
        self.assertEqual(health, 50.0)


class TestFactorsRunnable(unittest.TestCase):

    def test_all_factors_return_valid(self):
        bars = _uptrend_volup()
        for fn in FACTORS:
            res = fn(bars, DEFAULT_CTX)
            self.assertIsInstance(res, FactorResult)
            self.assertIn(res.signal, (SIGNAL_BULLISH, SIGNAL_BEARISH, SIGNAL_NEUTRAL))
            self.assertGreaterEqual(res.score, 0.0)
            self.assertLessEqual(res.score, 100.0)


class TestTrendVerify(unittest.TestCase):

    def test_uptrend_volup_is_bullish(self):
        res = trend_verify.factor(_uptrend_volup(), DEFAULT_CTX)
        self.assertEqual(res.signal, SIGNAL_BULLISH)


class TestDivergence(unittest.TestCase):

    def test_price_up_vol_shrink_is_bearish(self):
        # 前段高量，后段价更高但量大幅萎缩
        bars = []
        for i in range(5):
            bars.append(_bar(i * 86400000, 10.0 + i * 0.1, 2_000_000.0, pct=1.0))
        for i in range(5, 10):
            bars.append(_bar(i * 86400000, 11.0 + i * 0.1, 800_000.0, pct=1.0))
        res = divergence.factor(bars, DEFAULT_CTX)
        self.assertEqual(res.signal, SIGNAL_BEARISH)


class TestBreakout(unittest.TestCase):

    def test_breakout_with_volume_bullish(self):
        bars = [_bar(i * 86400000, 10.0, 1_000_000.0, pct=0.0) for i in range(25)]
        # 放量突破
        bars.append(_bar(25 * 86400000, 11.0, 2_000_000.0, pct=10.0))
        res = breakout.factor(bars, DEFAULT_CTX)
        self.assertEqual(res.signal, SIGNAL_BULLISH)

    def test_breakout_no_volume_bearish(self):
        bars = [_bar(i * 86400000, 10.0, 1_000_000.0, pct=0.0) for i in range(25)]
        bars.append(_bar(25 * 86400000, 11.0, 1_000_000.0, pct=10.0))
        res = breakout.factor(bars, DEFAULT_CTX)
        self.assertEqual(res.signal, SIGNAL_BEARISH)


class TestAnalyzeFallback(unittest.TestCase):

    def test_insufficient_data_fallback(self):
        # 通过 monkeypatch get_kline 返回过短数据
        import dragon_quant.data as data
        orig = data.get_kline
        data.get_kline = lambda code, source="xueqiu", days=20: []
        try:
            rep = analyze("000001")
            self.assertTrue(rep.fallback)
            self.assertEqual(rep.signal, SIGNAL_NEUTRAL)
        finally:
            data.get_kline = orig


if __name__ == "__main__":
    unittest.main()
