"""新旧双跑对账（AC2 证据）：差异必须落在已知类别，不进生产 JSON（方案 §14.3）。"""

import unittest

from dragon_quant.review_account.auction.scoring import evaluate
from dragon_quant.review_account.auction.serialize import verdict_to_payload
from dragon_quant.tests import support_legacy_auction_v1 as legacy
from dragon_quant.tests.support_auction import (
    quote, real_sample_shape, strong_to_weak, weak_to_strong,
)


class TestDualRunReconciliation(unittest.TestCase):
    def _report(self):
        report = []
        for name, items in (
            ("real_sample", real_sample_shape()),
            ("weak_to_strong", weak_to_strong()),
            ("strong_to_weak", strong_to_weak()),
        ):
            for quote_obj in (quote(open_px=10.2), quote(open_px=0.0),
                              quote(limit_down=0.0)):
                old = legacy.evaluate("600001", items, quote_obj)
                new = verdict_to_payload(evaluate("600001", items, quote_obj))
                categories = legacy.classify(old, new)
                report.append({"fixture": name, "categories": categories,
                               "old_strength": old.get("strength"),
                               "new_strength": new.get("strength")})
        return report

    def test_every_difference_is_classified(self):
        report = self._report()
        self.assertTrue(report)
        for row in report:
            self.assertNotIn("unexplained", row["categories"], row)
            self.assertTrue(set(row["categories"]) <= set(legacy.CATEGORIES), row)

    def test_known_categories_are_observed(self):
        observed = set()
        for row in self._report():
            observed.update(row["categories"])
        self.assertIn("unit_x100", observed)
        self.assertIn("full_window_sum", observed)
        self.assertIn("veto_missing_data", observed)

    def test_old_scores_are_not_written_to_production_payload(self):
        new = verdict_to_payload(evaluate("600001", real_sample_shape(), quote()))
        self.assertNotIn("legacy", new)
        self.assertNotIn("shadow", new)


if __name__ == "__main__":
    unittest.main()
