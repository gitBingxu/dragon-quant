"""premarket.context 纯投影测试（阶段 3 Task 2）。

覆盖 ``premarket_sc_v1`` 键序、五个时点分离、严格性 / proven_gap、三锚投影
与去重、候选 join 视图、行动上限、组合约束与稳定降级原因码。
"""

from __future__ import annotations

import copy
import unittest

from dragon_quant.review_account.premarket.context import (
    CONTEXT_VERSION,
    build_context,
)

T = "2026-10-30"
T1 = "2026-10-29"
RUN_AT = "2026-10-30T09:06:12+08:00"

STATES = ("confirmed", "weakened", "invalidated", "unknown")
FROZEN = {
    "confirmed": ["execute_plan", "execute_partial"],
    "weakened": ["downgrade_half", "wait_pullback"],
    "invalidated": ["cancel_entry", "exit_via_sell_rules"],
    "unknown": ["observe_only"],
}


def _review_candidate(code: str, *, role: str | None = "follower",
                      eligibility: str = "eligible",
                      position_pct=0.04, expectations=True,
                      mainline: dict | None = None) -> dict:
    return {
        "code": code,
        "name": f"股票{code}",
        "role": role,
        "mainline": mainline,
        "review_score": 70.3,
        "review_grade": "B",
        "confidence": 0.7,
        "risk_level": "low",
        "eligibility": eligibility,
        "position_pct": position_pct,
        "position_basis": ["factor", "value"],
        "reason_code": None,
        "gate_trace": [{"gate": "G1_hard_risk", "passed": True}],
        "warnings": [],
        "expectations": (
            {"gap_low": -0.02, "gap_high": 0.04, "ratio_low": 0.8,
             "seal_by": "10:00"} if expectations else None),
    }


def _feedback_plan(code: str, *, expectations=True) -> dict:
    if not expectations:
        return {"code": code, "role": None, "position_pct": None,
                "expected": None, "conditions": [],
                "allowed_actions": ["observe_only"],
                "unknown_reason": "expectations_unavailable"}
    return {
        "code": code,
        "role": "leader",
        "position_pct": 0.04,
        "expected": {"gap_low": -0.02, "gap_high": 0.04,
                     "ratio_low": 0.8, "seal_by": "10:00"},
        "conditions": [
            {"state": state, "requires": ["condition"],
             "allowed_actions": list(FROZEN[state])}
            for state in STATES
        ],
        "allowed_actions": list(FROZEN["confirmed"]),
        "unknown_reason": None,
    }


def _prep_result(candidates=None, plans=None) -> dict:
    candidates = candidates if candidates is not None else [
        _review_candidate("600001", role="leader",
                          mainline={"board_name": "机器人", "rank": 1,
                                    "state": "strengthening"}),
        _review_candidate("600002", role="vanguard", position_pct=0.0,
                          mainline={"board_name": "机器人", "rank": 1,
                                    "state": "strengthening"}),
        _review_candidate("600003", role="leader", position_pct=None,
                          mainline={"board_name": "AI算力", "rank": 2,
                                    "state": "neutral"}),
        _review_candidate("600005", role="mainstay", position_pct=0.03,
                          mainline={"board_name": "机器人", "rank": 1,
                                    "state": "strengthening"}),
        _review_candidate("600011", role="follower", position_pct=0.02,
                          mainline={"board_name": "机器人", "rank": 1,
                                    "state": "strengthening"}),
        _review_candidate("600020", role=None, position_pct=0.0,
                          mainline=None, expectations=False),
    ]
    plans = plans if plans is not None else [
        _feedback_plan(item["code"],
                       expectations=item["expectations"] is not None)
        for item in candidates
    ]
    return {
        "trade_date": T1,
        "version": "0.8",
        "sentiment": {
            "up_cnt": 2100, "down_cnt": 950, "limit_up_cnt": 62,
            "limit_down_cnt": 3, "broken_rate": 0.016,
            "yesterday_premium_pct": 5.67, "promotion_rate": 0.667,
            "max_board_height": 4,
        },
        "candidates": [
            {"code": "600001", "facts": {"limit_up_type": "换手板",
                                         "open_num": 0}},
            {"code": "600011", "facts": {"limit_up_type": "一字板",
                                         "open_num": 1,
                                         "risk_announcement": {"level": "hard",
                                                               "title": "立案"}}},
        ],
        "review": {
            "enabled": True,
            "config_version": "review_baseline_v1",
            "config_hash": "cfg-hash",
            "review_version": "review_v1",
            "market": {"state": "strong", "score": 72.0, "coeff": 1.0,
                       "evidence": ["涨停 62 家"], "unknown_reason": None},
            "mainlines": [
                {"board_name": "机器人", "state": "strengthening",
                 "role_map": {"leader": "600001", "vanguard": "600002",
                              "mainstay": "600005", "capacity": "600006"}},
                {"board_name": "AI算力", "state": "neutral",
                 "role_map": {"leader": "600003", "mainstay": "600003"}},
            ],
            "anchors": {
                "positive": [{"code": "600001", "reason": "市场最高 4 板"}],
                "negative": [{"code": "600010", "reason": "昨日涨停今日 -8.0%"}],
                "risk": {"limit_down_cnt": 3, "broken_rate": 0.016},
            },
            "portfolio": {"sum_pct": 9.0, "warning": None},
            "degraded": [],
            "review_candidates": candidates,
        },
        "review_candidates": candidates,
        "strategy_context": {
            "frozen_at": f"{T1}T16:40:00+08:00",
            "positions": [],
            "feedback_plans": plans,
        },
    }


