"""
tests for dragon_quant.cache.data_cache — 内存+本地双重缓存
"""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from dragon_quant.cache.data_cache import DataCache, CacheEntry


class TestCacheEntry(unittest.TestCase):

    def test_not_expired(self):
        entry = CacheEntry(data={"x": 1}, ttl=int(time.time() + 3600))
        self.assertFalse(entry.expired)

    def test_expired(self):
        entry = CacheEntry(data={"x": 1}, ttl=int(time.time() - 1))
        self.assertTrue(entry.expired)


class TestDataCacheBasic(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache = DataCache(cache_dir=Path(self.tmpdir))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_set_and_get(self):
        self.cache.set("key1", "value1")
        self.assertEqual(self.cache.get("key1"), "value1")

    def test_miss_returns_none(self):
        self.assertIsNone(self.cache.get("nonexistent"))

    def test_stats_hit_miss(self):
        self.cache.get("nonexistent")
        self.cache.set("key1", "value1")
        self.cache.get("key1")
        s = self.cache.stats()
        self.assertGreaterEqual(s["miss"], 1)
        self.assertGreaterEqual(s["hit"], 1)

    def test_invalidate(self):
        self.cache.set("key1", "value1")
        self.cache.invalidate("key1")
        self.assertIsNone(self.cache.get("key1"))

    def test_snapshot_and_load(self):
        self.cache.set("a", [1, 2, 3])
        snap = self.cache.snapshot()
        self.assertIn("a", snap)
        self.assertEqual(snap["a"], [1, 2, 3])

    def test_load_snapshot(self):
        snap = {"x": 42, "y": "hello"}
        self.cache.load_snapshot(snap)
        self.assertEqual(self.cache.get("x"), 42)
        self.assertEqual(self.cache.get("y"), "hello")

    def test_overwrite(self):
        self.cache.set("key1", "old")
        self.cache.set("key1", "new")
        self.assertEqual(self.cache.get("key1"), "new")


class TestDataCacheFilePersistence(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache = DataCache(cache_dir=Path(self.tmpdir))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_persist_and_load(self):
        self.cache.set("k", [1, 2, 3])
        fresh = DataCache(cache_dir=Path(self.tmpdir))
        loaded = fresh.load_persisted("k")
        self.assertEqual(loaded, [1, 2, 3])


class TestDataCacheThreadSafety(unittest.TestCase):
    """验证多线程并发 get/set 不崩溃"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache = DataCache(cache_dir=Path(self.tmpdir))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_concurrent_read_write(self):
        errors = []
        barrier = threading.Barrier(4, timeout=5)

        def worker(tid):
            try:
                for i in range(100):
                    self.cache.set(f"t{tid}_{i}", i)
                    val = self.cache.get(f"t{tid}_{i}")
                    if val is not None and val != i:
                        errors.append(f"t{tid}: expected {i}, got {val}")
                barrier.wait(timeout=5)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")


if __name__ == "__main__":
    unittest.main()
