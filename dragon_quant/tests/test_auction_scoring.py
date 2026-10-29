"""五维打分、聚合、最少维度、level、rank 与 pre_veto_strength（方案 §9）。"""

import unittest
from unittest.mock import patch

from dragon_quant.review_account.auction import registry as R
from dragon_quant.review_account.auction.dimensions import (
    _combine, lower_score, score_dimensions, upper_score,
)
from dragon_quant.review_account.auction.types import DimensionScore, RiskEvent
from dragon_quant.tests.support_auction import quote, real_sample_shape, strong_to_weak

from dragon_quant.review_account.auction import scoring


class TestBuckets(unittest.TestCase):
    def test_upper_buckets(self):
        buckets = R.BUCKETS["upper"]["open_gap"]
        self.assertEqual(upper_score(-6.0, buckets), 0.0)
        self.assertEqual(upper_score(-3.0, buckets), 15.0)
        self.assertEqual(upper_score(0.0, buckets), 30.0)
        self.assertEqual(upper_score(3.0, buckets), 75.0)
        self.assertEqual(upper_score(9.0, buckets), 100.0)

    def test_lower_buckets(self):
        buckets = R.BUCKETS["lower"]["turnover"]
        self.assertEqual(lower_score(0.01, buckets), 10.0)
        self.assertEqual(lower_score(0.05, buckets), 25.0)
        self.assertEqual(lower_score(0.9, buckets), 100.0)

    def test_combine_renormalises_available_subitems(self):
        score, missing = _combine({"a": 80.0, "b": None},
                                  {"a": 0.5, "b": 0.5})
        self.assertEqual(score, 80.0)
        self.assertEqual(missing, ["b"])


class TestDimensionDetails(unittest.TestCase):
    def _raw(self, **overrides):
        raw = {
            "effective_open_gap_pct": 1.0, "post20_price_return_pct": 1.0,
            "final_indicative_slippage_pct": 0.1, "auction_turnover_pct": 0.3,
            "auction_amount_estimated": 1e8, "final_unmatched_matched_ratio": 0.8,
            "post20_unmatched_change": 500.0, "last_matched_lots": 1000.0,
            "vol_price_agree_ratio": 0.8, "volume_price_divergence": False,
            "tail_matched_acceleration": 1.2, "tail_price_return_pct": 0.1,
            "tail_unmatched_change": 10.0,
        }
        raw.update(overrides)
        return raw

    def test_direction_degraded_when_subitem_missing(self):
        dims = score_dimensions(
            self._raw(post20_price_return_pct=None,
                      final_indicative_slippage_pct=None),
            {"weak_to_strong": False, "strong_to_weak": False}, [])
        direction = dims["direction"]
        self.assertEqual(direction.status, "degraded")
        self.assertIn("post20", direction.details["sub_missing"])
        self.assertEqual(direction.score, upper_score(
            1.0, R.BUCKETS["upper"]["open_gap"]))

    def test_liquidity_missing_when_no_turnover_or_amount(self):
        dims = score_dimensions(
            self._raw(auction_turnover_pct=None, auction_amount_estimated=None),
            {"weak_to_strong": False, "strong_to_weak": False}, [])
        self.assertIsNone(dims["liquidity"].score)
        self.assertEqual(dims["liquidity"].status, "missing")

    def test_sell_pressure_caps_supply(self):
        event = RiskEvent(code="600001", event_id="sell_pressure_acceleration@09:25:00",
                          family="risk", severity="medium", veto=False,
                          owner_dim="supply")
        dims = score_dimensions(self._raw(),
                                {"weak_to_strong": False, "strong_to_weak": False},
                                [event])
        self.assertEqual(dims["supply"].score, R.SUPPLY_SELL_PRESSURE_CAP)

    def test_tail_accel_score_is_log_scaled(self):
        dims = score_dimensions(self._raw(), {}, [])
        self.assertAlmostEqual(dims["tail_grab"].details["accel"], 50 + 25 * 0.263, 1)


class TestAggregation(unittest.TestCase):
    def test_ok_strength_covers_all_five_dims(self):
        verdict = scoring.evaluate("600001", real_sample_shape(), quote())
        self.assertEqual(verdict.data_quality.status, "ok")
        self.assertEqual(verdict.data_quality.completeness, 1.0)
        expected = round(sum(R.WEIGHTS[d] * verdict.dims[d].score
                             for d in R.DIM_ORDER), 2)
        self.assertAlmostEqual(verdict.strength, expected, places=2)

    def test_insufficient_dimensions_is_unavailable(self):
        dims = {
            "direction": DimensionScore("direction", "价格表态", 70.0, 0.25),
            "supply": DimensionScore("supply", "供需与转换", 60.0, 0.25),
        }
        with patch.object(scoring, "score_dimensions", return_value=dims):
            verdict = scoring.evaluate("600001", real_sample_shape(), quote())
        self.assertIsNone(verdict.strength)
        self.assertEqual(verdict.data_quality.status, "unavailable")
        self.assertIn("insufficient_dimensions", verdict.data_quality.reason_codes)
        self.assertFalse(verdict.is_weak)

    def test_veto_keeps_pre_veto_strength(self):
        verdict = scoring.evaluate("600001", strong_to_weak(), quote())
        self.assertTrue(verdict.is_weak)
        self.assertEqual(verdict.strength, 0.0)
        self.assertEqual(verdict.level, "弱")
        self.assertIsNotNone(verdict.pre_veto_strength)
        self.assertGreater(verdict.pre_veto_strength, 0.0)

    def test_level_thresholds(self):
        from dragon_quant.review_account.auction.scoring import _level_of
        self.assertEqual(_level_of(70.0), "强")
        self.assertEqual(_level_of(69.99), "中")
        self.assertEqual(_level_of(40.0), "中")
        self.assertEqual(_level_of(39.99), "弱")


if __name__ == "__main__":
    unittest.main()
