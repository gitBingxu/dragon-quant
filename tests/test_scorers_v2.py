"""tests for dragon_quant.scorers_v2 — 五维识别真龙评分体系。

覆盖：base 工具、leadership 排名分位、liquidity 一字不罚、drive 脉冲-跟随、
anti_drop 跳水段、absorption 中性回落、aggregator 门槛一票否决与不否决。
"""
import time
import unittest

from dragon_quant.cache.data_cache import DataCache
from dragon_quant.models.types import KBar, Quote, StockInfo, Candidate
from dragon_quant.scorers_v2 import evaluate, rank_verdicts
from dragon_quant.scorers_v2 import registry as R
from dragon_quant.scorers_v2.base import (
    clip, desc_rank_score, common_minute_axis, gain_curve)
from dragon_quant.scorers_v2 import leadership, liquidity, drive, anti_drop


def _min_bars(pre, pcts, start="2026-06-19 09:30"):
    """按累计涨幅(%)序列构造当日1分K（open=上一分钟close，首根open=pre）。"""
    base = int(time.mktime(time.strptime(start, "%Y-%m-%d %H:%M")))
    bars = []
    prev = pre
    for i, p in enumerate(pcts):
        px = pre * (1 + p / 100.0)
        bars.append(KBar(timestamp=(base + i * 60) * 1000, volume=100,
                         open=prev, high=max(px, prev), low=min(px, prev),
                         close=px, chg=0, pct=p, turnover=0, amount=0))
        prev = px
    return bars


def _quote(code, pct, lu, bid1=0.0, vol=100000.0, tr=10.0):
    return Quote(code=code, name=code, price=lu, prev_close=lu / 1.1,
                 open_px=lu, high=lu, low=lu / 1.1, pct=pct, chg=0,
                 turnover_rate=tr, amplitude=5, volume=vol, amount=0,
                 market_cap=0, float_market_cap=0, volume_ratio=1, pe=0,
                 limit_up=lu, limit_down=0, avg_price=0,
                 bid1_price=lu, bid1_volume=bid1, ask1_volume=0)


class TestBaseUtils(unittest.TestCase):

    def test_clip(self):
        self.assertEqual(clip(150), 100)
        self.assertEqual(clip(-5), 0)
        self.assertEqual(clip(50), 50)

    def test_desc_rank_top(self):
        s, r, n = desc_rank_score(30.0, [30.0, 20.0, 10.0])
        self.assertEqual(r, 1)
        self.assertEqual(n, 3)
        self.assertAlmostEqual(s, (1 - 1 / 3) * 100, places=4)

    def test_desc_rank_bottom(self):
        s, r, n = desc_rank_score(5.0, [30.0, 20.0, 5.0])
        self.assertEqual(r, 3)
        self.assertEqual(s, 0.0)

    def test_desc_rank_single(self):
        s, r, n = desc_rank_score(5.0, [5.0])
        self.assertEqual(s, 0.0)

    def test_gain_curve_fill(self):
        bars = _min_bars(10.0, [0, 1, 2])
        axis = common_minute_axis(bars)
        g = gain_curve(bars, axis)
        self.assertAlmostEqual(g[0], 0.0, places=4)
        self.assertAlmostEqual(g[-1], 0.02, places=4)


class TestLeadership(unittest.TestCase):

    def setUp(self):
        self.cache = DataCache()
        comps = [StockInfo(code="600001", name="龙头", sector_code="BK1",
                           pct=10, price=11, five_day_return=40.0),
                 StockInfo(code="600002", name="小弟", sector_code="BK1",
                           pct=10, price=5, five_day_return=15.0),
                 StockInfo(code="600003", name="小弟2", sector_code="BK1",
                           pct=5, price=8, five_day_return=5.0)]
        self.cache.set("sector:components:BK1", comps)

    def test_top_dragon_high(self):
        pool = [Candidate(code="600001", name="龙头", concepts=["BK1"],
                          board_count=3, fived_pct=40.0, primary_sector="BK1"),
                Candidate(code="600002", name="小弟", concepts=["BK1"],
                          board_count=1, fived_pct=15.0, primary_sector="BK1")]
        r = leadership.score("600001", self.cache, primary_sector="BK1",
                             candidate_pool=pool)
        # 板块最高连板(100) + 涨幅居首；样本=候选池2只 → 涨幅分位 50 → 综合 75
        self.assertEqual(r.score, 75.0)
        self.assertEqual(r.details["s_board"], 100.0)

    def test_low_board_penalized(self):
        pool = [Candidate(code="600001", name="龙头", concepts=["BK1"],
                          board_count=5, fived_pct=40.0, primary_sector="BK1"),
                Candidate(code="600002", name="小弟", concepts=["BK1"],
                          board_count=1, fived_pct=15.0, primary_sector="BK1")]
        r = leadership.score("600002", self.cache, primary_sector="BK1",
                             candidate_pool=pool)
        # 低 4 板 → s_board=60；涨幅排名靠后 → 综合明显低于龙头
        self.assertLess(r.score, 70)


