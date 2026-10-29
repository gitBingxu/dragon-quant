"""normalize + evaluate/rank 门面测试（v2）。"""

import unittest

from dragon_quant.review_account.auction import (
    AuctionVerdict, evaluate, rank_auctions,
)
from dragon_quant.review_account.auction import registry as R
from dragon_quant.review_account.auction.normalize import market_of, normalize
from dragon_quant.review_account.auction.scoring import _level_of
from dragon_quant.tests.support_auction import ascending, item, quote, real_sample_shape


class TestNormalize(unittest.TestCase):
    def test_sorts_by_time_and_slices_windows(self):
        items = [
            item("09:25:00", 10.0, 300, 0),
            item("09:15:00", 10.0, 100, 0),
            item("09:20:00", 10.0, 200, 0),
        ]
        data = normalize(items)
        self.assertEqual(data["times"][0], R.AUCTION_OPEN)
        self.assertEqual(data["times"][-1], R.AUCTION_CLOSE)
        self.assertEqual(len(data["pre_idx"]), 1)
        self.assertEqual(len(data["core_idx"]), 2)

    def test_core_window_is_the_only_scoring_window(self):
        data = normalize(real_sample_shape())
        self.assertTrue(all(R.NO_CANCEL <= data["times"][i] <= R.AUCTION_CLOSE
                            for i in data["core_idx"]))
        # 09:20 前 matched 下降不影响 core 增量
        self.assertGreater(len(data["pre_idx"]), 0)
        self.assertTrue(any(
            data["matched"][i] < data["matched"][i - 1] for i in data["pre_idx"]))

    def test_tail_extension_only_when_short(self):
        short = normalize([item("09:24:10", 10.0, 50, 0),
                           item("09:24:59", 10.0, 100, 0),
                           item("09:25:00", 10.0, 200, 0)])
        self.assertTrue(short["tail_extended"])
        long = normalize(real_sample_shape())
        self.assertFalse(long["tail_extended"])

    def test_truncates_after_close(self):
        data = normalize([item("09:25:00", 10.0, 100, 0),
                          item("09:25:30", 10.0, 200, 0)])
        self.assertTrue(data["truncated_after_close"])
        self.assertEqual(data["n"], 1)

    def test_market_of(self):
        self.assertEqual(market_of("600001"), "sh")
        self.assertEqual(market_of("000001"), "sz")
        self.assertEqual(market_of("300750"), "sz")
        self.assertEqual(market_of("830799"), "bj")
        self.assertEqual(market_of(""), "unknown")


class TestEvaluateFacade(unittest.TestCase):
    def test_returns_v2_verdict(self):
        verdict = evaluate("600001", real_sample_shape(), quote())
        self.assertEqual(verdict.validation_status, "baseline_unvalidated")
        self.assertEqual(verdict.config_hash, R.CONFIG_HASH)
        self.assertEqual(verdict.semantics["matched_unit"], "lots")
        self.assertEqual(set(verdict.dims), set(R.DIM_ORDER))
        self.assertIsNotNone(verdict.evidence["last_time"])

    def test_empty_items_is_unavailable_not_weak(self):
        verdict = evaluate("600001", [], quote())
        self.assertFalse(verdict.is_weak)          # 关键行为变更：空数据不再置 weak
        self.assertIsNone(verdict.strength)
        self.assertIsNone(verdict.level)
        self.assertTrue(verdict.unverified)
        self.assertEqual(verdict.reject_reason, R.AUCTION_ITEMS_MISSING)
        self.assertEqual(verdict.data_quality.status, "unavailable")


class TestLevelBuckets(unittest.TestCase):
    def test_level_buckets(self):
        self.assertEqual(_level_of(80.0), "强")
        self.assertEqual(_level_of(50.0), "中")
        self.assertEqual(_level_of(20.0), "弱")
        self.assertIsNone(_level_of(None))


class TestRankAuctions(unittest.TestCase):
    def test_rank_only_ok_non_veto(self):
        from dragon_quant.review_account.auction.types import DataQuality

        good = AuctionVerdict(code="a", strength=80.0, direction="bull", level="强",
                              data_quality=DataQuality("ok", 1.0))
        weak = AuctionVerdict(code="b", is_weak=True, strength=0.0, level="弱",
                              data_quality=DataQuality("ok", 1.0))
        degraded = AuctionVerdict(code="c", strength=60.0, level="中",
                                  data_quality=DataQuality("degraded", 0.8))
        unavailable = AuctionVerdict(code="d", strength=None, unverified=True,
                                     data_quality=DataQuality("unavailable", 0.0))
        other = AuctionVerdict(code="e", strength=70.0, level="强",
                               data_quality=DataQuality("ok", 1.0))
        rank_auctions([good, weak, degraded, unavailable, other])
        self.assertEqual(good.rank, 1)
        self.assertEqual(other.rank, 2)
        self.assertIsNone(weak.rank)
        self.assertEqual(weak.rank_reason, "vetoed")
        self.assertIsNone(degraded.rank)
        self.assertEqual(degraded.rank_reason, "partial_data")
        self.assertEqual(unavailable.rank_reason, "unavailable")


if __name__ == "__main__":
    unittest.main()
