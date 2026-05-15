"""
tests for orchestrator helper functions:
  _is_valid_candidate, _compute_consecutive_boards, _compute_5day_return, _to_serializable
"""
import time
import unittest
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


if __name__ == "__main__":
    unittest.main()
