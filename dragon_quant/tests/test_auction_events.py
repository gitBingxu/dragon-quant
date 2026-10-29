"""事件责任链：3 个 veto、pre-only 非 veto、去重归属、互斥、去重（方案 §7.3）。"""

import unittest

from dragon_quant.review_account.auction import evaluate
from dragon_quant.review_account.auction.events import (
    VETO_REASONS, detect, validate_veto_windows,
)
from dragon_quant.review_account.auction.features import compute_features
from dragon_quant.review_account.auction.normalize import normalize
from dragon_quant.review_account.auction.types import RiskEvent
from dragon_quant.tests.support_auction import (
    item, quote, real_sample_shape, strong_to_weak, weak_to_strong,
)


def _events(items, quote_obj=None):
    norm = normalize(items)
    feats = compute_features(norm, quote_obj, None, code="600001")
    return detect("600001", norm, feats, quote_obj), norm


class TestVetoEvents(unittest.TestCase):
    def test_strong_to_weak_vetoes_with_core_window(self):
        events, _ = _events(strong_to_weak(), quote())
        veto = [e for e in events if e.veto]
        self.assertEqual([e.event_id.split("@")[0] for e in veto],
                         ["strong_to_weak"])
        self.assertEqual(veto[0].owner_dim, "supply")
        self.assertEqual(validate_veto_windows(events), [])

    def test_limit_down_queue_sell_vetoes_on_static_price_fact(self):
        items = [
            item("09:20:00", 9.5, 1000, -900),
            item("09:22:00", 9.2, 2000, -900),
            item("09:25:00", 9.0, 3000, -1000),
        ]
        events, _ = _events(items, quote(open_px=10.0, limit_down=9.0))
        veto = [e for e in events if e.veto]
        self.assertEqual([e.event_id.split("@")[0] for e in veto],
                         ["limit_down_queue_sell"])

    def test_high_position_green_increasing_vetoes(self):
        items = [
            item("09:20:00", 10.4, 1000, 500),
            item("09:22:00", 10.5, 2000, 500),
            item("09:23:00", 10.6, 3000, 500),
            item("09:24:30", 10.7, 4000, -100),
            item("09:24:45", 10.7, 5000, -200),
            item("09:25:00", 10.7, 6000, -300),
        ]
        events, _ = _events(items, quote(prev_close=10.0, open_px=10.6,
                                         limit_down=9.0))
        veto = [e.event_id.split("@")[0] for e in events if e.veto]
        self.assertIn("high_position_green_increasing", veto)

    def test_no_veto_when_conditions_miss(self):
        events, _ = _events(real_sample_shape(), quote(open_px=10.2))
        self.assertFalse([e for e in events if e.veto])


class TestPreOnlyEvidence(unittest.TestCase):
    def _spoof_tape(self):
        return [
            item("09:15:00", 10.0, 100, 200),
            item("09:18:00", 10.0, 150, 1200),
            item("09:19:00", 10.0, 150, 1000),   # pre 峰值买单（匹配未增）
            item("09:20:00", 10.0, 300, 100),
            item("09:22:00", 10.0, 500, 50),
            item("09:25:00", 10.0, 700, 50),
        ]

    def test_cancel_spoof_is_evidence_never_veto(self):
        events, _ = _events(self._spoof_tape(), quote(open_px=10.0))
        spoof = [e for e in events if e.event_id.startswith("cancel_spoof_pre20")]
        self.assertEqual(len(spoof), 1)
        self.assertFalse(spoof[0].veto)
        self.assertIsNone(spoof[0].owner_dim)
        self.assertEqual(validate_veto_windows(events), [])

    def test_pre_only_event_cannot_veto_and_is_flagged(self):
        bogus = RiskEvent(code="600001", event_id="cancel_spoof_pre20@09:19:00",
                          family="context", severity="medium", veto=True,
                          window=["09:15:00", "09:19:00"], owner_dim=None)
        self.assertEqual(validate_veto_windows([bogus]),
                         ["cancel_spoof_pre20@09:19:00"])

    def test_early_cancel_estimate_present_and_non_veto(self):
        events, _ = _events(self._spoof_tape(), quote(open_px=10.0))
        early = [e for e in events if e.event_id.startswith("early_cancel_estimate")]
        self.assertTrue(early)
        self.assertFalse(early[0].veto)


class TestConversionAndDedup(unittest.TestCase):
    def test_weak_to_strong_is_conversion_not_veto(self):
        events, _ = _events(weak_to_strong(), quote())
        conversion = [e for e in events if e.event_id.startswith("weak_to_strong")]
        self.assertTrue(conversion)
        self.assertFalse(conversion[0].veto)
        self.assertEqual(conversion[0].owner_dim, "supply")

    def test_min_samples_prevent_conversion(self):
        items = [item("09:20:00", 10.0, 500, 100),
                 item("09:25:00", 10.0, 1000, 100)]
        events, _ = _events(items, quote())
        self.assertFalse([e for e in events
                          if e.event_id.split("@")[0] in ("weak_to_strong",
                                                          "strong_to_weak")])

    def test_event_ids_unique_and_owner_single(self):
        events, _ = _events(real_sample_shape(), quote())
        ids = [e.event_id for e in events]
        self.assertEqual(len(ids), len(set(ids)))
        for event in events:
            self.assertIn(event.owner_dim,
                          (None, "supply", "price", "vol_price", "tail_grab"))
            self.assertIn(event.family,
                          ("risk", "conversion", "absorption", "context", "data"))

    def test_cancel_spoof_and_early_cancel_are_distinct(self):
        events, _ = _events(TestPreOnlyEvidence()._spoof_tape(), quote(open_px=10.0))
        veto = [e for e in events if e.veto]
        self.assertEqual(veto, [])
        self.assertTrue(any(e.event_id.startswith("cancel_spoof_pre20")
                            for e in events))


class TestEmptyNeverVeto(unittest.TestCase):
    def test_empty_items_have_no_events(self):
        verdict = evaluate("600001", [], quote())
        self.assertEqual(verdict.risk_events, [])
        self.assertFalse(verdict.is_weak)

    def test_veto_reason_lookup(self):
        self.assertEqual(VETO_REASONS["strong_to_weak"], "强转弱")


if __name__ == "__main__":
    unittest.main()
