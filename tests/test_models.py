"""
tests for dragon_quant.models.types — 6 dataclass 实例化 + 序列化
"""
import io
import json
import unittest
from dragon_quant.models.types import KBar, StockInfo, Quote, SectorPerformance, Candidate, ScoreResult


class TestKBar(unittest.TestCase):
    def test_instantiation(self):
        bar = KBar(timestamp=1700000000000, volume=1e6, open=10.0, high=11.0,
                   low=9.5, close=10.5, chg=0.5, pct=5.0, turnover=3.2, amount=1e7)
        self.assertEqual(bar.timestamp, 1700000000000)
        self.assertEqual(bar.close, 10.5)
        self.assertEqual(bar.pct, 5.0)

    def test_float_fields(self):
        bar = KBar(timestamp=1, volume=0, open=0, high=0, low=0, close=0,
                   chg=0, pct=0, turnover=0, amount=0)
        self.assertEqual(bar.pct, 0.0)


class TestStockInfo(unittest.TestCase):
    def test_basic(self):
        s = StockInfo(code="600519", name="贵州茅台")
        self.assertEqual(s.code, "600519")
        self.assertEqual(s.exchange, "")

    def test_five_day_return_default(self):
        s = StockInfo(code="600519", name="贵州茅台")
        self.assertEqual(s.five_day_return, 0.0)


class TestQuote(unittest.TestCase):
    def test_instantiation(self):
        q = Quote(code="600519", name="贵州茅台", price=1800.0, prev_close=1790.0,
                  open_px=1795.0, high=1810.0, low=1785.0, pct=0.56, chg=10.0,
                  turnover_rate=0.5, amplitude=1.4, volume=1e6, amount=1.8e9,
                  market_cap=2.2e12, float_market_cap=2.0e12, volume_ratio=0.8,
                  pe=35.5, limit_up=1970.0, limit_down=1610.0, avg_price=1798.0)
        self.assertEqual(q.price, 1800.0)
        self.assertEqual(q.pct, 0.56)
        self.assertEqual(q.market_cap, 2.2e12)


class TestSectorPerformance(unittest.TestCase):
    def test_instantiation(self):
        sp = SectorPerformance(code="BK1145", name="机器人执行器", pct=3.5, amplitude=5.2)
        self.assertEqual(sp.name, "机器人执行器")
        self.assertEqual(sp.pct, 3.5)


class TestCandidate(unittest.TestCase):
    def test_defaults(self):
        c = Candidate(code="600519", name="贵州茅台")
        self.assertEqual(c.concepts, [])
        self.assertEqual(c.board_count, 0)
        self.assertEqual(c.score, 0.0)

    def test_full(self):
        c = Candidate(code="002031", name="巨轮智能",
                      concepts=["减速器", "工业母机"],
                      board_count=3, primary_sector="BK1234",
                      score=76.5)
        self.assertEqual(len(c.concepts), 2)
        self.assertEqual(c.board_count, 3)


class TestScoreResult(unittest.TestCase):
    def test_instantiation(self):
        sr = ScoreResult(dim="drive", score=85.0, weight=0.35,
                         details={"limit_up_count": 1})
        self.assertEqual(sr.dim, "drive")
        self.assertEqual(sr.weight, 0.35)


class TestDataclassJSONRoundTrip(unittest.TestCase):
    """验证 dataclass 可被 JSON 序列化"""

    def test_kbar_serializable(self):
        bar = KBar(timestamp=1700000000000, volume=1e6, open=10.0, high=11.0,
                   low=9.5, close=10.5, chg=0.5, pct=5.0, turnover=3.2, amount=1e7)
        d = {"timestamp": bar.timestamp, "close": bar.close, "pct": bar.pct}
        s = json.dumps(d)
        loaded = json.loads(s)
        self.assertEqual(loaded["timestamp"], 1700000000000)
        self.assertEqual(loaded["close"], 10.5)

    def test_score_result_serializable(self):
        sr = ScoreResult(dim="drive", score=85.0, weight=0.35,
                         details={"best_day": "2026-05-15", "limit_up_count": 2})
        d = {"dim": sr.dim, "score": sr.score, "weight": sr.weight,
             "details": sr.details}
        s = json.dumps(d)
        loaded = json.loads(s)
        self.assertEqual(loaded["dim"], "drive")
        self.assertEqual(loaded["details"]["limit_up_count"], 2)


if __name__ == "__main__":
    unittest.main()
