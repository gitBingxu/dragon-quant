"""
tests for dragon_quant.storage — paths + manager
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dragon_quant.storage.paths import DATA_DIR, CACHE_DIR, LOG_DIR, RESULTS_DIR, SHARED_DIR


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
        from dragon_quant.storage.manager import StorageManager
        self.mgr = StorageManager()

    def tearDown(self):
        pass

    def test_status(self):
        status = self.mgr.status()
        self.assertIn("results", status)
        self.assertIn("logs", status)

    def test_size(self):
        size = self.mgr.size()
        self.assertIn("total_bytes", size)
        self.assertIn("by_dir", size)

    def test_clear_no_error(self):
        self.mgr.clear_all()
        self.mgr.clear_results()
        self.mgr.clear_logs()


class TestGetPendingDragons(unittest.TestCase):
    """get_pending_dragons() 的 review_status 参数"""

    def setUp(self):
        import sqlite3
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        self._conn = sqlite3.connect(self._db_path)

        # 建表（与 db.py _ensure_schema 一致的最小结构）
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS dragons (
                trade_date TEXT, code TEXT, name TEXT,
                scan_id TEXT, rank INTEGER, composite_score REAL,
                board_count INTEGER, open_px REAL, close_px REAL,
                high_px REAL, low_px REAL, pct REAL,
                turnover_rate REAL, amount REAL, market_cap REAL,
                concepts_json TEXT, report_text TEXT,
                buy_date TEXT, buy_price REAL,
                max_return_5d REAL, max_drawdown_5d REAL,
                max_return_hold_days INTEGER,
                review_status TEXT, version TEXT DEFAULT ''
            )
        """)
        # 插入测试数据
        self._conn.executemany(
            "INSERT INTO dragons(trade_date, code, name, composite_score, review_status) "
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
        with patch("dragon_quant.storage.db._connect",
                   return_value=sqlite3.connect(self._db_path)):
            from dragon_quant.storage.db import get_pending_dragons
            results = get_pending_dragons(review_status=None)
        self.assertEqual(len(results), 3)

    def test_review_status_pending_filters(self):
        """review_status='pending' 只返回 pending 记录"""
        with patch("dragon_quant.storage.db._connect",
                   return_value=sqlite3.connect(self._db_path)):
            from dragon_quant.storage.db import get_pending_dragons
            results = get_pending_dragons(review_status="pending")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["code"], "600172")

    def test_review_status_default_is_pending(self):
        """默认参数 review_status='pending'"""
        with patch("dragon_quant.storage.db._connect",
                   return_value=sqlite3.connect(self._db_path)):
            from dragon_quant.storage.db import get_pending_dragons
            results = get_pending_dragons()
        self.assertEqual(len(results), 1)


class TestGetReviewSummary(unittest.TestCase):
    """get_review_summary() 的汇总统计口径"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        self._conn = sqlite3.connect(self._db_path)
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS dragons (
                trade_date TEXT, code TEXT, name TEXT,
                scan_id TEXT, rank INTEGER, composite_score REAL,
                board_count INTEGER, open_px REAL, close_px REAL,
                high_px REAL, low_px REAL, pct REAL,
                turnover_rate REAL, amount REAL, market_cap REAL,
                concepts_json TEXT, report_text TEXT,
                buy_date TEXT, buy_price REAL,
                max_return_5d REAL, max_drawdown_5d REAL,
                max_return_hold_days INTEGER,
                review_status TEXT, version TEXT DEFAULT ''
            )
        """)

    def tearDown(self):
        self._conn.close()
        self._tmpdir.cleanup()

    def _get_summary(self):
        with patch("dragon_quant.storage.db._connect",
                   return_value=sqlite3.connect(self._db_path)):
            from dragon_quant.storage.db import get_review_summary
            return get_review_summary()

    def test_win_rate_requires_positive_return_and_drawdown_above_minus_5(self):
        self._conn.executemany(
            "INSERT INTO dragons(trade_date, code, name, max_return_5d, max_drawdown_5d, review_status) "
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
            "INSERT INTO dragons(trade_date, code, name, max_return_5d, max_drawdown_5d, review_status) "
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
            "INSERT INTO dragons(trade_date, code, name, max_return_5d, max_drawdown_5d, review_status) "
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
        # 注意：db 层每次操作会主动 close 连接；因此这里必须用 side_effect 每次返回新连接。
        with patch("dragon_quant.storage.db._connect", side_effect=lambda: sqlite3.connect(self._db_path)):
            from dragon_quant.storage import db
            db.init_db()

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

            db.save_scan(scan1, day, 1.0, 5, 5, 1, [mk(c, i + 1, 90 - i) for i, c in enumerate(list("abcde"))])
            db.save_scan(scan2, day, 1.0, 5, 5, 1, [mk(c, i + 1, 80 - i) for i, c in enumerate(list("abxyz"))])
            db.save_scan(scan3, day, 1.0, 10, 5, 1, [mk(c, i + 1, 70 - i) for i, c in enumerate(list("abcdefghij"))])

            # 先给 e 标记 completed，确保后续删除 pending 时不删它
            db.save_dragons(day, [{
                "code": "e", "name": "e", "scan_id": scan1, "rank": 5,
                "composite_score": 86, "board_count": 1, "concepts": [], "report_text": "",
            }], version="")
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("UPDATE dragons SET review_status='completed' WHERE trade_date=? AND code='e'", (day,))
                conn.commit()
            finally:
                conn.close()

            # calendar: 简化为包含 day 以及 30 天内一堆日期，避免 5 日 gate 干扰
            calendar = {day, "2026-06-04", "2026-06-03", "2026-06-02", "2026-06-01"}
            stats = db.rebuild_dragons_for_date(day, version="x", calendar=calendar, apply_5day_gate=False)
            self.assertGreaterEqual(stats["upserted"], 1)

            # 并集应包含 a..j + x,y,z（e 也应在）
            rows = db.get_dragons(day)
            codes = {r["code"] for r in rows}
            for c in list("abcdefghij") + list("xyz"):
                self.assertIn(c, codes)
            self.assertIn("e", codes)

            # force 替换 top5：删除旧 top5 runs，然后写一个新的 top5=abcdn
            deleted = db.delete_scans_by_date_topn(day, 5)
            self.assertEqual(deleted, 2)
            scan4 = "20260605_110000_5_force"
            db.save_scan(scan4, day, 1.0, 5, 5, 1, [mk(c, i + 1, 60 - i) for i, c in enumerate(list("abcdn"))])

            db.rebuild_dragons_for_date(day, version="x", calendar=calendar, apply_5day_gate=False)
            rows2 = db.get_dragons(day)
            codes2 = {r["code"] for r in rows2}
            # top10 贡献仍在（a..j），top5 贡献已替换，新出现 n
            self.assertIn("n", codes2)
            # e 是 completed，不应因为不在贡献并集而被删除
            self.assertIn("e", codes2)
