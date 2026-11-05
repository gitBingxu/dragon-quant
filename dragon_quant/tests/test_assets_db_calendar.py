"""assets.db 日历/资产只读仓储单测（方案 §3.4 / §9.2，TDD 先 RED）。

覆盖 ``prep_calendar_rows`` / ``auction_calendar_rows`` /
``list_auction_asset_names`` / ``list_stock_daily_bars``：
单连接轻量投影、不读大 payload、库不存在不建库、去重与截断显式。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dragon_quant.infra.assets import db as assets_db


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "assets.db"

    def tearDown(self):
        self._tmp.cleanup()

    def _connect(self, create=True, path=None, *args, **kwargs):
        target = Path(path) if path else self.db_path
        if create is False and not target.exists():
            return None
        return sqlite3.connect(target)

    def _patched(self):
        return patch("dragon_quant.infra.assets.db._connect",
                     side_effect=self._connect)

    def _seed(self):
        assets_db.save_prep_assets(
            "2026-09-10",
            {
                "dragons": {"rows": [{"code": "600001"}]},
                "prev_plan": {"positions": []},
            },
            report={"dragons": {"status": "ok"}},
        )
        assets_db.save_prep_snapshot("2026-09-10", {"version": "0.8"})
        assets_db.save_prep_assets(
            "2026-09-11",
            {"market_breadth": {"rows": [{"code": "600002"}]}},
        )
        assets_db.save_auction_asset(
            "2026-09-11", "600001", "龙头A", items=[{"time": "09:25:00"}])
        assets_db.replace_auction_snapshots(
            "2026-09-11", {"600001": {"code": "600001", "version": "2"}})
        assets_db.replace_auction_snapshots(
            "2026-09-10", {"600002": {"code": "600002", "version": "2"}})


class TestPrepCalendarRows(_Base):
    def test_returns_asset_types_report_and_snapshot(self):
        with self._patched():
            self._seed()
            rows = assets_db.prep_calendar_rows("2026-09-01", "2026-09-30")

        self.assertEqual(set(rows), {"2026-09-10", "2026-09-11"})
        first = rows["2026-09-10"]
        self.assertEqual(set(first["asset_types"]),
                         {"dragons", "prev_plan", "collection_report"})
        self.assertTrue(first["has_snapshot"])
        self.assertEqual(first["report"]["dragons"]["status"], "ok")
        second = rows["2026-09-11"]
        self.assertFalse(second["has_snapshot"])
        self.assertIsNone(second["report"])

    def test_window_is_half_open_boundaries_inclusive(self):
        with self._patched():
            self._seed()
            rows = assets_db.prep_calendar_rows("2026-09-11", "2026-09-11")
        self.assertEqual(set(rows), {"2026-09-11"})

    def test_corrupt_report_row_is_isolated(self):
        with self._patched():
            self._seed()
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    "UPDATE prep_assets SET payload_json='{not json' "
                    "WHERE trade_date='2026-09-10' AND asset_type='collection_report'")
                conn.commit()
            finally:
                conn.close()
            rows = assets_db.prep_calendar_rows("2026-09-01", "2026-09-30")

        self.assertIn("2026-09-11", rows)          # 整批不中断
        self.assertIsNone(rows["2026-09-10"]["report"])
        self.assertTrue(rows["2026-09-10"]["report_error"])

    def test_missing_db_returns_empty_without_creating(self):
        with self._patched():
            rows = assets_db.prep_calendar_rows("2026-09-01", "2026-09-30")
        self.assertEqual(rows, {})
        self.assertFalse(self.db_path.exists())


class TestFirstPrepRecordDate(_Base):
    def test_returns_earliest_across_assets_and_snapshots(self):
        with self._patched():
            self._seed()
            assert assets_db.first_prep_record_date() == "2026-09-10"
            assert assets_db.first_prep_snapshot_date() == "2026-09-10"

    def test_missing_db_returns_none(self):
        with self._patched():
            assert assets_db.first_prep_record_date() is None


class TestAuctionCalendarRows(_Base):
    def test_returns_code_sets_per_date(self):
        with self._patched():
            self._seed()
            rows = assets_db.auction_calendar_rows("2026-09-01", "2026-09-30")

        day = rows["2026-09-11"]
        self.assertEqual(day["asset_codes"], ["600001"])
        self.assertEqual(day["snapshot_codes"], ["600001"])
        # 09-10 只有快照，没有原始资产。
        prev = rows["2026-09-10"]
        self.assertEqual(prev["asset_codes"], [])
        self.assertEqual(prev["snapshot_codes"], ["600002"])

    def test_missing_db_returns_empty(self):
        with self._patched():
            self.assertEqual(
                assets_db.auction_calendar_rows("2026-09-01", "2026-09-30"), {})


class TestListAuctionAssetNames(_Base):
    def test_names_do_not_depend_on_items_json(self):
        with self._patched():
            self._seed()
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    "UPDATE auction_assets SET items_json='{broken' "
                    "WHERE trade_date='2026-09-11'")
                conn.commit()
            finally:
                conn.close()
            names = assets_db.list_auction_asset_names("2026-09-11")
        self.assertEqual(names, {"600001": "龙头A"})

    def test_missing_db_returns_empty(self):
        with self._patched():
            self.assertEqual(assets_db.list_auction_asset_names("2026-09-11"), {})


class TestListStockDailyBars(_Base):
    def _seed_daily(self):
        with self._patched():
            assets_db.save_prep_assets("2026-09-10", {"stock_daily": {"rows": {
                "600001": [
                    {"date": "2026-09-09", "open": 10.0, "close": 10.5},
                    {"date": "2026-09-10", "open": 10.5, "close": 10.6},
                ],
            }}})
            assets_db.save_prep_assets("2026-09-11", {"stock_daily": {"rows": {
                "600001": [
                    {"date": "2026-09-10", "open": 10.5, "close": 10.6},
                    {"date": "2026-09-11", "open": 10.7, "close": 11.0},
                ],
                "600002": [
                    {"date": "2026-09-11", "open": 9.0, "close": 9.1},
                ],
            }}})

    def test_merges_multiple_days_and_dedupes_by_date(self):
        self._seed_daily()
        with self._patched():
            result = assets_db.list_stock_daily_bars(
                ["600001"], "2026-09-01", "2026-09-30")
        rows = result["rows"]["600001"]
        self.assertEqual([bar["date"] for bar in rows],
                         ["2026-09-09", "2026-09-10", "2026-09-11"])
        self.assertEqual(len([bar for bar in rows if bar["date"] == "2026-09-10"]), 1)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["bar_dates"],
                         ["2026-09-09", "2026-09-10", "2026-09-11"])

    def test_codes_filter_and_window(self):
        self._seed_daily()
        with self._patched():
            result = assets_db.list_stock_daily_bars(
                ["600002"], "2026-09-01", "2026-09-30")
            windowed = assets_db.list_stock_daily_bars(
                ["600001"], "2026-09-11", "2026-09-30")
        self.assertEqual(set(result["rows"]), {"600002"})
        self.assertEqual([bar["date"] for bar in windowed["rows"]["600001"]],
                         ["2026-09-11"])

    def test_max_bars_truncates_whole_groups_and_flags(self):
        self._seed_daily()
        with self._patched():
            result = assets_db.list_stock_daily_bars(
                ["600001", "600002"], "2026-09-01", "2026-09-30", max_bars=1)
        self.assertTrue(result["truncated"])
        # 截断按「最近 bar 日期优先、整组保留」：600002（09-11）优先于 600001。
        self.assertEqual(set(result["rows"]), {"600002"})
        self.assertEqual(len(result["rows"]["600002"]), 1)

    def test_missing_db_returns_empty_without_creating(self):
        with self._patched():
            result = assets_db.list_stock_daily_bars(
                ["600001"], "2026-09-01", "2026-09-30")
        self.assertEqual(result["rows"], {})
        self.assertFalse(result["truncated"])
        self.assertFalse(self.db_path.exists())

    def test_later_asset_fills_window_after_earlier_asset_is_empty(self):
        self._seed_daily()
        with self._patched():
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    "UPDATE prep_assets SET payload_json=? "
                    "WHERE trade_date='2026-09-10' AND asset_type='stock_daily'",
                    (json.dumps({"rows": {}}),))
                conn.commit()
            finally:
                conn.close()
            result = assets_db.list_stock_daily_bars(
                ["600001"], "2026-09-01", "2026-09-30")
        self.assertEqual([bar["date"] for bar in result["rows"]["600001"]],
                         ["2026-09-10", "2026-09-11"])


class TestListStockDailyBarDates(_Base):
    def _seed_daily(self):
        with self._patched():
            assets_db.save_prep_assets("2026-09-10", {"stock_daily": {"rows": {
                "600001": [{"date": "2026-09-09"}, {"date": "2026-09-10"}],
                "600002": [{"date": "2026-09-10"}],
            }}})
            assets_db.save_prep_assets("2026-09-11", {"stock_daily": {"rows": {
                "600001": [{"date": "2026-09-11"}],
            }}})

    def test_union_of_bars_dates_sorted_unique(self):
        self._seed_daily()
        with self._patched():
            dates = assets_db.list_stock_daily_bar_dates()
        self.assertEqual(dates, ["2026-09-09", "2026-09-10", "2026-09-11"])

    def test_window_filter_is_inclusive(self):
        self._seed_daily()
        with self._patched():
            dates = assets_db.list_stock_daily_bar_dates(
                "2026-09-10", "2026-09-10")
        self.assertEqual(dates, ["2026-09-10"])

    def test_corrupt_payload_is_skipped(self):
        self._seed_daily()
        with self._patched():
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    "UPDATE prep_assets SET payload_json='{broken' "
                    "WHERE trade_date='2026-09-11' AND asset_type='stock_daily'")
                conn.commit()
            finally:
                conn.close()
            dates = assets_db.list_stock_daily_bar_dates()
        self.assertEqual(dates, ["2026-09-09", "2026-09-10"])

    def test_missing_db_returns_empty_without_creating(self):
        with self._patched():
            self.assertEqual(assets_db.list_stock_daily_bar_dates(), [])
        self.assertFalse(self.db_path.exists())


class TestListRecentPlanCandidateCodes(_Base):
    def _seed(self):
        with self._patched():
            assets_db.save_prep_snapshot("2026-09-08", {
                "review_candidates": [
                    {"code": "600005", "position_pct": 8},
                ]})
            assets_db.save_prep_snapshot("2026-09-09", {
                "review_candidates": [
                    {"code": "600001", "position_pct": 0},
                    {"code": "600002", "eligibility": "not_qualified"},
                    {"code": "600001", "position_pct": 9},   # 同日内重复
                    {"position_pct": 5},                     # 无 code，忽略
                    "not-a-dict",
                ]})
            assets_db.save_prep_snapshot("2026-09-10", {
                "review_candidates": [{"code": "600003"}]})
            assets_db.save_prep_snapshot("2026-09-11", {
                "review_candidates": [{"code": "600004"}]})

    def test_strictly_before_orders_days_newest_first(self):
        self._seed()
        with self._patched():
            window = assets_db.list_recent_plan_candidate_codes(
                "2026-09-11", days=2)
        # 09-11 自身被排除；日序由新到旧（09-10 → 09-09）。
        self.assertEqual(window["codes"], ["600003", "600001", "600002"])
        self.assertEqual(window["trade_dates"], ["2026-09-09", "2026-09-10"])
        self.assertEqual(window["lookback_days"], 2)
        self.assertEqual(window["corrupt_dates"], [])

    def test_all_candidates_kept_regardless_of_eligibility_or_position(self):
        self._seed()
        with self._patched():
            window = assets_db.list_recent_plan_candidate_codes(
                "2026-09-11", days=10)
        # position_pct=0 / eligibility=not_qualified 也必须保留（评估样本=全量候选）。
        self.assertIn("600001", window["codes"])
        self.assertIn("600002", window["codes"])
        # 日序由新到旧：09-10 (600003) 先于 09-09，09-11 自身被排除。
        self.assertEqual(window["codes"][:2], ["600003", "600001"])

    def test_corrupt_result_json_is_reported_and_skipped(self):
        self._seed()
        with self._patched():
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    "UPDATE prep_snapshots SET result_json='{broken' "
                    "WHERE trade_date='2026-09-10'")
                conn.commit()
            finally:
                conn.close()
            window = assets_db.list_recent_plan_candidate_codes(
                "2026-09-11", days=10)
        self.assertEqual(window["corrupt_dates"], ["2026-09-10"])
        self.assertNotIn("600003", window["codes"])
        self.assertIn("600005", window["codes"])

    def test_missing_db_returns_empty_without_creating(self):
        with self._patched():
            window = assets_db.list_recent_plan_candidate_codes("2026-09-11")
        self.assertEqual(window["codes"], [])
        self.assertEqual(window["trade_dates"], [])
        self.assertEqual(window["lookback_days"], 10)
        self.assertFalse(self.db_path.exists())

    def test_days_must_be_positive(self):
        with self.assertRaises(ValueError):
            assets_db.list_recent_plan_candidate_codes("2026-09-11", days=0)


class TestStockDailyDetectionReads(_Base):
    def _seed_daily(self):
        with self._patched():
            assets_db.save_prep_assets("2026-09-10", {"stock_daily": {"rows": {
                "600001": [{"date": "2026-09-09"}, {"date": "2026-09-10"}],
                "600002": [{"date": "2026-09-08"}],
            }}})
            assets_db.save_prep_assets("2026-09-11", {"stock_daily": {"rows": {
                "600001": [{"date": "2026-09-11"}],
            }}})

    def test_latest_asset_date_and_last_bar_dates(self):
        self._seed_daily()
        with self._patched():
            self.assertEqual(
                assets_db.latest_stock_daily_asset_date(), "2026-09-11")
            last = assets_db.stock_daily_last_bar_dates(["600001", "600002"])
        # 600001 取最新日期；600002 只在更早资产日出现。
        self.assertEqual(last, {"600001": "2026-09-11", "600002": "2026-09-08"})

    def test_codes_filter_and_unknown_code_absent(self):
        self._seed_daily()
        with self._patched():
            last = assets_db.stock_daily_last_bar_dates(["600002", "600999"])
        self.assertEqual(last, {"600002": "2026-09-08"})

    def test_corrupt_payload_is_skipped(self):
        self._seed_daily()
        with self._patched():
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    "UPDATE prep_assets SET payload_json='{broken' "
                    "WHERE trade_date='2026-09-11' AND asset_type='stock_daily'")
                conn.commit()
            finally:
                conn.close()
            last = assets_db.stock_daily_last_bar_dates(["600001"])
            self.assertEqual(assets_db.latest_stock_daily_asset_date(),
                             "2026-09-11")
        # 最新资产日损坏 → 继续向旧扫描，取 2026-09-10 行。
        self.assertEqual(last, {"600001": "2026-09-10"})

    def test_missing_db_returns_empty_without_creating(self):
        with self._patched():
            self.assertIsNone(assets_db.latest_stock_daily_asset_date())
            self.assertEqual(assets_db.stock_daily_last_bar_dates(), {})
        self.assertFalse(self.db_path.exists())


if __name__ == "__main__":
    unittest.main()
