"""
tests for canonical storage modules — paths + manager
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dragon_quant.infra.storage.paths import DATA_DIR, CACHE_DIR, LOG_DIR, RESULTS_DIR, SHARED_DIR
from dragon_quant.infra.storage import connection as storage_connection
from dragon_quant.review_account import storage as review_storage
from dragon_quant.scan.storage import dragons as dragon_storage
from dragon_quant.scan.storage import scans as scan_storage

from dragon_quant.tests.support import patch_storage_dbs


class TestPaths(unittest.TestCase):

    def test_data_dir_exists(self):
        self.assertIsInstance(DATA_DIR, Path)

    def test_cache_dir_exists(self):
        self.assertIsInstance(CACHE_DIR, Path)
        self.assertEqual(CACHE_DIR.parent, DATA_DIR)

    def test_log_dir_exists(self):
        self.assertIsInstance(LOG_DIR, Path)

    def test_results_dir_exists(self):
        self.assertIsInstance(RESULTS_DIR, Path)

    def test_shared_dir_exists(self):
        self.assertIsInstance(SHARED_DIR, Path)


class TestStorageManager(unittest.TestCase):

    def setUp(self):
        from dragon_quant.infra.storage.manager import StorageManager
        self.mgr = StorageManager()

    def tearDown(self):
        pass

    def test_status(self):
        status = self.mgr.status()
        self.assertIn("results", status)
        self.assertIn("logs", status)
        self.assertIn("assets_db", status)
        self.assertIn("legacy_prep_db", status)
        self.assertNotIn("prep_db", status)

    def test_size(self):
        size = self.mgr.size()
        self.assertIn("total_bytes", size)
        self.assertIn("by_dir", size)

    def test_clear_no_error(self):
        """clear 只作用于临时目录；manager 只清文件，日志表由 scan.storage.logs 负责。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in ("cache", "logs", "results", "shared"):
                (base / name).mkdir()
            with patch("dragon_quant.infra.storage.manager.CACHE_DIR", base / "cache"), \
                 patch("dragon_quant.infra.storage.manager.LOG_DIR", base / "logs"), \
                 patch("dragon_quant.infra.storage.manager.RESULTS_DIR", base / "results"), \
                 patch("dragon_quant.infra.storage.manager.SHARED_DIR", base / "shared"):
                self.mgr.clear_all()
                self.mgr.clear_results()
                self.mgr.clear_logs()

    def test_clear_all_protects_databases(self):
        """数据库文件永不进入清理计数，作为独立 protected_databases 字段返回。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in ("dragon.db", "assets.db", "prep.db"):
                (base / name).write_bytes(b"x")
            with patch("dragon_quant.infra.storage.manager.CACHE_DIR", base / "cache"), \
                 patch("dragon_quant.infra.storage.manager.LOG_DIR", base / "logs"), \
                 patch("dragon_quant.infra.storage.manager.RESULTS_DIR", base / "results"), \
                 patch("dragon_quant.infra.storage.manager.SHARED_DIR", base / "shared"), \
                 patch("dragon_quant.infra.storage.manager.DB_PATH", base / "dragon.db"), \
                 patch("dragon_quant.infra.storage.manager.ASSETS_DB_PATH", base / "assets.db"), \
                 patch("dragon_quant.infra.storage.manager.LEGACY_PREP_DB_PATH", base / "prep.db"):
                result = self.mgr.clear_all()

        protected = {Path(p).name for p in result["protected_databases"]}
        self.assertEqual(protected, {"dragon.db", "assets.db", "prep.db"})
        # 保护字段不混入清理计数
        for key in ("cache", "results", "logs", "shared"):
            self.assertIsInstance(result[key], int)


class TestGetPendingDragons(unittest.TestCase):
    """get_pending_dragons() 的 review_status 参数"""

    def setUp(self):
        import sqlite3
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        self._conn = sqlite3.connect(self._db_path)
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            storage_connection.init_db()

        # 插入测试数据
        self._conn.executemany(
            "INSERT INTO dragons_v2(trade_date, code, name, composite_score, review_status) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("20260521", "000725", "京东方A", 80.0, "completed"),
                ("20260522", "600172", "黄河旋风", 75.0, "pending"),
                ("20260525", "002975", "博杰股份", 70.0, "completed"),
            ],
        )
        self._conn.commit()

    def tearDown(self):
        self._conn.close()
        self._tmpdir.cleanup()

    def test_review_status_none_gets_all(self):
        """review_status=None 不做事态过滤，返回全量"""
        with patch("dragon_quant.infra.storage.connection._connect",
                   return_value=sqlite3.connect(self._db_path)):
            from dragon_quant.scan.storage.dragons import get_pending_dragons
            results = get_pending_dragons(review_status=None)
        self.assertEqual(len(results), 3)

    def test_review_status_pending_filters(self):
        """review_status='pending' 只返回 pending 记录"""
        with patch("dragon_quant.infra.storage.connection._connect",
                   return_value=sqlite3.connect(self._db_path)):
            from dragon_quant.scan.storage.dragons import get_pending_dragons
            results = get_pending_dragons(review_status="pending")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["code"], "600172")

    def test_review_status_default_is_pending(self):
        """默认参数 review_status='pending'"""
        with patch("dragon_quant.infra.storage.connection._connect",
                   return_value=sqlite3.connect(self._db_path)):
            from dragon_quant.scan.storage.dragons import get_pending_dragons
            results = get_pending_dragons()
        self.assertEqual(len(results), 1)


class TestGetReviewSummary(unittest.TestCase):
    """get_review_summary() 的汇总统计口径"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        self._conn = sqlite3.connect(self._db_path)
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            storage_connection.init_db()

    def tearDown(self):
        self._conn.close()
        self._tmpdir.cleanup()

    def _get_summary(self):
        with patch("dragon_quant.infra.storage.connection._connect",
                   return_value=sqlite3.connect(self._db_path)):
            from dragon_quant.scan.storage.dragons import get_review_summary
            return get_review_summary()

    def test_win_rate_requires_positive_return_and_drawdown_above_minus_5(self):
        self._conn.executemany(
            "INSERT INTO dragons_v2(trade_date, code, name, max_return_5d, max_drawdown_5d, review_status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("20260521", "000001", "样本A", 8.5, -3.2, "completed"),
                ("20260522", "000002", "样本B", 6.0, -5.0, "completed"),
                ("20260523", "000003", "样本C", 4.5, -6.2, "completed"),
                ("20260524", "000004", "样本D", -1.0, -2.0, "completed"),
                ("20260525", "000005", "样本E", 9.0, -1.0, "pending"),
            ],
        )
        self._conn.commit()

        summary = self._get_summary()

        self.assertEqual(summary["completed"], 4)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["win_rate"], 25.0)

    def test_win_rate_is_none_when_no_completed_rows(self):
        self._conn.execute(
            "INSERT INTO dragons_v2(trade_date, code, name, max_return_5d, max_drawdown_5d, review_status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("20260525", "000005", "样本E", 9.0, -1.0, "pending"),
        )
        self._conn.commit()

        summary = self._get_summary()

        self.assertEqual(summary["completed"], 0)
        self.assertEqual(summary["pending"], 1)
        self.assertIsNone(summary["win_rate"])

    def test_completed_rows_with_null_metrics_count_in_denominator_only(self):
        self._conn.executemany(
            "INSERT INTO dragons_v2(trade_date, code, name, max_return_5d, max_drawdown_5d, review_status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("20260521", "000001", "样本A", 8.5, -3.2, "completed"),
                ("20260522", "000002", "样本B", None, -1.0, "completed"),
                ("20260523", "000003", "样本C", 3.0, None, "completed"),
            ],
        )
        self._conn.commit()

        summary = self._get_summary()

        self.assertEqual(summary["completed"], 3)
        self.assertEqual(summary["win_rate"], 33.3)