class TestLiquidityYizi(unittest.TestCase):

    def test_yizi_not_penalized(self):
        """一字板：开板0次 + 封单大 → 封板质量满分，不被惩罚。"""
        cache = DataCache()
        # 一字封死：全程涨停价
        minute = _min_bars(10.0, [10.0] * 10)
        cache.set("kline:1min:600001", minute)
        q = _quote("600001", 10.0, 11.0, bid1=50000, vol=100000, tr=2.0)
        cache.set("quotes:batch", [q])
        cache.set("sector:components:BK1",
                  [StockInfo(code="600001", name="龙头", sector_code="BK1",
                             pct=10, price=11)])
        r = liquidity.score("600001", cache, primary_sector="BK1")
        # 封单强度 50000/100000=0.5 > ref0.3 满分；稳定性满分
        self.assertEqual(r.details["s_seal"], 100.0)
        self.assertEqual(r.details["n_open"], 0)


class TestDrivePulse(unittest.TestCase):

    def test_early_seal_details_include_time_and_bid_volume(self):
        cache = DataCache()
        stock = _min_bars(10.0, [0, 2, 5, 10, 10, 10])
        cache.set("kline:1min:600001", stock)
        comps = [StockInfo(code="600001", name="龙头", sector_code="BK1",
                           pct=10, price=11)]
        q = _quote("600001", 10.0, 11.0, bid1=12345)

        s, d = drive._early_seal("600001", cache, "BK1", comps, {"600001": q})

        self.assertEqual(s, 100.0)
        self.assertTrue(d["sealed"])
        self.assertEqual(d["seal_time"], "09:33")
        self.assertEqual(d["bid1_volume"], 12345)

    def test_pure_follower_zero(self):
        """纯跟风票（板块先拉、个股后跟，无主动带动）→ lead 子因子 0 分。"""
        cache = DataCache()
        # 板块第1分钟就先拉起来，个股拖到第4分钟才跟 → 个股脉冲前板块已抢跑
        sector = _min_bars(100.0, [0, 0.4, 0.7, 0.9, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        stock = _min_bars(10.0, [0, 0, 0, 0, 4.0, 7.0, 10.0, 10.0, 10.0, 10.0])
        s, d = drive._lead_sector(stock, sector)
        self.assertEqual(d["n_lead"], 0)
        self.assertEqual(s, 0.0)

    def test_lead_sector_records_lead_event_details(self):
        stock = _min_bars(10.0, [0, 0, 0, 0, 1.5, 3.5, 4.0, 4.0, 4.0, 4.0])
        sector = _min_bars(100.0, [0, 0, 0, 0, 0.05, 0.45, 0.50, 0.50, 0.50, 0.50])

        s, d = drive._lead_sector(stock, sector)

        self.assertGreater(s, 0)
        self.assertEqual(d["n_lead"], 1)
        event = d["lead_events"][0]
        self.assertEqual(event["event_time"], "09:32")
        self.assertIn("stock_gain_pct", event)
        self.assertIn("sector_gain_pct", event)
        self.assertGreater(event["stock_gain_pct"], 0)
        self.assertGreater(event["sector_gain_pct"], 0)

    def test_lead_sector_records_follow_event_details(self):
        sector = _min_bars(100.0, [0, 0, 0, 0.4, 0.7, 0.7, 0.7, 1.1, 1.1, 1.1])
        stock = _min_bars(10.0, [0, 0, 0, 0, 0, 0, 0, 3.2, 4.0, 4.0])

        s, d = drive._lead_sector(stock, sector)

        self.assertEqual(s, 0.0)
        self.assertGreaterEqual(d["n_follow"], 1)
        event = d["follow_events"][0]
        self.assertEqual(event["sector_event_time"], "09:34")
        self.assertIn("stock_follow_time", event)
        self.assertGreater(event["sector_gain_pct"], 0)
        self.assertGreater(event["stock_gain_pct"], 0)


class TestAntiDropDetails(unittest.TestCase):

    def test_antidrop_records_dip_event_details(self):
        base = _min_bars(3000.0, [0, -0.1, -0.2, -0.8, -0.7, -0.6, -0.5])
        stock = _min_bars(10.0, [0, 0.1, 0.1, 0.0, 0.2, 0.3, 0.3])

        s, d = anti_drop._antidrop_vs(base, stock)

        self.assertGreater(s, 0)
        event = d["dip_events"][0]
        self.assertEqual(event["start_time"], "09:30")
        self.assertEqual(event["bottom_time"], "09:33")
        self.assertLess(event["base_drop_pct"], 0)
        self.assertIn("stock_change_pct", event)


class TestAggregator(unittest.TestCase):

    def _full_cache(self, bid1=50000, market_dip=True):
        cache = DataCache()
        stock = _min_bars(10.0, [0, 1.5, 4.2, 7, 10, 10, 10, 10, 10, 10])
        cache.set("kline:1min:600001", stock)
        sector = _min_bars(100.0, [0, 0, 0.1, 0.4, 0.6, 0.6, 0.5, 0.5, 0.5, 0.5])
        cache.set("kline:1min:sector:BK1", sector)
        if market_dip:
            mkt = _min_bars(3000.0, [0, -0.2, -0.6, -0.8, -0.5, -0.2, 0, 0.1, 0.2, 0.3])
        else:
            mkt = _min_bars(3000.0, [0] * 10)
        cache.set("kline:1min:000001", mkt)
        comps = [StockInfo(code="600001", name="龙头", sector_code="BK1",
                           pct=10, price=11, five_day_return=40.0),
                 StockInfo(code="600002", name="小弟", sector_code="BK1",
                           pct=10, price=5, five_day_return=15.0),
                 StockInfo(code="600003", name="小弟2", sector_code="BK1",
                           pct=5, price=8, five_day_return=5.0),
                 StockInfo(code="600004", name="小弟3", sector_code="BK1",
                           pct=3.5, price=8, five_day_return=3.0)]
        cache.set("sector:components:BK1", comps)
        cache.set("quotes:batch", [
            _quote("600001", 10.0, 11.0, bid1=bid1, vol=100000, tr=12.0),
            _quote("600002", 10.0, 5.5, bid1=8000, vol=90000, tr=8.0),
            _quote("600003", 5.0, 8.4, bid1=0, vol=50000, tr=5.0),
            _quote("600004", 3.5, 8.28, bid1=0, vol=30000, tr=3.0)])
        cache.set("kline:1min:600002", _min_bars(5.0, [0, 2, 4, 6, 8, 10, 10, 10, 10, 10]))
        pool = [Candidate(code="600001", name="龙头", concepts=["BK1"],
                          board_count=3, fived_pct=40.0, primary_sector="BK1"),
                Candidate(code="600002", name="小弟", concepts=["BK1"],
                          board_count=2, fived_pct=15.0, primary_sector="BK1")]
        return cache, pool

    def test_true_dragon_pass(self):
        cache, pool = self._full_cache()
        v = evaluate("600001", cache, candidate_pool=pool, primary_sector="BK1",
                     all_sector_codes=["BK1"], sector_name_map={})
        self.assertTrue(v.is_true_dragon)
        self.assertIsNone(v.reject_reason)
        self.assertEqual(set(v.dims.keys()),
                         {"drive", "leadership", "anti_drop", "liquidity", "absorption"})

    def test_veto_on_low_dim(self):
        """涨幅垫底 + 低连板 → leadership 低于门槛 → 一票否决。"""
        cache, pool = self._full_cache()
        # 600004 涨幅最低、无连板、未涨停
        pool.append(Candidate(code="600004", name="小弟3", concepts=["BK1"],
                              board_count=0, fived_pct=3.0, primary_sector="BK1"))
        v = evaluate("600004", cache, candidate_pool=pool, primary_sector="BK1",
                     all_sector_codes=["BK1"], sector_name_map={})
        self.assertFalse(v.is_true_dragon)
        self.assertIsNotNone(v.reject_reason)

    def test_absorption_not_veto(self):
        """absorption 必为中性 50（无历史5分K），但绝不触发否决。"""
        cache, pool = self._full_cache()
        v = evaluate("600001", cache, candidate_pool=pool, primary_sector="BK1",
                     all_sector_codes=["BK1"], sector_name_map={})
        self.assertEqual(v.dims["absorption"].score, R.ABS_NEUTRAL)
        self.assertTrue(v.is_true_dragon)  # absorption 低也不否决

    def test_rank_assignment(self):
        cache, pool = self._full_cache()
        v1 = evaluate("600001", cache, candidate_pool=pool, primary_sector="BK1",
                      all_sector_codes=["BK1"], sector_name_map={})
        v2 = evaluate("600002", cache, candidate_pool=pool, primary_sector="BK1",
                      all_sector_codes=["BK1"], sector_name_map={})
        ranked = rank_verdicts([v2, v1])
        dragons = [v for v in ranked if v.is_true_dragon]
        if len(dragons) >= 2:
            ranks = sorted(v.rank for v in dragons)
            self.assertEqual(ranks, list(range(1, len(dragons) + 1)))


if __name__ == "__main__":
    unittest.main()
