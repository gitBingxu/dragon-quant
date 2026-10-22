"""P1 main 验收指定的 3 项必修 + P0.5 known_issues 顺修测试。

  - stale 逾期判定：calendar 缺失 → ``stale_calendar_missing``；
  - ``cohort_excluded_profile``：综合分分位 + 规模代理；
  - ``assets status``：``factor_*`` / ``fin_*`` not_synced 行；
  - ``invalid_denominator`` / ``missing_field`` 细分与 ``layout_unverified`` 计数。
"""

import unittest

from dragon_quant.factors import finance as fin
from dragon_quant.factors.builtin import financial
from dragon_quant.factors.types import FactorContext
from dragon_quant.storage import finance as fin_store
from tests.test_factors_pit import TempDbBase


class TestMainAcceptance(TempDbBase):
    CODE = "600001"
    VALUES = {"meigujing_zichan": 5.5, "jing_zichan": 5.5e8,
              "zhuying_shouru": 3e8, "jing_lirun": 3e7}

    def test_missing_calendar_is_reported(self):
        self.seed_period("2026-06-30", {self.CODE: self.VALUES})
        source = fin.load_fin_source("2026-09-01", [self.CODE])
        self.assertIn("stale_calendar_missing", source["warnings"])

    def test_calendar_not_covering_trade_date_is_reported(self):
        self.seed_period("2026-06-30", {self.CODE: self.VALUES})
        # 日历窗口晚于 trade_date：非空但不覆盖，必须显式降级
        source = fin.load_fin_source(
            "2026-09-01", [self.CODE], calendar=["2026-10-01", "2026-10-02"])
        self.assertTrue(any("stale_calendar_missing" in warning
                            for warning in source["warnings"]))

    def test_cohort_excluded_profile_has_percentile_and_size_proxy(self):
        self.seed_period("2026-06-30", {
            "600001": self.VALUES, "600002": self.VALUES, "600003": self.VALUES})
        self.seed_period("2026-03-31", {"600004": self.VALUES},
                         md5="m2", first_seen="2026-06-20T10:00:00")
        pool_profile = {
            "600001": {"composite": 90.0, "market_cap": 1e9},
            "600002": {"composite": 80.0, "market_cap": 2e9},
            "600003": {"composite": 70.0, "market_cap": 3e9},
            "600004": {"composite": 20.0, "market_cap": 5e9},
        }
        source = fin.load_fin_source(
            "2026-09-01", ["600001", "600002", "600003", "600004"],
            pool_profile=pool_profile)
        profile = source["cohort"]["excluded_profile"]
        self.assertIsNotNone(profile["composite_percentile"])
        self.assertEqual(profile["composite_percentile"]["median"], 12.5)
        self.assertEqual(profile["size_proxy"]["field"], "market_cap")
        self.assertEqual(profile["size_proxy"]["median"], 5e9)
        self.assertIsNotNone(profile["size_proxy"]["included_median"])

    def test_layout_unverified_counted(self):
        fin_store.upsert_report_file(
            "2026-06-30", filename="x", md5="m9", filesize=1,
            first_seen_at="2026-08-20T10:00:00", status="ok")
        fin_store.insert_report_vintage(
            "2026-06-30", [{"code": self.CODE, "values": self.VALUES,
                            "available_at": "2026-08-31T23:59:59",
                            "pit_basis": "statutory_backfilled"}],
            file_md5="m9", layout_key="gpcw_99")
        source = fin.load_fin_source("2026-09-01", [self.CODE])
        self.assertEqual(source["layout_unverified"], 1)

    def test_denominator_and_missing_field_are_distinct(self):
        self.assertEqual(financial.compute_roe(
            {"jing_lirun": 10.0, "jing_zichan": -5.0}, "2026-06-30"),
            (None, "invalid_denominator"))
        self.assertEqual(financial.compute_roe(
            {"jing_lirun": 10.0}, "2026-06-30"), (None, "missing_field"))
        self.assertEqual(financial.compute_net_margin(
            {"jing_lirun": 10.0, "zhuying_shouru": 0.0}, "2026-06-30"),
            (None, "invalid_denominator"))
        ctx = FactorContext(
            trade_date="2026-09-01", codes=(self.CODE,),
            source_rows={"current": {self.CODE: {
                "period": "2026-06-30", "pit_basis": "observed",
                "values": {"jing_lirun": 10.0, "jing_zichan": -5.0}}},
                "prior": {}, "observed": {}, "cohort": {},
                "available_at": "2026-09-01T16:40:00"},
            as_of="2026-09-01T16:40:00")
        meta = financial.compute_roe_factor(ctx).meta
        self.assertEqual(meta["skipped"], {"invalid_denominator": 1})

    def test_assets_status_marks_factor_and_fin_not_synced(self):
        from dragon_quant import cli  # noqa: F401 — 先走正常入口，规避 cli↔cli_commands 循环
        from dragon_quant.cli_commands import _assets_human_summary
        text = _assets_human_summary("assets status", {"summary": {}})
        self.assertIn("factor_*: not_synced", text)
        self.assertIn("fin_*: not_synced", text)


if __name__ == "__main__":
    unittest.main()
