"""tests for 按交易日的 provider 响应磁盘缓存（data_cache + orchestrator helper）。"""
import tempfile
import unittest
from pathlib import Path

from dragon_quant.cache.data_cache import (
    DataCache, _resolve_type, _from_dict, _deserialize,
)
from dragon_quant.models.types import KBar, StockInfo, Quote, SectorPerformance
from dragon_quant import orchestrator as orch


def _mk_kbar(ts=1700000000000):
    return KBar(timestamp=ts, volume=1.0, open=1.0, high=2.0, low=0.5,
                close=1.5, chg=0.5, pct=10.0, turnover=2.0, amount=100.0)


class TestTypeRegistry(unittest.TestCase):

    def test_prefix_resolution(self):
        # 长前缀优先：kline:1min:sector: 不被 kline:1min: 抢配
        self.assertIs(_resolve_type("kline:1min:sector:881101")[0], KBar)
        self.assertIs(_resolve_type("kline:5min:sector:881101")[0], KBar)
        self.assertIs(_resolve_type("kline:1min:600000")[0], KBar)
        self.assertIs(_resolve_type("kline:1min:000001")[0], KBar)
        self.assertIs(_resolve_type("kline:day:600000")[0], KBar)
        self.assertIs(_resolve_type("sector:components:881101")[0], StockInfo)
        self.assertIs(_resolve_type("quotes:batch")[0], Quote)
        self.assertIs(_resolve_type("sector:ranking")[0], SectorPerformance)

    def test_unregistered_returns_none(self):
        self.assertIsNone(_resolve_type("__meta__:candidates"))
        self.assertIsNone(_resolve_type("random:key"))


class TestFromDict(unittest.TestCase):

    def test_round_trip_kbar(self):
        from dragon_quant.cache.data_cache import _to_json_safe
        d = _to_json_safe(_mk_kbar())
        obj = _from_dict(KBar, d)
        self.assertIsInstance(obj, KBar)
        self.assertEqual(obj.close, 1.5)
        self.assertIsInstance(obj.timestamp, int)

    def test_timestamp_float_corrected_to_int(self):
        d = {"timestamp": 1.7e12, "volume": 0, "open": 0, "high": 0,
             "low": 0, "close": 0, "chg": 0, "pct": 0, "turnover": 0, "amount": 0}
        obj = _from_dict(KBar, d)
        self.assertIsInstance(obj.timestamp, int)

    def test_missing_field_uses_default(self):
        # StockInfo 多数字段有默认值，缺失不应报错
        obj = _from_dict(StockInfo, {"code": "600000", "name": "x"})
        self.assertEqual(obj.code, "600000")
        self.assertEqual(obj.pct, 0.0)

    def test_extra_field_ignored(self):
        obj = _from_dict(SectorPerformance,
                         {"code": "881101", "name": "x", "pct": 1.0,
                          "amplitude": 0.0, "unknown_field": 999})
        self.assertEqual(obj.code, "881101")
        self.assertFalse(hasattr(obj, "unknown_field"))

    def test_deserialize_list(self):
        from dragon_quant.cache.data_cache import _to_json_safe
        raw = _to_json_safe([_mk_kbar(), _mk_kbar(1700000060000)])
        out = _deserialize("kline:day:600000", raw)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(isinstance(x, KBar) for x in out))


class TestTradeDateCache(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = DataCache(cache_dir=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trip_restores_dataclass(self):
        bars = [_mk_kbar()]
        self.cache.set_for_trade_date("kline:day:600000", bars, "2026-06-26")
        fresh = DataCache(cache_dir=Path(self._tmp.name))
        got = fresh.load_for_trade_date("kline:day:600000", "2026-06-26")
        self.assertIsInstance(got[0], KBar)
        self.assertEqual(got[0].close, 1.5)

    def test_file_lands_in_trade_date_dir(self):
        self.cache.set_for_trade_date("kline:day:600000", [_mk_kbar()], "2026-06-26")
        p = Path(self._tmp.name) / "provider" / "2026-06-26" / "kline_day_600000.json"
        self.assertTrue(p.exists())

    def test_namespace_isolation(self):
        self.cache.set_for_trade_date(
            "sector:components:881101",
            [StockInfo(code="600000", name="x", pct=10.0)],
            "2026-06-26", namespace="v2")
        fresh = DataCache(cache_dir=Path(self._tmp.name))
        # v1 命名空间读不到 v2 写入的文件
        self.assertIsNone(fresh.load_for_trade_date(
            "sector:components:881101", "2026-06-26", namespace="v1"))
        self.assertIsNotNone(fresh.load_for_trade_date(
            "sector:components:881101", "2026-06-26", namespace="v2"))

    def test_meta_not_resolved(self):
        # __meta__ 不在注册表，磁盘恢复时原样返回 dict（不还原 dataclass）
        self.cache.set_for_trade_date("__meta__:foo", {"a": 1}, "2026-06-26")
        fresh = DataCache(cache_dir=Path(self._tmp.name))
        got = fresh.load_for_trade_date("__meta__:foo", "2026-06-26")
        self.assertEqual(got, {"a": 1})


class TestCachedFetchHelpers(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = DataCache(cache_dir=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_cache_worth_writing(self):
        self.assertFalse(orch._cache_worth_writing(None))
        self.assertFalse(orch._cache_worth_writing([]))
        self.assertTrue(orch._cache_worth_writing([1]))
        self.assertTrue(orch._cache_worth_writing({"a": 1}))

    def test_sync_hit_skips_fetch(self):
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return [_mk_kbar()]

        # 首次：未命中 → fetch + 落盘
        r1 = orch._cached_fetch_sync(self.cache, "kline:day:600000", fetch,
                                     "2026-06-26", refresh=False, volatile=False)
        self.assertEqual(calls["n"], 1)
        self.assertIsInstance(r1[0], KBar)
        # 新实例（清空内存）再读：命中磁盘 → 不再 fetch
        fresh = DataCache(cache_dir=Path(self._tmp.name))
        r2 = orch._cached_fetch_sync(fresh, "kline:day:600000", fetch,
                                     "2026-06-26", refresh=False, volatile=False)
        self.assertEqual(calls["n"], 1)
        self.assertIsInstance(r2[0], KBar)

    def test_sync_refresh_forces_fetch(self):
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return [_mk_kbar()]

        orch._cached_fetch_sync(self.cache, "kline:day:600000", fetch,
                                "2026-06-26", refresh=False, volatile=False)
        fresh = DataCache(cache_dir=Path(self._tmp.name))
        orch._cached_fetch_sync(fresh, "kline:day:600000", fetch,
                                "2026-06-26", refresh=True, volatile=False)
        self.assertEqual(calls["n"], 2)

    def test_empty_result_not_written(self):
        def fetch():
            return []

        orch._cached_fetch_sync(self.cache, "kline:day:600000", fetch,
                                "2026-06-26", refresh=False, volatile=False)
        p = Path(self._tmp.name) / "provider" / "2026-06-26" / "kline_day_600000.json"
        self.assertFalse(p.exists())

    def test_volatile_not_written(self):
        def fetch():
            return [_mk_kbar()]

        orch._cached_fetch_sync(self.cache, "kline:day:600000", fetch,
                                "2026-06-26", refresh=False, volatile=True)
        p = Path(self._tmp.name) / "provider" / "2026-06-26" / "kline_day_600000.json"
        self.assertFalse(p.exists())
        # 但内存可读
        self.assertIsNotNone(self.cache.get("kline:day:600000"))


if __name__ == "__main__":
    unittest.main()
