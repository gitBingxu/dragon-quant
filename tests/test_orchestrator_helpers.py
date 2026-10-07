"""
tests for orchestrator helper functions:
  _is_valid_candidate, _compute_consecutive_boards, _compute_5day_return, _to_serializable
"""
import time
import unittest
import io
import json
import sqlite3
import tempfile
from pathlib import Path
from contextlib import ExitStack, redirect_stdout
from unittest.mock import Mock, patch
from dragon_quant import orchestrator as orch
from dragon_quant.cache.data_cache import DataCache
from dragon_quant.models.types import SectorPerformance
from dragon_quant.storage import db
from tests.test_scorers import _min_bars, _quote
from dragon_quant.models.types import StockInfo, KBar, Candidate
from dragon_quant.orchestrator import (
    _is_valid_candidate,
    _compute_consecutive_boards,
    _compute_5day_return,
    _to_serializable,
)


def _make_kbar(ts, pct, close=10.0):
    return KBar(timestamp=ts, volume=1e6, open=close, high=close,
                low=close, close=close, chg=0, pct=pct, turnover=1.0, amount=1e7)


class TestIsValidCandidate(unittest.TestCase):

    def test_normal(self):
        s = StockInfo(code="600519", name="贵州茅台")
        self.assertTrue(_is_valid_candidate(s))

    def test_st_filtered(self):
        s = StockInfo(code="000620", name="*ST新华联")
        self.assertFalse(_is_valid_candidate(s))

    def test_star_board_filtered(self):
        s = StockInfo(code="688981", name="中芯国际")
        self.assertFalse(_is_valid_candidate(s))

    def test_chi_next_filtered(self):
        s = StockInfo(code="300750", name="宁德时代")
        self.assertFalse(_is_valid_candidate(s))

    def test_beijing_exchange_8(self):
        s = StockInfo(code="834021", name="北交所某股")
        self.assertFalse(_is_valid_candidate(s))

    def test_beijing_exchange_92(self):
        s = StockInfo(code="920100", name="三协电机")
        self.assertFalse(_is_valid_candidate(s))

    def test_empty_code(self):
        s = StockInfo(code="", name="无名")
        self.assertFalse(_is_valid_candidate(s))

    def test_empty_name(self):
        s = StockInfo(code="600519", name="")
        self.assertTrue(_is_valid_candidate(s))


