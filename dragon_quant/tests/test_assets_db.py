"""assets.db（数据资产独立库）存储层单元测试。

覆盖：4 表 schema、prep 资产/快照、auction 原始明细、形状探针、真实库守护。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dragon_quant.infra.storage.paths import ASSETS_DB_PATH


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
            "测试写入了数据目录的 assets.db：请检查 patch 目标是否失效"
            "（pytest 下该路径已被 conftest 重定向到临时目录）",
        )
        self._tmpdir.cleanup()

    def _patch(self):
        return patch("dragon_quant.infra.assets.db._connect",
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
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            assets_db.get_prep_assets("1999-01-01")   # 触发 _ensure_schema
        self.assertTrue({
            "prep_assets", "prep_snapshots", "auction_assets", "auction_snapshots",
        }.issubset(self._table_names()))

    def test_manager_and_paths_share_assets_db_path(self):
        from dragon_quant.infra.storage import manager, paths
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

        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            with self.assertRaises(assets_db.AssetsDbSchemaError):
                assets_db.get_prep_assets("1999-01-01")


class TestPrepAssets(AssetsDbTestBase):
    def test_roundtrip(self):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            assets_db.save_prep_assets("2026-10-19", {"dragons": {"count": 2}},
                                       {"dragons": {"status": "ok"}})
            self.assertEqual(assets_db.get_prep_asset("2026-10-19", "dragons")["count"], 2)
            assets = assets_db.get_prep_assets("2026-10-19")
            self.assertIn("dragons", assets)
            self.assertIn("collection_report", assets)
            self.assertEqual(assets["collection_report"]["dragons"]["status"], "ok")

    def test_upsert_single_row_and_idempotent(self):
        from dragon_quant.infra.assets import db as assets_db
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

    def test_partial_report_merge_keeps_existing(self):
        """逐键合并：未出现在本次 report 的条目沿用存量。"""
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            assets_db.save_prep_assets("2026-10-19", {"a": {"v": 1}},
                                       {"dragons": {"status": "ok"}})
            assets_db.save_prep_assets("2026-10-19", {"b": {"v": 2}},
                                       {"market_breadth": {"status": "unavailable"}})
            report = assets_db.get_prep_asset("2026-10-19", "collection_report")
        self.assertEqual(report["dragons"], {"status": "ok"})
        self.assertEqual(report["market_breadth"], {"status": "unavailable"})

    def test_empty_report_leaves_report_untouched(self):
        """report 为空 → 不触碰 report 行（整日零采集语义）。"""
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            assets_db.save_prep_assets("2026-10-19", {"a": {"v": 1}},
                                       {"dragons": {"status": "ok"}})
            assets_db.save_prep_assets("2026-10-19", {"a": {"v": 2}}, {})
            assets_db.save_prep_assets("2026-10-19", {"a": {"v": 3}}, None)
            report = assets_db.get_prep_asset("2026-10-19", "collection_report")
            payload = assets_db.get_prep_asset("2026-10-19", "a")
        self.assertEqual(report, {"dragons": {"status": "ok"}})
        self.assertEqual(payload, {"v": 3})

    def test_malformed_report_is_reset_with_audit_key(self):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            assets_db.save_prep_assets("2026-10-19", {"a": {"v": 1}},
                                       {"dragons": {"status": "ok"}})
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(
                    "UPDATE prep_assets SET payload_json='[1,2]' "
                    "WHERE trade_date=? AND asset_type='collection_report'",
                    ("2026-10-19",))
                conn.commit()
            finally:
                conn.close()
            assets_db.save_prep_assets("2026-10-19", {"b": {"v": 2}},
                                       {"market_breadth": {"status": "ok"}})
            report = assets_db.get_prep_asset("2026-10-19", "collection_report")
        self.assertEqual(report, {"market_breadth": {"status": "ok"},
                                  "report_reset": "malformed"})

    def test_noop_when_payloads_and_report_empty(self):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            assets_db.save_prep_assets("2026-10-19", {}, {})
        self.assertFalse(Path(self._db_path).exists())

    def test_rollback_covers_report_merge(self):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            assets_db.save_prep_assets("2026-10-19", {"a": {"v": 1}},
                                       {"dragons": {"status": "ok"}})
            with self.assertRaises(ValueError):
                assets_db.save_prep_assets("2026-10-19",
                                           {"bad": {"v": float("nan")}},
                                           {"market_breadth": {"status": "ok"}})
            self.assertEqual(
                assets_db.get_prep_asset("2026-10-19", "collection_report"),
                {"dragons": {"status": "ok"}})

    def test_single_transaction_rollback(self):
        """任一 payload 序列化失败 → 整批不落库（无部分新旧混合）。"""
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            with self.assertRaises(ValueError):
                assets_db.save_prep_assets("2026-10-19",
                                           {"ok": {"v": 1}, "bad": {"v": float("nan")}}, {})
            self.assertEqual(assets_db.get_prep_assets("2026-10-19"), {})

    def test_missing_asset_returns_none(self):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            self.assertIsNone(assets_db.get_prep_asset("1999-01-01", "x"))
            self.assertEqual(assets_db.get_prep_assets("1999-01-01"), {})


class TestPrepSnapshots(AssetsDbTestBase):
    def test_roundtrip_and_upsert(self):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            assets_db.save_prep_snapshot("2026-10-19", {"sentiment": {"limit_up_cnt": 59}})
            self.assertTrue(assets_db.has_prep_snapshot("2026-10-19"))
            self.assertEqual(
                assets_db.get_prep_snapshot("2026-10-19")["sentiment"]["limit_up_cnt"], 59)
            assets_db.save_prep_snapshot("2026-10-19", {"v": 2})
            self.assertEqual(assets_db.get_prep_snapshot("2026-10-19"), {"v": 2})

    def test_missing_returns_none(self):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            self.assertIsNone(assets_db.get_prep_snapshot("1999-01-01"))
            self.assertFalse(assets_db.has_prep_snapshot("1999-01-01"))

    def test_nan_rejected(self):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            with self.assertRaises(ValueError):
                assets_db.save_prep_snapshot("2026-10-19", {"v": float("inf")})

    def test_assets_and_snapshots_isolated(self):
        """两表互不影响：删/写其一不改变另一。"""
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            assets_db.save_prep_assets("2026-10-19", {"a": {"v": 1}}, {})
            assets_db.save_prep_snapshot("2026-10-19", {"s": 1})
            self.assertIsNotNone(assets_db.get_prep_asset("2026-10-19", "a"))
            self.assertIsNotNone(assets_db.get_prep_snapshot("2026-10-19"))


class TestAuctionAssets(AssetsDbTestBase):
    def test_roundtrip_and_upsert(self):
        from dragon_quant.infra.assets import db as assets_db
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
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            for code in ("600002", "000001"):
                assets_db.save_auction_asset(code=code, trade_date="2026-10-20",
                                             name=code, items=[])
            codes = [r["code"] for r in assets_db.list_auction_assets("2026-10-20")]
        self.assertEqual(codes, ["000001", "600002"])

    def test_missing_returns_none_and_empty(self):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            self.assertIsNone(assets_db.get_auction_asset("1999-01-01", "600172"))
            self.assertEqual(assets_db.list_auction_assets("1999-01-01"), [])

    def test_quote_roundtrip_and_null(self):
        from dragon_quant.infra.assets import db as assets_db
        items = [{"time": "09:25:00", "price": 10.0, "matched": 100, "unmatched": 50}]
        quote = {"code": "600172", "prev_close": 10.0, "limit_down": 9.0,
                 "float_market_cap": 3.2e9, "price": 10.5}
        with self._patch():
            assets_db.save_auction_asset("2026-10-20", "600172", "黄河旋风",
                                         items, quote=quote)
            self.assertEqual(assets_db.get_auction_asset("2026-10-20", "600172")["quote"], quote)
            assets_db.save_auction_asset("2026-10-20", "600001", "A", items)
            self.assertIsNone(assets_db.get_auction_asset("2026-10-20", "600001")["quote"])
            by_code = {r["code"]: r for r in assets_db.list_auction_assets("2026-10-20")}
            self.assertEqual(by_code["600172"]["quote"], quote)
            self.assertIsNone(by_code["600001"]["quote"])

    def test_nan_rejected(self):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            with self.assertRaises(ValueError):
                assets_db.save_auction_asset(
                    "2026-10-20", "600172", "x",
                    [{"time": "09:25:00", "price": float("nan"),
                      "matched": 1, "unmatched": 0}])

    def test_null_quote_keeps_previous_quote(self):
        """同日重采行情缺失不得抹掉上次成功快照（原始资产不可再生）。"""
        from dragon_quant.infra.assets import db as assets_db
        quote = {"code": "600172", "prev_close": 10.0, "limit_down": 9.0}
        with self._patch():
            assets_db.save_auction_asset("2026-10-20", "600172", "x", [], quote=quote)
            assets_db.save_auction_asset("2026-10-20", "600172", "x", [], quote=None)
            self.assertEqual(
                assets_db.get_auction_asset("2026-10-20", "600172")["quote"], quote)

    def test_quote_nan_rejected(self):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            with self.assertRaises(ValueError):
                assets_db.save_auction_asset(
                    "2026-10-20", "600172", "x", [],
                    quote={"code": "600172", "price": float("nan")})

    def test_isolated_from_prep_tables(self):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            assets_db.save_auction_asset("2026-10-20", "600172", "x", [])
            self.assertEqual(assets_db.get_prep_assets("2026-10-20"), {})


class TestReadDoesNotCreateDb(AssetsDbTestBase):
    """只读路径在库不存在时必须返回空值，且不落空库（防回归）。"""

    def test_reads_do_not_create_missing_db(self):
        from dragon_quant.infra.assets import db as assets_db
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

        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            self.assertEqual(assets_db.get_prep_assets("2026-10-20"), {})
            assets_db.save_auction_asset("2026-10-20", "600172", "x", [])
        self.assertTrue({"auction_assets", "auction_snapshots"}.issubset(self._table_names()))


class TestAuctionSnapshotsTable(AssetsDbTestBase):
    """快照表已由采集/重算写入；本用例校验 analysis_json 契约可被承载。"""

    def test_table_accepts_analysis_json(self):
        import json
        from dragon_quant.infra.assets import db as assets_db
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


class TestAuctionQuoteMigration(AssetsDbTestBase):
    """旧 auction_assets 缺 quote_json：读路径不写 schema，写路径幂等补列。"""

    def _create_old_schema(self):
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript("""
            CREATE TABLE auction_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                items_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(trade_date, code)
            );
            CREATE TABLE auction_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                code TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'v2',
                analysis_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(trade_date, code, source)
            );
            """)
            conn.execute(
                "INSERT INTO auction_assets(trade_date, code, name, items_json) "
                "VALUES ('2026-10-19', '600001', 'old', '[]')")
            conn.commit()
        finally:
            conn.close()

    def _columns(self, table):
        conn = sqlite3.connect(self._db_path)
        try:
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        finally:
            conn.close()

    def test_read_path_does_not_migrate(self):
        self._create_old_schema()
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            asset = assets_db.get_auction_asset("2026-10-19", "600001")
            self.assertEqual(asset["items"], [])
            self.assertIsNone(asset["quote"])
            self.assertEqual(len(assets_db.list_auction_assets("2026-10-19")), 1)
        self.assertNotIn("quote_json", self._columns("auction_assets"))

    def test_write_path_migrates_idempotently(self):
        self._create_old_schema()
        from dragon_quant.infra.assets import db as assets_db
        quote = {"code": "600172", "prev_close": 10.0}
        with self._patch():
            assets_db.save_auction_asset("2026-10-20", "600172", "x", [], quote=quote)
            assets_db.save_auction_asset("2026-10-20", "600172", "x", [], quote=quote)
            self.assertEqual(assets_db.get_auction_asset("2026-10-20", "600172")["quote"], quote)
            self.assertIsNone(assets_db.get_auction_asset("2026-10-19", "600001")["quote"])
        self.assertIn("quote_json", self._columns("auction_assets"))

    def test_wrong_db_still_raises_on_read_and_write(self):
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("CREATE TABLE auction_snapshots (trade_date TEXT, code TEXT, "
                         "name TEXT, items_json TEXT)")
            conn.commit()
        finally:
            conn.close()
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            with self.assertRaises(assets_db.AssetsDbSchemaError):
                assets_db.get_auction_asset("2026-10-20", "600172")
            with self.assertRaises(assets_db.AssetsDbSchemaError):
                assets_db.save_auction_asset("2026-10-20", "600172", "x", [])


    def test_wrong_db_write_does_not_alter_before_probe(self):
        """错库必须先被形状探针拦截，绝不能先 ALTER 再报错。"""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript("""
            CREATE TABLE auction_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                items_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(trade_date, code)
            );
            CREATE TABLE auction_snapshots (
                trade_date TEXT, code TEXT, name TEXT, items_json TEXT
            );
            """)
            conn.commit()
        finally:
            conn.close()

        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            with self.assertRaises(assets_db.AssetsDbSchemaError):
                assets_db.save_auction_asset("2026-10-20", "600172", "x", [])
        self.assertNotIn("quote_json", self._columns("auction_assets"))


class TestAuctionSnapshotsReplace(AssetsDbTestBase):
    def _rows(self, trade_date="2026-10-20", source="v2"):
        conn = sqlite3.connect(self._db_path)
        try:
            return conn.execute(
                "SELECT code, analysis_json FROM auction_snapshots "
                "WHERE trade_date=? AND source=? ORDER BY code",
                (trade_date, source),
            ).fetchall()
        finally:
            conn.close()

    def test_replace_rerun_and_stale_removal(self):
        from dragon_quant.infra.assets import db as assets_db
        p1 = {"version": "1", "code": "600001", "strength": 10.0}
        p2 = {"version": "1", "code": "600002", "strength": 20.0}
        with self._patch():
            self.assertEqual(
                assets_db.replace_auction_snapshots("2026-10-20", {"600001": p1, "600002": p2}),
                2)
            self.assertEqual(len(self._rows()), 2)
            self.assertEqual(
                assets_db.replace_auction_snapshots("2026-10-20", {"600001": p1}), 1)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "600001")

    def test_empty_payloads_keeps_old_rows(self):
        from dragon_quant.infra.assets import db as assets_db
        p1 = {"version": "1", "code": "600001"}
        with self._patch():
            assets_db.replace_auction_snapshots("2026-10-20", {"600001": p1})
            self.assertEqual(assets_db.replace_auction_snapshots("2026-10-20", {}), 0)
        self.assertEqual(len(self._rows()), 1)

    def test_nan_payload_rolls_back(self):
        from dragon_quant.infra.assets import db as assets_db
        p1 = {"version": "1", "code": "600001"}
        with self._patch():
            assets_db.replace_auction_snapshots("2026-10-20", {"600001": p1})
            with self.assertRaises(ValueError):
                assets_db.replace_auction_snapshots(
                    "2026-10-20", {"600002": {"x": float("nan")}})
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "600001")


class TestAuctionSnapshotRead(AssetsDbTestBase):
    """竞价快照读接口：无库降级、摊平契约、逐行损坏隔离。"""

    def _seed(self, payloads):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            assets_db.replace_auction_snapshots("2026-10-20", payloads, source="v2")

    def test_missing_db_returns_empty_and_none(self):
        from dragon_quant.infra.assets import db as assets_db
        with patch("dragon_quant.infra.assets.db._connect", return_value=None):
            self.assertEqual(assets_db.list_auction_snapshots("2026-10-20"), [])
            self.assertIsNone(assets_db.get_auction_snapshot("2026-10-20", "600001"))

    def test_hit_miss_and_flattened_contract(self):
        payload = {"version": "1", "code": "600001", "is_weak": False,
                   "strength": 72.5, "direction": "bull", "level": "强",
                   "dims": {}, "reject_reason": None, "unverified": True, "rank": None}
        self._seed({"600001": payload})
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            rows = assets_db.list_auction_snapshots("2026-10-20")
            hit = assets_db.get_auction_snapshot("2026-10-20", "600001")
            miss = assets_db.get_auction_snapshot("2026-10-20", "600002")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "600001")
        self.assertIs(rows[0]["is_weak"], False)
        self.assertEqual(rows[0]["strength"], 72.5)
        self.assertIs(rows[0]["unverified"], True)
        self.assertEqual(hit["level"], "强")
        self.assertIsNone(miss)

    def test_corrupt_analysis_row_isolated(self):
        self._seed({"600001": {"version": "1", "code": "600001", "is_weak": False},
                    "600002": {"version": "1", "code": "600002", "is_weak": True}})
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "UPDATE auction_snapshots SET analysis_json='{bad' "
                "WHERE trade_date=? AND code=?", ("2026-10-20", "600001"))
            conn.commit()
        finally:
            conn.close()
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            rows = assets_db.list_auction_snapshots("2026-10-20")
            bad = assets_db.get_auction_snapshot("2026-10-20", "600001")
            good = assets_db.get_auction_snapshot("2026-10-20", "600002")
        self.assertEqual(len(rows), 2)
        by_code = {r["code"]: r for r in rows}
        self.assertIn("JSONDecodeError", by_code["600001"]["error"])
        self.assertNotIn("error", by_code["600002"])
        self.assertIn("JSONDecodeError", bad["error"])
        self.assertIs(good["is_weak"], True)


class TestAuctionCorruptJson(AssetsDbTestBase):
    """单行 JSON 损坏必须逐行隔离，不得打断整批读取。"""

    def _seed(self, code, items_json, quote_json):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            assets_db.save_auction_asset("2026-10-20", code, "x", [])
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "UPDATE auction_assets SET items_json=?, quote_json=? "
                "WHERE trade_date=? AND code=?",
                (items_json, quote_json, "2026-10-20", code))
            conn.commit()
        finally:
            conn.close()

    def test_corrupt_quote_isolated(self):
        self._seed("600001", "[]", "{not-json")
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            asset = assets_db.get_auction_asset("2026-10-20", "600001")
            self.assertIsNone(asset["quote"])
            self.assertIn("JSONDecodeError", asset["quote_error"])
            rows = assets_db.list_auction_assets("2026-10-20")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["quote"])
        self.assertIsNotNone(rows[0]["quote_error"])

    def test_corrupt_items_isolated(self):
        self._seed("600001", "{bad", None)
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            asset = assets_db.get_auction_asset("2026-10-20", "600001")
            self.assertEqual(asset["items"], [])
            self.assertIn("JSONDecodeError", asset["items_error"])
            self.assertEqual(len(assets_db.list_auction_assets("2026-10-20")), 1)


if __name__ == "__main__":
    unittest.main()


class TestPreviousPrepSnapshot(AssetsDbTestBase):
    """严格早于 before_date 的最近快照（prev 注入；绝不返回当日）。"""

    def _save(self, day, version="0.8", source="v2"):
        from dragon_quant.infra.assets import db as assets_db
        with self._patch():
            assets_db.save_prep_snapshot(day, {"version": version, "trade_date": day},
                                         source=source)

    def test_strictly_before_returns_latest_earlier_snapshot(self):
        from dragon_quant.infra.assets import db as assets_db
        self._save("2026-10-16")
        self._save("2026-10-18")
        self._save("2026-10-19")
        with self._patch():
            payload, meta = assets_db.get_previous_prep_snapshot("2026-10-19")
        self.assertEqual(payload["trade_date"], "2026-10-18")
        self.assertEqual(meta["trade_date"], "2026-10-18")
        self.assertIsNone(meta["error"])

    def test_same_day_snapshot_is_never_returned(self):
        from dragon_quant.infra.assets import db as assets_db
        self._save("2026-10-19")
        with self._patch():
            payload, meta = assets_db.get_previous_prep_snapshot("2026-10-19")
        self.assertIsNone(payload)
        self.assertIsNone(meta["trade_date"])

    def test_first_day_has_no_previous_snapshot(self):
        from dragon_quant.infra.assets import db as assets_db
        self._save("2026-10-19")
        with self._patch():
            payload, meta = assets_db.get_previous_prep_snapshot("2026-10-16")
        self.assertIsNone(payload)
        self.assertEqual(meta, {"trade_date": None, "created_at": None, "error": None})

    def test_missing_db_is_read_only(self):
        from dragon_quant.infra.assets import db as assets_db
        missing = str(Path(self._tmpdir.name) / "absent.db")
        with patch("dragon_quant.infra.assets.db._connect",
                   side_effect=lambda *a, **k: None):
            payload, meta = assets_db.get_previous_prep_snapshot("2026-10-19")
        self.assertIsNone(payload)
        self.assertIsNone(meta["error"])
        self.assertFalse(Path(missing).exists())

    def test_corrupt_snapshot_reports_error(self):
        from dragon_quant.infra.assets import db as assets_db
        self._save("2026-10-18")
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("UPDATE prep_snapshots SET result_json='{bad' "
                         "WHERE trade_date='2026-10-18'")
            conn.commit()
        finally:
            conn.close()
        with self._patch():
            payload, meta = assets_db.get_previous_prep_snapshot("2026-10-19")
        self.assertIsNone(payload)
        self.assertEqual(meta["trade_date"], "2026-10-18")
        self.assertIn("JSONDecodeError", meta["error"])

    def test_source_filter_is_applied(self):
        from dragon_quant.infra.assets import db as assets_db
        self._save("2026-10-18", source="v1")
        with self._patch():
            payload, meta = assets_db.get_previous_prep_snapshot("2026-10-19")
        self.assertIsNone(payload)
        self.assertIsNone(meta["trade_date"])
