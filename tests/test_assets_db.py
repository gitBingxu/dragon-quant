"""assets.db（数据资产独立库）存储层单元测试。

覆盖：4 表 schema、prep 资产/快照、auction 原始明细、形状探针、真实库守护。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dragon_quant.storage.paths import ASSETS_DB_PATH


def _signature(path: Path):
    """真实库的 (exists, size, mtime_ns)，用于守护「测试不得写真实数据目录」。"""
    try:
        st = path.stat()
        return (True, st.st_size, st.st_mtime_ns)
    except FileNotFoundError:
        return (False, 0, 0)


class AssetsDbTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "assets.db")
        self._real_sig = _signature(ASSETS_DB_PATH)

    def tearDown(self):
        self.assertEqual(
            _signature(ASSETS_DB_PATH), self._real_sig,
            "测试写入了真实 assets.db：请检查 patch 目标是否失效",
        )
        self._tmpdir.cleanup()

    def _patch(self):
        return patch("dragon_quant.storage.assets_db._connect",
                     side_effect=lambda *args, **kwargs: sqlite3.connect(self._db_path))

    def _table_names(self) -> set[str]:
        conn = sqlite3.connect(self._db_path)
        try:
            return {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()


class TestAssetsSchema(AssetsDbTestBase):
    def test_four_tables_exist(self):
        from dragon_quant.storage import assets_db
        with self._patch():
            assets_db.get_prep_assets("1999-01-01")   # 触发 _ensure_schema
        self.assertTrue({
            "prep_assets", "prep_snapshots", "auction_assets", "auction_snapshots",
        }.issubset(self._table_names()))

    def test_manager_and_paths_share_assets_db_path(self):
        from dragon_quant.storage import manager, paths
        self.assertIs(manager.ASSETS_DB_PATH, paths.ASSETS_DB_PATH)

    def test_wrong_shaped_table_raises(self):
        """旧 dragon.db 形状的同名 auction_snapshots 误放路径 → 明确报错。"""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "CREATE TABLE auction_snapshots (trade_date TEXT, code TEXT, "
                "name TEXT, items_json TEXT)")
            conn.commit()
        finally:
            conn.close()

        from dragon_quant.storage import assets_db
        with self._patch():
            with self.assertRaises(assets_db.AssetsDbSchemaError):
                assets_db.get_prep_assets("1999-01-01")


class TestPrepAssets(AssetsDbTestBase):
    def test_roundtrip(self):
        from dragon_quant.storage import assets_db
        with self._patch():
            assets_db.save_prep_assets("2026-10-19", {"dragons": {"count": 2}},
                                       {"dragons": {"status": "ok"}})
            self.assertEqual(assets_db.get_prep_asset("2026-10-19", "dragons")["count"], 2)
            assets = assets_db.get_prep_assets("2026-10-19")
            self.assertIn("dragons", assets)
            self.assertIn("collection_report", assets)
            self.assertEqual(assets["collection_report"]["dragons"]["status"], "ok")

    def test_upsert_single_row_and_idempotent(self):
        from dragon_quant.storage import assets_db
        with self._patch():
            assets_db.save_prep_assets("2026-10-19", {"a": {"v": 1}}, {})
            assets_db.save_prep_assets("2026-10-19", {"a": {"v": 2}}, {})
            self.assertEqual(assets_db.get_prep_asset("2026-10-19", "a")["v"], 2)
            conn = sqlite3.connect(self._db_path)
            try:
                n = conn.execute("SELECT COUNT(*) FROM prep_assets WHERE asset_type='a'"
                                 ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(n, 1)

    def test_single_transaction_rollback(self):
        """任一 payload 序列化失败 → 整批不落库（无部分新旧混合）。"""
        from dragon_quant.storage import assets_db
        with self._patch():
            with self.assertRaises(ValueError):
                assets_db.save_prep_assets("2026-10-19",
                                           {"ok": {"v": 1}, "bad": {"v": float("nan")}}, {})
            self.assertEqual(assets_db.get_prep_assets("2026-10-19"), {})

    def test_missing_asset_returns_none(self):
        from dragon_quant.storage import assets_db
        with self._patch():
            self.assertIsNone(assets_db.get_prep_asset("1999-01-01", "x"))
            self.assertEqual(assets_db.get_prep_assets("1999-01-01"), {})


class TestPrepSnapshots(AssetsDbTestBase):
    def test_roundtrip_and_upsert(self):
        from dragon_quant.storage import assets_db
        with self._patch():
            assets_db.save_prep_snapshot("2026-10-19", {"sentiment": {"limit_up_cnt": 59}})
            self.assertTrue(assets_db.has_prep_snapshot("2026-10-19"))
            self.assertEqual(
                assets_db.get_prep_snapshot("2026-10-19")["sentiment"]["limit_up_cnt"], 59)
            assets_db.save_prep_snapshot("2026-10-19", {"v": 2})
            self.assertEqual(assets_db.get_prep_snapshot("2026-10-19"), {"v": 2})

    def test_missing_returns_none(self):
        from dragon_quant.storage import assets_db
        with self._patch():
            self.assertIsNone(assets_db.get_prep_snapshot("1999-01-01"))
            self.assertFalse(assets_db.has_prep_snapshot("1999-01-01"))

    def test_nan_rejected(self):
        from dragon_quant.storage import assets_db
        with self._patch():
            with self.assertRaises(ValueError):
                assets_db.save_prep_snapshot("2026-10-19", {"v": float("inf")})

    def test_assets_and_snapshots_isolated(self):
        """两表互不影响：删/写其一不改变另一。"""
        from dragon_quant.storage import assets_db
        with self._patch():
            assets_db.save_prep_assets("2026-10-19", {"a": {"v": 1}}, {})
            assets_db.save_prep_snapshot("2026-10-19", {"s": 1})
            self.assertIsNotNone(assets_db.get_prep_asset("2026-10-19", "a"))
            self.assertIsNotNone(assets_db.get_prep_snapshot("2026-10-19"))


class TestAuctionAssets(AssetsDbTestBase):
    def test_roundtrip_and_upsert(self):
        from dragon_quant.storage import assets_db
        items = [{"time": "09:25:00", "price": 10.0, "matched": 100, "unmatched": 50}]
        with self._patch():
            assets_db.save_auction_asset("2026-10-20", "600172", "黄河旋风", items)
            snap = assets_db.get_auction_asset("2026-10-20", "600172")
            self.assertEqual(snap["name"], "黄河旋风")
            self.assertEqual(snap["items"], items)

            assets_db.save_auction_asset(
                "2026-10-20", "600172", "新名字",
                [{"time": "09:15:00", "price": 9.0, "matched": 1, "unmatched": 0}])
            snap = assets_db.get_auction_asset("2026-10-20", "600172")
            self.assertEqual(snap["name"], "新名字")
            self.assertEqual(snap["items"][0]["price"], 9.0)
            self.assertEqual(len(assets_db.list_auction_assets("2026-10-20")), 1)

    def test_list_sorted_by_code(self):
        from dragon_quant.storage import assets_db
        with self._patch():
            for code in ("600002", "000001"):
                assets_db.save_auction_asset(code=code, trade_date="2026-10-20",
                                             name=code, items=[])
            codes = [r["code"] for r in assets_db.list_auction_assets("2026-10-20")]
        self.assertEqual(codes, ["000001", "600002"])

    def test_missing_returns_none_and_empty(self):
        from dragon_quant.storage import assets_db
        with self._patch():
            self.assertIsNone(assets_db.get_auction_asset("1999-01-01", "600172"))
            self.assertEqual(assets_db.list_auction_assets("1999-01-01"), [])

    def test_nan_rejected(self):
        from dragon_quant.storage import assets_db
        with self._patch():
            with self.assertRaises(ValueError):
                assets_db.save_auction_asset(
                    "2026-10-20", "600172", "x",
                    [{"time": "09:25:00", "price": float("nan"),
                      "matched": 1, "unmatched": 0}])

    def test_isolated_from_prep_tables(self):
        from dragon_quant.storage import assets_db
        with self._patch():
            assets_db.save_auction_asset("2026-10-20", "600172", "x", [])
            self.assertEqual(assets_db.get_prep_assets("2026-10-20"), {})


class TestReadDoesNotCreateDb(AssetsDbTestBase):
    """只读路径在库不存在时必须返回空值，且不落空库（防回归）。"""

    def test_reads_do_not_create_missing_db(self):
        from dragon_quant.storage import assets_db
        missing = Path(self._tmpdir.name) / "missing-assets.db"
        with patch.object(assets_db, "ASSETS_DB_PATH", missing):
            self.assertIsNone(assets_db.get_prep_asset("2026-10-20", "x"))
            self.assertEqual(assets_db.get_prep_assets("2026-10-20"), {})
            self.assertIsNone(assets_db.get_prep_snapshot("2026-10-20"))
            self.assertFalse(assets_db.has_prep_snapshot("2026-10-20"))
            self.assertIsNone(assets_db.get_auction_asset("2026-10-20", "600172"))
            self.assertEqual(assets_db.list_auction_assets("2026-10-20"), [])
            self.assertFalse(missing.exists())

            assets_db.save_prep_assets("2026-10-20", {"a": {"v": 1}}, {})
            self.assertTrue(missing.exists())


class TestLegacyPrepDbCompatibility(AssetsDbTestBase):
    """旧 prep.db 重命名为 assets.db 后可通过形状探针并补齐竞价表。"""

    def test_legacy_two_table_shape_is_upgraded(self):
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript("""
            CREATE TABLE prep_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(trade_date, asset_type)
            );
            CREATE TABLE prep_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'v2',
                result_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(trade_date, source)
            );
            """)
            conn.commit()
        finally:
            conn.close()

        from dragon_quant.storage import assets_db
        with self._patch():
            self.assertEqual(assets_db.get_prep_assets("2026-10-20"), {})
            assets_db.save_auction_asset("2026-10-20", "600172", "x", [])
        self.assertTrue({"auction_assets", "auction_snapshots"}.issubset(self._table_names()))


class TestAuctionSnapshotsTable(AssetsDbTestBase):
    """结果表本期仅建表：schema 可承载 AuctionVerdict 序列化契约。"""

    def test_table_accepts_analysis_json(self):
        import json
        from dragon_quant.storage import assets_db
        payload = {"version": "1", "code": "600172", "is_weak": False,
                   "strength": 72.5, "direction": "bull", "level": "强",
                   "dims": {}, "reject_reason": None, "unverified": True, "rank": 1}
        with self._patch():
            assets_db.get_prep_assets("1999-01-01")   # 建表
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT INTO auction_snapshots(trade_date, code, source, analysis_json) "
                "VALUES (?, ?, ?, ?)",
                ("2026-10-20", "600172", "v2", json.dumps(payload, ensure_ascii=False)))
            conn.commit()
            row = conn.execute(
                "SELECT analysis_json FROM auction_snapshots "
                "WHERE trade_date=? AND code=? AND source=?",
                ("2026-10-20", "600172", "v2")).fetchone()
        finally:
            conn.close()
        self.assertEqual(json.loads(row[0]), payload)


if __name__ == "__main__":
    unittest.main()
