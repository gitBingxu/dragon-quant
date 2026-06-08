"""
tests for dragon_quant.scorers.drive — 带动性评分器
"""
import time
import unittest
from dragon_quant.cache.data_cache import DataCache
from dragon_quant.models.types import KBar, StockInfo, Candidate, Quote
from dragon_quant.scorers.drive import (
    _find_limit_up_dates,
    _detect_board_time,
    _is_yiziban,
    _seal_rank_score,
    _early_time_score,
    _gap_score,
    _voice_score,
    _follow_score,
    score,
    DRIVE_SAMPLE_LIMIT,
)


def _make_kbar(ts, pct, open_px=10.0, high_px=11.0, low_px=9.5, close_px=10.5,
               turnover=5.0):
    return KBar(timestamp=ts, volume=1e6, open=open_px, high=high_px,
                low=low_px, close=close_px, chg=0.5, pct=pct,
                turnover=turnover, amount=1e7)


class TestFindLimitUpDates(unittest.TestCase):
    """核心测试: 连板截断修复后验证"""

    def test_single_limit_up(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        klines = [_make_kbar(now, 10.0, close_px=11.0, high_px=11.0)]
        result = _find_limit_up_dates(klines, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["consecutive"], 1)

    def test_three_board_streak(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        klines = [
            _make_kbar(now - 2 * day, 10.0),
            _make_kbar(now - 1 * day, 10.0),
            _make_kbar(now, 10.0),
        ]
        result = _find_limit_up_dates(klines, [])
        self.assertEqual(len(result), 3)
        cons_vals = [r["consecutive"] for r in result]
        self.assertIn(3, cons_vals)

    def test_truncation_fix_streak_behind_gap(self):
        """修复验证: [10,10,10,5,10] → 应有 consecutive=3 的条目"""
        now = int(time.time() * 1000)
        day = 86400 * 1000
        klines = [
            _make_kbar(now - 4 * day, 10.0),
            _make_kbar(now - 3 * day, 10.0),
            _make_kbar(now - 2 * day, 10.0),
            _make_kbar(now - 1 * day, 5.0),
            _make_kbar(now, 10.0),
        ]
        result = _find_limit_up_dates(klines, [])
        cons_vals = [r["consecutive"] for r in result]
        self.assertEqual(max(cons_vals), 3,
                         "max_cons should be 3 after truncation fix")

    def test_no_limit_ups(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        klines = [_make_kbar(now, 5.0), _make_kbar(now - day, -2.0)]
        result = _find_limit_up_dates(klines, [])
        self.assertEqual(len(result), 0)


class TestDetectBoardTime(unittest.TestCase):

    def test_empty_bars(self):
        self.assertIsNone(_detect_board_time([], 0))

    def test_limit_up_price_found(self):
        now = int(time.time() * 1000)
        minute = 60 * 1000
        bars = [
            KBar(timestamp=now, volume=1, open=10.0, high=10.5, low=9.8, close=10.2, chg=0, pct=0, turnover=0, amount=0),
            KBar(timestamp=now + 5 * minute, volume=1, open=10.2, high=10.8, low=10.0, close=10.5, chg=0, pct=0, turnover=0, amount=0),
            KBar(timestamp=now + 10 * minute, volume=1, open=10.5, high=11.0, low=10.3, close=11.0, chg=0, pct=0, turnover=0, amount=0),
        ]
        result = _detect_board_time(bars, now)
        self.assertIsNotNone(result)


class TestIsYiziban(unittest.TestCase):

    def test_yizi(self):
        lu = {"board_time": None, "turnover": 0.5}
        self.assertTrue(_is_yiziban(lu))

    def test_has_board_time(self):
        lu = {"board_time": "09:45", "turnover": 0.5}
        self.assertFalse(_is_yiziban(lu))

    def test_high_turnover(self):
        lu = {"board_time": None, "turnover": 5.0}
        self.assertFalse(_is_yiziban(lu))


class TestSealRankScore(unittest.TestCase):

    def test_first_to_seal(self):
        lu = {"board_time": "09:30", "code": "A"}
        peers = [
            {"code": "B", "board_time": "09:35", "board_timestamp": 9 * 60 + 35},
            {"code": "C", "board_time": "09:40", "board_timestamp": 9 * 60 + 40},
        ]
        score, rank = _seal_rank_score(lu, peers)
        self.assertGreater(score, 90)
        self.assertEqual(rank, 1)

    def test_no_board_time(self):
        lu = {"board_time": None, "code": "A"}
        peers = [{"code": "B", "board_time": "09:35", "board_timestamp": 9 * 60 + 35}]
        score, rank = _seal_rank_score(lu, peers)
        self.assertEqual(score, 50.0)


class TestEarlyTimeScore(unittest.TestCase):

    def test_open_seal(self):
        self.assertAlmostEqual(_early_time_score("09:30"), 100.0)

    def test_morning(self):
        self.assertAlmostEqual(_early_time_score("10:00"), 70.0)

    def test_afternoon(self):
        self.assertAlmostEqual(_early_time_score("14:00"), 10.0)

    def test_none(self):
        self.assertAlmostEqual(_early_time_score(None), 1.0)


class TestGapScore(unittest.TestCase):

    def test_close_gap(self):
        lu = {"board_time": "09:30", "code": "A"}
        peers = [
            {"code": "B", "board_time": "09:32", "board_timestamp": 9 * 60 + 32},
            {"code": "C", "board_time": "09:34", "board_timestamp": 9 * 60 + 34},
        ]
        score, detail = _gap_score(lu, peers)
        self.assertGreater(score, 80)

    def test_no_peers(self):
        lu = {"board_time": "09:30", "code": "A"}
        peers = []
        score, detail = _gap_score(lu, peers)
        self.assertEqual(score, 50.0)

    def test_no_board_time(self):
        lu = {"board_time": None, "code": "A"}
        peers = [{"code": "B", "board_time": "09:35", "board_timestamp": 9 * 60 + 35}]
        score, detail = _gap_score(lu, peers)
        self.assertEqual(score, 50.0)


class TestDriveSampleScoring(unittest.TestCase):

    def test_voice_score_uses_active_sample(self):
        components = []
        for i in range(60):
            pct = 0.5
            if i < 6:
                pct = 10.0
            elif i < DRIVE_SAMPLE_LIMIT:
                pct = 4.0
            components.append(StockInfo(code=f"{i:06d}", name=f"S{i}", pct=pct))

        score_val, raw = _voice_score("BK0001", components, {})
        self.assertEqual(raw["total"], 60)
        self.assertEqual(raw["scoring_total"], DRIVE_SAMPLE_LIMIT)
        self.assertEqual(raw["limit_up"], 6)
        self.assertAlmostEqual(score_val, 100.0)

    def test_follow_score_all_limit_up_gets_full_mark(self):
        components = [StockInfo(code=f"{i:06d}", name=f"S{i}", pct=10.0) for i in range(20)]
        score_val, raw = _follow_score("BK0001", components, {})
        self.assertEqual(raw["scoring_total"], 20)
        self.assertEqual(raw["limit_up"], 20)
        self.assertAlmostEqual(score_val, 100.0)


class TestDriveScoreIntegration(unittest.TestCase):

    def test_score_contains_sample_fields_and_none_board_time_uses_one_point(self):
        cache = DataCache(cache_dir=None)
        now = int(time.time() * 1000)
        day = 86400 * 1000

        cache.set("kline:day:600001", [
            _make_kbar(now - day, 3.0, turnover=3.0),
            _make_kbar(now, 10.0, turnover=5.0),
        ])
        cache.set("kline:1min:600001", [])

        components = [StockInfo(code="600001", name="龙头", pct=10.0)]
        for i in range(1, 55):
            pct = 4.0 if i < 10 else -1.0
            components.append(StockInfo(code=f"60{i:04d}", name=f"跟风{i}", pct=pct))
        cache.set("sector:components:BK0001", components)

        quote = Quote(
            code="600001", name="龙头", price=10.0, prev_close=9.0, open_px=9.1,
            high=10.0, low=9.0, pct=10.0, chg=1.0, turnover_rate=5.0, amplitude=10.0,
            volume=1e6, amount=1e7, market_cap=1e9, float_market_cap=8e8,
            volume_ratio=1.0, pe=10.0, limit_up=10.0, limit_down=8.0, avg_price=9.5,
        )
        cache.set("quotes:batch", [quote])

        candidate_pool = [Candidate(code="600001", name="龙头", concepts=["BK0001"], primary_sector="BK0001")]
        result = score("600001", cache, candidate_pool=candidate_pool, primary_sector="BK0001")

        best_day = result.details["best_day_detail"]
        self.assertEqual(best_day["voice_raw"]["total"], 55)
        self.assertEqual(best_day["voice_raw"]["scoring_total"], DRIVE_SAMPLE_LIMIT)
        self.assertEqual(best_day["follow_raw"]["scoring_total"], DRIVE_SAMPLE_LIMIT)
        self.assertEqual(best_day["board_detail"]["early_score"], 1.0)


if __name__ == "__main__":
    unittest.main()