class TestReviewAccountStorage(unittest.TestCase):
    """账户级 review 持久化查询。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        self._conn = sqlite3.connect(self._db_path)
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            storage_connection.init_db()

    def tearDown(self):
        self._conn.close()
        self._tmpdir.cleanup()

    def test_account_run_roundtrip(self):
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            run_id = review_storage.create_review_account_run(
                source="v2",
                strategy_name="dragon_pullback_daily",
                strategy_params_json='{"initial_cash":100000}',
                date_from="2026-05-01",
                date_to="2026-06-01",
                initial_cash=100000,
                final_equity=112000,
                total_return=12.0,
                max_drawdown=-3.0,
                trade_count=2,
                win_rate=50.0,
                display_name="五月账户回测",
            )
            review_storage.save_review_account_results(
                run_id,
                snapshots=[{
                    "trade_date": "2026-05-01", "cash": 0, "market_value": 100000,
                    "total_equity": 100000, "daily_return": 0, "cumulative_return": 0,
                    "drawdown": 0, "position_code": "000001", "position_name": "样本",
                    "position_qty": 1000, "position_cost": 10,
                    "position_market_price": 10, "position_unrealized_return": 0,
                }],
                trades=[{
                    "trade_date": "2026-05-01", "code": "000001", "name": "样本",
                    "side": "BUY", "price": 10, "qty": 1000, "amount": 10000,
                    "fee": 3, "realized_pnl": -3, "cash_after": 90000, "position_after": 1000,
                    "reason_code": "buy_ma5_pullback", "reason_text": "回踩MA5",
                    "signal_json": '{"ma5": 9.8}',
                }],
                positions=[],
                events=[{
                    "event_date": "2026-05-01", "event_type": "BUY",
                    "code": "000001", "name": "样本", "title": "买入 样本",
                    "detail": "回踩MA5", "reason_code": "buy_ma5_pullback",
                    "cash": 90000, "total_equity": 100000,
                    "signal_json": '{"ma5": 9.8}',
                }],
            )

            runs = review_storage.query_review_account_runs(source="v2")
            snapshots = review_storage.query_review_account_snapshots(run_id)
            trades = review_storage.query_review_account_trades(run_id)
            events = review_storage.query_review_account_events(run_id)

        self.assertEqual(runs[0]["id"], run_id)
        self.assertEqual(runs[0]["display_name"], "五月账户回测")
        self.assertEqual(runs[0]["total_return"], 12.0)
        self.assertEqual(snapshots[0]["position_code"], "000001")
        self.assertEqual(trades[0]["reason_code"], "buy_ma5_pullback")
        self.assertEqual(trades[0]["realized_pnl"], -3)
        self.assertEqual(trades[0]["signal"]["ma5"], 9.8)
        self.assertEqual(events[0]["event_type"], "BUY")
        self.assertEqual(events[0]["signal"]["ma5"], 9.8)

    def test_account_run_delete_cascades_children(self):
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            run_id = review_storage.create_review_account_run(
                source="v2",
                strategy_name="dragon_pullback_daily",
                strategy_params_json="{}",
                date_from="2026-05-01",
                date_to="2026-06-01",
                initial_cash=100000,
                final_equity=100000,
                total_return=0.0,
                max_drawdown=0.0,
                trade_count=1,
                win_rate=None,
                display_name="待删除记录",
            )
            review_storage.save_review_account_results(
                run_id,
                snapshots=[{
                    "trade_date": "2026-05-01", "cash": 90000, "market_value": 10000,
                    "total_equity": 100000, "daily_return": 0, "cumulative_return": 0,
                    "drawdown": 0, "position_code": "000001", "position_name": "样本",
                    "position_qty": 1000, "position_cost": 10,
                    "position_market_price": 10, "position_unrealized_return": 0,
                }],
                trades=[{
                    "trade_date": "2026-05-01", "code": "000001", "name": "样本",
                    "side": "BUY", "price": 10, "qty": 1000, "amount": 10000,
                    "fee": 3, "realized_pnl": -3, "cash_after": 90000, "position_after": 1000,
                    "reason_code": "buy_ma5_pullback", "reason_text": "回踩MA5",
                    "signal_json": "{}",
                }],
                positions=[{
                    "code": "000001", "name": "样本", "entry_date": "2026-05-01",
                    "entry_price": 10, "qty": 1000, "entry_reason_code": "buy_ma5_pullback",
                    "entry_signal_json": "{}", "exit_date": "2026-05-06", "exit_price": 11,
                    "exit_reason_code": "trailing_take_profit", "exit_signal_json": "{}",
                    "realized_return": 10.0, "hold_days": 3, "status": "closed",
                }],
                events=[{
                    "event_date": "2026-05-01", "event_type": "BUY",
                    "code": "000001", "name": "样本", "title": "买入 样本",
                    "detail": "回踩MA5", "reason_code": "buy_ma5_pullback",
                    "cash": 90000, "total_equity": 100000, "signal_json": "{}",
                }],
            )

            deleted = review_storage.delete_review_account_run(run_id)
            missing = review_storage.delete_review_account_run(run_id)

            runs = review_storage.query_review_account_runs(source="v2")
            snapshots = review_storage.query_review_account_snapshots(run_id)
            trades = review_storage.query_review_account_trades(run_id)
            positions = review_storage.query_review_account_positions(run_id)
            events = review_storage.query_review_account_events(run_id)

        self.assertTrue(deleted)
        self.assertFalse(missing)
        self.assertEqual(runs, [])
        self.assertEqual(snapshots, [])
        self.assertEqual(trades, [])
        self.assertEqual(positions, [])
        self.assertEqual(events, [])

    def test_post_exit_columns_roundtrip(self):
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            run_id = review_storage.create_review_account_run(
                source="v2", strategy_name="dragon_pullback_daily",
                strategy_params_json="{}", date_from="2026-05-01", date_to="2026-06-01",
                initial_cash=100000, final_equity=100000, total_return=0.0,
                max_drawdown=0.0, trade_count=1, win_rate=100.0,
            )
            review_storage.save_review_account_results(
                run_id, snapshots=[], trades=[],
                positions=[{
                    "code": "000001", "name": "样本", "entry_date": "2026-05-01",
                    "entry_price": 10, "qty": 1000, "entry_reason_code": "buy_open_ma5_pullback",
                    "entry_signal_json": "{}", "exit_date": "2026-05-06", "exit_price": 11,
                    "exit_reason_code": "trailing_take_profit", "exit_signal_json": "{}",
                    "realized_return": 10.0, "hold_days": 3, "status": "closed",
                    "post_exit_max_return": 3.2, "post_exit_max_drawdown": -1.4,
                    "post_exit_window_days": 5,
                }],
            )
            positions = review_storage.query_review_account_positions(run_id)

        self.assertEqual(len(positions), 1)
        self.assertAlmostEqual(positions[0]["post_exit_max_return"], 3.2)
        self.assertAlmostEqual(positions[0]["post_exit_max_drawdown"], -1.4)
        self.assertEqual(positions[0]["post_exit_window_days"], 5)

    def test_post_exit_columns_added_to_old_schema_as_null(self):
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            run_id = review_storage.create_review_account_run(
                source="v2", strategy_name="dragon_pullback_daily",
                strategy_params_json="{}", date_from="2026-05-01", date_to="2026-06-01",
                initial_cash=100000, final_equity=100000, total_return=0.0,
                max_drawdown=0.0, trade_count=1, win_rate=100.0,
            )
        # 模拟旧库：positions 表没有三列，且已有一行历史数据。
        self._conn.execute("DROP TABLE review_account_positions")
        self._conn.execute(
            """
            CREATE TABLE review_account_positions (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id               INTEGER NOT NULL,
                code                 TEXT NOT NULL,
                name                 TEXT,
                entry_date           TEXT,
                entry_price          REAL,
                qty                  INTEGER,
                entry_reason_code    TEXT,
                entry_signal_json    TEXT,
                exit_date            TEXT,
                exit_price           REAL,
                exit_reason_code     TEXT,
                exit_signal_json     TEXT,
                realized_return      REAL,
                hold_days            INTEGER,
                status               TEXT
            )
            """
        )
        self._conn.execute(
            "INSERT INTO review_account_positions("
            "run_id, code, entry_date, entry_price, qty, entry_reason_code, "
            "exit_date, exit_price, exit_reason_code, realized_return, hold_days, status"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, "000001", "2026-05-01", 10, 1000, "buy_open_ma5_pullback",
             "2026-05-06", 11, "trailing_take_profit", 10.0, 3, "closed"),
        )
        self._conn.commit()

        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            storage_connection.init_db()   # 幂等补列
            positions = review_storage.query_review_account_positions(run_id)

        self.assertEqual(len(positions), 1)
        self.assertIsNone(positions[0]["post_exit_max_return"])
        self.assertIsNone(positions[0]["post_exit_max_drawdown"])
        self.assertIsNone(positions[0]["post_exit_window_days"])

    def test_account_run_display_name_column_is_added_to_old_schema(self):
        self._conn.execute("DROP TABLE review_account_runs")
        self._conn.execute(
            """
            CREATE TABLE review_account_runs (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                source               TEXT DEFAULT 'v2',
                strategy_name        TEXT NOT NULL,
                strategy_params_json TEXT,
                date_from            TEXT NOT NULL,
                date_to              TEXT NOT NULL,
                initial_cash         REAL,
                final_equity         REAL,
                total_return         REAL,
                max_drawdown         REAL,
                trade_count          INTEGER,
                win_rate             REAL,
                created_at           TEXT DEFAULT (datetime('now','localtime'))
            )
            """
        )
        self._conn.commit()

        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            storage_connection.init_db()
            run_id = review_storage.create_review_account_run(
                source="v2",
                strategy_name="dragon_pullback_daily",
                strategy_params_json="{}",
                date_from="2026-05-01",
                date_to="2026-06-01",
                initial_cash=100000,
                final_equity=100000,
                total_return=0.0,
                max_drawdown=0.0,
                trade_count=0,
                win_rate=None,
                display_name="旧库补列测试",
            )
            run = review_storage.get_review_account_run(run_id)

        self.assertEqual(run["display_name"], "旧库补列测试")


if __name__ == "__main__":
    unittest.main()


class TestDragonsRebuildUnion(unittest.TestCase):
    """验证：同日多次扫描并集 + force 替换 topN 贡献 + 不删除 completed"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_union_and_force_replace(self):
        # db 层每次操作主动 close 连接，side_effect 每次返回新连接；
        # save_dragons 会 fan-out 双写 assets.db 镜像，故两个连接点一起 patch。
        with patch_storage_dbs(self._db_path):
            storage_connection.init_db()

            # 同日两次 top5 + 一次 top10
            day = "2026-06-05"
            scan1 = "20260605_100000_5_a"
            scan2 = "20260605_100500_5_b"
            scan3 = "20260605_101000_10_c"

            def mk(code, rank, score):
                return {
                    "code": code,
                    "name": code,
                    "rank": rank,
                    "composite_score": score,
                    "board_count": 1,
                    "concepts": [],
                    "dimensions": {},
                    "report_text": "",
                }

            scan_storage.save_scan(
                scan1, day, 1.0, 5, 5, 1,
                [mk(c, i + 1, 90 - i) for i, c in enumerate(list("abcde"))])
            scan_storage.save_scan(
                scan2, day, 1.0, 5, 5, 1,
                [mk(c, i + 1, 80 - i) for i, c in enumerate(list("abxyz"))])
            scan_storage.save_scan(
                scan3, day, 1.0, 10, 5, 1,
                [mk(c, i + 1, 70 - i) for i, c in enumerate(list("abcdefghij"))])

            # 先给 e 标记 completed，确保后续删除 pending 时不删它
            dragon_storage.save_dragons(day, [{
                "code": "e", "name": "e", "scan_id": scan1, "rank": 5,
                "composite_score": 86, "board_count": 1, "concepts": [], "report_text": "",
            }], version="")
            dragon_storage.update_dragon_review(day, "e", review_status="completed")

            # calendar: 简化为包含 day 以及 30 天内一堆日期，避免 5 日 gate 干扰
            calendar = {day, "2026-06-04", "2026-06-03", "2026-06-02", "2026-06-01"}
            stats = dragon_storage.rebuild_dragons_for_date(day, version="x", calendar=calendar, apply_5day_gate=False)
            self.assertGreaterEqual(stats["upserted"], 1)

            # 并集应包含 a..j + x,y,z（e 也应在）
            rows = dragon_storage.get_dragons(day)
            codes = {r["code"] for r in rows}
            for c in list("abcdefghij") + list("xyz"):
                self.assertIn(c, codes)
            self.assertIn("e", codes)

            # force 替换 top5：删除旧 top5 runs，然后写一个新的 top5=abcdn
            deleted = scan_storage.delete_scans_by_date_topn(day, 5)
            self.assertEqual(deleted, 2)
            scan4 = "20260605_110000_5_force"
            scan_storage.save_scan(
                scan4, day, 1.0, 5, 5, 1,
                [mk(c, i + 1, 60 - i) for i, c in enumerate(list("abcdn"))])

            dragon_storage.rebuild_dragons_for_date(day, version="x", calendar=calendar, apply_5day_gate=False)
            rows2 = dragon_storage.get_dragons(day)
            codes2 = {r["code"] for r in rows2}
            # top10 贡献仍在（a..j），top5 贡献已替换，新出现 n
            self.assertIn("n", codes2)
            # e 是 completed，不应因为不在贡献并集而被删除
            self.assertIn("e", codes2)


