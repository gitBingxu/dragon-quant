"""strategy_context（auction_sc_v1）：覆盖范围、状态、动作枚举、as_of。"""

import json
import unittest

from dragon_quant.review_account.auction import evaluate
from dragon_quant.review_account.auction.context import (
    CONTEXT_VERSION, DISCLAIMER, build_strategy_context, evidence_summary,
)
from dragon_quant.tests.support_auction import quote, real_sample_shape, strong_to_weak

FORBIDDEN = ("买入", "卖出", "加仓", "减仓", "清仓", "目标价")


def _verdicts():
    scored = evaluate("600001", real_sample_shape(), quote(code="600001"))
    vetoed = evaluate("600003", strong_to_weak(), quote(code="600003"))
    unavailable = evaluate("600002", real_sample_shape(), quote(code="600002",
                                                                  prev_close=0.0))
    return [scored, vetoed, unavailable]


class TestPositions(unittest.TestCase):
    def test_status_coverage(self):
        context = build_strategy_context(
            "2026-10-29", mode="collect", evaluations=_verdicts(),
            names={"600001": "甲", "600002": "乙", "600003": "丙"},
            failed=[{"code": "600004", "stage": "auction", "error": "x"}],
            empty=["600005"], candidate_codes=["600001", "600002", "600003",
                                                "600006"])
        statuses = {p["code"]: p["status"] for p in context["positions"]}
        self.assertEqual(statuses, {
            "600001": "scored", "600003": "vetoed", "600002": "unavailable",
            "600004": "failed", "600005": "empty", "600006": "unknown",
        })
        self.assertEqual(context["context_version"], CONTEXT_VERSION)
        self.assertEqual(context["summary"]["known"], 6)
        self.assertEqual(context["summary"]["scored"], 1)
        self.assertEqual(context["summary"]["failed"], 1)

    def test_collect_vs_recompute_coverage(self):
        collect = build_strategy_context(
            "2026-10-29", mode="collect", evaluations=_verdicts(),
            failed=[], empty=[], candidate_codes=["600001", "600099"])
        self.assertIn("600099", {p["code"] for p in collect["positions"]})
        recompute = build_strategy_context(
            "2026-10-29", mode="recompute", evaluations=_verdicts(),
            failed=[], empty=[], candidate_codes=None)
        self.assertEqual([p["code"] for p in recompute["positions"]],
                         ["600001", "600002", "600003"])
        self.assertEqual(recompute["pool"]["coverage"], "assets_only")

    def test_vetoed_position_carries_flag_risk_action(self):
        context = build_strategy_context("2026-10-29", mode="collect",
                                         evaluations=_verdicts())
        position = next(p for p in context["positions"] if p["code"] == "600003")
        self.assertEqual(position["status"], "vetoed")
        self.assertTrue(position["is_weak"])
        self.assertEqual(position["actions"][0]["kind"], "flag_risk")
        self.assertTrue(position["actions"][0]["event_id"].startswith("strong_to_weak@"))


class TestActionsAndDisclaimers(unittest.TestCase):
    def test_action_enumeration_and_no_buy_sell_words(self):
        context = build_strategy_context("2026-10-29", mode="collect",
                                         evaluations=_verdicts())
        allowed = {"observe", "await_confirmation", "flag_risk", "request_data"}
        for position in context["positions"]:
            for action in position["actions"]:
                self.assertIn(action["kind"], allowed)
                for word in FORBIDDEN:
                    self.assertNotIn(word, action.get("text", ""))

    def test_disclaimer_and_validation_status_are_present(self):
        context = build_strategy_context("2026-10-29", mode="collect",
                                         evaluations=_verdicts())
        self.assertEqual(context["disclaimer"], DISCLAIMER)
        self.assertEqual(context["validation_status"], "baseline_unvalidated")
        json.dumps(context, ensure_ascii=False, allow_nan=False)

    def test_post_open_actions_are_conditional(self):
        scored = evaluate("600001", real_sample_shape(), quote(code="600001"))
        context = build_strategy_context("2026-10-29", mode="collect",
                                         evaluations=[scored])
        observe = context["positions"][0]["actions"][0]
        self.assertEqual(observe["kind"], "observe")
        self.assertEqual(observe["requires"], "post_open")
        self.assertIn("condition", observe)

    def test_evidence_summary_is_deterministic(self):
        verdict = evaluate("600001", real_sample_shape(), quote())
        self.assertEqual(evidence_summary(verdict), evidence_summary(verdict))
        self.assertLessEqual(len(evidence_summary(verdict).split()), 3)


if __name__ == "__main__":
    unittest.main()
