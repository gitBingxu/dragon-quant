"""真实语义回归（AC2）：单位 ×100、官方开盘/指示价分离、窗口、符号。"""

import unittest

from dragon_quant.review_account.auction import evaluate
from dragon_quant.review_account.auction import registry as R
from dragon_quant.tests.support_auction import item, quote, real_sample_shape


def _turnover_tape():
    return [
        item("09:20:00", 10.0, 400, 100),
        item("09:22:00", 10.0, 700, 100),
        item("09:25:00", 10.0, 1000, 100),
    ]


class TestSemanticsContract(unittest.TestCase):
    def test_snapshot_carries_semantics_block(self):
        verdict = evaluate("600001", real_sample_shape(), quote())
        self.assertEqual(verdict.semantics["semantics_version"], R.SEMANTICS_VERSION)
        self.assertEqual(verdict.semantics["matched_unit"], "lots")
        self.assertEqual(verdict.semantics["unmatched_sign"],
                         "positive_is_buy_remainder")
        self.assertEqual(verdict.semantics["core_window"], ["09:20:00", "09:25:00"])

    def test_turnover_applies_x100_units(self):
        # 流通市值 5 亿、价格 10 元 → 5e7 股；1000 手 = 1e5 股 → 0.2%
        verdict = evaluate("600001", _turnover_tape(),
                           quote(price=10.0, float_cap=5.0e8))
        self.assertAlmostEqual(verdict.raw_factors["auction_turnover_pct"], 0.2,
                               places=6)

    def test_official_open_is_the_only_gap_source(self):
        items = [
            item("09:20:00", 10.0, 400, 100),
            item("09:22:00", 10.1, 700, 100),
            item("09:24:59", 10.2, 1000, 100),
        ]
        verdict = evaluate("600001", items,
                           quote(prev_close=10.0, open_px=10.5, price=10.2))
        self.assertAlmostEqual(verdict.raw_factors["official_open_gap_pct"], 5.0)
        self.assertAlmostEqual(verdict.raw_factors["last_indicative_gap_pct"], 2.0)
        self.assertAlmostEqual(
            verdict.raw_factors["final_indicative_slippage_pct"],
            (10.5 / 10.2 - 1) * 100, places=4)
        self.assertNotEqual(verdict.raw_factors["official_open_gap_pct"],
                            verdict.raw_factors["last_indicative_gap_pct"])

    def test_last_point_need_not_be_exactly_0925(self):
        verdict = evaluate("600001", real_sample_shape(), quote())
        self.assertNotEqual(verdict.data_quality.status, "unavailable")
        self.assertEqual(verdict.evidence["last_time"], "09:24:59")

    def test_pre20_matched_decline_is_not_negative_volume(self):
        verdict = evaluate("600001", real_sample_shape(), quote())
        # 09:20 前的下降不触发数据警告
        self.assertNotIn("post20_matched_decrease",
                         verdict.data_quality.reason_codes)
        self.assertGreater(verdict.raw_factors["last_matched_lots"], 0)

    def test_post20_matched_decline_warns_but_never_vetoes(self):
        items = [
            item("09:20:00", 10.0, 5000, 100),
            item("09:22:00", 10.0, 4000, 100),
            item("09:25:00", 10.0, 3000, 100),
        ]
        verdict = evaluate("600001", items, quote(open_px=10.0))
        self.assertIn("post20_matched_decrease",
                      verdict.data_quality.reason_codes)
        self.assertFalse(verdict.is_weak)

    def test_unmatched_sign_positive_is_buy(self):
        verdict = evaluate("600001", real_sample_shape(), quote())
        self.assertGreater(verdict.raw_factors["final_unmatched_lots_signed"], 0)
        self.assertGreater(verdict.raw_factors["final_unmatched_matched_ratio"], 0)


if __name__ == "__main__":
    unittest.main()
