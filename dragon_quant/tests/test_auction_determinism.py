"""确定性：同输入两次一致、无 wall clock、键序稳定（方案 §13.2）。"""

import json
import unittest

from dragon_quant.review_account.auction.context import as_of
from dragon_quant.review_account.auction.scoring import evaluate, evaluate_batch
from dragon_quant.review_account.auction.serialize import verdict_to_payload
from dragon_quant.tests.support_auction import quote, real_sample_shape


def _assets():
    return [
        {"code": "600001", "items": list(real_sample_shape()),
         "quote": quote(code="600001")},
        {"code": "600002", "items": list(real_sample_shape()),
         "quote": quote(code="600002")},
    ]


class TestDeterminism(unittest.TestCase):
    def test_same_input_twice_is_identical(self):
        first = verdict_to_payload(evaluate("600001", real_sample_shape(), quote()))
        second = verdict_to_payload(evaluate("600001", real_sample_shape(), quote()))
        self.assertEqual(
            json.dumps(first, sort_keys=True, allow_nan=False),
            json.dumps(second, sort_keys=True, allow_nan=False))

    def test_batch_is_identical(self):
        a = evaluate_batch(_assets())
        b = evaluate_batch(_assets())
        self.assertEqual(
            json.dumps(a[0], sort_keys=True, allow_nan=False),
            json.dumps(b[0], sort_keys=True, allow_nan=False))
        self.assertEqual(
            json.dumps(a[2], sort_keys=True, allow_nan=False),
            json.dumps(b[2], sort_keys=True, allow_nan=False))

    def test_key_order_is_stable(self):
        payload = verdict_to_payload(evaluate("600001", real_sample_shape(), quote()))
        self.assertEqual(list(payload)[:4],
                         ["version", "code", "is_weak", "strength"])
        self.assertEqual(list(payload)[-3:],
                         ["reference_prices", "windows", "actions"])

    def test_as_of_is_derived_from_trade_date(self):
        self.assertEqual(as_of("20261029"), "2026-10-29T09:25:00+08:00")
        self.assertEqual(as_of("2026-10-29"), "2026-10-29T09:25:00+08:00")


if __name__ == "__main__":
    unittest.main()
