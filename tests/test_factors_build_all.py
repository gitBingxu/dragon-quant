"""``factor build --all`` 测试（AC7）：全量名单 / 互斥 / 触网边界 / 区间展开。

全部使用临时 SQLite（patch 连接门面），零网络。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dragon_quant.infra.factor import service
from dragon_quant.infra.factor.registry import spec_names


class BuildAllBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.assets_path = root / "assets.db"
        self.dragon_path = root / "dragon.db"
        self._patches = [
            patch("dragon_quant.infra.assets.db._connect",
                  side_effect=self._assets_connect),
            patch("dragon_quant.infra.storage.connection._connect",
                  side_effect=lambda *a, **k: sqlite3.connect(self.dragon_path)),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in reversed(self._patches):
            item.stop()
        self._tmp.cleanup()

    def _assets_connect(self, create=True, path=None):
        target = Path(path) if path is not None else self.assets_path
        if not create and not target.exists():
            return None
        return sqlite3.connect(target)


class TestBuildAllSelection(BuildAllBase):
    def test_all_expands_to_every_registered_factor(self):
        fake = {"ok": True, "build_id": 1, "factors": {}}
        with patch("dragon_quant.infra.factor.service.engine.build_date",
                   return_value=fake) as build_date:
            out = service.build(date="2026-10-19", all_factors=True)
        names = build_date.call_args.kwargs["names"]
        self.assertEqual(set(names), set(spec_names()))
        self.assertEqual(out["mode"], "date")

    def test_default_build_keeps_enabled_only(self):
        fake = {"ok": True, "build_id": 1, "factors": {}}
        with patch("dragon_quant.infra.factor.service.engine.build_date",
                   return_value=fake) as build_date:
            service.build(date="2026-10-19")
        self.assertIsNone(build_date.call_args.kwargs["names"])

    def test_all_and_only_are_mutually_exclusive(self):
        with self.assertRaises(ValueError) as cm:
            service.build(date="2026-10-19", only="five_dim_composite",
                          all_factors=True)
        self.assertIn("互斥", str(cm.exception))

    def test_range_all_expands_each_date(self):
        fake = {"ok": True, "build_id": 1, "factors": {}}
        with patch("dragon_quant.infra.factor.service._resolve_dates",
                   return_value=(["2026-10-19", "2026-10-20"], "range")), \
             patch("dragon_quant.infra.factor.service.engine.build_date",
                   return_value=fake) as build_date:
            out = service.build(date_from="2026-10-19", date_to="2026-10-20",
                                all_factors=True)
        self.assertEqual(build_date.call_count, 2)
        self.assertEqual(out["dates"], ["2026-10-19", "2026-10-20"])


class TestBuildAllNetworkBoundary(BuildAllBase):
    def test_no_fin_data_does_not_load_calendar(self):
        """无财务数据时不触网：不调用 load_trade_calendar，fin 因子 fail-closed。"""
        fake = {"ok": True, "build_id": 1, "factors": {}}
        with patch("dragon_quant.infra.factor.finance_store.has_any_fin_data",
                   return_value=False), \
             patch("dragon_quant.infra.factor.finance.load_trade_calendar") as loader, \
             patch("dragon_quant.infra.factor.service.engine.build_date",
                   return_value=fake) as build_date:
            out = service.build(date="2026-10-19", all_factors=True)
        loader.assert_not_called()
        self.assertEqual(build_date.call_args.kwargs["calendar"], ())
        self.assertEqual(out["warnings"], [])

    def test_fin_data_present_loads_calendar(self):
        fake = {"ok": True, "build_id": 1, "factors": {}}
        with patch("dragon_quant.infra.factor.finance_store.has_any_fin_data",
                   return_value=True), \
             patch("dragon_quant.infra.factor.finance.load_trade_calendar",
                   return_value=(["2026-10-16", "2026-10-19"], None)) as loader, \
             patch("dragon_quant.infra.factor.service.engine.build_date",
                   return_value=fake) as build_date:
            service.build(date="2026-10-19", all_factors=True)
        loader.assert_called_once()
        self.assertEqual(build_date.call_args.kwargs["calendar"],
                         ("2026-10-16", "2026-10-19"))

    def test_calendar_failure_is_warning_not_block(self):
        fake = {"ok": True, "build_id": 1, "factors": {}}
        with patch("dragon_quant.infra.factor.finance_store.has_any_fin_data",
                   return_value=True), \
             patch("dragon_quant.infra.factor.finance.load_trade_calendar",
                   return_value=([], "stale_calendar_missing: boom")), \
             patch("dragon_quant.infra.factor.service.engine.build_date",
                   return_value=fake):
            out = service.build(date="2026-10-19", all_factors=True)
        self.assertTrue(out["ok"])
        self.assertTrue(any("stale_calendar_missing" in w
                            for w in out["warnings"]))

    def test_build_forwards_services_to_calendar_and_engine(self):
        services = object()
        fake = {"ok": True, "build_id": 1, "factors": {}}
        with patch("dragon_quant.infra.factor.finance_store.has_any_fin_data",
                   return_value=True), \
             patch("dragon_quant.infra.factor.finance.load_trade_calendar",
                   return_value=(["2026-10-16"], None)) as calendar, \
             patch("dragon_quant.infra.factor.service.engine.build_date",
                   return_value=fake) as build_date:
            service.build(date="2026-10-19", all_factors=True, services=services)

        self.assertIs(calendar.call_args.kwargs["services"], services)
        self.assertIs(build_date.call_args.kwargs["services"], services)


class TestBuildAllRealRun(BuildAllBase):
    def test_real_build_reports_every_factor(self):
        """真实 build_date（临时库、无扫描）：全部因子都有报告，fin 为 missing_source。"""
        out = service.build(date="2026-10-19", all_factors=True)
        self.assertEqual(out["mode"], "date")
        self.assertEqual(len(out["reports"]), 1)
        report = out["reports"][0]
        self.assertEqual(set(report["factors"]), set(spec_names()))
        self.assertIn("no_scan_for_date", " ".join(report["warnings"]))
        fin_reports = [v for k, v in report["factors"].items() if k.startswith("fin_")]
        self.assertTrue(fin_reports)
        self.assertTrue(all(item.get("status") == "missing_source"
                            for item in fin_reports))