def prep_envelope(*, result=None, available=True, state="ready",
                  frozen_at=f"{T1}T16:40:00+08:00",
                  frozen_at_reason=None, schema_version="0.8",
                  schema_supported=True, review_available=True,
                  review_config=None) -> dict:
    config = review_config if review_config is not None else {
        "config_version": "review_baseline_v1",
        "config_hash": "cfg-hash",
        "review_version": "review_v1",
    }
    return {
        "available": available,
        "state": state,
        "trade_date": T1 if available else None,
        "created_at": f"{T1} 16:52:11" if available else None,
        "frozen_at": frozen_at,
        "frozen_at_reason": frozen_at_reason,
        "schema_version": schema_version,
        "schema_supported": schema_supported,
        "review_available": review_available,
        "review_config": config if review_available else None,
        "result": result,
    }


def auction_analysis(code: str, *, is_weak=False, quality="ok",
                     reject_reason=None, version="2") -> dict:
    return {
        "version": version,
        "code": code,
        "is_weak": is_weak,
        "strength": 69.9,
        "level": "中",
        "direction": "neutral",
        "rank": 1,
        "dims": {},
        "reject_reason": reject_reason if is_weak else None,
        "unverified": False,
        "config_version": "auction_v2_baseline",
        "config_hash": "hash-a",
        "validation_status": "baseline_unvalidated",
        "semantics": {"semantics_version": "auction_semantics_v1"},
        "data_quality": {"status": quality, "completeness": 1.0,
                         "reason_codes": [], "reasons": []},
        "risk_events": [{"event_id": "high_green@2026-10-30"}] if is_weak else [],
        "raw_factors": {"official_open_gap_pct": 4.0,
                        "auction_turnover_pct": 6.56},
        "evidence": {"last_time": "09:25:00", "official_open_px": 10.4},
        "reference_prices": {"prev_close": 10.0, "official_open": 10.4},
        "windows": {"core": ["09:20:00", "09:25:00"]},
        "actions": [{"kind": "await_confirmation", "requires": "post_open",
                     "text": "若…则观察…"}],
    }


def auction_position(code: str, *, usable=True, **kwargs) -> dict:
    return {"code": code, "name": f"股票{code}", "planned": True,
            "usable": usable, "created_at": "2026-10-30 09:26:01",
            "error": None, "analysis": auction_analysis(code, **kwargs)}


