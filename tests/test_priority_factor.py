"""P2 排序接入：因子分量、缺失桶、整日降级、关闭时与旧实现逐位等价。"""

import unittest

from dragon_quant.review_account.priority import (
    FACTOR_RANK_MISSING, FACTOR_RANK_SCORED, key_components, rank_key,
)

DAY = "2026-10-20"
PREV = "2026-10-19"


def _candidate(code="A", *, factor=None, composite=80, prep=True, auction=None,
               **extra):
    if factor is not None:
        extra["entry_priority"] = {"prep": {"state": prep},
                                   "auction": {"state": auction},
                                   "factor": factor}
    else:
        extra["entry_priority"] = {"prep": {"state": prep},
                                   "auction": {"state": auction}}
    candidate = {"code": code, "composite_score": composite, **extra}
    return candidate


def _factor(mode="on", score=73.4):
    return {"mode": mode, "score": score, "model": "equal_rank_v1",
            "model_version": "v1.0.0", "coverage": 1.0,
            "cross_section": "pool_mixed_vintage_by_candidate_date",
            "missing": score is None, "degraded_reason": None}


def legacy_rank_key(signal, candidate, auction_ranking_enabled=False):
    """旧实现（P1）的等价副本，用于逐位黄金对照。"""
    order = {True: 0, None: 1, False: 2}

    def side(side_name):
        entry = candidate.get("entry_priority") or {}
        block = entry.get(side_name)
        state = block.get("state") if isinstance(block, dict) else None
        return state if isinstance(state, bool) else None

    a_state = side("auction") if auction_ranking_enabled else None
    composite = candidate.get("composite_score") or 0
    priority = (signal or {}).get("priority") or 0
    return (order.get(a_state, 1), order.get(side("prep"), 1),
            -float(composite), -int(priority), str(candidate.get("code") or ""))


class TestFactorComponents(unittest.TestCase):
    def test_flag_off_forces_zero_and_is_bitwise_equivalent(self):
        signal = {"priority": 300}
        for factor in (None, _factor("on", 99.0), _factor("degraded", None)):
            candidate = _candidate("A", factor=factor, prep=True, auction=False)
            parts = key_components(signal, candidate, True, False)
            self.assertEqual(parts["factor_rank"], 0)
            self.assertEqual(parts["factor_score"], 0.0)
            self.assertEqual(parts["factor_mode"], "off")
            new = rank_key(signal, candidate, True, False)
            old = legacy_rank_key(signal, candidate, True)
            # 插入的两个零分量对所有候选相同：去掉第 3、4 位即旧元组，逐位相等。
            self.assertEqual(new[:2] + new[4:], old)
            self.assertEqual(new[2:4], (0, -0.0))

    def test_flag_off_ignores_residual_annotation(self):
        candidate = _candidate("A", factor={"mode": "on", "score": 100.0})
        self.assertEqual(key_components({}, candidate, False, False)["factor_mode"],
                         "off")

    def test_scored_candidate_rank_zero(self):
        parts = key_components({"priority": 300}, _candidate(factor=_factor()),
                               False, True)
        self.assertEqual(parts["factor_rank"], FACTOR_RANK_SCORED)
        self.assertAlmostEqual(parts["factor_score"], 73.4)
        self.assertEqual(parts["factor_mode"], "on")

    def test_missing_candidate_goes_to_rank_one_bucket(self):
        parts = key_components({"priority": 300},
                               _candidate(factor=_factor("on", None)), False, True)
        self.assertEqual(parts["factor_rank"], FACTOR_RANK_MISSING)
        self.assertEqual(parts["factor_score"], 0.0)
        self.assertEqual(parts["factor_mode"], "on")

    def test_degraded_annotation_falls_back(self):
        parts = key_components({"priority": 300},
                               _candidate(factor=_factor("degraded", None)),
                               False, True)
        self.assertEqual(parts["factor_rank"], 0)
        self.assertEqual(parts["factor_score"], 0.0)
        self.assertEqual(parts["factor_mode"], "degraded")

    def test_enabled_without_annotation_is_degraded(self):
        parts = key_components({"priority": 300}, _candidate(), False, True)
        self.assertEqual(parts["factor_mode"], "degraded")
        self.assertEqual(parts["factor_rank"], 0)


class TestFactorOrdering(unittest.TestCase):
    def test_factor_score_sits_inside_tier_before_composite(self):
        # B 因子分更高、五维分更低：开启因子后 B 必须排到 A 之前。
        a = _candidate("A", factor=_factor("on", 20.0), composite=99)
        b = _candidate("B", factor=_factor("on", 90.0), composite=10)
        signal = {"priority": 300}
        self.assertLess(rank_key(signal, a, False, False),
                        rank_key(signal, b, False, False))
        self.assertLess(rank_key(signal, b, False, True),
                        rank_key(signal, a, False, True))

    def test_missing_bucket_sorts_after_scored(self):
        scored = _candidate("Z", factor=_factor("on", 1.0), composite=0)
        missing = _candidate("A", factor=_factor("on", None), composite=99)
        signal = {"priority": 300}
        self.assertLess(rank_key(signal, scored, False, True),
                        rank_key(signal, missing, False, True))

    def test_degraded_day_equals_flag_off(self):
        candidates = [
            _candidate("A", factor=_factor("degraded", None), composite=90),
            _candidate("B", factor=_factor("degraded", None), composite=80),
            _candidate("C", factor=_factor("degraded", None), composite=70),
        ]
        signal = {"priority": 300}
        on = sorted(candidates, key=lambda c: rank_key(signal, c, False, True))
        off = sorted(candidates, key=lambda c: rank_key(signal, c, False, False))
        self.assertEqual([c["code"] for c in on], [c["code"] for c in off])

    def test_tier_still_dominates_factor(self):
        # A 维 tier 优先于因子分：A False 的高因子分不能越过 A True 的低因子分。
        strong_a = _candidate("A", factor=_factor("on", 1.0), auction=True)
        weak_a = _candidate("B", factor=_factor("on", 100.0), auction=False)
        signal = {"priority": 300}
        self.assertLess(rank_key(signal, strong_a, True, True),
                        rank_key(signal, weak_a, True, True))


if __name__ == "__main__":
    unittest.main()
