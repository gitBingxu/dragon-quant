"""tests for dragon_quant.scorers — 五维识别真龙评分体系。

覆盖：base 工具、leadership 排名分位、liquidity 一字不罚、drive 脉冲-跟随、
anti_drop 跳水段、absorption 中性回落、aggregator 门槛一票否决与不否决。
"""
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from dragon_quant.scorers.base import CHINA_TZ
from dragon_quant.scorers import absorption, aggregator

from dragon_quant.cache.data_cache import DataCache
from dragon_quant.models.types import KBar, Quote, StockInfo, Candidate
from dragon_quant.scorers import evaluate, rank_verdicts
from dragon_quant.scorers import registry as R
from dragon_quant.scorers.base import (
    clip, desc_rank_score, common_minute_axis, gain_curve)
from dragon_quant.scorers import leadership, liquidity, drive, anti_drop


def _min_bars(pre, pcts, start="2026-06-19 09:30"):
    """按累计涨幅(%)序列构造当日1分K（open=上一分钟close，首根open=pre）。"""
    base = int(datetime.strptime(start, "%Y-%m-%d %H:%M").replace(tzinfo=CHINA_TZ).timestamp())
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
        self.cache = DataCache(cache_dir="")
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
        cache = DataCache(cache_dir="")
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
        cache = DataCache(cache_dir="")
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
        cache = DataCache(cache_dir="")
        # 板块第1分钟就先拉起来，个股拖到第4分钟才跟 → 个股脉冲前板块已抢跑
        sector = _min_bars(100.0, [0, 0.4, 0.7, 0.9, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        stock = _min_bars(10.0, [0, 0, 0, 0, 4.0, 7.0, 10.0, 10.0, 10.0, 10.0])
        s, d = drive._lead_sector(stock, sector)
        self.assertEqual(d["n_lead"], 0)
        self.assertEqual(s, 0.0)

    def test_lead_sector_records_lead_event_details(self):
        stock = _min_bars(10.0, [0, 0, 0, 0, 1.5, 3.5, 4.0, 4.0, 4.0, 4.0])
        sector = _min_bars(100.0, [0, 0, 0, 0, 0, 0.05, 0.45, 0.50, 0.50, 0.50])

        s, d = drive._lead_sector(stock, sector)

        self.assertGreater(s, 0)
        self.assertEqual(d["n_lead"], 1)
        event = d["lead_events"][0]
        self.assertEqual(event["event_time"], "09:34")
        self.assertEqual(d["n_follow"], 0)
        self.assertIn("stock_gain_pct", event)
        self.assertIn("sector_gain_pct", event)
        self.assertGreater(event["stock_gain_pct"], 0)
        self.assertGreater(event["sector_gain_pct"], 0)

    def test_lead_sector_records_follow_event_details(self):
        sector = _min_bars(100.0, [0, 0, 0, 0.4, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7])
        stock = _min_bars(10.0, [0, 0, 0, 0, 0, 3.2, 4.0, 4.0, 4.0, 4.0])

        s, d = drive._lead_sector(stock, sector)

        self.assertEqual(s, 0.0)
        self.assertGreaterEqual(d["n_follow"], 1)
        event = d["follow_events"][0]
        self.assertEqual(event["sector_event_time"], "09:33")
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
        cache = DataCache(cache_dir="")
        stock = _min_bars(10.0, [0, 1.5, 4.2, 7, 10, 10, 10, 10, 10, 10])
        cache.set("kline:1min:600001", stock)
        sector = _min_bars(100.0, [0, 0, 0.1, 0.4, 0.6, 0.6, 0.5, 0.5, 0.5, 0.5])
        cache.set("kline:1min:sector:BK1", sector)
        if market_dip:
            mkt = _min_bars(3000.0, [0, -0.2, -0.6, -0.8, -0.5, -0.2, 0, 0.1, 0.2, 0.3])
        else:
            mkt = _min_bars(3000.0, [0] * 10)
        cache.set("kline:1min:SH000001", mkt)
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


class TestScoringRegressions(unittest.TestCase):

    def setUp(self):
        self.cache = DataCache(cache_dir="")

    def test_stable_seal_uses_last_reseal(self):
        bars = _min_bars(10, [0, 10, 8, 8, 10, 10])
        self.assertEqual(drive._first_seal_minute(bars, 11), bars[4].timestamp // 60000)
        self.assertIsNone(drive._first_seal_minute(_min_bars(10, [10, 8]), 11))

    def test_early_seal_ties_and_candidate_scope(self):
        bars = _min_bars(10, [0, 10, 10])
        comps = [StockInfo(code=c, name=c) for c in ("600001", "600002", "300001")]
        quotes = {s.code: _quote(s.code, 10, 11) for s in comps}
        for s in comps:
            self.cache.set(f"kline:1min:{s.code}", bars)
        pool = [Candidate(code=s.code, name=s.name, primary_sector="S") for s in comps[:2]]
        for code in ("600001", "600002"):
            score, detail = drive._early_seal(code, self.cache, "S", comps, quotes, pool)
            self.assertEqual((score, detail["rank"], detail["pool_size"]), (50, 1, 2))

    def test_missing_and_flat_drive_are_degraded(self):
        score, detail = drive._early_seal("600001", self.cache, "S", [], {})
        self.assertEqual(score, R.DRIVE_NEUTRAL)
        self.assertTrue(detail["degraded"])
        score, detail = drive._lead_sector(_min_bars(10, [10] * 10), _min_bars(100, [0] * 10))
        self.assertEqual(score, R.DRIVE_NEUTRAL)
        self.assertTrue(detail["degraded"])

    def test_simultaneous_pulse_is_neither_lead_nor_follow(self):
        stock = _min_bars(10, [0, 0, 0, 0, 1.5, 3.5, 4, 4, 4, 4])
        sector = _min_bars(100, [0, 0, 0, 0, .05, .45, .5, .5, .5, .5])
        score, detail = drive._lead_sector(stock, sector)
        self.assertEqual((score, detail["n_lead"], detail["n_follow"]), (0, 0, 0))

    def test_pulse_selects_local_maximum(self):
        pulses = drive._pulses([0, 0, 0, 0, .015, .035, .04, .04], 3, .03)
        self.assertEqual(len(pulses), 1)
        self.assertEqual(pulses[0]["trigger"], 6)

    def test_bonus_excludes_first_touch_and_later_minutes(self):
        stock = _min_bars(10, [0, 0, 0, 1, 4, 7, 10, 8, 10, 10])
        sector = _min_bars(100, [0, 0, 0, 0, .1, .5, .6, 1, 2, 3])
        with patch.object(drive, "_corr_bonus", return_value=0) as corr:
            drive._lead_sector(stock, sector, 11)
        self.assertEqual(len(corr.call_args.args[0]), 6)

    def test_strong_voice_excludes_exactly_three_percent(self):
        comps = [StockInfo("600001", "A")]
        _, detail = drive._voice(comps, {"600001": _quote("600001", 3, 11)})
        self.assertEqual(detail["n_strong"], 0)

    def test_non_limit_bid_is_not_a_seal(self):
        q = _quote("600001", 1, 11, bid1=50000)
        q.price = q.bid1_price = 10.1
        self.cache.set("quotes:batch", [q])
        self.cache.set("kline:1min:600001", _min_bars(10, [1] * 10))
        result = liquidity.score("600001", self.cache)
        self.assertEqual(result.details["s_seal"], 0)

    def test_invalid_limit_bid_and_missing_minutes_degrade(self):
        q = _quote("600001", 10, 11, bid1=50000)
        q.bid1_price = 10
        self.cache.set("quotes:batch", [q])
        result = liquidity.score("600001", self.cache)
        self.assertEqual(result.details["s_seal_strength"], R.SEAL_NEUTRAL)
        self.assertEqual(result.details["s_seal_stable"], R.SEAL_NEUTRAL)
        self.assertTrue(result.details["degraded"])

    def test_intraminute_touch_and_reopen_counts(self):
        bars = _min_bars(10, [9])
        bars[0].high = 11
        self.assertEqual(liquidity._count_open(bars, 11), 1)
        bars = _min_bars(10, [10, 8, 7, 10, 9])
        self.assertEqual(liquidity._count_open(bars, 11), 2)

    def test_flat_stock_or_flat_base_has_no_rebound_reward(self):
        market = [0, 0, 0, -.01, -.008, -.005, 0]
        self.assertEqual(anti_drop._rebound(market, [0] * 7, 3), 0)
        self.assertEqual(anti_drop._rebound([0, 0, 0, -.01, -.01, -.01, -.01],
                                          [0, 0, 0, 0, .01, .02, .03], 3), 0)

    def test_real_early_rebound_and_short_window(self):
        market = [0, -.002, -.005, -.01, -.008, -.005, 0]
        stock = [0, -.001, -.003, 0, .01, .02, .03]
        self.assertGreater(anti_drop._rebound(market, stock, 3), 40)
        self.assertEqual(anti_drop._rebound(market[:-1], stock[:-1], 3), 0)
        self.assertIn("不足", anti_drop._rebound_result(market[:-1], stock[:-1], 3)[1])

    def test_flat_bottom_uses_last_minimum(self):
        market = [0, 0, 0, -.01, -.008, -.005, 0]
        score = anti_drop._rebound(market, [0, 0, 0, 0, .01, .02, .03], 3)
        self.assertEqual(score, 40)

    def test_minute_windows_do_not_cross_lunch(self):
        market = _min_bars(100, [0, 0, 0], "2026-06-19 11:28") + _min_bars(100, [-1, -1, -1], "2026-06-19 13:00")
        stock = _min_bars(10, [0, 0, 0], "2026-06-19 11:28") + _min_bars(10, [1, 1, 1], "2026-06-19 13:00")
        _, detail = anti_drop._antidrop_vs(market, stock)
        self.assertTrue(detail["no_dip"])
        stock = _min_bars(10, [0, 0, 0], "2026-06-19 11:28") + _min_bars(10, [4, 4, 4], "2026-06-19 13:00")
        _, detail = drive._lead_sector(stock, market)
        self.assertEqual(detail["n_thrust"], 0)

    def test_all_scoring_errors_reject(self):
        def fail(*args, **kwargs):
            raise RuntimeError("synthetic failure")
        with patch.dict(aggregator._SCORERS, {dim: fail for dim in R.DIM_WEIGHTS}):
            result = evaluate("600001", self.cache)
        self.assertFalse(result.is_true_dragon)
        self.assertIn("评分异常", result.reject_reason)
        self.assertEqual(result.dims["absorption"].score, 50)

    def test_low_or_failed_absorption_does_not_veto(self):
        from dragon_quant.models.types import ScoreResult
        def passing(*args, **kwargs):
            return ScoreResult("test", 80, 0)
        def fail(*args, **kwargs):
            raise RuntimeError("synthetic failure")
        for fn in (fail, lambda *a, **kw: ScoreResult("absorption", 0, .1)):
            with patch.dict(aggregator._SCORERS, {dim: passing for dim in R.DIM_WEIGHTS}):
                with patch.dict(aggregator._SCORERS, {"absorption": fn}):
                    result = evaluate("600001", self.cache)
            self.assertTrue(result.is_true_dragon)

    def test_rank_clears_stale_rejected_rank(self):
        from dragon_quant.scorers.base import DragonVerdict
        rejected = DragonVerdict("A", False, 99, rank=1)
        passed = DragonVerdict("B", True, 60)
        rank_verdicts([rejected, passed])
        self.assertIsNone(rejected.rank)
        self.assertEqual(passed.rank, 1)

    def test_leadership_deduplicates_primary_sector_samples(self):
        a = Candidate("A", "A", primary_sector="S", board_count=1, fived_pct=20)
        b = Candidate("B", "B", primary_sector="T", concepts=["S"], board_count=5, fived_pct=30)
        result = leadership.score("A", self.cache, primary_sector="S", candidate_pool=[a, a, b])
        self.assertEqual(result.details["pct_n"], 1)
        self.assertEqual(result.details["b_max"], 1)


class TestAbsorptionEvents(unittest.TestCase):

    def bars(self, pcts, start="2026-06-19 09:35"):
        bars = _min_bars(100, pcts, start)
        ts = bars[0].timestamp
        for i, bar in enumerate(bars):
            bar.timestamp = ts + i * 300000
        return bars

    def test_gradual_drop_is_valid_and_same_lead_not_duplicated(self):
        target = self.bars([0, .05, .15, .25, .35, .45])
        other = self.bars([0, -.1, -.2, -.3, -.4, -.5])
        events = absorption._detect_events(target, {"A": other, "B": other}, {})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["fleeing_count"], 2)

    def test_previous_window_match_is_counted_once_per_sector(self):
        target = self.bars([0, 0, .1, .2, .3, .4, .5])
        other = self.bars([0, -.1, -.2, -.3, -.4, -.5, -.6])
        events = absorption._detect_events(target, {"A": other, "B": other}, {})
        event = next(e for e in events if e["start_bar"] == 1)
        self.assertEqual(event["fleeing_count"], 2)
        self.assertEqual(len({s["code"] for s in event["fleeing_sectors"]}), 2)

    def test_forward_window_cannot_cross_lunch(self):
        target = self.bars([0, 0, .2, .4, .6, .8], "2026-06-19 13:05")
        other = self.bars([0, -.1, -.2, -.3, -.4, -.5], "2026-06-19 13:05")
        morning = self.bars([0], "2026-06-19 11:30")[0]
        self.assertIsNone(absorption._wret_opt([morning] + other, 0, 5))
        self.assertEqual(len(absorption._detect_events(target, {"A": other, "B": other}, {})), 1)

    def test_late_fleeing_sector_does_not_satisfy_breadth(self):
        target = self.bars([0, 0, .2, .4, .6, .8])
        early = self.bars([0, -.6, -.7, -.8, -.9, -1])
        late = self.bars([0, 0, 0, -.6, -.7, -.8])
        self.assertEqual(absorption._detect_events(target, {"A": early, "B": late}, {}), [])

    def test_cross_session_or_missing_bars_are_rejected(self):
        for gap in (5 * 60000, 90 * 60000, 18 * 3600000):
            with self.subTest(gap=gap):
                target = self.bars([0, 0, .2, .4, .6, .8])
                other = self.bars([0, -.6, -.7, -.8, -.9, -1])
                for bars in (target, other):
                    for bar in bars[4:]:
                        bar.timestamp += gap
                self.assertEqual(absorption._detect_events(target, {"A": other, "B": other}, {}), [])

    def test_missing_opponent_middle_bar_is_not_interpolated(self):
        target = self.bars([0, .05, .15, .25, .35, .45])
        other = self.bars([0, -.1, -.2, -.3, -.4, -.5])
        incomplete = other[:2] + other[3:]
        self.assertEqual(absorption._detect_events(target, {"A": other, "B": incomplete}, {}), [])

    def test_no_signal_has_explicit_fallback(self):
        cache = DataCache(cache_dir="")
        bars = self.bars([0] * 6)
        for sector in ("S", "A", "B"):
            cache.set(f"kline:5min:sector:{sector}", bars)
        result = absorption.score("x", cache, primary_sector="S", all_sector_codes=["A", "B"])
        self.assertEqual(result.score, R.ABS_NEUTRAL)
        self.assertTrue(result.details["fallback"])

    def test_only_last_ten_dates_are_used(self):
        bars = [self.bars([0], start=f"2026-06-{day:02d} 09:35")[0] for day in range(1, 12)]
        dates = absorption._last_n_dates(bars, 10)
        self.assertEqual(len(dates), 10)
        self.assertNotIn(datetime(2026, 6, 1).date(), dates)


class TestAbsorptionAggregation(unittest.TestCase):

    def event(self, start="2026-06-19 09:35", target=3.0, drop=-1.0, count=10, universe=10, drawdown=0.0):
        ts = int(datetime.strptime(start, "%Y-%m-%d %H:%M").replace(tzinfo=CHINA_TZ).timestamp() * 1000)
        return {"start_timestamp": ts, "end_timestamp": ts + 25 * 60000,
                "target_pct": target, "fleeing_avg_drop": drop, "fleeing_count": count,
                "sector_universe": universe, "drawdown_ratio": drawdown}

    def test_duplicate_windows_do_not_increase_score(self):
        event = self.event(target=1.5)
        dates = [datetime(2026, 6, 19).date()]
        single, _ = absorption._aggregate_events([event], dates)
        duplicated, details = absorption._aggregate_events([event] * 10, dates)
        self.assertEqual(single, duplicated)
        self.assertEqual(details["raw_event_count"], 10)
        self.assertEqual(details["event_count"], 1)
        self.assertEqual(details["selected_event_count"], 1)
        self.assertNotIn("multi_event_bonus", details)

    def test_chain_overlaps_merge_and_use_strongest_representative(self):
        events = [self.event("2026-06-19 09:35", target=1),
                  self.event("2026-06-19 09:55", target=2),
                  self.event("2026-06-19 10:15", target=1.5)]
        score, details = absorption._aggregate_events(list(reversed(events)), [datetime(2026, 6, 19).date()])
        self.assertEqual(details["event_count"], 1)
        self.assertEqual(details["best_event"]["start_timestamp"], events[1]["start_timestamp"])
        self.assertEqual(details["best_event"]["window_count"], 3)
        self.assertAlmostEqual(score, absorption._score_event(events[1]))

    def test_representative_tie_prefers_newer_window(self):
        older = self.event()
        newer = self.event("2026-06-19 09:40")
        _, details = absorption._aggregate_events([newer, older], [datetime(2026, 6, 19).date()])
        self.assertEqual(details["best_event"]["start_timestamp"], newer["start_timestamp"])

    def test_distinct_sessions_and_dates_remain_separate(self):
        events = [self.event("2026-06-18 14:35"), self.event("2026-06-19 11:05"),
                  self.event("2026-06-19 13:05")]
        _, details = absorption._aggregate_events(events, [datetime(2026, 6, 18).date(), datetime(2026, 6, 19).date()])
        self.assertEqual(details["event_count"], 3)

    def test_half_life_uses_trading_dates_not_weekend_days(self):
        event = self.event("2026-06-19 09:35")
        dates = [datetime(2026, 6, d).date() for d in (19, 22, 23, 24)]
        score, details = absorption._aggregate_events([event], dates)
        self.assertEqual(score, 75.0)
        self.assertEqual(details["best_event"]["age_trade_days"], 3)
        self.assertEqual(details["best_event"]["recency_weight"], .5)
        monday, _ = absorption._aggregate_events([event], dates[:2])
        self.assertAlmostEqual(monday, round(50 + 50 * 2 ** (-1 / 3), 2))

    def test_weaker_old_evidence_returns_toward_neutral(self):
        event = self.event(target=.4, drop=-.4, count=2)
        dates = [datetime(2026, 6, d).date() for d in (19, 22, 23, 24)]
        score, _ = absorption._aggregate_events([event], dates)
        raw = absorption._score_event(event)
        self.assertLess(raw, score)
        self.assertLess(score, 50)
        self.assertAlmostEqual(score, round(50 + (raw - 50) * .5, 2))

    def test_top_three_mean_uses_adjusted_scores(self):
        events = [self.event("2026-06-19 09:35", target=3),
                  self.event("2026-06-24 09:35", target=1),
                  self.event("2026-06-24 10:35", target=1.5),
                  self.event("2026-06-24 13:05", target=2)]
        score, details = absorption._aggregate_events(events, [datetime(2026, 6, d).date() for d in (19, 22, 23, 24)])
        self.assertEqual(details["event_count"], 4)
        self.assertEqual(details["selected_event_count"], 3)
        self.assertEqual([e["adjusted_score"] for e in details["all_events"]], [90, 85, 80])
        self.assertEqual(score, 85)

    def test_new_extreme_event_can_still_score_one_hundred(self):
        score, _ = absorption._aggregate_events([self.event()], [datetime(2026, 6, 19).date()])
        self.assertEqual(score, 100)
        self.assertEqual(R.DIM_WEIGHTS["absorption"], .1)
        self.assertNotIn("absorption", R.DIM_FLOORS)

    def test_stricter_intensity_scales_monotonically(self):
        weak = absorption._score_event(self.event(target=.4, drop=-3, count=20, universe=20))
        medium = absorption._score_event(self.event(target=1.5, drop=-3, count=20, universe=20))
        strong = absorption._score_event(self.event(target=3, drop=-3, count=20, universe=20))
        self.assertAlmostEqual(weak, 74)
        self.assertLess(weak, medium)
        self.assertLess(medium, strong)
        self.assertLess(absorption._score_event(self.event(drop=-.5)), absorption._score_event(self.event(drop=-1)))

    def test_score_integrates_detection_and_deduplication(self):
        fixture = TestAbsorptionEvents()
        cache = DataCache(cache_dir="")
        cache.set("kline:5min:sector:S", fixture.bars([0, .05, .15, .25, .35, .45, .55]))
        for sector in ("A", "B"):
            cache.set(f"kline:5min:sector:{sector}", fixture.bars([0, -.1, -.2, -.3, -.4, -.5, -.6]))
        result = absorption.score("x", cache, primary_sector="S", all_sector_codes=["A", "B"])
        self.assertEqual(result.details["raw_event_count"], 2)
        self.assertEqual(result.details["event_count"], 1)
        self.assertEqual(result.score, round(result.details["best_event"]["adjusted_score"], 2))


if __name__ == "__main__":
    unittest.main()
