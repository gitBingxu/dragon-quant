"""premarket.actions 行动上限过滤链（阶段 3 Task 2）。

真值表覆盖：prep 不可用 → 资格 → 仓位三态 → 冻结预期 → 竞价可用 → veto
→ 数据质量 → wait。任何输出动作必须属于既有枚举；``0`` 只表示不新建，不是清仓。
"""

from __future__ import annotations

import unittest

from dragon_quant.review_account.premarket.actions import (
    ACTION_VALUES,
    build_ceiling,
)

STATES = ("confirmed", "weakened", "invalidated", "unknown")

FROZEN = {
    "confirmed": ["execute_plan", "execute_partial"],
    "weakened": ["downgrade_half", "wait_pullback"],
    "invalidated": ["cancel_entry", "exit_via_sell_rules"],
    "unknown": ["observe_only"],
}


def ceiling(**overrides):
    base = dict(
        prep_usable=True,
        prep_reason=None,
        eligibility="eligible",
        position_pct=0.04,
        expectations_present=True,
        auction_present=True,
        auction_usable=True,
        auction_status="scored",
        is_weak=False,
        reject_reason=None,
        auction_quality="ok",
        after_verification=FROZEN,
    )
    base.update(overrides)
    return build_ceiling(**base)


class TestActionCeiling(unittest.TestCase):
    def test_prep_unusable_observes_only(self):
        result = ceiling(prep_usable=False, prep_reason="prep_missing")
        self.assertEqual(result["status"], "observe_only")
        self.assertEqual(result["blocked_by"], ["prep_missing"])
        self.assertEqual(result["allowed_now"], ["observe_only"])

    def test_non_eligible_never_opens_a_new_position(self):
        for value, reason in (("not_qualified", "eligibility_not_qualified"),
                              ("unknown", "eligibility_unknown"),
                              (None, "eligibility_unknown")):
            with self.subTest(value=value):
                result = ceiling(eligibility=value)
                self.assertEqual(result["status"], "no_new_position")
                self.assertEqual(result["blocked_by"], [reason])

    def test_position_null_observes_and_zero_means_no_new_position(self):
        null = ceiling(position_pct=None)
        self.assertEqual(null["status"], "observe_only")
        self.assertEqual(null["blocked_by"], ["position_pct_null"])

        zero = ceiling(position_pct=0)
        self.assertEqual(zero["status"], "no_new_position")
        self.assertEqual(zero["blocked_by"], ["position_pct_zero"])
        self.assertNotIn("exit_via_sell_rules", zero["allowed_now"])
        self.assertEqual(zero["position_exit"], "existing_sell_rules_only")

    def test_missing_expectations_observe_only(self):
        result = ceiling(expectations_present=False)
        self.assertEqual(result["status"], "observe_only")
        self.assertEqual(result["blocked_by"], ["expectations_unavailable"])

    def test_missing_or_unusable_auction_observes_only(self):
        for overrides in ({"auction_present": False},
                          {"auction_usable": False},
                          {"auction_status": "unavailable"}):
            with self.subTest(**overrides):
                result = ceiling(**overrides)
                self.assertEqual(result["status"], "observe_only")
                self.assertEqual(result["blocked_by"], ["auction_unavailable"])

    def test_veto_blocks_new_position_with_original_reason(self):
        weak = ceiling(is_weak=True, reject_reason="high_green")
        self.assertEqual(weak["status"], "no_new_position")
        self.assertEqual(weak["blocked_by"], ["auction_veto:high_green"])

        status_veto = ceiling(auction_status="vetoed")
        self.assertEqual(status_veto["status"], "no_new_position")
        self.assertEqual(status_veto["blocked_by"], ["auction_veto:weak"])

    def test_auction_data_quality_not_ok_observes_only(self):
        result = ceiling(auction_quality="degraded")
        self.assertEqual(result["status"], "observe_only")
        self.assertEqual(result["blocked_by"], ["auction_data_quality_degraded"])

    def test_all_gates_open_waits_and_copies_frozen_actions(self):
        result = ceiling()
        self.assertEqual(result["status"], "wait")
        self.assertEqual(result["allowed_now"], ["wait"])
        self.assertEqual(result["after_verification"], FROZEN)
        self.assertEqual(result["requires"], [
            "open_verification", "external_evidence_check", "account_gate"])
        self.assertEqual(result["position_exit"], "existing_sell_rules_only")

    def test_blocked_states_never_copy_frozen_actions(self):
        result = ceiling(position_pct=0)
        self.assertEqual(result["after_verification"],
                         {state: ["observe_only"] for state in STATES})

    def test_every_emitted_action_is_in_the_existing_enum(self):
        cases = [ceiling(), ceiling(position_pct=0), ceiling(is_weak=True),
                 ceiling(prep_usable=False)]
        for result in cases:
            for action in result["allowed_now"]:
                self.assertIn(action, ACTION_VALUES)
            for actions in result["after_verification"].values():
                for action in actions:
                    self.assertIn(action, ACTION_VALUES)

    def test_after_verification_always_has_four_states(self):
        for result in (ceiling(), ceiling(position_pct=0)):
            self.assertEqual(set(result["after_verification"]), set(STATES))

    def test_missing_states_in_frozen_map_default_to_observe_only(self):
        result = ceiling(after_verification={"confirmed": ["execute_plan"]})
        self.assertEqual(result["after_verification"]["confirmed"],
                         ["execute_plan"])
        for state in ("weakened", "invalidated", "unknown"):
            self.assertEqual(result["after_verification"][state],
                             ["observe_only"])


if __name__ == "__main__":
    unittest.main()
