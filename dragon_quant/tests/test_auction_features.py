"""原始因子公式 / 单位 / 窗口 / 缺失 / 分母保护（方案 §8）。"""

import unittest

from dragon_quant.review_account.auction.features import compute_features
from dragon_quant.review_account.auction.normalize import normalize
from dragon_quant.tests.support_auction import item, quote, real_sample_shape


def _features(items, quote_obj=None, history=None, code="600001"):
    return compute_features(normalize(items), quote_obj, history, code=code)


class TestPriceFeatures(unittest.TestCase):
    def test_gap_and_slippage(self):
        items = [item("09:20:00", 10.0, 500, 100),
                 item("09:25:00", 10.2, 1000, 100)]
        feats = _features(items, quote(prev_close=10.0, open_px=10.5, price=10.2))
        raw = feats["raw"]
        self.assertAlmostEqual(raw["official_open_gap_pct"], 5.0)
        self.assertAlmostEqual(raw["last_indicative_gap_pct"], 2.0)
        self.assertAlmostEqual(raw["post20_price_return_pct"], 2.0)
        self.assertEqual(raw["effective_open_source"], "official")

    def test_official_open_missing_uses_last_indicative(self):
        items = [item("09:20:00", 10.0, 500, 100),
                 item("09:25:00", 10.2, 1000, 100)]
        feats = _features(items, quote(open_px=0.0, price=10.2))
        raw = feats["raw"]
        self.assertIsNone(raw["official_open_gap_pct"])
        self.assertEqual(raw["effective_open_source"], "last_indicative")
        self.assertIn("official_open_missing",
                      [r["code"] for r in feats["reasons"]])

    def test_drawdown_and_flat_ratio(self):
        items = [item("09:20:00", 10.0, 500, 100),
                 item("09:21:00", 10.2, 700, 100),
                 item("09:22:00", 10.1, 900, 100),
                 item("09:23:00", 10.1, 1100, 100),
                 item("09:25:00", 10.0, 1500, 100)]
        raw = _features(items, quote())["raw"]
        self.assertAlmostEqual(raw["post20_max_drawdown_pct"],
                               (10.0 / 10.2 - 1) * 100, places=4)
        self.assertGreater(raw["price_flat_ratio"], 0)


class TestMatchedAndLiquidity(unittest.TestCase):
    def test_rate_is_dt_normalised_across_sampling(self):
        dense = [item("09:20:00", 10.0, 1000, 100),
                 item("09:22:00", 10.0, 2000, 100),
                 item("09:23:00", 10.0, 3000, 100),
                 item("09:25:00", 10.0, 5000, 100)]
        sparse = [item("09:20:00", 10.0, 1000, 100),
                  item("09:25:00", 10.0, 5000, 100)]
        dense_rate = _features(dense, quote())["raw"]["post20_matched_rate"]
        sparse_rate = _features(sparse, quote())["raw"]["post20_matched_rate"]
        self.assertAlmostEqual(dense_rate, sparse_rate, places=6)

    def test_volume_ratio_requires_five_days(self):
        items = [item("09:20:00", 10.0, 500, 100),
                 item("09:25:00", 10.0, 1000, 100)]
        five = _features(items, quote(),
                         {"last_matched_lots": [200, 200, 200, 200, 200]})["raw"]
        self.assertEqual(five["volume_ratio_history_n"], 5)
        self.assertAlmostEqual(five["volume_ratio_5d"], 5.0)
        three = _features(items, quote(),
                          {"last_matched_lots": [500, 500, 500]})["raw"]
        self.assertEqual(three["volume_ratio_history_n"], 3)
        self.assertIsNone(three["volume_ratio_5d"])
        self.assertAlmostEqual(three["volume_ratio_partial"], 2.0)

    def test_tiny_matched_base_nulls_ratios(self):
        items = [item("09:20:00", 10.0, 20, 100),
                 item("09:25:00", 10.0, 50, 100)]
        feats = _features(items, quote(),
                          {"last_matched_lots": [1000] * 5})
        raw = feats["raw"]
        self.assertIsNone(raw["final_unmatched_matched_ratio"])
        self.assertIsNone(raw["volume_ratio_5d"])
        self.assertIn("low_matched_base",
                      [r["code"] for r in feats["reasons"]])

    def test_float_shares_uses_same_snapshot_cap_and_price(self):
        items = [item("09:20:00", 10.0, 500, 100),
                 item("09:25:00", 10.0, 1000, 100)]
        a = _features(items, quote(price=10.0, float_cap=5.0e8))["raw"]
        b = _features(items, quote(price=20.0, float_cap=1.0e9))["raw"]
        self.assertAlmostEqual(a["float_shares"], b["float_shares"])
        self.assertAlmostEqual(a["auction_turnover_pct"],
                               b["auction_turnover_pct"])


class TestConversionFeatures(unittest.TestCase):
    def test_samples_insufficient_returns_none(self):
        items = [item("09:20:00", 10.0, 500, 100),
                 item("09:25:00", 10.0, 1000, 100)]
        feats = _features(items, quote())
        self.assertIsNone(feats["flags"]["weak_to_strong"])
        self.assertIsNone(feats["flags"]["strong_to_weak"])

    def test_conversion_flags_are_mutually_exclusive(self):
        from dragon_quant.tests.support_auction import strong_to_weak, weak_to_strong

        w2s = _features(weak_to_strong(), quote())["flags"]
        s2w = _features(strong_to_weak(), quote())["flags"]
        self.assertTrue(w2s["weak_to_strong"])
        self.assertFalse(w2s["strong_to_weak"])
        self.assertTrue(s2w["strong_to_weak"])
        self.assertFalse(s2w["weak_to_strong"])

    def test_vol_price_agree_ratio(self):
        raw = _features(real_sample_shape(), quote())["raw"]
        self.assertIsNotNone(raw["vol_price_agree_ratio"])


if __name__ == "__main__":
    unittest.main()