def auction_envelope(*, state="ready", available=True, positions=None,
                     planned_codes=None, config_versions=None,
                     weekend=False, pre_ready=False) -> dict:
    positions = positions if positions is not None else [
        auction_position("600001"),
        auction_position("600002", quality="degraded"),
        auction_position("600003"),
        auction_position("600005"),
        auction_position("600011", is_weak=True, reject_reason="high_green"),
        auction_position("600099"),
    ]
    if planned_codes is not None:
        for position in positions:
            position["planned"] = position["code"] in planned_codes
    versions = config_versions if config_versions is not None else [
        "auction_v2_baseline"]
    return {
        "available": available,
        "state": state,
        "trade_date": T,
        "as_of": f"{T}T09:25:00+08:00",
        "ready_at": f"{T}T09:25:00+08:00",
        "row_count": len(positions),
        "pre_ready_rows_present": pre_ready,
        "corrupt_codes": [],
        "legacy_schema_codes": [],
        "created_at": {"min": "2026-10-30 09:26:01",
                       "max": "2026-10-30 09:26:04"},
        "config": {
            "config_versions": versions,
            "config_hashes": ["hash-a"],
            "semantics_versions": ["auction_semantics_v1"],
            "validation_status": ["baseline_unvalidated"],
            "mixed": len(versions) > 1,
        },
        "cache": {"status": "unknown",
                  "reason": "not_persisted_in_snapshot"},
        "positions": positions,
        "weekend": weekend,
    }


def build(prep=None, auction=None, *, local_trade_days=None) -> dict:
    return build_context(
        prep if prep is not None else prep_envelope(result=_prep_result()),
        auction if auction is not None else auction_envelope(),
        trade_date=T, run_at=RUN_AT, local_trade_days=local_trade_days)


TOP_KEYS = [
    "ok", "context_version", "generated_by", "source", "trade_date", "run_at",
    "info_cutoff", "trading_day_verified", "compatibility", "as_of", "prep",
    "auction", "gates", "market", "anchors", "portfolio", "candidates",
    "data_quality", "disclaimer",
]
PREP_KEYS = [
    "available", "state", "trade_date", "strictness", "proven_gap",
    "age_calendar_days", "created_at", "frozen_at", "frozen_at_reason",
    "schema_version", "schema_supported", "review_available", "review_config",
    "result",
]
AUCTION_KEYS = [
    "available", "state", "trade_date", "as_of", "ready_at", "row_count",
    "pre_ready_rows_present", "corrupt_codes", "legacy_schema_codes",
    "created_at", "config", "cache", "pool", "positions", "unplanned_codes",
]
CANDIDATE_KEYS = [
    "code", "name", "order", "mainline", "role", "prep", "auction",
    "action_ceiling", "execution_constraints",
]
CANDIDATE_AUCTION_KEYS = [
    "present", "status", "strength", "level", "direction", "rank", "is_weak",
    "reject_reason", "data_quality", "risk_event_codes", "evidence",
    "raw_factors", "reference_prices", "windows", "actions", "unknown_reason",
    "created_at",
]


class TestContextShape(unittest.TestCase):
    def test_top_level_keys_and_versions_are_fixed(self):
        context = build()
        self.assertEqual(list(context), TOP_KEYS)
        self.assertEqual(context["context_version"], CONTEXT_VERSION)
        self.assertEqual(context["generated_by"], "premarket")
        self.assertEqual(context["source"], "v2")
        self.assertTrue(context["ok"])
        self.assertEqual(list(context["prep"]), PREP_KEYS)
        self.assertEqual(list(context["auction"]), AUCTION_KEYS)
        self.assertEqual(list(context["candidates"][0]), CANDIDATE_KEYS)
        self.assertEqual(list(context["candidates"][0]["auction"]),
                         CANDIDATE_AUCTION_KEYS)

    def test_timestamps_are_separated(self):
        context = build()
        self.assertEqual(context["run_at"], RUN_AT)
        self.assertEqual(context["info_cutoff"], RUN_AT)
        self.assertFalse(context["trading_day_verified"])
        self.assertEqual(context["as_of"]["prep_frozen_at"],
                         f"{T1}T16:40:00+08:00")
        self.assertEqual(context["as_of"]["auction_as_of"],
                         f"{T}T09:25:00+08:00")
        self.assertEqual(context["prep"]["created_at"], f"{T1} 16:52:11")
        first = context["candidates"][0]
        self.assertEqual(first["auction"]["created_at"],
                         "2026-10-30 09:26:01")
        self.assertEqual(first["auction"]["evidence"]["last_time"], "09:25:00")

    def test_build_is_deterministic_and_does_not_mutate_inputs(self):
        prep = prep_envelope(result=_prep_result())
        auction = auction_envelope()
        before_prep = copy.deepcopy(prep)
        before_auction = copy.deepcopy(auction)
        first = build(prep, auction)
        second = build(prep, auction)
        self.assertEqual(first, second)
        self.assertEqual(prep, before_prep)
        self.assertEqual(auction, before_auction)