class TestSaveDragonsSourceIsolation(unittest.TestCase):
    """验证：dragons_v1 / dragons_v2 分表隔离，互不覆盖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")

    def tearDown(self):
        self._tmpdir.cleanup()

    def _dragon_row(self, table, trade_date, code):
        conn = sqlite3.connect(self._db_path)
        try:
            r = conn.execute(
                f"SELECT rank, composite_score, report_text, source FROM {table} WHERE trade_date=? AND code=?",
                (trade_date, code)).fetchone()
            return r
        finally:
            conn.close()

    def test_schema_has_only_versioned_core_tables(self):
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            storage_connection.init_db()

        conn = sqlite3.connect(self._db_path)
        try:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertIn("scans_v1", tables)
            self.assertIn("scans_v2", tables)
            self.assertIn("dragons_v1", tables)
            self.assertIn("dragons_v2", tables)
            self.assertIn("scan_logs_v1", tables)
            self.assertIn("scan_logs_v2", tables)
            self.assertNotIn("scans", tables)
            self.assertNotIn("scan_stocks", tables)
            self.assertNotIn("dragons", tables)
            self.assertNotIn("scan_logs", tables)

            dragon_cols = {r[1] for r in conn.execute("PRAGMA table_info(dragons_v1)")}
            self.assertIn("source", dragon_cols)
            self.assertNotIn("scorer_version", dragon_cols)
        finally:
            conn.close()

    def test_scorer_version_parameter_is_not_supported(self):
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            storage_connection.init_db()
            with self.assertRaises(TypeError):
                dragon_storage.save_dragons("2026-06-20", [{"code": "A"}], scorer_version="v2")

    def test_default_v2_and_explicit_v1_are_separate_tables(self):
        # db 层每次操作主动 close 连接，side_effect 每次返回新连接；
        # v2 写入会 fan-out 双写 assets.db 镜像，故两个连接点一起 patch。
        with patch_storage_dbs(self._db_path):
            storage_connection.init_db()
            D = "2026-06-20"

            # 默认参数 → v2（向前兼容已有 dragons_v2 数据）
            dragon_storage.save_dragons(D, [{"code": "W", "rank": 1, "composite_score": 90, "report_text": "v2"}])
            self.assertEqual(self._dragon_row("dragons_v2", D, "W")[3], "v2")
            self.assertIsNone(self._dragon_row("dragons_v1", D, "W"))

            # 同日同票 v1/v2 各保存一行，rank/score/report 互不覆盖
            dragon_storage.save_dragons(
                D, [{"code": "A", "rank": 1, "composite_score": 91,
                     "report_text": "from-v1"}], source="v1")
            dragon_storage.save_dragons(
                D, [{"code": "A", "rank": 2, "composite_score": 82,
                     "report_text": "from-v2"}], source="v2")
            self.assertEqual(self._dragon_row("dragons_v1", D, "A")[:3], (1, 91.0, "from-v1"))
            self.assertEqual(self._dragon_row("dragons_v2", D, "A")[:3], (2, 82.0, "from-v2"))

    def test_query_and_review_update_are_source_scoped(self):
        with patch_storage_dbs(self._db_path):
            storage_connection.init_db()
            D = "2026-06-20"
            dragon_storage.save_dragons(D, [{"code": "A", "rank": 1}, {"code": "B", "rank": 2}],
                            source="v1")
            dragon_storage.save_dragons(D, [{"code": "A", "rank": 1}, {"code": "C", "rank": 2}],
                            source="v2")

            self.assertEqual({r["code"] for r in dragon_storage.get_pending_dragons(source="v1")}, {"A", "B"})
            self.assertEqual({r["code"] for r in dragon_storage.get_pending_dragons(source="v2")}, {"A", "C"})

            dragon_storage.update_dragon_review(D, "A", max_return_5d=10.0, review_status="completed", source="v1")
            self.assertEqual(dragon_storage.get_review_summary(source="v1")["completed"], 1)
            self.assertEqual(dragon_storage.get_review_summary(source="v2")["completed"], 0)

    def test_is_true_dragon_written_and_read_for_v2(self):
        with patch_storage_dbs(self._db_path):
            storage_connection.init_db()
            D = "2026-06-20"

            dragon_cols = {r[1] for r in sqlite3.connect(self._db_path)
                           .execute("PRAGMA table_info(dragons_v2)")}
            self.assertIn("is_true_dragon", dragon_cols)

            dragon_storage.save_dragons(D, [
                {"code": "A", "rank": 1, "composite_score": 90, "is_true_dragon": True},
                {"code": "B", "rank": 2, "composite_score": 80, "is_true_dragon": False},
            ], source="v2")

            rows = {r["code"]: r for r in dragon_storage.get_dragons(D, source="v2")}
            self.assertIs(rows["A"]["is_true_dragon"], True)
            self.assertIs(rows["B"]["is_true_dragon"], False)

            pend = {r["code"]: r for r in dragon_storage.get_pending_dragons(source="v2")}
            self.assertIs(pend["A"]["is_true_dragon"], True)
            self.assertIs(pend["B"]["is_true_dragon"], False)

            q = {r["code"]: r for r in dragon_storage.query_dragons(source="v2")}
            self.assertIs(q["A"]["is_true_dragon"], True)

    def test_is_true_dragon_is_none_when_not_provided(self):
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            storage_connection.init_db()
            D = "2026-06-20"
            dragon_storage.save_dragons(D, [{"code": "A", "rank": 1}], source="v1")
            rows = dragon_storage.get_dragons(D, source="v1")
            self.assertIsNone(rows[0]["is_true_dragon"])


class TestDragonDbHasNoAuctionStorage(unittest.TestCase):
    """存储层重构回归：dragon.db 不再创建/暴露竞价相关表与 API。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "dragon.db")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_init_db_does_not_create_auction_tables(self):
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            storage_connection.init_db()
        conn = sqlite3.connect(self._db_path)
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        self.assertNotIn("auction_snapshots", names)
        self.assertNotIn("auction_assets", names)

    def test_db_no_longer_exports_auction_apis(self):
        for name in ("save_auction_snapshot", "get_auction_snapshot",
                     "list_auction_snapshots"):
            self.assertFalse(hasattr(dragon_storage, name), f"dragon_storage.{name} 应已移除")

    def test_legacy_auction_table_is_ignored(self):
        """旧库已有 auction_snapshots 时，新代码不依赖也不报错。"""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "CREATE TABLE auction_snapshots (trade_date TEXT, code TEXT, "
                "name TEXT, items_json TEXT)")
            conn.commit()
        finally:
            conn.close()
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            storage_connection.init_db()
            dragon_storage.save_dragons("2026-10-20", [{"code": "A", "rank": 1}], source="v1")
            rows = dragon_storage.get_dragons("2026-10-20", source="v1")
        self.assertEqual(len(rows), 1)
