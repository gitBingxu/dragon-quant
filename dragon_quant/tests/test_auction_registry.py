"""AUCTION_PROFILE：权重和、分桶递增、窗口序、config_hash 稳定。"""

import unittest

from dragon_quant.review_account.auction import registry as R
from dragon_quant.infra.factor.builtin import auction as factor_auction


class TestProfile(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(R.WEIGHTS.values()), 1.0)
        self.assertEqual(set(R.WEIGHTS), set(R.DIM_ORDER))
        self.assertEqual(len(R.DIM_LABELS), 5)

    def test_upper_buckets_strictly_increasing(self):
        for name, buckets in R.BUCKETS["upper"].items():
            upper = [row[0] for row in buckets]
            self.assertEqual(upper, sorted(upper), name)
            self.assertEqual(len(upper), len(set(upper)), name)

    def test_lower_buckets_strictly_increasing(self):
        for name, buckets in R.BUCKETS["lower"].items():
            lower = [row[0] for row in buckets]
            self.assertEqual(lower, sorted(lower), name)

    def test_window_order(self):
        self.assertLess(R.AUCTION_OPEN, R.NO_CANCEL)
        self.assertLess(R.NO_CANCEL, R.TAIL_EXTENDED_START)
        self.assertLess(R.TAIL_EXTENDED_START, R.TAIL_DEFAULT_START)
        self.assertLess(R.TAIL_DEFAULT_START, R.AUCTION_CLOSE)

    def test_config_hash_stable_and_matches_factor_literal(self):
        self.assertEqual(len(R.CONFIG_HASH), 12)
        self.assertEqual(R.CONFIG_HASH, R.AUCTION_PROFILE["config_hash"])
        self.assertEqual(R.CONFIG_HASH, factor_auction.CONFIG_HASH)
        self.assertEqual(R.CONFIG_VERSION, factor_auction.CONFIG_VERSION)

    def test_neutral_constant_removed(self):
        self.assertFalse(hasattr(R, "AUCTION_NEUTRAL"))


if __name__ == "__main__":
    unittest.main()