class TestStrictness(unittest.TestCase):
    def test_no_local_gap_evidence_stays_null(self):
        context = build(local_trade_days=[T1])
        self.assertEqual(context["prep"]["strictness"],
                         "latest_strictly_before")
        self.assertIsNone(context["prep"]["proven_gap"])
        self.assertEqual(context["prep"]["age_calendar_days"], 1)

    def test_local_dragon_day_between_prep_and_t_proves_the_gap(self):
        prep = prep_envelope(result=_prep_result())
        prep["trade_date"] = "2026-10-28"
        context = build(prep, local_trade_days=["2026-10-28", "2026-10-29"])
        self.assertTrue(context["prep"]["proven_gap"])
        self.assertEqual(context["prep"]["strictness"],
                         "proven_not_t_minus_1")
        self.assertIn("prep_not_strict_t_minus_1",
                      context["data_quality"]["degraded"])
        self.assertEqual(context["prep"]["age_calendar_days"], 2)

    def test_auction_dates_in_mirror_do_not_count_as_gap(self):
        context = build(local_trade_days=[T1, T])
        self.assertIsNone(context["prep"]["proven_gap"])


class TestDegradation(unittest.TestCase):
    def test_missing_prep_degrades_without_fake_market_or_candidates(self):
        prep = prep_envelope(result=None, available=False, state="missing",
                             frozen_at=None)
        context = build(prep)
        self.assertFalse(context["prep"]["available"])
        self.assertEqual(context["prep"]["state"], "missing")
        self.assertIsNone(context["prep"]["result"])
        self.assertEqual(context["candidates"], [])
        self.assertFalse(context["data_quality"]["plan_usable"])
        self.assertEqual(context["data_quality"]["status"], "degraded")
        for reason in ("prep_missing", "auction_pool_unavailable"):
            self.assertIn(reason, context["data_quality"]["degraded"])
        self.assertEqual(context["market"]["state"], None)
        self.assertEqual(context["market"]["unknown_reason"],
                         "prep_unavailable")
        self.assertEqual(context["auction"]["pool"]["available"], False)

    def test_corrupt_prep_is_structured(self):
        prep = prep_envelope(result=None, available=False, state="corrupt",
                             frozen_at=None)
        context = build(prep)
        self.assertEqual(context["prep"]["state"], "corrupt")
        self.assertIn("prep_corrupt", context["data_quality"]["degraded"])

    def test_not_ready_auction_never_becomes_bearish_action(self):
        auction = auction_envelope(state="not_ready", available=False,
                                   pre_ready=True)
        for position in auction["positions"]:
            position["usable"] = False
        context = build(auction=auction)
        self.assertEqual(context["auction"]["state"], "not_ready")
        self.assertFalse(context["auction"]["available"])
        self.assertIn("auction_not_ready_before_0925",
                      context["data_quality"]["degraded"])
        for candidate in context["candidates"]:
            self.assertNotEqual(candidate["action_ceiling"]["status"], "wait")
        by_code = {item["code"]: item for item in context["candidates"]}
        self.assertEqual(by_code["600001"]["action_ceiling"]["status"],
                         "observe_only")
        self.assertEqual(by_code["600001"]["action_ceiling"]["blocked_by"],
                         ["auction_unavailable"])

    def test_config_mixed_is_degraded(self):
        auction = auction_envelope(
            config_versions=["auction_v2_baseline", "auction_v2_alt"])
        context = build(auction=auction)
        self.assertTrue(context["auction"]["config"]["mixed"])
        self.assertIn("auction_config_mixed",
                      context["data_quality"]["degraded"])
        self.assertEqual(context["data_quality"]["status"], "degraded")

    def test_cache_and_account_are_unknown_not_degraded_by_themselves(self):
        context = build()
        self.assertEqual(context["auction"]["cache"],
                         {"status": "unknown",
                          "reason": "not_persisted_in_snapshot"})
        self.assertEqual(context["gates"]["account"]["status"], "unknown")
        self.assertEqual(context["gates"]["account"]["reason"],
                         "not_provided_by_cli")
        self.assertEqual(context["data_quality"]["status"], "ok")
        self.assertEqual(context["data_quality"]["degraded"], [])
        self.assertEqual(context["data_quality"]["notes"], [
            "cache_status_unknown：auction_snapshots 未持久化 from_cache",
            "trading_day_unverified：离线无交易日历",
        ])


