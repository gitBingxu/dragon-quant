"""
tests for dragon_quant.infra.storage.cache — 内存+本地双重缓存
"""
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from dragon_quant.infra.storage.cache import DataCache, CacheEntry


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


class TestScanCache(unittest.TestCase):
    """Task 10: scan 专属缓存门面（通用实现仍在 infra.storage.cache）。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def test_scan_cache_keys_and_type(self):
        from dragon_quant.infra.storage.cache import DataCache
        from dragon_quant.scan.cache.main import ScanCache, create_scan_cache

        cache = create_scan_cache(Path(self.tmpdir))
        self.assertIsInstance(cache, ScanCache)
        self.assertIsInstance(cache, DataCache)
        self.assertEqual(cache.sector_ranking_key("tdx", "hy"),
                         "sector:ranking:tdx:hy")

    def test_scan_cache_delegates_all_scan_keys_to_sectors(self):
        from dragon_quant.scan import sectors
        from dragon_quant.scan.cache.main import ScanCache

        cache = ScanCache(cache_dir=Path(self.tmpdir))
        composite = sectors.sector_key("tdx", "gn", "880683")
        self.assertEqual(cache.sector_components_key(composite),
                         sectors.components_key(composite))
        self.assertEqual(cache.sector_1min_key(composite),
                         sectors.sector_1min_key(composite))
        self.assertEqual(cache.sector_5min_key(composite),
                         sectors.sector_5min_key(composite))
        self.assertEqual(cache.stock_1min_key("600519"),
                         "kline:1min:600519")
        self.assertEqual(cache.stock_5min_key("600519"),
                         "kline:5min:600519")

    def test_quotes_helper_reads_batch_quotes(self):
        from dragon_quant.scan.cache.main import ScanCache

        cache = ScanCache(cache_dir=Path(self.tmpdir))
        self.assertEqual(cache.quotes(), [])
        cache.set(cache.quotes_key, ["quote"])
        self.assertEqual(cache.quotes(), ["quote"])


if __name__ == "__main__":
    unittest.main()
