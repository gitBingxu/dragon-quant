"""tests for 板块三维融合：规范化键、合并/去重/冲突剔除、扫描集成。"""
import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from dragon_quant import orchestrator as orch
from dragon_quant.cache.data_cache import DataCache
from dragon_quant.models.types import Candidate, SectorPerformance, StockInfo
from dragon_quant.sectors import (
    SECTOR_TOP_N, components_key, sector_1min_key, sector_5min_key,
    sector_key, split_sector_key,
)
from tests.test_scorers import _min_bars, _quote
from tests.support import patch_storage_dbs


def _sp(code, name, pct, category, source="tdx"):
    return SectorPerformance(code=code, name=name, pct=pct, amplitude=0.0,
                             category=category, source=source)


class TestSectorKey(unittest.TestCase):

    def test_round_trip(self):
        key = sector_key("tdx", "gn", "880683")
        self.assertEqual(key, "tdx:gn:880683")
        self.assertEqual(split_sector_key(key), ("tdx", "gn", "880683"))

    def test_invalid_returns_raw(self):
        self.assertEqual(split_sector_key("881001"), ("", "", "881001"))
        self.assertEqual(split_sector_key(""), ("", "", ""))

    def test_cache_key_helpers(self):
        self.assertEqual(components_key("tdx:gn:1"), "sector:components:tdx:gn:1")
        self.assertEqual(sector_1min_key("tdx:gn:1"), "kline:1min:sector:tdx:gn:1")
        self.assertEqual(sector_5min_key("tdx:gn:1"), "kline:5min:sector:tdx:gn:1")


class TestMergeSectorRankings(unittest.TestCase):

    def test_sorted_and_truncated(self):
        rankings = {
            "hy": [_sp("881001", "A", 2.0, "hy"), _sp("881002", "B", 5.0, "hy")],
            "gn": [_sp("880001", "C", 4.0, "gn")],
            "dq": [_sp("880201", "D", 3.0, "dq")],
        }
        out = orch._merge_sector_rankings(rankings, top_n=3, keep=lambda s: True)
        self.assertEqual([s.code for s in out], ["881002", "880001", "880201"])

    def test_keep_filters_and_dedupes(self):
        rankings = {"hy": [_sp("881001", "昨日涨停X", 9.0, "hy"),
                           _sp("881002", "A", 1.0, "hy")],
                    "gn": [_sp("881002", "A2", 1.0, "gn")]}
        out = orch._merge_sector_rankings(
            rankings, top_n=8, keep=lambda s: not s.name.startswith("昨日涨停"))
        # 同一 code 不同维度是不同键，但跨维度同 code 应在 Phase A 冲突剔除阶段处理
        self.assertEqual([s.code for s in out], ["881002", "881002"])


class TestDropCodeCollisions(unittest.TestCase):

    def test_board_level_not_dimension_level(self):
        rankings = {
            "hy": [_sp("880001", "H1", 5.0, "hy")],
            "gn": [_sp("880001", "G1", 4.0, "gn"), _sp("880002", "G2", 3.0, "gn")],
            "dq": [_sp("880002", "D1", 2.0, "dq")],
        }
        out, dropped = orch._drop_code_collisions(rankings)
        self.assertEqual([(c, rows[0].code) for c, rows in out.items()],
                         [("hy", "880001"), ("gn", "880002")])
        self.assertNotIn("dq", out)  # dq 唯一板块 880002 冲突 → 整维空
        self.assertEqual(len(dropped), 2)


class TestBuildCandidates(unittest.TestCase):

    def test_dedup_and_highest_board_wins(self):
        top = [_sp("880001", "强板块", 5.0, "gn"),
               _sp("881001", "行业", 3.0, "hy")]
        filtered = {
            sector_key("tdx", "gn", "880001"): [StockInfo("600001", "X", pct=10.0)],
            sector_key("tdx", "hy", "881001"): [StockInfo("600001", "X", pct=10.0),
                                                StockInfo("600002", "Y", pct=9.95)],
        }
        out = orch._build_candidates(top, filtered, limit_up_pct=9.9)
        self.assertEqual(set(out), {"600001", "600002"})
        self.assertEqual(out["600001"].primary_sector, "880001")
        self.assertEqual(out["600001"].primary_sector_key, "tdx:gn:880001")
        self.assertEqual(out["600001"].primary_sector_category, "gn")
        self.assertEqual(out["600001"].concepts, ["强板块", "行业"])
        self.assertEqual(out["600002"].primary_sector, "881001")

    def test_below_limit_up_filtered(self):
        top = [_sp("880001", "S", 5.0, "gn")]
        filtered = {sector_key("tdx", "gn", "880001"): [StockInfo("600001", "X", pct=5.0)]}
        self.assertEqual(orch._build_candidates(top, filtered, 9.9), {})


