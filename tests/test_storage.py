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
