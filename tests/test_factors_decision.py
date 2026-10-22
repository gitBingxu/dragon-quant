"""P2 决策接入：FactorModel 协议 / rank 归一 / 快照读写 / annotate_pool 降级。

零网络、零真实 DB：全部临时 ``assets.db``（patch ``assets_db._connect``）。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dragon_quant.factors import storage as fstore
from dragon_quant.factors.decision import (
    DEGRADED_BELOW_MIN_COVERAGE, DEGRADED_DATE_FALLBACK,
    DEGRADED_FACTOR_READ_ERROR, DEGRADED_MODEL_MISSING,
    DEGRADED_MODEL_READ_ERROR,
    EqualWeightRankModel, PoolInput, PoolItem, annotate_pool, average_ranks,
    build_completion_rate, candidate_factor_date, default_snapshot,
    save_default_model_snapshot,
)
from dragon_quant.factors.types import FactorValue

DAY = "2026-10-20"
CAND_DATE = "2026-10-19"


class TempDbBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "assets.db")
        self._patch = patch("dragon_quant.storage.assets_db._connect",
                            side_effect=self._connect)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def _connect(self, *args, create=True, path=None, **kwargs):
        target = path or self._db_path
        if not create and not Path(target).exists():
            return None
        return sqlite3.connect(target)

    def seed_values(self, trade_date, values, *, name="five_dim_drive",
                    version="vtest"):
        rows = [FactorValue(
            trade_date=trade_date, code=code, factor_name=name,
            factor_version=version, value=value,
            available_at=f"{trade_date}T15:05:00", source="scan")
            for code, value in values.items()]
        fstore.replace_factor_day(trade_date, name, version, rows)


class TestAverageRanks(unittest.TestCase):
    def test_ties_get_average_zero_based_rank(self):
        self.assertEqual(average_ranks([10, 20, 20, 40]), [0.0, 1.5, 1.5, 3.0])
        self.assertEqual(average_ranks([5, 5, 5]), [1.0, 1.0, 1.0])


class TestEqualWeightRankModel(unittest.TestCase):
    def _model(self, **kwargs):
        params = {
            "factor_versions": {"f1": "v1", "f2": "v1"},
            "weights": {"f1": 1.0, "f2": 1.0},
            "lower_better": (),
            "neutral": (),
        }
        params.update(kwargs)
        return EqualWeightRankModel(**params)

    def test_rank_pct_and_score_range(self):
        model = self._model(factor_versions={"f1": "v1"},
                            weights={"f1": 1.0})
        items = tuple(PoolItem(code=c, factor_date=CAND_DATE,
                               values={"f1": v})
                      for c, v in (("A", 10), ("B", 20), ("C", 30), ("D", 40)))
        scores = model.score(PoolInput(DAY, items))
        self.assertAlmostEqual(scores["A"], 0.0)
        self.assertAlmostEqual(scores["B"], 100 / 3)
        self.assertAlmostEqual(scores["C"], 200 / 3)
        self.assertAlmostEqual(scores["D"], 100.0)

    def test_lower_better_inverts_rank(self):
        model = self._model(factor_versions={"f2": "v1"},
                            weights={"f2": 1.0}, lower_better=("f2",))
        items = tuple(PoolItem(code=c, factor_date=CAND_DATE,
                               values={"f2": v})
                      for c, v in (("A", 10), ("B", 20), ("C", 30)))
        scores = model.score(PoolInput(DAY, items))
        self.assertAlmostEqual(scores["A"], 100.0)
        self.assertAlmostEqual(scores["C"], 0.0)

    def test_missing_factor_is_not_scored(self):
        model = self._model()
        items = (
            PoolItem("A", CAND_DATE, {"f1": 1.0, "f2": 4.0}),
            PoolItem("B", CAND_DATE, {"f1": 2.0, "f2": 3.0}),
            PoolItem("C", CAND_DATE, {"f1": 3.0, "f2": 2.0}),
            PoolItem("D", CAND_DATE, {"f1": 4.0}),          # 缺 f2 → 不得分
        )
        scores = model.score(PoolInput(DAY, items))
        self.assertEqual(set(scores), {"A", "B", "C"})
        self.assertNotIn("D", scores)
        self.assertEqual(model.last_meta["missing_factor_counts"]["f2"], 1)

    def test_missing_counts_cover_every_missing_factor(self):
        model = self._model()
        items = (
            PoolItem("A", CAND_DATE, {"f1": 1.0, "f2": 4.0}),
            PoolItem("B", CAND_DATE, {"f1": 2.0, "f2": 3.0}),
            PoolItem("C", CAND_DATE, {"f1": 3.0, "f2": 2.0}),
            PoolItem("D", CAND_DATE, {}),                   # f1、f2 都缺
        )
        model.score(PoolInput(DAY, items))
        self.assertEqual(model.last_meta["missing_factor_counts"],
                         {"f1": 1, "f2": 1})

    def test_small_cohort_entire_group_missing(self):
        model = self._model(factor_versions={"f1": "v1"}, weights={"f1": 1.0})
        items = (PoolItem("A", CAND_DATE, {"f1": 1.0}),
                 PoolItem("B", CAND_DATE, {"f1": 2.0}))
        self.assertEqual(model.score(PoolInput(DAY, items)), {})
        self.assertEqual(model.last_meta["small_cohorts"],
                         [{"date": CAND_DATE, "n": 2}])

    def test_cohorts_are_normalized_independently(self):
        model = self._model(factor_versions={"f1": "v1"}, weights={"f1": 1.0})
        items = tuple(
            PoolItem(f"{date}-{index}", date, {"f1": value})
            for date, values in (("2026-10-17", (10, 20, 30)),
                                 ("2026-10-19", (100, 200, 300)))
            for index, value in enumerate(values))
        scores = model.score(PoolInput(DAY, items))
        self.assertAlmostEqual(scores["2026-10-17-0"], scores["2026-10-19-0"])
        self.assertAlmostEqual(scores["2026-10-17-2"], scores["2026-10-19-2"])

    def test_neutral_factor_is_not_required(self):
        model = self._model(factor_versions={"f1": "v1", "flag": "v1"},
                            weights={"f1": 1.0, "flag": 0.0},
                            neutral=("flag",))
        items = (PoolItem("A", CAND_DATE, {"f1": 1.0}),
                 PoolItem("B", CAND_DATE, {"f1": 2.0}),
                 PoolItem("C", CAND_DATE, {"f1": 3.0}))
        self.assertEqual(len(model.score(PoolInput(DAY, items))), 3)

    def test_snapshot_roundtrip(self):
        model = self._model(lower_better=("f2",))
        rebuilt = EqualWeightRankModel.from_snapshot(model.snapshot())
        self.assertEqual(rebuilt.factor_versions, model.factor_versions)
        self.assertEqual(rebuilt.weights, model.weights)
        self.assertEqual(rebuilt.lower_better, {"f2"})


class TestModelSnapshotStorage(TempDbBase):
    def test_save_then_read_latest_and_immutable(self):
        self.assertTrue(fstore.save_model_snapshot(
            "m", "v1", params={"weights": {"a": 1.0}}, factor_versions={"a": "x"}))
        self.assertFalse(fstore.save_model_snapshot(
            "m", "v1", params={"weights": {"a": 9.0}}, factor_versions={"a": "y"}))
        snapshot = fstore.get_model_snapshot("m")
        self.assertEqual(snapshot["model_version"], "v1")
        self.assertEqual(snapshot["params"]["weights"], {"a": 1.0})
        self.assertEqual(snapshot["factor_versions"], {"a": "x"})

    def test_empty_version_uses_latest_and_missing_returns_none(self):
        self.assertIsNone(fstore.get_model_snapshot("m"))
        fstore.save_model_snapshot("m", "v1", params={}, factor_versions={"a": "x"})
        fstore.save_model_snapshot("m", "v2", params={}, factor_versions={"a": "y"})
        self.assertEqual(fstore.get_model_snapshot("m")["model_version"], "v2")
        self.assertIsNone(fstore.get_model_snapshot("m", "nope"))

    def test_default_snapshot_excludes_composite_and_neutral_weight_zero(self):
        snapshot = default_snapshot()
        self.assertNotIn("five_dim_composite", snapshot["factor_versions"])
        self.assertIn("five_dim_drive", snapshot["factor_versions"])
        flag = "five_dim_is_true_dragon"
        self.assertEqual(snapshot["params"]["weights"][flag], 0.0)
        self.assertIn(flag, snapshot["params"]["neutral"])

    def test_save_default_model_snapshot(self):
        self.assertTrue(save_default_model_snapshot())
        self.assertFalse(save_default_model_snapshot())


class TestAnnotatePool(TempDbBase):
    def _candidates(self, codes, trade_date=CAND_DATE):
        return [{"code": code, "trade_date": trade_date, "composite_score": 80}
                for code in codes]

    def test_candidate_factor_date_fallback(self):
        self.assertEqual(candidate_factor_date({"trade_date": CAND_DATE}, DAY),
                         CAND_DATE)
        self.assertEqual(candidate_factor_date({}, DAY), DAY)
        self.assertEqual(candidate_factor_date({"candidate_trade_date": CAND_DATE}, DAY),
                         CAND_DATE)

    def test_missing_snapshot_degrades_without_fallback_model(self):
        pool = self._candidates(["A", "B"])
        ctx = annotate_pool(pool, decision_date=DAY)
        self.assertEqual(ctx["factor_mode"], "degraded")
        self.assertEqual(ctx["factor_degraded"], [DEGRADED_MODEL_MISSING])
        self.assertIsNone(ctx["factor_model"])
        for candidate in pool:
            block = candidate["entry_priority"]["factor"]
            self.assertEqual(block["mode"], "degraded")
            self.assertIsNone(block["score"])
            self.assertTrue(block["missing"])

    def test_on_mode_scores_only_candidates_with_required_factors(self):
        snapshot = default_snapshot(names=["five_dim_drive"])
        fstore.save_model_snapshot(
            snapshot["model_name"], snapshot["model_version"],
            params=snapshot["params"],
            factor_versions=snapshot["factor_versions"])
        version = snapshot["factor_versions"]["five_dim_drive"]
        self.seed_values(CAND_DATE, {"A": 10.0, "B": 20.0, "C": 30.0},
                         version=version)
        pool = self._candidates(["A", "B", "C", "D"])
        ctx = annotate_pool(pool, decision_date=DAY)
        self.assertEqual(ctx["factor_mode"], "on", ctx["factor_degraded"])
        self.assertAlmostEqual(ctx["factor_coverage"], 0.75)
        by_code = {c["code"]: c["entry_priority"]["factor"] for c in pool}
        self.assertAlmostEqual(by_code["A"]["score"], 0.0)
        self.assertAlmostEqual(by_code["C"]["score"], 100.0)
        self.assertIsNone(by_code["D"]["score"])
        self.assertTrue(by_code["D"]["missing"])

    def test_coverage_below_threshold_degrades_whole_day(self):
        save_default_model_snapshot(names=["five_dim_drive"])
        self.seed_values(CAND_DATE, {"A": 10.0, "B": 20.0})
        pool = self._candidates(["A", "B", "C", "D"])
        ctx = annotate_pool(pool, decision_date=DAY, min_coverage=0.6)
        self.assertEqual(ctx["factor_mode"], "degraded")
        self.assertTrue(any(DEGRADED_BELOW_MIN_COVERAGE in reason
                            for reason in ctx["factor_degraded"]))
        self.assertTrue(all(c["entry_priority"]["factor"]["score"] is None
                            for c in pool))

    def test_malformed_snapshot_degrades_instead_of_raising(self):
        # params 合法 JSON 但结构非法：weights 缺因子 / min_cohort 非法
        fstore.save_model_snapshot(
            "equal_rank_v1", "vbad",
            params={"weights": {"a": 1.0}, "min_cohort": 1},
            factor_versions={"a": "v1", "b": "v1"})
        pool = self._candidates(["A", "B", "C"])
        ctx = annotate_pool(pool, decision_date=DAY, model_version="vbad")
        self.assertEqual(ctx["factor_mode"], "degraded")
        self.assertTrue(any(DEGRADED_MODEL_READ_ERROR in reason
                            for reason in ctx["factor_degraded"]))
        self.assertTrue(all(c["entry_priority"]["factor"]["mode"] == "degraded"
                            for c in pool))

    def test_loader_exception_degrades_without_raising(self):
        save_default_model_snapshot(names=["five_dim_drive"])

        def boom(*args, **kwargs):
            raise RuntimeError("db locked")

        pool = self._candidates(["A", "B", "C"])
        ctx = annotate_pool(pool, decision_date=DAY, factor_loader=boom)
        self.assertEqual(ctx["factor_mode"], "degraded")
        self.assertTrue(any(DEGRADED_FACTOR_READ_ERROR in reason
                            for reason in ctx["factor_degraded"]))

    def test_missing_trade_date_forces_degraded(self):
        save_default_model_snapshot(names=["five_dim_drive"])
        pool = [{"code": "A"}, {"code": "B"}, {"code": "C"}]
        ctx = annotate_pool(pool, decision_date=DAY)
        self.assertEqual(ctx["factor_mode"], "degraded")
        self.assertTrue(any(DEGRADED_DATE_FALLBACK in reason
                            for reason in ctx["factor_degraded"]))

    def test_loader_receives_each_candidate_own_date(self):
        save_default_model_snapshot(names=["five_dim_drive"])
        calls = []

        def loader(trade_date, names, codes, versions, **kwargs):
            calls.append((trade_date, tuple(codes)))
            return {code: {"five_dim_drive": 10.0} for code in codes}

        pool = [{"code": "A", "trade_date": "2026-10-17"},
                {"code": "B", "trade_date": "2026-10-17"},
                {"code": "C", "trade_date": CAND_DATE}]
        ctx = annotate_pool(pool, decision_date=DAY, factor_loader=loader,
                            min_coverage=0.0)
        self.assertEqual(ctx["factor_mode"], "on")
        self.assertEqual(calls, [("2026-10-17", ("A", "B")),
                                 (CAND_DATE, ("C",))])


class TestBuildCompletionRate(TempDbBase):
    def test_rate_uses_days_with_values(self):
        days = ["2026-10-15", "2026-10-16", CAND_DATE, DAY]
        self.assertEqual(build_completion_rate(days), 0.0)
        self.seed_values(CAND_DATE, {"A": 1.0})
        self.seed_values(DAY, {"A": 1.0})
        self.assertEqual(build_completion_rate(days), 0.5)
        self.assertIsNone(build_completion_rate([]))


if __name__ == "__main__":
    unittest.main()
