"""tests.test_auction_serialize — 竞价序列化契约与 NaN 清洗。"""

import unittest

from dragon_quant.models.types import Quote, ScoreResult
from dragon_quant.auction import AuctionVerdict
from dragon_quant.auction.serialize import (
    QuotePayloadError,
    quote_from_payload,
    quote_to_payload,
    sanitize_json,
    verdict_to_payload,
)


def _quote(**overrides):
    values = dict(
        code="600172", name="黄河旋风", price=10.5, prev_close=10.0,
        open_px=10.2, high=10.6, low=9.9, pct=5.0, chg=0.5,
        turnover_rate=1.2, amplitude=7.0, volume=123456.0, amount=1.3e8,
        market_cap=5.0e9, float_market_cap=3.2e9, volume_ratio=2.1, pe=35.0,
        limit_up=11.0, limit_down=9.0, avg_price=10.4,
        bid1_price=10.5, bid1_volume=800.0, ask1_volume=120.0,
        timestamp=0, main_net_amount=None,
    )
    values.update(overrides)
    return Quote(**values)


def _verdict(**overrides):
    dims = {
        "direction": ScoreResult(dim="direction", score=70.0, weight=0.25,
                                 details={"open_pct": 1.5, "nan": float("nan")}),
        "supply": ScoreResult(dim="supply", score=80.0, weight=0.30, details={}),
        "vol_price": ScoreResult(dim="vol_price", score=65.0, weight=0.25, details={}),
        "tail_grab": ScoreResult(dim="tail_grab", score=60.0, weight=0.20, details={}),
    }
    values = dict(code="600172", is_weak=False, strength=72.5, direction="bull",
                  level="强", dims=dims, reject_reason=None, unverified=True, rank=None)
    values.update(overrides)
    return AuctionVerdict(**values)


class TestSanitizeJson(unittest.TestCase):
    def test_non_finite_to_none(self):
        self.assertIsNone(sanitize_json(float("nan")))
        self.assertIsNone(sanitize_json(float("inf")))
        self.assertIsNone(sanitize_json(float("-inf")))
        self.assertEqual(sanitize_json([1, float("nan"), {"x": float("inf")}]),
                         [1, None, {"x": None}])

    def test_rejects_unsupported_type(self):
        with self.assertRaises(TypeError):
            sanitize_json(object())


class TestQuotePayload(unittest.TestCase):
    def test_roundtrip(self):
        quote = _quote()
        payload = quote_to_payload(quote)
        self.assertEqual(quote_from_payload(payload), quote)

    def test_nan_and_inf_cleaned(self):
        payload = quote_to_payload(_quote(price=float("nan"), pe=float("inf")))
        self.assertIsNone(payload["price"])
        self.assertIsNone(payload["pe"])
        restored = quote_from_payload(payload)
        self.assertEqual(restored.price, 0.0)
        self.assertEqual(restored.pe, 0.0)

    def test_none(self):
        self.assertIsNone(quote_to_payload(None))
        self.assertIsNone(quote_from_payload(None))

    def test_unknown_keys_ignored(self):
        payload = quote_to_payload(_quote())
        payload["future_field"] = "x"
        self.assertEqual(quote_from_payload(payload), _quote())

    def test_partial_null_scalars_restored_to_zero(self):
        payload = {"code": "600172", "prev_close": 10.0, "limit_down": None,
                   "float_market_cap": None, "name": None, "price": None}
        quote = quote_from_payload(payload)
        self.assertEqual(quote.limit_down, 0.0)
        self.assertEqual(quote.float_market_cap, 0.0)
        self.assertEqual(quote.name, "")
        self.assertEqual(quote.price, 0.0)

    def test_missing_required_raises(self):
        payload = quote_to_payload(_quote())
        for field in ("code", "prev_close", "limit_down", "float_market_cap"):
            broken = dict(payload)
            broken.pop(field)
            with self.assertRaises(QuotePayloadError):
                quote_from_payload(broken)

    def test_required_numeric_unparseable_raises(self):
        payload = quote_to_payload(_quote())
        payload["prev_close"] = "abc"
        with self.assertRaises(QuotePayloadError):
            quote_from_payload(payload)
        payload["prev_close"] = float("nan")
        with self.assertRaises(QuotePayloadError):
            quote_from_payload(payload)

    def test_invalid_payload_raises(self):
        with self.assertRaises(QuotePayloadError):
            quote_from_payload(["not", "a", "dict"])
        with self.assertRaises(QuotePayloadError):
            quote_from_payload({"code": "", "prev_close": 10.0,
                                "limit_down": 9.0, "float_market_cap": 1e9})


class TestVerdictPayload(unittest.TestCase):
    def test_contract_fields(self):
        payload = verdict_to_payload(_verdict())
        self.assertEqual(set(payload), {
            "version", "code", "is_weak", "strength", "direction", "level",
            "dims", "reject_reason", "unverified", "rank",
        })
        self.assertEqual(payload["version"], "1")
        self.assertIsNone(payload["rank"])
        self.assertEqual(set(payload["dims"]), {
            "direction", "supply", "vol_price", "tail_grab",
        })
        self.assertEqual(set(payload["dims"]["direction"]),
                         {"dim", "score", "weight", "details"})
        self.assertIsNone(payload["dims"]["direction"]["details"]["nan"])

    def test_rank_serialized(self):
        self.assertEqual(verdict_to_payload(_verdict(rank=2))["rank"], 2)

    def test_non_finite_contract_numeric_raises(self):
        with self.assertRaises(ValueError):
            verdict_to_payload(_verdict(strength=float("nan")))
        bad_dims = dict(_verdict().dims)
        bad_dims["supply"] = ScoreResult(dim="supply", score=float("inf"),
                                         weight=0.30, details={})
        with self.assertRaises(ValueError):
            verdict_to_payload(_verdict(dims=bad_dims))


class TestScorePipelineOrder(unittest.TestCase):
    def test_rank_is_serialized_after_ranking(self):
        """evaluate → rank_auctions → payload：rank 不得因顺序错误丢失。"""
        from unittest.mock import patch
        from dragon_quant.auction import registry as R
        from dragon_quant.auction.service import _score_auction_assets

        items = [
            {"time": "09:20:00", "price": 10.0, "matched": 100, "unmatched": 100},
            {"time": "09:24:30", "price": 10.0, "matched": 200, "unmatched": 200},
            {"time": "09:25:00", "price": 10.0, "matched": 300, "unmatched": 300},
        ]
        assets = [
            {"code": "600001", "items": items, "quote": _quote(code="600001")},
            {"code": "600002", "items": items, "quote": _quote(code="600002")},
        ]
        with patch.object(R, "SEMANTICS_VERIFIED", True):
            payloads, failures, _ = _score_auction_assets(assets)
        self.assertEqual(failures, [])
        self.assertEqual({p["rank"] for p in payloads.values()}, {1, 2})


if __name__ == "__main__":
    unittest.main()
