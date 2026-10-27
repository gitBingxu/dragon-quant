"""财务原始链路存储层测试：五表 / 观测 UPSERT / 追加式 vintage / as-of（方案 §5.1.1、§5.4）。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dragon_quant.infra.factor import finance_store as store


class FinanceStorageBase(unittest.TestCase):
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

    def _observed(self, code="600001", updated=20260630, values=None,
                  captured="2026-08-20T10:00:00", values_hash=None):
        payload = values if values is not None else {"jing_zichan": 100.0}
        return {
            "code": code, "updated_date": updated, "period": "2026-06-30",
            "values": payload, "values_hash": values_hash or f"h-{code}",
            "captured_at": captured,
        }

    def _vintage(self, period="2026-06-30", md5="m1", first_seen="2026-08-20T10:00:00",
                 rows=None, layout="gpcw_20"):
        return store.insert_report_vintage(
            period, rows or [], file_md5=md5, layout_key=layout,
            retracted_at="2026-09-01T10:00:00")


class TestSchema(FinanceStorageBase):
    def test_five_tables_created(self):
        store.upsert_observed_snapshots("2026-08-20", [self._observed()])
        tables = {r[0] for r in self._rows(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(set(store.FIN_TABLES).issubset(tables))

    def test_required_columns_registered(self):
        from dragon_quant.infra.assets import db as assets_db
        for table in store.FIN_TABLES:
            self.assertIn(table, assets_db._REQUIRED_COLUMNS)


class TestObserved(FinanceStorageBase):
    def test_upsert_is_idempotent(self):
        self.assertEqual(store.upsert_observed_snapshots(
            "2026-08-20", [self._observed()]), 1)
        self.assertEqual(store.upsert_observed_snapshots(
            "2026-08-20", [self._observed()]), 1)
        self.assertEqual(self._rows(
            "SELECT COUNT(*) FROM fin_observed_snapshots")[0][0], 1)

    def test_updated_date_rollback_only_refreshes_captured_at(self):
        store.upsert_observed_snapshots("2026-08-20", [self._observed(
            updated=20260630, values={"jing_zichan": 100.0}, values_hash="new")])
        store.upsert_observed_snapshots("2026-08-20", [self._observed(
            updated=20260331, values={"jing_zichan": 1.0}, values_hash="old",
            captured="2026-08-20T11:00:00")])
        row = store.get_observed_snapshots("2026-08-20")["600001"]
        self.assertEqual(row["updated_date"], 20260630)
        self.assertEqual(row["values"], {"jing_zichan": 100.0})
        self.assertEqual(row["values_hash"], "new")
        self.assertEqual(row["captured_at"], "2026-08-20T11:00:00")

    def test_find_match_requires_same_hash_and_before(self):
        store.upsert_observed_snapshots("2026-08-20", [self._observed(
            values_hash="same", captured="2026-08-20T10:00:00")])
        self.assertEqual(
            store.find_observed_match(
                "600001", "same", before="2026-08-21T00:00:00"),
            "2026-08-20T10:00:00")
        self.assertIsNone(store.find_observed_match(
            "600001", "other", before="2026-08-21T00:00:00"))
        self.assertIsNone(store.find_observed_match(
            "600001", "same", before="2026-08-20T09:00:00"))

    def test_latest_observed_before_is_cross_day_asof(self):
        store.upsert_observed_snapshots("2026-08-20", [self._observed(
            values_hash="v1", captured="2026-08-20T17:02:00")])
        store.upsert_observed_snapshots("2026-08-21", [self._observed(
            values_hash="v2", captured="2026-08-21T10:00:00")])
        same_day = store.latest_observed_before(
            ["600001"], "2026-08-20T16:40:00")
        self.assertEqual(same_day, {})
        next_day = store.latest_observed_before(
            ["600001"], "2026-08-21T16:40:00")
        self.assertEqual(next_day["600001"]["values_hash"], "v2")


class TestReportVintage(FinanceStorageBase):
    def _rows_for(self, codes, available="2026-08-31T23:59:59"):
        return [{"code": code, "values": {"jing_zichan": float(i)},
                 "available_at": available, "pit_basis": "statutory_backfilled",
                 "first_seen_at": "2026-08-20T10:00:00"}
                for i, code in enumerate(codes, start=1)]

    def test_append_vintage_and_idempotent(self):
        result = self._vintage(rows=self._rows_for(["600001", "600002"]))
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["vintages"], 1)
        again = self._vintage(rows=self._rows_for(["600001", "600002"]))
        self.assertEqual(again["inserted"], 0, "同一 vintage 重跑零新增")
        self.assertEqual(again["vintages"], 1)

    def test_revision_appends_and_keeps_old_rows(self):
        self._vintage(md5="m1", rows=self._rows_for(["600001"]))
        self._vintage(md5="m2", rows=self._rows_for(["600001"]))
        rows = self._rows(
            "SELECT file_md5 FROM fin_report_records ORDER BY file_md5")
        self.assertEqual([r[0] for r in rows], ["m1", "m2"])

    def test_missing_code_is_retracted(self):
        self._vintage(md5="m1", rows=self._rows_for(["600001", "600002"]))
        result = self._vintage(
            md5="m2", rows=self._rows_for(["600001"]),
            first_seen="2026-09-01T10:00:00")
        # 600002 的 m1 行被撤销 + 600001 的 m2 行新增
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["retracted"], 1)
        retracted = self._rows(
            "SELECT code, retracted_at FROM fin_report_records "
            "WHERE retracted_at IS NOT NULL")
        self.assertEqual(retracted[0][0], "600002")

    def test_empty_records_do_not_delete(self):
        self._vintage(md5="m1", rows=self._rows_for(["600001"]))
        result = self._vintage(md5="m1", rows=[])
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["retracted"], 0)
        self.assertEqual(self._rows(
            "SELECT COUNT(*) FROM fin_report_records")[0][0], 1)

    def test_has_prior_records(self):
        self._vintage(md5="m1", rows=self._rows_for(["600001"]))
        self.assertFalse(store.has_prior_records("2026-06-30", "m1"))
        self.assertTrue(store.has_prior_records("2026-06-30", "m2"))


class TestAsOf(FinanceStorageBase):
    def _row(self, code, period, md5, available, basis="statutory_backfilled",
             value=1.0, retracted=None):
        return {
            "report_period": period, "code": code, "file_md5": md5,
            "layout_key": "gpcw_20",
            "values": {"jing_zichan": value},
            "available_at": available, "pit_basis": basis,
            "first_seen_at": "2026-01-01T00:00:00", "retracted_at": retracted,
        }

    def test_available_at_hard_gate(self):
        store.insert_report_vintage(
            "2026-06-30", [self._row("600001", "2026-06-30", "m1",
                                      "2026-08-31T23:59:59")],
            file_md5="m1", layout_key="gpcw_20")
        self.assertEqual(store.get_report_asof(
            ["600001"], "2026-08-31T16:40:00"), {})
        selected = store.get_report_asof(["600001"], "2026-09-01T16:40:00")
        self.assertEqual(selected["600001"]["available_at"],
                         "2026-08-31T23:59:59")

    def test_latest_period_wins(self):
        for period, value in (("2026-03-31", 1.0), ("2026-06-30", 2.0)):
            store.insert_report_vintage(
                period, [self._row("600001", period, f"m-{period}", "2026-07-01T00:00:00",
                                    value=value)],
                file_md5=f"m-{period}", layout_key="gpcw_20")
        selected = store.get_report_asof(["600001"], "2026-09-01T16:40:00")
        self.assertEqual(selected["600001"]["report_period"], "2026-06-30")
        self.assertEqual(selected["600001"]["values"]["jing_zichan"], 2.0)

    def test_retracted_vintage_excluded_after_withdraw_time(self):
        store.insert_report_vintage(
            "2026-06-30", [self._row("600001", "2026-06-30", "m1",
                                      "2026-08-31T23:59:59")],
            file_md5="m1", layout_key="gpcw_20")
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("UPDATE fin_report_records SET retracted_at = ?",
                         ("2026-09-10T10:00:00",))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(store.get_report_asof(
            ["600001"], "2026-09-11T16:40:00"), {})
        self.assertIn("600001", store.get_report_asof(
            ["600001"], "2026-09-09T16:40:00"))


class TestCalibration(FinanceStorageBase):
    def test_upsert_and_read(self):
        store.upsert_field_calibration("gpcw_20", {
            "num_fields": 20,
            "mapping": {"jing_zichan": {"index": 3, "scale": 1.0}},
            "confidence": {"jing_zichan": {"best_err": 0.0}},
            "status": "verified", "sample_n": 5,
        })
        store.upsert_field_calibration("gpcw_20", {
            "num_fields": 20, "mapping": {}, "confidence": {},
            "status": "unverified", "sample_n": 2,
        })
        rows = store.list_field_calibrations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "unverified")
        self.assertEqual(store.get_field_calibration("gpcw_20")["sample_n"], 2)
        self.assertIsNone(store.get_field_calibration("gpcw_99"))

    def test_json_corruption_is_isolated(self):
        store.upsert_field_calibration("gpcw_20", {
            "num_fields": 20, "mapping": {}, "confidence": {},
            "status": "verified", "sample_n": 1,
        })
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("UPDATE fin_field_calibrations SET mapping_json='{bad'")
            conn.commit()
        finally:
            conn.close()
        row = store.get_field_calibration("gpcw_20")
        self.assertIn("JSONDecodeError", row["json_error"])


if __name__ == "__main__":
    unittest.main()