class TestAnchors(unittest.TestCase):
    def test_three_anchors_project_only_snapshot_fields(self):
        anchors = build()["anchors"]
        self.assertEqual(anchors["method"], "prep_review_projection_v1")
        self.assertIn("not_full_market", anchors["scope"])
        self.assertEqual(anchors["trend"]["index"], None)
        self.assertEqual(anchors["trend"]["index_missing_reason"],
                         "not_in_snapshot")
        leaders = anchors["short_term"]["mainline_leaders"]
        self.assertIn({"board_name": "机器人", "code": "600001",
                       "role": "leader"}, leaders)
        self.assertIn({"board_name": "机器人", "code": "600002",
                       "role": "vanguard"}, leaders)
        self.assertEqual(anchors["short_term"]["positive"][0]["code"], "600001")
        self.assertEqual(anchors["short_term"]["negative"][0]["code"], "600010")
        self.assertEqual(anchors["sentiment"]["breadth"]["limit_up_cnt"], 62)
        self.assertEqual(anchors["sentiment"]["breadth"]["up_cnt"], 2100)

    def test_role_card_dedupe_prefers_short_term_then_trend(self):
        anchors = build()["anchors"]
        short_codes = {item["code"]
                       for item in anchors["short_term"]["mainline_leaders"]}
        trend_codes = {item["code"] for key in ("mainstay", "capacity")
                       for item in anchors["trend"][key]}
        self.assertFalse(short_codes & trend_codes)
        # 600003 is leader of AI算力 and would otherwise be a trend mainstay card.
        self.assertNotIn("600003", trend_codes)
        self.assertIn("600005", trend_codes)

    def test_missing_review_keeps_anchor_slots_empty(self):
        prep = prep_envelope(result=_prep_result(), review_available=False,
                             review_config=None)
        prep["result"]["review"]["enabled"] = False
        anchors = build(prep)["anchors"]
        self.assertEqual(anchors["short_term"]["mainline_leaders"], [])
        self.assertEqual(anchors["short_term"]["positive"], [])
        self.assertEqual(anchors["trend"]["mainstay"], [])
        self.assertIsNone(anchors["sentiment"]["breadth"]["limit_up_cnt"])


class TestCandidates(unittest.TestCase):
    def test_join_view_keeps_prep_order_and_full_facts(self):
        context = build()
        codes = [item["code"] for item in context["candidates"]]
        self.assertEqual(codes, ["600001", "600002", "600003", "600005",
                                 "600011", "600020"])
        first = context["candidates"][0]
        self.assertEqual(first["order"], 1)
        self.assertEqual(first["prep"]["source_trade_date"], T1)
        self.assertEqual(first["prep"]["eligibility"], "eligible")
        self.assertEqual(first["prep"]["position_pct"], 0.04)
        self.assertEqual(first["prep"]["expectations"]["gap_low"], -0.02)
        self.assertEqual(first["prep"]["facts"]["limit_up_type"], "换手板")
        self.assertEqual(first["prep"]["allowed_actions_by_state"], FROZEN)
        self.assertEqual(first["prep"]["default_allowed_actions"],
                         FROZEN["confirmed"])

    def test_action_ceiling_truth_table_through_context(self):
        by_code = {item["code"]: item for item in build()["candidates"]}
        self.assertEqual(by_code["600001"]["action_ceiling"]["status"], "wait")
        self.assertEqual(by_code["600002"]["action_ceiling"]["status"],
                         "no_new_position")
        self.assertEqual(by_code["600002"]["action_ceiling"]["blocked_by"],
                         ["position_pct_zero"])
        self.assertEqual(by_code["600003"]["action_ceiling"]["status"],
                         "observe_only")
        self.assertEqual(by_code["600003"]["action_ceiling"]["blocked_by"],
                         ["position_pct_null"])
        self.assertEqual(by_code["600011"]["action_ceiling"]["status"],
                         "no_new_position")
        self.assertEqual(by_code["600011"]["action_ceiling"]["blocked_by"],
                         ["auction_veto:high_green"])
        self.assertEqual(by_code["600020"]["action_ceiling"]["status"],
                         "observe_only")
        self.assertEqual(by_code["600020"]["action_ceiling"]["blocked_by"],
                         ["expectations_unavailable"])

    def test_position_zero_is_not_liquidation(self):
        zero = {item["code"]: item for item in build()["candidates"]}["600002"]
        self.assertEqual(zero["action_ceiling"]["position_exit"],
                         "existing_sell_rules_only")
        self.assertNotIn("exit_via_sell_rules",
                         zero["action_ceiling"]["allowed_now"])

    def test_execution_constraints_use_existing_facts_only(self):
        by_code = {item["code"]: item for item in build()["candidates"]}
        one_word = by_code["600011"]["execution_constraints"]
        self.assertEqual(one_word["limit_up_type"], "一字板")
        self.assertTrue(one_word["one_line_board"])
        self.assertIsNone(one_word["slippage_estimate"])
        self.assertTrue(one_word["t_plus_1"])
        regular = by_code["600001"]["execution_constraints"]
        self.assertFalse(regular["one_line_board"])
        self.assertEqual(regular["prev_close"], 10.0)
        self.assertEqual(regular["official_open"], 10.4)
        self.assertEqual(regular["gap_pct"], 4.0)

    def test_unplanned_snapshot_codes_are_observed_not_promoted(self):
        context = build()
        self.assertEqual(context["auction"]["unplanned_codes"], ["600099"])
        self.assertEqual(context["auction"]["pool"]["extra_codes"], ["600099"])
        self.assertEqual(context["auction"]["pool"]["missing_codes"], [])
        codes = [item["code"] for item in context["candidates"]]
        self.assertNotIn("600099", codes)

    def test_actions_are_always_within_the_existing_enum(self):
        from dragon_quant.review_account.premarket.actions import ACTION_VALUES

        for candidate in build()["candidates"]:
            ceiling = candidate["action_ceiling"]
            for action in ceiling["allowed_now"]:
                self.assertIn(action, ACTION_VALUES)
            for actions in ceiling["after_verification"].values():
                for action in actions:
                    self.assertIn(action, ACTION_VALUES)
            self.assertNotIn("buy", ceiling["allowed_now"])
            self.assertNotIn("sell", ceiling["allowed_now"])


