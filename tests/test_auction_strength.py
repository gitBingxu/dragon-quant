"""tests.test_auction_strength — 集合竞价强度评分器单元测试。"""

import unittest

from dragon_quant.models.types import Quote
from dragon_quant.auction import evaluate, rank_auctions, AuctionVerdict
from dragon_quant.auction import registry as R
from dragon_quant.auction.strength import normalize


def _item(h, m, s, price, matched, unmatched):
    return {"time": f"{h:02d}:{m:02d}:{s:02d}",
            "price": price, "matched": matched, "unmatched": unmatched}


def _quote(prev_close=10.0, limit_down=9.0, float_market_cap=50e8):
    return Quote(code="600000", name="x", price=10.0, prev_close=prev_close,
                 open_px=10.0, high=10.0, low=10.0, pct=0.0, chg=0.0,
                 turnover_rate=0.0, amplitude=0.0, volume=0.0, amount=0.0,
                 market_cap=100e8, float_market_cap=float_market_cap,
                 volume_ratio=0.0, pe=0.0, limit_up=11.0, limit_down=limit_down,
                 avg_price=10.0)


class TestNormalize(unittest.TestCase):
    def test_sorts_by_time(self):
        items = [
            _item(9, 25, 0, 10.0, 300, 0),
            _item(9, 15, 0, 10.0, 100, 0),
            _item(9, 20, 0, 10.0, 200, 0),
        ]
        data = normalize(items)
        self.assertEqual(data["times"][0], R.AUCTION_OPEN)
        self.assertEqual(data["times"][-1], R.AUCTION_CLOSE)

    def test_cumulative_total_matched(self):
        items = [
            _item(9, 20, 0, 10.0, 100, 0),
            _item(9, 21, 0, 10.0, 250, 0),
            _item(9, 25, 0, 10.0, 400, 0),
        ]
        data = normalize(items)
        self.assertEqual(data["total_matched"], 400)  # 累计语义取末值

    def test_buy_side_sign(self):
        items = [
            _item(9, 20, 0, 10.0, 100, 50),
            _item(9, 25, 0, 10.0, 200, -30),
        ]
        data = normalize(items)
        self.assertEqual(data["buy_side"][0], 50 * R.UNMATCHED_SIGN)
        self.assertEqual(data["buy_side"][1], -30 * R.UNMATCHED_SIGN)


class TestRejectGate(unittest.TestCase):
    def test_empty_items(self):
        v = evaluate("600000", [])
        self.assertTrue(v.is_weak)
        self.assertEqual(v.reject_reason, "竞价明细缺失")

    def test_quote_none_conservative_reject(self):
        items = [
            _item(9, 20, 0, 10.0, 100, 10),
            _item(9, 25, 0, 10.0, 200, 10),
        ]
        v = evaluate("600000", items, quote=None)
        self.assertTrue(v.is_weak)
        self.assertIn("0.4", v.reject_reason)
        self.assertTrue(v.unverified)

    def test_0_2_strong_to_weak(self):
        items = [
            _item(9, 20, 0, 10.0, 100, 100),
            _item(9, 21, 0, 10.0, 200, 100),
            _item(9, 22, 0, 10.0, 300, 100),
            _item(9, 23, 0, 10.0, 400, 100),
            _item(9, 24, 30, 10.0, 500, -50),
            _item(9, 24, 45, 10.0, 600, -60),
            _item(9, 25, 0, 10.0, 700, -70),
        ]
        v = evaluate("600000", items, quote=_quote())
        self.assertTrue(v.is_weak)
        self.assertIn("0.2 强转弱", v.reject_reason)

    def test_0_4_tail_neg_amplify(self):
        items = [
            _item(9, 20, 0, 10.0, 100, -10),
            _item(9, 22, 0, 10.0, 200, -10),
            _item(9, 24, 30, 10.0, 300, -20),
            _item(9, 24, 45, 10.0, 400, -40),
            _item(9, 25, 0, 10.0, 500, -60),
        ]
        v = evaluate("600000", items, quote=_quote())
        self.assertTrue(v.is_weak)
        self.assertIn("0.4 跌停价排队卖", v.reject_reason)

    def test_0_1_high_green_incr(self):
        items = [
            _item(9, 20, 0, 10.0, 100, 50),
            _item(9, 21, 0, 10.1, 200, 50),
            _item(9, 22, 0, 10.2, 300, 50),
            _item(9, 23, 0, 10.3, 400, 50),
            _item(9, 24, 30, 10.5, 500, -10),
            _item(9, 24, 45, 10.5, 600, -20),
            _item(9, 25, 0, 10.5, 700, -30),
        ]
        v = evaluate("600000", items, quote=_quote(prev_close=10.0))
        self.assertTrue(v.is_weak)
        self.assertIn("0.1 高位绿柱递增", v.reject_reason)

    def test_0_3_cancel_order_bait(self):
        items = [
            _item(9, 19, 50, 10.0, 100, 1000),
            _item(9, 20, 0, 10.0, 200, 100),
            _item(9, 21, 0, 10.0, 300, 50),
            _item(9, 22, 0, 10.0, 400, 50),
            _item(9, 23, 0, 10.0, 500, 50),
            _item(9, 24, 30, 10.0, 600, 50),
            _item(9, 24, 45, 10.0, 700, 50),
            _item(9, 25, 0, 10.0, 800, 50),
        ]
        v = evaluate("600000", items, quote=_quote())
        self.assertTrue(v.is_weak)
        self.assertIn("0.3 卡点撤单诱多", v.reject_reason)

    def test_0_4_priority_over_0_2(self):
        items = [
            _item(9, 20, 0, 10.0, 100, 100),
            _item(9, 21, 0, 10.0, 200, 100),
            _item(9, 22, 0, 10.0, 300, -50),
            _item(9, 23, 0, 10.0, 400, 100),
            _item(9, 24, 30, 10.0, 500, -20),
            _item(9, 24, 45, 10.0, 600, -50),
            _item(9, 25, 0, 10.0, 700, -150),
        ]
        v = evaluate("600000", items, quote=_quote())
        self.assertTrue(v.is_weak)
        self.assertIn("0.4 跌停价排队卖", v.reject_reason)


