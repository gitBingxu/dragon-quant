"""数据质量四轴：ok/degraded/unavailable、原因码、completeness、PIT 白名单。"""

import unittest

from dragon_quant.review_account.auction import evaluate
from dragon_quant.review_account.auction.features import PIT_QUOTE_FIELDS
from dragon_quant.tests.support_auction import item, quote, real_sample_shape


class TestStatuses(unittest.TestCase):
    def test_ok_when_all_dims_complete(self):
        verdict = evaluate("600001", real_sample_shape(), quote())
        self.assertEqual(verdict.data_quality.status, "ok")
        self.assertEqual(verdict.data_quality.completeness, 1.0)
        self.assertFalse(verdict.unverified)

    def test_official_open_missing_degrades(self):
        verdict = evaluate("600001", real_sample_shape(),
                           quote(open_px=0.0, price=10.3))
        self.assertEqual(verdict.data_quality.status, "degraded")
        self.assertIn("official_open_missing", verdict.data_quality.reason_codes)
        self.assertIsNone(verdict.rank)

    def test_prev_close_missing_is_unavailable(self):
        verdict = evaluate("600001", real_sample_shape(), quote(prev_close=0.0))
        self.assertEqual(verdict.data_quality.status, "unavailable")
        self.assertIsNone(verdict.strength)
        self.assertTrue(verdict.unverified)
        self.assertFalse(verdict.is_weak)
        self.assertEqual(verdict.data_quality.reason_codes, ["prev_close_missing"])

    def test_core_window_short_is_unavailable(self):
        items = [item("09:15:00", 10.0, 100, 100),
                 item("09:18:00", 10.0, 200, 100),
                 item("09:25:00", 10.0, 300, 100)]
        verdict = evaluate("600001", items, quote())
        self.assertEqual(verdict.data_quality.status, "unavailable")
        self.assertIn("core_window_short", verdict.data_quality.reason_codes)

    def test_rank_only_for_ok(self):
        degraded = evaluate("600001", real_sample_shape(),
                            quote(open_px=0.0, price=10.3))
        self.assertIsNone(degraded.rank)
        self.assertEqual(degraded.rank_reason, "partial_data")


class TestReasonCodesStable(unittest.TestCase):
    def test_warning_codes_do_not_degrade(self):
        verdict = evaluate("600001", real_sample_shape(),
                           quote(limit_down=0.0, open_px=10.2))
        self.assertIn("limit_down_missing", verdict.data_quality.reason_codes)
        self.assertEqual(verdict.data_quality.status, "ok")

    def test_no_duplicate_reason_codes(self):
        verdict = evaluate("600001", real_sample_shape(),
                           quote(limit_down=0.0))
        codes = verdict.data_quality.reason_codes
        self.assertEqual(len(codes), len(set(codes)))


class TestPitWhitelist(unittest.TestCase):
    def test_quote_fields_used_excludes_intraday_fields(self):
        verdict = evaluate("600001", real_sample_shape(), quote())
        used = verdict.evidence["quote_fields_used"]
        self.assertNotIn("pct", used)
        self.assertNotIn("amount", used)
        self.assertNotIn("volume", used)
        self.assertNotIn("main_net_amount", used)
        self.assertNotIn("turnover_rate", used)
        self.assertTrue(set(used) <= set(PIT_QUOTE_FIELDS))

    def test_price_only_used_for_float_share_conversion(self):
        verdict = evaluate("600001", real_sample_shape(), quote())
        self.assertIn("price", verdict.evidence["quote_fields_used"])
        self.assertEqual(verdict.evidence["float_shares_source"],
                         "quote.float_market_cap/quote.price")

    def test_quote_missing_is_unavailable(self):
        verdict = evaluate("600001", real_sample_shape(), None)
        self.assertEqual(verdict.data_quality.status, "unavailable")
        self.assertIn("prev_close_missing", verdict.data_quality.reason_codes)


if __name__ == "__main__":
    unittest.main()