class TestGatesAndQuality(unittest.TestCase):
    def test_gates_expose_external_risk_and_unknown_account(self):
        context = build()
        self.assertEqual(context["gates"]["data"]["status"], "ok")
        hard = context["gates"]["hard_risk"]
        self.assertEqual(hard["status"], "external_required")
        self.assertEqual(hard["overnight_external"],
                         "agent_evidence_required")
        risk = {item["code"]: item for item in hard["prep_known"]}
        self.assertEqual(risk["600011"]["risk_level"], "low")
        self.assertEqual(risk["600011"]["risk_announcement"]["level"], "hard")

    def test_data_quality_coverage_and_portfolio(self):
        context = build()
        quality = context["data_quality"]
        self.assertEqual(quality["coverage"]["prep_rows"], 1)
        self.assertEqual(quality["coverage"]["auction_rows"], 6)
        self.assertEqual(quality["coverage"]["expected_pool_codes"], 3)
        self.assertEqual(quality["coverage"]["candidates"], 6)
        self.assertEqual(context["portfolio"]["sum_pct"], 9.0)
        self.assertEqual(context["portfolio"]["same_mainline_counts"],
                         {"AI算力": 1, "机器人": 4})

    def test_compatibility_reports_snapshot_schemas(self):
        context = build()
        compat = context["compatibility"]
        self.assertEqual(compat["premarket"], "premarket_sc_v1")
        self.assertEqual(compat["prep_schema"], "0.8")
        self.assertTrue(compat["prep_supported"])
        self.assertEqual(compat["auction_schemas"], ["2"])
        self.assertTrue(compat["auction_supported"])

    def test_unsupported_prep_schema_is_not_plan_usable(self):
        prep = prep_envelope(result=_prep_result(), state="unsupported_schema",
                             schema_version="0.6", schema_supported=False)
        context = build(prep)
        self.assertIn("prep_schema_unsupported",
                      context["data_quality"]["degraded"])
        self.assertFalse(context["data_quality"]["plan_usable"])
        first = context["candidates"][0]
        self.assertEqual(first["action_ceiling"]["status"], "observe_only")
        self.assertEqual(first["action_ceiling"]["blocked_by"],
                         ["prep_schema_unsupported"])


if __name__ == "__main__":
    unittest.main()
