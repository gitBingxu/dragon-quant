"""因子表存储层测试：DDL / 整日替换 / prune / DO NOTHING / 读取语义（方案 §5 / §8.1）。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dragon_quant.infra.factor import sql as store


class FactorsStorageBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "assets.db")
        self._patch = patch(
            "dragon_quant.infra.assets.db._connect",
            side_effect=self._connect)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def _connect(self, *args, create=True, path=None, **kwargs):
        target = path or self._db_path
        if not create and not Path(target).exists():
            return None
        return sqlite3.connect(target)

    def _rows(self, sql, params=()):
        conn = sqlite3.connect(self._db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    @staticmethod
    def _value(code="600001", value=80.0, available_at="2026-05-19T15:05:00",
               source="scan", scan_id="v2_20260519_25"):
        return {
            "code": code, "value": value, "available_at": available_at,
            "source": source, "source_scan_id": scan_id,
            "source_meta_json": '{"k":1}',
        }


class TestSchema(FactorsStorageBase):
    def test_five_tables_and_indexes_created(self):
        store.replace_factor_day("2026-05-19", "f", "v1", [self._value()])
        tables = {r[0] for r in self._rows(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(set(store.FACTOR_TABLES).issubset(tables))
        indexes = {r[0] for r in self._rows(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn("idx_factor_values_date_name", indexes)
        self.assertIn("idx_factor_values_code_date", indexes)
        self.assertIn("idx_factor_sector_date", indexes)
        self.assertIn("idx_factor_build_runs_date", indexes)

    def test_required_columns_registered(self):
        from dragon_quant.infra.assets import db as assets_db
        for table in store.FACTOR_TABLES:
            self.assertIn(table, assets_db._REQUIRED_COLUMNS)
            self.assertIn(table, assets_db._WRITE_REQUIRED_COLUMNS)


class TestReplaceFactorDay(FactorsStorageBase):
    def test_write_and_read(self):
        written = store.replace_factor_day("2026-05-19", "f", "v1", [
            self._value("600001", 80.0), self._value("600002", 55.5)])
        self.assertEqual(written, 2)
        rows = store.get_day_values("2026-05-19")
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["code"] for r in rows}, {"600001", "600002"})
        self.assertEqual(rows[0]["source_scan_id"], "v2_20260519_25")
        self.assertEqual(rows[0]["available_at"], "2026-05-19T15:05:00")

    def test_rebuild_prunes_old_codes(self):
        store.replace_factor_day("2026-05-19", "f", "v1", [
            self._value("600001"), self._value("600002")])
        written = store.replace_factor_day("2026-05-19", "f", "v1", [
            self._value("600001")])
        self.assertEqual(written, 1)
        codes = {r["code"] for r in store.get_day_values("2026-05-19")}
        self.assertEqual(codes, {"600001"})

    def test_empty_result_deletes_rows(self):
        store.replace_factor_day("2026-05-19", "f", "v1", [self._value()])
        written = store.replace_factor_day("2026-05-19", "f", "v1", [])
        self.assertEqual(written, 0)
        self.assertEqual(store.get_day_values("2026-05-19"), [])

    def test_other_version_untouched(self):
        store.replace_factor_day("2026-05-19", "f", "v1", [self._value()])
        store.replace_factor_day("2026-05-19", "f", "v2", [self._value(value=90.0)])
        rows = store.get_day_values("2026-05-19")
        self.assertEqual({r["factor_version"] for r in rows}, {"v1", "v2"})

    def test_nan_value_rolls_back_and_preserves_old_rows(self):
        store.replace_factor_day("2026-05-19", "f", "v1", [self._value("600001")])
        with self.assertRaises(sqlite3.IntegrityError):
            store.replace_factor_day("2026-05-19", "f", "v1", [
                self._value("600002", float("nan"))])
        codes = {r["code"] for r in store.get_day_values("2026-05-19")}
        self.assertEqual(codes, {"600001"})

    def test_read_missing_db_returns_empty(self):
        Path(self._db_path).unlink(missing_ok=True)
        self.assertEqual(store.get_day_values("2026-05-19"), [])
        self.assertEqual(store.get_series("f", None, "2026-05-01", "2026-05-31"), [])
        self.assertEqual(store.resolve_versions(["f"]), {})
        self.assertFalse(Path(self._db_path).exists(), "只读路径不得建库")

    def test_series_and_code_filter(self):
        for date, value in (("2026-05-18", 1.0), ("2026-05-19", 2.0)):
            store.replace_factor_day(date, "f", "v1", [
                self._value("600001", value), self._value("600002", value + 1)])
        series = store.get_series("f", ["600001"], "2026-05-18", "2026-05-19")
        self.assertEqual([r["value"] for r in series], [1.0, 2.0])
        all_codes = store.get_series("f", None, "2026-05-18", "2026-05-18")
        self.assertEqual(len(all_codes), 2)


class TestRegistrySnapshots(FactorsStorageBase):
    def _row(self, version, name="f", created_at=None):
        return {
            "factor_name": name, "factor_version": version, "label": "L",
            "frequency": "1d", "source": "scan", "depends_on_json": "[]",
            "lookback": 0, "direction": "higher_better", "unit": "score",
            "available_phase": "close", "pit_rule": "recorded", "scope_json": '["candidate"]',
            "params_json": "{}", "default_enabled": 1, "valid_from": None,
            "deprecated_at": None, "owner": "", "description": "",
            "spec_hash": "h" * 64,
        }

    def test_do_nothing_and_default_version_not_hijacked(self):
        self.assertEqual(store.save_registry_snapshots([self._row("v1")]), 1)
        self.assertEqual(store.save_registry_snapshots([self._row("v2")]), 1)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("UPDATE factor_registry SET created_at='2026-01-01 00:00:00' "
                         "WHERE factor_version='v1'")
            conn.execute("UPDATE factor_registry SET created_at='2026-01-02 00:00:00' "
                         "WHERE factor_version='v2'")
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(store.resolve_versions(["f"]), {"f": "v2"})
        # 重插旧版本：DO NOTHING → created_at 不刷新，默认版本不漂移
        self.assertEqual(store.save_registry_snapshots([self._row("v1")]), 0)
        self.assertEqual(store.resolve_versions(["f"]), {"f": "v2"})

    def test_tie_breaks_by_version_lexicographic(self):
        store.save_registry_snapshots([self._row("va"), self._row("vb")])
        self.assertEqual(store.resolve_versions(["f"]), {"f": "vb"})

    def test_list_registry_parses_json(self):
        store.save_registry_snapshots([self._row("v1")])
        rows = store.list_registry()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["depends_on"], [])
        self.assertEqual(rows[0]["scope"], ["candidate"])
        self.assertIsNone(rows[0]["json_error"])

    def test_unregistered_name_not_returned(self):
        self.assertEqual(store.resolve_versions(["nope"]), {})


class TestBuildRuns(FactorsStorageBase):
    def test_record_and_list_with_corrupt_row_isolation(self):
        first = store.record_build_run("2026-05-19", {"ok": True})
        second = store.record_build_run("2026-05-20", {"ok": False})
        self.assertGreater(second, first)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("UPDATE factor_build_runs SET report_json='{bad' WHERE id=?", (first,))
            conn.commit()
        finally:
            conn.close()
        rows = store.list_build_runs()
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id[second]["report"], {"ok": False})
        self.assertIsNone(by_id[second]["report_error"])
        self.assertIn("JSONDecodeError", by_id[first]["report_error"])

    def test_filter_by_trade_date(self):
        store.record_build_run("2026-05-19", {"ok": True})
        store.record_build_run("2026-05-20", {"ok": True})
        rows = store.list_build_runs(trade_date="2026-05-20")
        self.assertEqual([r["trade_date"] for r in rows], ["2026-05-20"])


class TestSectorSnapshots(FactorsStorageBase):
    @staticmethod
    def _sector(code="600001", category="hy", industry="881167", name="低空"):
        return {"code": code, "industry_code": industry, "industry_name": name,
                "category": category, "source": "tdx", "scan_id": "s1"}

    def test_replace_and_prune(self):
        store.replace_sector_day("2026-05-19", [
            self._sector(), self._sector(category="gn", industry="885001")])
        self.assertEqual(len(store.get_sector_map("2026-05-19")), 1)
        store.replace_sector_day("2026-05-19", [self._sector()])
        mapping = store.get_sector_map("2026-05-19")
        self.assertEqual(set(mapping), {"600001"})
        self.assertEqual(mapping["600001"]["category"], "hy")

    def test_status_counts(self):
        store.replace_sector_day("2026-05-19", [self._sector()])
        status = store.factor_status()
        self.assertEqual(status["factor_sector_snapshots"]["rows"], 1)
        self.assertEqual(status["factor_sector_snapshots"]["by_category"], {"hy": 1})
        self.assertEqual(status["factor_values"]["rows"], 0)


if __name__ == "__main__":
    unittest.main()