class TestScoring(unittest.TestCase):
    def _weak_to_strong(self):
        return [
            _item(9, 20, 0, 10.0, 100, -100),
            _item(9, 21, 0, 10.05, 200, -100),
            _item(9, 22, 0, 10.1, 300, -100),
            _item(9, 23, 0, 10.15, 400, -50),
            _item(9, 24, 30, 10.18, 500, 50),
            _item(9, 24, 45, 10.19, 600, 80),
            _item(9, 25, 0, 10.2, 700, 100),
        ]

    def test_weak_to_strong_bull(self):
        v = evaluate("600000", self._weak_to_strong(), quote=_quote(prev_close=10.0))
        self.assertFalse(v.is_weak)
        self.assertEqual(v.direction, "bull")
        self.assertIn(v.level, ("强", "中", "弱"))

    def test_dims_populated(self):
        v = evaluate("600000", self._weak_to_strong(), quote=_quote(prev_close=10.0))
        self.assertEqual(set(v.dims.keys()), {"direction", "supply", "vol_price", "tail_grab"})
        for dim, sr in v.dims.items():
            self.assertEqual(sr.weight, R.AUCTION_DIM_WEIGHTS[dim])

    def test_level_buckets(self):
        from dragon_quant.auction.strength import _level_of
        self.assertEqual(_level_of(80.0), "强")
        self.assertEqual(_level_of(50.0), "中")
        self.assertEqual(_level_of(20.0), "弱")

    def test_tail_grab_snatch(self):
        items = [
            _item(9, 15, 0, 10.0, 50, 0),
            _item(9, 20, 0, 10.0, 100, 50),
            _item(9, 21, 0, 10.0, 110, 50),
            _item(9, 22, 0, 10.0, 120, 50),
            _item(9, 23, 0, 10.0, 130, 50),
            _item(9, 24, 30, 10.0, 160, 80),
            _item(9, 24, 45, 10.0, 210, 30),
            _item(9, 25, 0, 10.0, 280, 10),
        ]
        v = evaluate("600000", items, quote=_quote())
        self.assertFalse(v.is_weak)
        tg = v.dims["tail_grab"]
        self.assertEqual(tg.score, 100.0)
        self.assertEqual(tg.details["event"], "抢筹")


class TestRankAuctions(unittest.TestCase):
    def test_rank_only_usable(self):
        vs = [
            AuctionVerdict(code="a", is_weak=False, strength=80.0, direction="bull", level="强"),
            AuctionVerdict(code="b", is_weak=True, strength=0.0, direction="neutral", level="弱"),
            AuctionVerdict(code="c", is_weak=False, strength=60.0, direction="bull", level="中", unverified=True),
            AuctionVerdict(code="d", is_weak=False, strength=70.0, direction="bull", level="强"),
        ]
        rank_auctions(vs)
        by_code = {v.code: v.rank for v in vs}
        self.assertEqual(by_code["a"], 1)
        self.assertIsNone(by_code["b"])
        self.assertIsNone(by_code["c"])
        self.assertEqual(by_code["d"], 2)


if __name__ == "__main__":
    unittest.main()
