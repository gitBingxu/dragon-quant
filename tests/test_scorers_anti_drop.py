"""
tests for dragon_quant.scorers.anti_drop — 抗跌性评分器
"""
import time
import unittest
from dragon_quant.models.types import KBar
from dragon_quant.scorers.anti_drop import (
    _find_matching_index,
    _ensure_asc,
    _relative_retreat,
    _intraday_hold,
    _rebound_by_date,
    _rebound,
    _consecutive_plunge_bonus_by_date,
    _consecutive_plunge_bonus,
)


def _make_kbar(ts, pct, open_px=10.0, high_px=11.0, low_px=9.5, close_px=10.5):
    return KBar(timestamp=ts, volume=1e6, open=open_px, high=high_px,
                low=low_px, close=close_px, chg=0.5, pct=pct,
                turnover=5.0, amount=1e7)


class TestFindMatchingIndex(unittest.TestCase):

    def test_match_found(self):
        now = int(time.time() * 1000)
        klines = [_make_kbar(now, 0)]
        idx = _find_matching_index(klines, now)
        self.assertEqual(idx, 0)

    def test_no_match(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        klines = [_make_kbar(now - day, 0)]
        idx = _find_matching_index(klines, now)
        self.assertIsNone(idx)


class TestRelativeRetreat(unittest.TestCase):

    def test_stock_positive(self):
        bar = KBar(timestamp=0, volume=1, open=10, high=11, low=9, close=11,
                   chg=0, pct=5.0, turnover=1, amount=1e6)
        self.assertEqual(_relative_retreat(bar, -2.0), 100.0)

    def test_excess_positive(self):
        bar = KBar(timestamp=0, volume=1, open=10, high=11, low=9, close=10,
                   chg=0, pct=-1.0, turnover=1, amount=1e6)
        score = _relative_retreat(bar, -3.0)
        self.assertGreater(score, 60)

    def test_moderate_drop(self):
        """pct=-1.5 with market=-5 → excess=3.5>0 → 60+3.5/5*40=88"""
        bar = KBar(timestamp=0, volume=1, open=10, high=11, low=9, close=10,
                   chg=0, pct=-1.5, turnover=1, amount=1e6)
        self.assertAlmostEqual(_relative_retreat(bar, -5.0), 88.0, places=2)

    def test_severe_drop(self):
        bar = KBar(timestamp=0, volume=1, open=10, high=11, low=9, close=10,
                   chg=0, pct=-5.0, turnover=1, amount=1e6)
        self.assertEqual(_relative_retreat(bar, -5.0), 0.0)


class TestIntradayHold(unittest.TestCase):

    def test_normal_case(self):
        bar = KBar(timestamp=0, volume=1, open=10.0, high=11.0, low=9.5,
                   close=10.8, chg=0, pct=8.0, turnover=1, amount=1e6)
        score = _intraday_hold(bar, 10.0)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_flat_bar(self):
        bar = KBar(timestamp=0, volume=1, open=10.0, high=10.0, low=10.0,
                   close=10.0, chg=0, pct=0, turnover=1, amount=1e6)
        score = _intraday_hold(bar, 10.0)
        self.assertEqual(score, 50.0)


class TestRebound(unittest.TestCase):

    def test_dive_is_latest(self):
        """跳水是最近一天 — 无次日数据"""
        now = int(time.time() * 1000)
        day = 86400 * 1000
        stock_klines = [_make_kbar(now, 2.0)]
        market_klines = [_make_kbar(now, -1.0)]
        self.assertEqual(_rebound(stock_klines, market_klines, 0, 0), 50.0)

    def test_stock_alone_up(self):
        """按日期对齐：跳水日次日个股涨、大盘不涨 → 100"""
        now = int(time.time() * 1000)
        day = 86400 * 1000

        # 升序：旧→新
        d0 = now - day
        d1 = now
        stock_klines = [_make_kbar(d0, -1.0), _make_kbar(d1, 3.0)]
        market_klines = [_make_kbar(d0, -1.5), _make_kbar(d1, -0.5)]

        stock_klines = _ensure_asc(stock_klines)
        market_klines = _ensure_asc(market_klines)

        market_dates = [
            time.strftime("%Y-%m-%d", time.localtime((d0) / 1000)),
            time.strftime("%Y-%m-%d", time.localtime((d1) / 1000)),
        ]
        market_date_to_idx = {market_dates[0]: 0, market_dates[1]: 1}
        market_date_to_bar = {market_dates[0]: market_klines[0], market_dates[1]: market_klines[1]}
        stock_date_to_bar = {market_dates[0]: stock_klines[0], market_dates[1]: stock_klines[1]}

        # plunge day = d0，next day = d1
        score, note = _rebound_by_date(
            stock_date_to_bar=stock_date_to_bar,
            market_dates=market_dates,
            market_date_to_idx=market_date_to_idx,
            market_date_to_bar=market_date_to_bar,
            plunge_date=market_dates[0],
        )
        self.assertEqual(score, 100.0)
        self.assertEqual(note, "stock_up_market_not_up")


class TestConsecutivePlungeBonus(unittest.TestCase):

    def test_single_plunge(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        stock = [_make_kbar(now - day, -2.0), _make_kbar(now, 3.0)]
        market = [_make_kbar(now - day, -1.5), _make_kbar(now, 0.5)]
        plunge = [(1, 1, "2026-05-14", -1.5)]
        self.assertEqual(_consecutive_plunge_bonus(stock, market, plunge), 0.0)

    def test_two_consecutive_anti_drop(self):
        """连续2日跳水，个股跌幅 << 大盘 → +10分"""
        now = int(time.time() * 1000)
        day = 86400 * 1000
        stock = [
            _make_kbar(now - 1 * day, -0.5, close_px=9.95),
            _make_kbar(now, -0.3, close_px=9.92),
        ]
        market = [
            _make_kbar(now - 1 * day, -2.0, close_px=98),
            _make_kbar(now, -1.5, close_px=96.5),
        ]
        plunge = [(0, 0, "2026-05-14", -2.0), (1, 1, "2026-05-15", -1.5)]
        self.assertGreaterEqual(_consecutive_plunge_bonus(stock, market, plunge), 0)

    def test_two_consecutive_but_stock_drops_hard(self):
        """连续2日跳水，个股也大跌 → 不加分"""
        now = int(time.time() * 1000)
        day = 86400 * 1000
        stock = [
            _make_kbar(now - 1 * day, -3.0, close_px=9.7),
            _make_kbar(now, -2.5, close_px=9.46),
        ]
        market = [
            _make_kbar(now - 1 * day, -2.0, close_px=98),
            _make_kbar(now, -1.5, close_px=96.5),
        ]
        plunge = [(1, 1, "2026-05-15", -1.5), (0, 0, "2026-05-14", -2.0)]
        self.assertEqual(_consecutive_plunge_bonus(stock, market, plunge), 0.0)


class TestConsecutivePlungeBonusByDate(unittest.TestCase):

    def test_bonus_10_when_segment_consecutive_and_anti_drop(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        d0 = now - day
        d1 = now

        date0 = time.strftime("%Y-%m-%d", time.localtime(d0 / 1000))
        date1 = time.strftime("%Y-%m-%d", time.localtime(d1 / 1000))

        stock_date_to_bar = {
            date0: _make_kbar(d0, -0.3, close_px=9.97),
            date1: _make_kbar(d1, -0.2, close_px=9.95),
        }
        market_date_to_bar = {
            date0: _make_kbar(d0, -2.0, close_px=98),
            date1: _make_kbar(d1, -1.5, close_px=96.5),
        }
        plunge_days = [
            {"date": date0, "m_idx": 0, "s_idx": 0, "market_pct": -2.0, "stock_pct": -0.3},
            {"date": date1, "m_idx": 1, "s_idx": 1, "market_pct": -1.5, "stock_pct": -0.2},
        ]
        # 由于函数按 m_idx 从近到远寻找连续段，需保证最近日 m_idx 更大
        bonus = _consecutive_plunge_bonus_by_date(
            plunge_days=plunge_days,
            stock_date_to_bar=stock_date_to_bar,
            market_date_to_bar=market_date_to_bar,
        )
        self.assertEqual(bonus, 10.0)

    def test_break_segment_when_stock_missing_day(self):
        """市场连续跳水，但个股缺失其中一天 → 断段，不加分"""
        now = int(time.time() * 1000)
        day = 86400 * 1000
        d0 = now - day
        d1 = now
        date0 = time.strftime("%Y-%m-%d", time.localtime(d0 / 1000))
        date1 = time.strftime("%Y-%m-%d", time.localtime(d1 / 1000))

        stock_date_to_bar = {
            date0: _make_kbar(d0, -0.3, close_px=9.97),
            # date1 缺失
        }
        market_date_to_bar = {
            date0: _make_kbar(d0, -2.0, close_px=98),
            date1: _make_kbar(d1, -1.5, close_px=96.5),
        }
        plunge_days = [
            {"date": date1, "m_idx": 1, "s_idx": 0, "market_pct": -1.5, "stock_pct": 0.0},
            {"date": date0, "m_idx": 0, "s_idx": 0, "market_pct": -2.0, "stock_pct": -0.3},
        ]
        bonus = _consecutive_plunge_bonus_by_date(
            plunge_days=plunge_days,
            stock_date_to_bar=stock_date_to_bar,
            market_date_to_bar=market_date_to_bar,
        )
        self.assertEqual(bonus, 0.0)


class TestEnsureAsc(unittest.TestCase):

    def test_ensure_asc_sorts_desc(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        klines = [_make_kbar(now, 0.0), _make_kbar(now - day, 0.0)]
        asc = _ensure_asc(klines)
        self.assertLessEqual(asc[0].timestamp, asc[1].timestamp)


if __name__ == "__main__":
    unittest.main()
