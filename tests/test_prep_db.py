"""prep.db（独立复盘资产库）存储层单元测试。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class PrepDbTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "prep.db")

    def tearDown(self):
        self._tmpdir.cleanup()

    def _patch(self):
        return patch("dragon_quant.storage.prep_db._connect",
                     side_effect=lambda: sqlite3.connect(self._db_path))


class TestPrepAssets(PrepDbTestBase):
    def test_roundtrip(self):
        from dragon_quant.storage import prep_db
        with self._patch():
            prep_db.save_prep_assets("2026-10-19", {"dragons": {"count": 2}},
                                     {"dragons": {"status": "ok"}})
            self.assertEqual(prep_db.get_prep_asset("2026-10-19", "dragons")["count"], 2)
            assets = prep_db.get_prep_assets("2026-10-19")
            self.assertIn("dragons", assets)
            self.assertIn("collection_report", assets)
            self.assertEqual(assets["collection_report"]["dragons"]["status"], "ok")

    def test_upsert_single_row_and_idempotent(self):
        from dragon_quant.storage import prep_db
        with self._patch():
            prep_db.save_prep_assets("2026-10-19", {"a": {"v": 1}}, {})
            prep_db.save_prep_assets("2026-10-19", {"a": {"v": 2}}, {})
            self.assertEqual(prep_db.get_prep_asset("2026-10-19", "a")["v"], 2)
            conn = sqlite3.connect(self._db_path)
            try:
                n = conn.execute("SELECT COUNT(*) FROM prep_assets WHERE asset_type='a'"
                                 ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(n, 1)

    def test_single_transaction_rollback(self):
        """任一 payload 序列化失败 → 整批不落库（无部分新旧混合）。"""
        from dragon_quant.storage import prep_db
        with self._patch():
            with self.assertRaises(ValueError):
                prep_db.save_prep_assets("2026-10-19",
                                         {"ok": {"v": 1}, "bad": {"v": float("nan")}}, {})
            self.assertEqual(prep_db.get_prep_assets("2026-10-19"), {})

    def test_missing_asset_returns_none(self):
        from dragon_quant.storage import prep_db
        with self._patch():
            self.assertIsNone(prep_db.get_prep_asset("1999-01-01", "x"))
            self.assertEqual(prep_db.get_prep_assets("1999-01-01"), {})


class TestPrepSnapshots(PrepDbTestBase):
    def test_roundtrip_and_upsert(self):
        from dragon_quant.storage import prep_db
        with self._patch():
            prep_db.save_prep_snapshot("2026-10-19", {"sentiment": {"limit_up_cnt": 59}})
            self.assertTrue(prep_db.has_prep_snapshot("2026-10-19"))
            self.assertEqual(
                prep_db.get_prep_snapshot("2026-10-19")["sentiment"]["limit_up_cnt"], 59)
            prep_db.save_prep_snapshot("2026-10-19", {"v": 2})
            self.assertEqual(prep_db.get_prep_snapshot("2026-10-19"), {"v": 2})

    def test_missing_returns_none(self):
        from dragon_quant.storage import prep_db
        with self._patch():
            self.assertIsNone(prep_db.get_prep_snapshot("1999-01-01"))
            self.assertFalse(prep_db.has_prep_snapshot("1999-01-01"))

    def test_nan_rejected(self):
        from dragon_quant.storage import prep_db
        with self._patch():
            with self.assertRaises(ValueError):
                prep_db.save_prep_snapshot("2026-10-19", {"v": float("inf")})

    def test_assets_and_snapshots_isolated(self):
        """两表互不影响：删/写其一不改变另一。"""
        from dragon_quant.storage import prep_db
        with self._patch():
            prep_db.save_prep_assets("2026-10-19", {"a": {"v": 1}}, {})
            prep_db.save_prep_snapshot("2026-10-19", {"s": 1})
            self.assertIsNotNone(prep_db.get_prep_asset("2026-10-19", "a"))
            self.assertIsNotNone(prep_db.get_prep_snapshot("2026-10-19"))


if __name__ == "__main__":
    unittest.main()
