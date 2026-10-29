"""dragon_quant.tests.test_auction_serialize — v2 契约与 null / NaN 清洗。"""

import json
import unittest

from dragon_quant.infra.models import Quote
from dragon_quant.review_account.auction import AuctionVerdict
from dragon_quant.review_account.auction.serialize import (
    QuotePayloadError,
    quote_from_payload,
    quote_to_payload,
    sanitize_json,
    verdict_to_payload,
)
from dragon_quant.review_account.auction.types import DataQuality, DimensionScore


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


def _dims(**overrides):
    dims = {
        "direction": DimensionScore("direction", "价格表态", 70.0, 0.25, 0.25, "scored",
                                    {"nan": float("nan")}),
        "liquidity": DimensionScore("liquidity", "竞价流动性", 55.0, 0.20, 0.20),
        "supply": DimensionScore("supply", "供需与转换", 80.0, 0.25, 0.25),
        "vol_price": DimensionScore("vol_price", "量价协同", 65.0, 0.20, 0.20),
        "tail_grab": DimensionScore("tail_grab", "末段执行", 60.0, 0.10, 0.10),
    }
    dims.update(overrides)
    return dims


def _verdict(**overrides):
    values = dict(code="600172", is_weak=False, strength=72.5, direction="bull",
                  level="强", dims=_dims(), reject_reason=None, unverified=False,
                  rank=None, rank_reason=None, pre_veto_strength=72.5,
                  validation_status="baseline_unvalidated",
                  config_version="auction_v2_baseline", config_hash="d16623308182",
                  semantics={"semantics_version": "auction_sem_2026_10_29"},
                  data_quality=DataQuality("ok", 1.0), risk_events=[],
                  raw_factors={"official_open_gap_pct": 2.0}, evidence={"market": "sh"},
                  reference_prices={"prev_close": 10.0}, windows={"core": {"n": 3}},
                  actions=[{"kind": "observe", "text": "观察"}])
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
        self.assertEqual(quote_from_payload(quote_to_payload(quote)), quote)

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
    def test_contract_fields_and_key_order(self):
        payload = verdict_to_payload(_verdict())
        self.assertEqual(list(payload), [
            "version", "code", "is_weak", "strength", "direction", "level",
            "dims", "reject_reason", "unverified", "rank", "rank_reason",
            "pre_veto_strength", "validation_status", "config_version",
            "config_hash", "semantics", "data_quality", "risk_events",
            "raw_factors", "evidence", "reference_prices", "windows", "actions",
        ])
        self.assertEqual(payload["version"], "2")
        self.assertIsNone(payload["rank"])
        self.assertEqual(set(payload["dims"]), {
            "direction", "liquidity", "supply", "vol_price", "tail_grab",
        })
        self.assertEqual(set(payload["dims"]["direction"]),
                         {"dim", "label", "score", "weight", "effective_weight",
                          "status", "details"})
        self.assertIsNone(payload["dims"]["direction"]["details"]["nan"])

    def test_null_strength_and_level_are_legal(self):
        payload = verdict_to_payload(_verdict(strength=None, level=None,
                                              unverified=True,
                                              data_quality=DataQuality(
                                                  "unavailable", 0.0,
                                                  [{"code": "prev_close_missing",
                                                    "severity": "unavailable",
                                                    "field": "quote.prev_close"}])))
        self.assertIsNone(payload["strength"])
        self.assertIsNone(payload["level"])
        self.assertEqual(payload["data_quality"]["status"], "unavailable")
        self.assertEqual(payload["data_quality"]["reason_codes"],
                         ["prev_close_missing"])
        # 可 JSON 落库（allow_nan=False）
        json.dumps(payload, ensure_ascii=False, allow_nan=False)

    def test_non_finite_numeric_cleaned_to_null(self):
        payload = verdict_to_payload(_verdict(strength=float("nan")))
        self.assertIsNone(payload["strength"])
        bad = _dims()
        bad["supply"] = DimensionScore("supply", "供需与转换", float("inf"),
                                       0.25, 0.25)
        payload = verdict_to_payload(_verdict(dims=bad))
        self.assertIsNone(payload["dims"]["supply"]["score"])

    def test_rank_serialized(self):
        self.assertEqual(verdict_to_payload(_verdict(rank=2))["rank"], 2)


class TestScorePipelineOrder(unittest.TestCase):
    def test_rank_is_serialized_after_ranking(self):
        """evaluate → rank → payload：rank 不得因顺序错误丢失。"""
        from dragon_quant.review_account.auction.main import _score_auction_assets

        items = [
            {"time": "09:20:00", "price": 10.0, "matched": 1000, "unmatched": 100},
            {"time": "09:23:00", "price": 10.1, "matched": 2000, "unmatched": 200},
            {"time": "09:24:30", "price": 10.1, "matched": 3000, "unmatched": 200},
            {"time": "09:25:00", "price": 10.2, "matched": 4000, "unmatched": 300},
        ]
        assets = [
            {"code": "600001", "items": items, "quote": _quote(code="600001")},
            {"code": "600002", "items": items, "quote": _quote(code="600002")},
        ]
        payloads, failures, _ = _score_auction_assets(assets)
        self.assertEqual(failures, [])
        self.assertEqual({p["rank"] for p in payloads.values()}, {1, 2})
        for payload in payloads.values():
            self.assertEqual(payload["version"], "2")
            self.assertEqual(payload["data_quality"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