class TestThreeDimensionScan(unittest.TestCase):
    """全链路：三维合并 / 缓存隔离 / 回退降级 / Top8。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.db_path = str(Path(self.tmp.name) / "test.db")
        # orch.scan() 会 save_dragons → 锁外 fan-out 双写 assets.db 镜像，
        # 必须两个连接点一起隔离，否则镜像写落到真实数据目录。
        self.stack.enter_context(patch_storage_dbs(self.db_path))
        self.stack.enter_context(patch.object(orch, "RESULTS_DIR", Path(self.tmp.name)))
        self.cache = DataCache(cache_dir="")
        self.stack.enter_context(patch.object(orch, "DataCache", return_value=self.cache))
        self.stack.enter_context(patch.object(orch, "_get_trade_date", return_value="2026-09-11"))
        self.stack.enter_context(patch(
            "dragon_quant.utils.trading.build_trade_calendar", return_value={"2026-09-11"}))

        self.tdx, self.ths, self.xq, self.tx = Mock(), Mock(), Mock(), Mock()
        self.tdx.get_kline.return_value = []
        self.tdx.get_minute_kline.return_value = []
        self.tdx.batch_get_quotes.return_value = []
        self.tdx.get_sector_components.return_value = []
        self.tdx.get_sector_5min_kline_history.return_value = []
        self.tdx.get_sector_1min_kline.return_value = []
        self.ths.get_sector_components.return_value = []
        self.ths.get_sector_5min_kline_history.return_value = []
        self.ths.get_sector_1min_kline.return_value = _min_bars(100, [0, 1])
        self.xq.get_kline.return_value = _min_bars(10, [0, 1, 2, 3, 4, 10])
        self.xq.get_minute_kline.return_value = _min_bars(10, [0, 10])
        self.tx.batch_get_quotes.side_effect = lambda codes: [_quote(c, 10, 11) for c in codes]
        self.stack.enter_context(patch.object(
            orch, "create_providers",
            return_value={"tdx": self.tdx, "ths": self.ths,
                          "xueqiu": self.xq, "tencent": self.tx}))
        limiter = Mock()
        limiter.submit.side_effect = lambda provider, fn: fn()
        self.stack.enter_context(patch.object(orch, "RateLimiter", return_value=limiter))
        self.real_score = orch._score_one
        self.stack.enter_context(patch.object(orch, "_score_one", side_effect=self.fake_score))

    def fake_score(self, cand, cache, pool, down_keys, name_map, logger):
        return {"code": cand.code, "name": cand.name, "concepts": cand.concepts,
                "board_count": cand.board_count,
                "primary_sector": cand.primary_sector,
                "primary_sector_key": cand.primary_sector_key,
                "primary_sector_category": cand.primary_sector_category,
                "primary_sector_source": cand.primary_sector_source,
                "primary_sector_name": name_map.get(cand.primary_sector_key, ""),
                "composite_score": 80.0, "dimensions": {}, "is_true_dragon": False,
                "reject_reason": "test"}

    def _rankings(self, hy=(), gn=(), dq=()):
        by_cat = {"hy": list(hy), "gn": list(gn), "dq": list(dq)}
        self.tdx.get_sector_ranking.side_effect = (
            lambda asc=False, category="hy", **kw: list(by_cat.get(category, [])))
        self.ths.get_sector_ranking.side_effect = (
            lambda asc=False, category="hy", **kw: [])

    def test_three_dimensions_merged_and_cache_isolated(self):
        self._rankings(
            hy=[_sp("881001", "行业A", 2.0, "hy")],
            gn=[_sp("880001", "概念A", 5.0, "gn")],
            dq=[_sp("880201", "地区A", 3.0, "dq")],
        )
        comps = {("hy", "881001"): [StockInfo("600001", "H", pct=10.0)],
                 ("gn", "880001"): [StockInfo("600001", "H", pct=10.0)],
                 ("dq", "880201"): [StockInfo("600002", "D", pct=10.0)]}
        self.tdx.get_sector_components.side_effect = (
            lambda code, page=1, all_pages=False, category="hy", **kw:
            comps.get((category, code), []))
        out = orch.scan(top_n=2, verbose=False, force=True)
        self.assertFalse(out.get("sectors", {}).get("degraded"))
        self.assertEqual(set(out["sectors"]["sources"].values()), {"tdx"})
        # 缓存键按维度隔离
        self.assertIsNotNone(self.cache.get("sector:components:tdx:hy:881001"))
        self.assertIsNotNone(self.cache.get("sector:components:tdx:gn:880001"))
        self.assertIsNotNone(self.cache.get("sector:components:tdx:dq:880201"))
        by_code = {r["code"]: r for r in out["ranking"]}
        self.assertEqual(by_code["600001"]["primary_sector"], "880001")  # 概念涨幅最高
        self.assertEqual(by_code["600001"]["primary_sector_category"], "gn")
        self.assertEqual(by_code["600002"]["primary_sector_category"], "dq")
        self.assertEqual(len(out["sectors"]["up"]), 3)

    def test_top8_truncation(self):
        hy = [_sp(f"881{i:03d}", f"H{i}", 10.0 - i * 0.1, "hy") for i in range(10)]
        self._rankings(hy=hy)
        self.tdx.get_sector_components.return_value = [StockInfo("600001", "X", pct=10.0)]
        out = orch.scan(top_n=1, verbose=False, force=True)
        self.assertEqual(len(out["sectors"]["up"]), SECTOR_TOP_N)

    def test_dimension_fallback_and_degraded(self):
        self.tdx.get_sector_ranking.side_effect = (
            lambda asc=False, category="hy", **kw: (
                [_sp("881001", "行业A", 2.0, "hy")] if category == "hy" else []))
        self.ths.get_sector_ranking.side_effect = (
            lambda asc=False, category="hy", **kw: (
                [_sp("300816", "概念A", 4.0, "gn", "ths")] if category == "gn" else []))
        self.ths.get_sector_components.return_value = [StockInfo("600001", "X", pct=10.0)]
        out = orch.scan(top_n=1, verbose=False, force=True)
        self.assertEqual(out["sectors"]["sources"]["hy"], "tdx")
        self.assertEqual(out["sectors"]["sources"]["gn"], "ths")
        self.assertEqual(out["sectors"]["sources"]["dq"], "unavailable")
        self.assertEqual(out["sectors"]["degraded"], ["dq"])

    def test_collision_drops_board_keeps_dimension(self):
        self._rankings(
            hy=[_sp("880001", "H", 5.0, "hy")],
            gn=[_sp("880001", "G1", 4.0, "gn"), _sp("880002", "G2", 3.0, "gn")],
        )
        comps = {("hy", "880001"): [StockInfo("600001", "H", pct=10.0)],
                 ("gn", "880002"): [StockInfo("600002", "G", pct=10.0)]}
        self.tdx.get_sector_components.side_effect = (
            lambda code, page=1, all_pages=False, category="hy", **kw:
            comps.get((category, code), []))
        out = orch.scan(top_n=2, verbose=False, force=True)
        self.assertEqual(out["sectors"]["collisions"], ["gn:880001"])
        self.assertEqual(out["sectors"]["sources"]["gn"], "tdx")  # 维度仍可用
        codes = {r["code"] for r in out["ranking"]}
        self.assertIn("600002", codes)

    def test_collision_emptied_dimension_marked_unavailable(self):
        self._rankings(
            hy=[_sp("880001", "H", 5.0, "hy")],
            gn=[_sp("880001", "G", 4.0, "gn")],
        )
        self.tdx.get_sector_components.return_value = [StockInfo("600001", "X", pct=10.0)]
        out = orch.scan(top_n=1, verbose=False, force=True)
        self.assertEqual(out["sectors"]["sources"]["gn"], "unavailable")
        self.assertIn("gn", out["sectors"]["degraded"])

    def test_st_filter_on_concept_components(self):
        self._rankings(gn=[_sp("880001", "概念A", 5.0, "gn")])
        self.tdx.get_sector_components.side_effect = (
            lambda code, page=1, all_pages=False, category="hy", **kw: [
                StockInfo("300001", "创业", pct=20.0),
                StockInfo("688001", "科创", pct=20.0),
                StockInfo("600001", "*ST坏", pct=10.0),
                StockInfo("600002", "好", pct=10.0),
            ])
        out = orch.scan(top_n=1, verbose=False, force=True)
        self.assertEqual([r["code"] for r in out["ranking"]], ["600002"])


if __name__ == "__main__":
    unittest.main()
