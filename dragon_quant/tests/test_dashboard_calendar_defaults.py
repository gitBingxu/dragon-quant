"""默认日期纯函数 ``calendar_defaults``（方案 §6 状态机）。"""

from __future__ import annotations

import unittest

from web_ui.backend.presenters.calendar_defaults import resolve_default_date


class TestPrepDefaults(unittest.TestCase):
    def test_latest_bar_equals_today_selects_previous(self):
        result = resolve_default_date(
            page="prep",
            bar_dates=["2026-10-29", "2026-10-30"],
            reference_date="2026-10-30",
            latest_record_date="2026-10-30")
        self.assertEqual(result["default_date"], "2026-10-29")
        self.assertEqual(result["default_date_source"], "local_bar_previous")
        self.assertEqual(result["calendar_basis"],
                         "local_stock_daily_bars_union")
        self.assertEqual(result["calendar_latest_date"], "2026-10-30")
        self.assertFalse(result["degraded"])
        self.assertIsNone(result["degraded_reason"])

    def test_latest_bar_before_today_selects_latest(self):
        # 周末 / 当日 bar 未写入：confirmed[-1] 已是前一交易日。
        result = resolve_default_date(
            page="prep",
            bar_dates=["2026-10-29", "2026-10-30"],
            reference_date="2026-11-01",
            latest_record_date=None)
        self.assertEqual(result["default_date"], "2026-10-30")
        self.assertEqual(result["default_date_source"], "local_bar_latest")

    def test_only_one_bar_equal_reference_degrades(self):
        result = resolve_default_date(
            page="prep",
            bar_dates=["2026-10-30", "2026-11-02"],
            reference_date="2026-10-30",
            latest_record_date=None)
        self.assertEqual(result["default_date"], "2026-10-30")
        self.assertEqual(result["default_date_source"], "local_bar_latest")
        self.assertTrue(result["degraded"])
        self.assertEqual(result["degraded_reason"], "no_previous_trading_day")

    def test_bar_dates_after_reference_are_ignored(self):
        result = resolve_default_date(
            page="prep",
            bar_dates=["2026-10-29", "2026-10-30", "2026-11-02"],
            reference_date="2026-10-30",
            latest_record_date=None)
        self.assertEqual(result["default_date"], "2026-10-29")
        self.assertEqual(result["calendar_latest_date"], "2026-10-30")

    def test_no_bars_falls_back_to_record_date(self):
        result = resolve_default_date(
            page="prep", bar_dates=[], reference_date="2026-10-30",
            latest_record_date="2026-10-28")
        self.assertEqual(result["default_date"], "2026-10-28")
        self.assertEqual(result["default_date_source"], "record_date_fallback")
        self.assertEqual(result["calendar_basis"], "record_dates")
        self.assertIsNone(result["calendar_latest_date"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["degraded_reason"], "local_bars_missing")

    def test_no_bars_no_record_falls_back_to_today(self):
        result = resolve_default_date(
            page="prep", bar_dates=[], reference_date="2026-10-30",
            latest_record_date=None)
        self.assertEqual(result["default_date"], "2026-10-30")
        self.assertEqual(result["default_date_source"], "today_fallback")
        self.assertEqual(result["calendar_basis"], "record_dates")
        self.assertTrue(result["degraded"])
        self.assertEqual(result["degraded_reason"], "local_bars_missing")


class TestAuctionDefaults(unittest.TestCase):
    def test_auction_always_uses_latest_confirmed_bar(self):
        result = resolve_default_date(
            page="auction",
            bar_dates=["2026-10-29", "2026-10-30"],
            reference_date="2026-10-30",
            latest_record_date="2026-10-30")
        self.assertEqual(result["default_date"], "2026-10-30")
        self.assertEqual(result["default_date_source"], "local_bar_latest")
        self.assertFalse(result["degraded"])

    def test_auction_weekend_uses_latest(self):
        result = resolve_default_date(
            page="auction",
            bar_dates=["2026-10-30"],
            reference_date="2026-11-01",
            latest_record_date="2026-10-31")
        self.assertEqual(result["default_date"], "2026-10-30")
        self.assertEqual(result["default_date_source"], "local_bar_latest")


if __name__ == "__main__":
    unittest.main()
