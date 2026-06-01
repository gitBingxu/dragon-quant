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


if __name__ == "__main__":
    unittest.main()