class TestComputeConsecutiveBoards(unittest.TestCase):

    def test_empty(self):
        self.assertEqual(_compute_consecutive_boards([]), 0)

    def test_single_limit_up(self):
        now = int(time.time() * 1000)
        klines = [_make_kbar(now, 10.0)]
        self.assertEqual(_compute_consecutive_boards(klines), 1)

    def test_single_non_limit(self):
        now = int(time.time() * 1000)
        klines = [_make_kbar(now, 5.0)]
        self.assertEqual(_compute_consecutive_boards(klines), 0)

    def test_three_consecutive(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        klines = [
            _make_kbar(now - 2 * day, 10.0),
            _make_kbar(now - 1 * day, 10.02),
            _make_kbar(now, 9.95),
        ]
        self.assertEqual(_compute_consecutive_boards(klines), 3)

    def test_broken_by_non_limit(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        klines = [
            _make_kbar(now - 3 * day, 10.0),
            _make_kbar(now - 2 * day, 10.0),
            _make_kbar(now - 1 * day, 2.0),
            _make_kbar(now, 10.0),
        ]
        self.assertEqual(_compute_consecutive_boards(klines), 1)

    def test_pct_zero_breaks(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        klines = [
            _make_kbar(now - 2 * day, 10.0),
            _make_kbar(now - 1 * day, 0.0),
            _make_kbar(now, 10.0),
        ]
        self.assertEqual(_compute_consecutive_boards(klines), 1)


class TestCompute5DayReturn(unittest.TestCase):

    def test_six_bars_normal(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        klines = [
            _make_kbar(now - 5 * day, 0, close=10.0),
            _make_kbar(now - 4 * day, 0, close=10.5),
            _make_kbar(now - 3 * day, 0, close=11.0),
            _make_kbar(now - 2 * day, 0, close=10.8),
            _make_kbar(now - 1 * day, 0, close=11.5),
            _make_kbar(now, 0, close=12.0),
        ]
        expected = (12.0 / 10.0 - 1) * 100
        self.assertAlmostEqual(_compute_5day_return(klines), expected, places=4)

    def test_less_than_six_bars(self):
        now = int(time.time() * 1000)
        klines = [_make_kbar(now, 5.0, close=10.0)]
        self.assertEqual(_compute_5day_return(klines), 0.0)

    def test_zero_close_guard(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        klines = [
            _make_kbar(now - 5 * day, 0, close=0.0),
            _make_kbar(now - 4 * day, 0, close=10.0),
            _make_kbar(now - 3 * day, 0, close=10.0),
            _make_kbar(now - 2 * day, 0, close=10.0),
            _make_kbar(now - 1 * day, 0, close=10.0),
            _make_kbar(now, 0, close=12.0),
        ]
        self.assertEqual(_compute_5day_return(klines), 0.0)

    def test_negative_return(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        klines = [
            _make_kbar(now - 5 * day, 0, close=10.0),
            _make_kbar(now - 4 * day, 0, close=9.5),
            _make_kbar(now - 3 * day, 0, close=9.0),
            _make_kbar(now - 2 * day, 0, close=8.5),
            _make_kbar(now - 1 * day, 0, close=8.0),
            _make_kbar(now, 0, close=7.5),
        ]
        expected = (7.5 / 10.0 - 1) * 100
        self.assertAlmostEqual(_compute_5day_return(klines), expected, places=4)
        self.assertLess(_compute_5day_return(klines), 0)


class TestToSerializable(unittest.TestCase):

    def test_dataclass(self):
        s = StockInfo(code="600519", name="茅台")
        d = _to_serializable(s)
        self.assertEqual(d["code"], "600519")
        self.assertEqual(d["name"], "茅台")

    def test_list_of_dataclasses(self):
        klines = [_make_kbar(1000, 5.0), _make_kbar(2000, -2.0)]
        result = _to_serializable(klines)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["pct"], 5.0)

    def test_bytes(self):
        result = _to_serializable(b"hello")
        self.assertEqual(result, "hello")

    def test_dict(self):
        d = {"a": 1, "b": b"world"}
        result = _to_serializable(d)
        self.assertEqual(result["a"], 1)
        self.assertEqual(result["b"], "world")

    def test_primitive(self):
        self.assertEqual(_to_serializable(42), 42)
        self.assertEqual(_to_serializable("hello"), "hello")
        self.assertEqual(_to_serializable(3.14), 3.14)


class TestScanScoringIntegration(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.db_path = str(Path(self.tmp.name) / "test.db")
        self.stack.enter_context(patch.object(db, "_connect", side_effect=lambda: sqlite3.connect(self.db_path)))
        self.stack.enter_context(patch.object(orch, "RESULTS_DIR", Path(self.tmp.name)))
        self.cache = DataCache(cache_dir="")
        self.stack.enter_context(patch.object(orch, "DataCache", return_value=self.cache))
        self.stack.enter_context(patch.object(orch, "_get_trade_date", return_value="2026-09-11"))
        self.stack.enter_context(patch("dragon_quant.utils.trading.build_trade_calendar", return_value={"2026-09-11"}))
        self.ths, self.xq, self.tx = Mock(), Mock(), Mock()
        self.ths.get_sector_ranking.return_value = [SectorPerformance("881001", "S", 2, 2)]
        self.ths.get_sector_components.return_value = [StockInfo("600001", "Rejected", pct=10), StockInfo("600002", "Passed", pct=10)]
        self.ths.get_sector_5min_kline_history.return_value = []
        self.ths.get_sector_1min_kline.return_value = _min_bars(100, [0, 1])
        self.xq.get_kline.return_value = _min_bars(10, [0, 1, 2, 3, 4, 10])
        self.xq.get_minute_kline.return_value = _min_bars(10, [0, 10])
        self.tx.batch_get_quotes.side_effect = lambda codes: [_quote(c, 10, 11) for c in codes]
        self.stack.enter_context(patch.object(orch, "create_providers", return_value={"ths": self.ths, "xueqiu": self.xq, "tencent": self.tx}))
        limiter = Mock()
        limiter.submit.side_effect = lambda provider, endpoint, fn: fn()
        self.stack.enter_context(patch.object(orch, "RateLimiter", return_value=limiter))
        self.pass_codes = {"600002"}
        self.real_score_one = orch._score_one
        self.stack.enter_context(patch.object(orch, "_score_one", side_effect=self.fake_score))

    def fake_score(self, cand, *args):
        passed = cand.code in self.pass_codes
        return {"code": cand.code, "name": cand.name, "concepts": cand.concepts,
                "board_count": cand.board_count, "primary_sector": cand.primary_sector,
                "primary_sector_name": "S", "composite_score": 60 if passed else 80,
                "dimensions": {}, "is_true_dragon": passed,
                "reject_reason": None if passed else "drive below floor"}

    def rows(self, table):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(f"SELECT code, rank, is_true_dragon FROM {table} ORDER BY code").fetchall()

    def test_real_scorers_run_through_full_scan(self):
        self.ths.get_sector_components.return_value = [StockInfo("600001", "Dragon", pct=10)]
        self.ths.get_sector_1min_kline.return_value = _min_bars(100, [0, 0, 0, 0, .1, .5, .6, .6, .6, .6])
        self.xq.get_minute_kline.side_effect = lambda code: (
            _min_bars(3000, [0] * 10) if code == "SH000001"
            else _min_bars(10, [0, 0, 0, 1, 4, 7, 10, 10, 10, 10]))
        self.tx.batch_get_quotes.side_effect = lambda codes: [_quote(c, 10, 11, bid1=50000, tr=15) for c in codes]
        with patch.object(orch, "_score_one", side_effect=self.real_score_one):
            output = orch.scan(top_n=1, verbose=False, force=True)
        result = output["ranking"][0]
        self.assertTrue(result["is_true_dragon"])
        self.assertEqual(result["rank"], 1)
        self.assertEqual(set(result["dimensions"]), {"drive", "leadership", "anti_drop", "liquidity", "absorption"})
        self.assertEqual(self.rows("dragons_v2"), [("600001", 1, 1)])

    def test_veto_never_occupies_top_slot_and_all_details_are_saved(self):
        output = orch.scan(top_n=1, verbose=False, force=True)
        self.assertEqual(self.rows("dragons_v2"), [("600002", 1, 1)])
        self.assertEqual(self.rows("scan_stocks_v2"), [("600001", None, 0), ("600002", 1, 1)])
        self.assertEqual(len(output["ranking"]), 2)
        self.assertNotIn("Rejected", output["report_text"])
        self.assertIn("Passed", output["report_text"])
        self.xq.get_minute_kline.assert_any_call("SH000001")
        self.assertIsNotNone(self.cache.get("kline:1min:SH000001"))
        with redirect_stdout(io.StringIO()) as stream:
            orch._print_cached_output(output, 1)
        self.assertIn("Passed", stream.getvalue())
        self.assertNotIn("Rejected", stream.getvalue())
        with sqlite3.connect(self.db_path) as conn:
            raw = json.loads(conn.execute("SELECT raw_output FROM scans_v2").fetchone()[0])
        self.assertEqual(raw["ranking"], output["ranking"])

    def test_no_dragon_still_persists_scan_and_reasons(self):
        self.pass_codes.clear()
        output = orch.scan(top_n=1, verbose=False, force=True)
        self.assertEqual(self.rows("dragons_v2"), [])
        self.assertEqual(len(self.rows("scan_stocks_v2")), 2)
        self.assertIn("本轮无符合条件的真龙", output["report_text"])
        with redirect_stdout(io.StringIO()) as stream:
            orch._print_cached_output(output, 1)
        self.assertIn("本轮无符合条件的真龙", stream.getvalue())

    def test_quotes_over_two_hundred_are_sorted_and_complete(self):
        stocks = [StockInfo(str(600000 + i), str(i), pct=10 if i == 204 else 0) for i in range(205)]
        self.ths.get_sector_components.return_value = list(reversed(stocks))
        orch.scan(top_n=1, verbose=False, force=True)
        batches = [call.args[0] for call in self.tx.batch_get_quotes.call_args_list]
        self.assertEqual([len(batch) for batch in batches], [200, 5])
        self.assertEqual([c for batch in batches for c in batch], sorted(s.code for s in stocks))
        self.assertEqual(len(self.cache.get("quotes:batch")), 205)

    def test_five_day_dedupe_preserves_original_rank(self):
        self.pass_codes = {"600001", "600002"}
        with patch.object(db, "get_last_entry_with_rank", side_effect=lambda code, **kw: ("2000-01-01", 1) if code == "600001" else None), patch("dragon_quant.utils.trading.trade_days_between", return_value=1):
            orch.scan(top_n=2, verbose=False, force=True)
        self.assertEqual(self.rows("dragons_v2"), [("600002", 2, 1)])

    def test_rebuild_does_not_promote_rejected_or_beyond_topn(self):
        self.pass_codes = {"600002", "600003"}
        self.ths.get_sector_components.return_value.append(StockInfo("600003", "Passed2", pct=10))
        output = orch.scan(top_n=1, verbose=False, force=True)
        day = output["timestamp"][:8]
        date = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        db.rebuild_dragons_for_date(date, version="test", calendar={date}, apply_5day_gate=False)
        self.assertEqual(self.rows("dragons_v2"), [("600002", 1, 1)])


if __name__ == "__main__":
    unittest.main()
