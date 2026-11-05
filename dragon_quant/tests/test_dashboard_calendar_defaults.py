"""默认日期纯函数 ``calendar_defaults``（方案 §6 状态机）。"""

from __future__ import annotations

import unittest

from web_ui.backend.presenters.calendar_defaults import resolve_default_date


class TestPrepDefaults(unittest.TestCase):
    """复盘默认日 = 最近有复盘数据的日期（快照优先），不按 K 线推断。"""

    def test_prefers_latest_snapshot_over_newer_asset_date(self):
        result = resolve_default_date(
            page="prep",
            bar_dates=["2026-10-29", "2026-10-30"],
            reference_date="2026-10-30",
            latest_record_date="2026-10-29",
            latest_snapshot_date="2026-10-28",
            latest_asset_date="2026-10-29")
        self.assertEqual(result["default_date"], "2026-10-28")
        self.assertEqual(result["default_date_source"], "latest_snapshot")
        self.assertEqual(result["calendar_basis"], "record_dates")
        self.assertFalse(result["degraded"])

    def test_falls_back_to_latest_real_asset_date_without_snapshot(self):
        result = resolve_default_date(
            page="prep",
            bar_dates=["2026-10-29", "2026-10-30"],
            reference_date="2026-10-30",
            latest_record_date="2026-10-29",
            latest_asset_date="2026-10-29")
        self.assertEqual(result["default_date"], "2026-10-29")
        self.assertEqual(result["default_date_source"], "record_date_fallback")

    def test_uses_record_date_even_when_bar_is_older(self):
        # 旧实现会取「本地前一交易日」；新语义必须取真实复盘记录日。
        result = resolve_default_date(
            page="prep",
            bar_dates=["2026-10-29", "2026-10-30"],
            reference_date="2026-10-30",
            latest_record_date="2026-10-30",
            latest_snapshot_date="2026-10-30")
        self.assertEqual(result["default_date"], "2026-10-30")
        self.assertEqual(result["default_date_source"], "latest_snapshot")
        self.assertNotEqual(result["default_date_source"], "local_bar_previous")

    def test_record_after_reference_is_ignored(self):
        result = resolve_default_date(
            page="prep",
            bar_dates=["2026-10-29", "2026-10-30"],
            reference_date="2026-10-30",
            latest_record_date="2026-11-02",
            latest_snapshot_date="2026-11-02")
        self.assertEqual(result["default_date"], "2026-10-30")
        self.assertEqual(result["default_date_source"], "local_bar_latest")

    def test_no_records_falls_back_to_latest_real_trading_day(self):
        result = resolve_default_date(
            page="prep",
            bar_dates=["2026-10-29", "2026-10-30"],
            reference_date="2026-11-01",
            latest_record_date=None)
        self.assertEqual(result["default_date"], "2026-10-30")
        self.assertEqual(result["default_date_source"], "local_bar_latest")
        self.assertFalse(result["degraded"])

    def test_no_bars_falls_back_to_record_date(self):
        result = resolve_default_date(
            page="prep", bar_dates=[], reference_date="2026-10-30",
            latest_record_date="2026-10-28",
            latest_snapshot_date="2026-10-28")
        self.assertEqual(result["default_date"], "2026-10-28")
        self.assertEqual(result["default_date_source"], "latest_snapshot")
        self.assertEqual(result["calendar_basis"], "record_dates")
        self.assertIsNone(result["calendar_latest_date"])
        self.assertFalse(result["degraded"])

    def test_no_bars_no_record_falls_back_to_today(self):
        result = resolve_default_date(
            page="prep", bar_dates=[], reference_date="2026-10-30",
            latest_record_date=None)
        self.assertEqual(result["default_date"], "2026-10-30")
        self.assertEqual(result["default_date_source"], "today_fallback")
        self.assertEqual(result["calendar_basis"], "record_dates")
        self.assertTrue(result["degraded"])
        self.assertEqual(result["degraded_reason"], "local_bars_missing")


class TestPrepCalendarContract(unittest.TestCase):
    """latest_record_date（公开键）与 default_date（快照优先）互不混淆。"""

    def test_latest_record_date_keeps_max_while_default_prefers_snapshot(self):
        from web_ui.backend.presenters.prep_view import build_calendar

        rows = {
            "2026-11-02": {
                "asset_types": ["dragons", "collection_report"],
                "report": {"dragons": {"status": "ok"}},
                "report_error": None,
                "has_snapshot": True,
            },
            "2026-11-04": {
                "asset_types": ["dragons", "collection_report"],
                "report": {"dragons": {"status": "ok"}},
                "report_error": None,
                "has_snapshot": False,
            },
            "2026-11-05": {
                "asset_types": ["collection_report"],
                "report": {"dragons": {"status": "error"}},
                "report_error": None,
                "has_snapshot": False,
            },
        }
        built = build_calendar(
            rows=rows, start_date="2026-11-01", end_date="2026-11-05",
            today="2026-11-05", review_assets_enabled=True,
            first_snapshot_date="2026-11-02",
        )
        # 公开键保持既有语义：最近真实资产或快照（较新者）。
        self.assertEqual(built["latest_record_date"], "2026-11-04")
        self.assertEqual(built["latest_snapshot_date"], "2026-11-02")
        self.assertEqual(built["latest_asset_date"], "2026-11-04")

        default = resolve_default_date(
            page="prep", bar_dates=["2026-11-04"], reference_date="2026-11-05",
            latest_record_date=built["latest_record_date"],
            latest_snapshot_date=built["latest_snapshot_date"],
            latest_asset_date=built["latest_asset_date"],
        )
        # 默认日仍优先快照；collection_report-only 日不参与。
        self.assertEqual(default["default_date"], "2026-11-02")
        self.assertEqual(default["default_date_source"], "latest_snapshot")


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
