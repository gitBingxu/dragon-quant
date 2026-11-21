"""W2 共同场景、概率资格、支配/敏感性与历史/压力路径。"""

from __future__ import annotations

import copy
import json

import pytest

from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.review_account.strategy.bs_policy import compare
from dragon_quant.tests._bs_policy_support import (
    _account,
    _candidate,
    _checks,
    _context,
    _plan,
    _position,
    _steps,
)

# ─── 场景与比较：同口径、EV 资格、支配、敏感性与 tie-break ─────────────────


def test_all_plans_share_the_same_scenario_set():
    bundle = bs.evaluate_account_policy(_context(candidates=(_candidate(),)))
    scenario_ids = {path["scenario_id"] for path in bundle.scenarios}
    for plan_id, row in bundle.outcome_matrix.items():
        assert set(row) == scenario_ids, plan_id
        for outcome in row.values():
            # 中间路径与场景上下限都可展示（不是只给一个分数）。
            assert outcome["path_equity"]
            assert {"step", "at", "equity"} <= set(outcome["path_equity"][0])
    assert len(scenario_ids) == 3
    assert all(item.scenario_low is not None and item.scenario_high is not None
               for item in bundle.comparisons if item.score is not None)


def test_probability_qualified_uses_ev_else_hurwicz():
    config = bs.teaching_fixture()
    probabilities = bs.ProbabilityInput(
        event_id="event-1", policy_id=config.policy_id,
        profile_version=config.profile_version,
        horizon_days=config.scenario.horizon_days, matured=True,
        sample_size=100, calibration_ref="cal/2026-10",
        calibration_version="v1", applicability=("all",),
        event_definition="close_above_entry_after_5_sessions",
        training_end="2026-10-01T15:00:00+08:00",
        label_matured_at="2026-10-15T15:00:00+08:00",
        calibration_available_at="2026-10-20T15:00:00+08:00",
        probabilities={"up": 0.4, "down": 0.3, "flat": 0.3})
    qualified = bs.evaluate_account_policy(_context(
        config=config, candidates=(_candidate(),), probability=probabilities))
    assert qualified.audit["probability"]["qualified"] is True
    assert {item.score_kind for item in qualified.comparisons} == {"ev"}

    bad = bs.ProbabilityInput(
        event_id="event-1", policy_id=config.policy_id,
        profile_version=config.profile_version,
        horizon_days=config.scenario.horizon_days, matured=True,
        sample_size=100, calibration_ref="cal/2026-10",
        calibration_version="v1", applicability=("all",),
        event_definition="close_above_entry_after_5_sessions",
        training_end="2026-10-01T15:00:00+08:00",
        label_matured_at="2026-10-15T15:00:00+08:00",
        calibration_available_at="2026-10-20T15:00:00+08:00",
        probabilities={"up": 0.4, "down": 0.3, "flat": 0.4})
    unqualified = bs.evaluate_account_policy(_context(
        config=config, candidates=(_candidate(),), probability=bad))
    assert unqualified.audit["probability"]["qualified"] is False
    assert {item.score_kind for item in unqualified.comparisons} == {"hurwicz"}


def test_dominated_plan_is_removed_from_ranking():
    config = bs.teaching_fixture()
    plans = (
        bs.PolicyPlan(plan_id="a", plan_kind="breakout", code="X",
                      executable=True, risk_amount=10.0),
        bs.PolicyPlan(plan_id="b", plan_kind="breakout", code="X",
                      executable=True, risk_amount=10.0),
    )
    matrix = bs.ScenarioMatrix(outcomes={
        "a": {"s1": bs.PlanOutcome("a", "s1", 1.0),
              "s2": bs.PlanOutcome("a", "s2", 1.0)},
        "b": {"s1": bs.PlanOutcome("b", "s1", 2.0),
              "s2": bs.PlanOutcome("b", "s2", 2.0)},
    })
    scenarios = bs.ScenarioSet(paths=(
        bs.ScenarioPath("s1", "historical_joint",
                        (bs.ScenarioStep(1, "x", {}, {}),)),
        bs.ScenarioPath("s2", "historical_joint",
                        (bs.ScenarioStep(1, "x", {}, {}),)),
    ))
    comparisons, _ = compare.build_comparisons(
        plans, matrix, scenarios=scenarios, config=config,
        probability_qualified=False, probabilities={})
    values = {item.plan_id: item for item in comparisons}
    assert values["a"].dominated_by == "b"
    assert compare.select_plan(comparisons) == "b"


def test_rho_sensitivity_can_reverse_ranking_and_is_visible():
    config = bs.teaching_fixture()
    plans = (
        bs.PolicyPlan(plan_id="steady", plan_kind="breakout", executable=True,
                      risk_amount=10.0),
        bs.PolicyPlan(plan_id="lottery", plan_kind="breakout", executable=True,
                      risk_amount=10.0),
    )
    matrix = bs.ScenarioMatrix(outcomes={
        "steady": {"s1": bs.PlanOutcome("steady", "s1", 1.0),
                   "s2": bs.PlanOutcome("steady", "s2", 1.0)},
        "lottery": {"s1": bs.PlanOutcome("lottery", "s1", -2.0),
                    "s2": bs.PlanOutcome("lottery", "s2", 5.0)},
    })
    scenarios = bs.ScenarioSet(paths=(
        bs.ScenarioPath("s1", "historical_joint",
                        (bs.ScenarioStep(1, "x", {}, {}),)),
        bs.ScenarioPath("s2", "historical_joint",
                        (bs.ScenarioStep(1, "x", {}, {}),)),
    ))
    conservative = compare.build_comparisons(
        plans, matrix, scenarios=scenarios, config=config,
        probability_qualified=False, probabilities={})[0]
    aggressive_config = bs.teaching_fixture(preference={"rho": 0.2})
    aggressive = compare.build_comparisons(
        plans, matrix, scenarios=scenarios, config=aggressive_config,
        probability_qualified=False, probabilities={})[0]
    assert compare.rank_plans(conservative)[0] == "steady"
    assert compare.rank_plans(aggressive)[0] == "lottery"
    assert compare.rank_reversal(compare.rank_plans(conservative),
                                 compare.rank_plans(aggressive)) is True


def test_tie_break_prefers_less_new_risk_then_stable_id():
    config = bs.teaching_fixture()
    plans = (
        bs.PolicyPlan(plan_id="more", plan_kind="breakout", executable=True,
                      risk_amount=20.0),
        bs.PolicyPlan(plan_id="less", plan_kind="breakout", executable=True,
                      risk_amount=10.0),
        bs.PolicyPlan(plan_id="same", plan_kind="breakout", executable=True,
                      risk_amount=10.0),
    )
    matrix = bs.ScenarioMatrix(outcomes={
        plan_id: {"s1": bs.PlanOutcome(plan_id, "s1", 1.0)}
        for plan_id in ("more", "less", "same")
    })
    scenarios = bs.ScenarioSet(paths=(
        bs.ScenarioPath("s1", "historical_joint",
                        (bs.ScenarioStep(1, "x", {}, {}),)),
    ))
    comparisons = compare.build_comparisons(
        plans, matrix, scenarios=scenarios, config=config,
        probability_qualified=False, probabilities={})[0]
    # less 与 same 同分同风险 → 稳定 plan_id 字典序。
    assert compare.rank_plans(comparisons)[:2] == ("less", "same")


def test_bundle_exposes_sensitivity_and_rank_reversal_flags():
    bundle = bs.evaluate_account_policy(_context(candidates=(_candidate(),)))
    assert bundle.comparisons
    for item in bundle.comparisons:
        assert "rank_reversal" in item.sensitivity
        assert set(item.sensitivity["rank_reversal"]) >= {
            "rho_low", "rho_high", "cost_high", "latency_high"}


# ─── 共同场景构造：历史联合路径与压力路径 ──────────────────────────────────


def test_historical_joint_paths_require_mature_windows_and_keep_joint_data():
    config = bs.teaching_fixture()
    windows = [
        {"window_id": "mature", "start": "2026-06-01T15:00:00+08:00",
         "steps": [
             {"at": f"2026-06-0{index}T15:00:00+08:00",
              "prices": {"600001": 10.0 + index, "600002": 20.0 + index},
              "tradable": {"600001": True, "600002": True}}
             for index in range(1, 6)]},
        {"window_id": "future", "start": "2026-11-20T15:00:00+08:00",
         "steps": [
             {"at": f"2026-11-2{index}T15:00:00+08:00",
              "prices": {"600001": 10.0 + index},
              "tradable": {"600001": True}}
             for index in range(1, 6)]},
    ]
    scenarios = bs.historical_joint_paths(
        windows, decision_at="2026-11-18T16:00:00+08:00",
        spec=config.scenario)
    assert [path.scenario_id for path in scenarios.paths] == ["hist:mature"]
    path = scenarios.paths[0]
    # 每个 step 同时包含两只股票（相关性不被拆散）。
    assert all(set(step.prices) == {"600001", "600002"} for step in path.steps)
    assert path.provenance["simulated_relative_steps"] is True
    assert path.provenance["matured_at"] == path.steps[-1].at

    insufficient = bs.historical_joint_paths(
        windows[1:], decision_at="2026-11-18T16:00:00+08:00",
        spec=config.scenario)
    assert insufficient.paths == ()
    assert insufficient.insufficiency


def test_pressure_paths_are_gates_not_probabilities():
    config = bs.teaching_fixture()
    path = bs.pressure_path(
        base_prices={"600001": 10.0}, pressure_pct={"600001": 0.2},
        spec=config.scenario)
    assert path.kind == bs.SCENARIO_PRESSURE
    assert path.steps[-1].prices["600001"] == pytest.approx(8.0)
    with pytest.raises(bs.ScenarioError):
        bs.pressure_path(base_prices={"600001": 10.0}, pressure_pct={},
                         spec=config.scenario)


def test_scenario_validation_rejects_independent_shapes_and_future_windows():
    config = bs.teaching_fixture()
    steps = _steps({"up": {"600001": [10.0] * 5}})[0]["steps"]
    shorter = copy.deepcopy(steps)[:4]
    with pytest.raises(bs.ScenarioError):
        bs.scenario_set_from_records([
            {"scenario_id": "a", "kind": "historical_joint", "steps": steps},
            {"scenario_id": "b", "kind": "historical_joint", "steps": shorter},
        ], spec=config.scenario)


def test_pressure_scenario_is_reported_but_excluded_from_score():
    config = bs.teaching_fixture()
    historical = bs.scenario_set_from_records(
        _steps({"up": {"600001": [10.0, 10.2, 10.4, 10.6, 10.8]}}),
        spec=config.scenario)
    pressure = bs.pressure_path(
        base_prices={"600001": 10.0}, pressure_pct={"600001": 0.3},
        spec=config.scenario,
        step_ats=[step.at for step in historical.paths[0].steps])
    scenarios = bs.ScenarioSet(
        paths=historical.paths + (pressure,))
    bundle = bs.evaluate_account_policy(_context(
        config=config, candidates=(_candidate(),), scenarios=scenarios))
    ids = {path["scenario_id"] for path in bundle.scenarios}
    assert "pressure_common_drawdown" in ids
    for item in bundle.comparisons:
        assert "pressure_common_drawdown" in item.pressure
        # EV/H 只使用可比场景；压力值不参与 score。
        assert item.score is not None


def test_insufficient_scenarios_do_not_invent_new_risk():
    config = bs.teaching_fixture()
    scenarios = bs.ScenarioSet(paths=(), insufficiency=("insufficient_data",))
    bundle = bs.evaluate_account_policy(_context(
        config=config, candidates=(_candidate(),), scenarios=scenarios))
    assert bundle.status == "insufficient_data"
    assert bundle.selected_plan_id == _plan(bundle, bs.PLAN_WAIT).plan_id
    assert bundle.comparisons == ()


def test_pressure_path_is_a_gate_for_new_risk_not_a_score():
    config = bs.teaching_fixture()
    historical = bs.scenario_set_from_records(
        _steps({"up": {"600001": [10.0, 10.2, 10.4, 10.6, 10.8]}}),
        spec=config.scenario)
    pressure = bs.pressure_path(
        base_prices={"600001": 10.0}, pressure_pct={"600001": 0.30},
        spec=config.scenario,
        step_ats=[step.at for step in historical.paths[0].steps],
        trade_dates=[step.trade_date for step in historical.paths[0].steps],
        # 跌停锁死只在入场成交之后出现（step2 仍可成交，step3+ 无法退出）。
        tradable_by_step={index: {"600001": False}
                          for index in range(3, 6)})
    scenarios = bs.ScenarioSet(paths=historical.paths + (pressure,))
    candidate = _candidate(structure={
        "breakout_level": 9.0, "pullback_level": 9.8,
        "confirm_level": 9.0, "probe_level": 9.0})
    bundle = bs.evaluate_account_policy(_context(
        config=config, candidates=(candidate,), scenarios=scenarios))
    ids = {item.plan_id for item in bundle.comparisons}
    breakout = _plan(bundle, bs.PLAN_BREAKOUT)
    assert breakout.plan_id not in ids
    assert not breakout.executable
    assert "stress_path_breach" in breakout.blocked_reasons
    assert any(item["gate"] == "G6_stress_path"
               for item in bundle.gate_trace if not item["passed"])
    # 等待方案仍可比，压力值只出现在 pressure 字段。
    wait_comparison = next(item for item in bundle.comparisons
                           if item.plan_id == _plan(bundle, bs.PLAN_WAIT).plan_id)
    assert "pressure_common_drawdown" in wait_comparison.pressure


def test_envelope_available_risk_caps_new_risk():
    config = bs.teaching_fixture()
    bundle = bs.evaluate_account_policy(_context(
        config=config, candidates=(_candidate(),),
        envelope=bs.RiskEnvelope(available_risk=200.0, available_cash=100_000.0)))
    entry = _plan(bundle, bs.PLAN_BREAKOUT)
    assert not entry.executable
    assert "account_pressure_budget_exceeded" in entry.blocked_reasons
    account_check = next(item for item in bundle.risk_checks
                         if item["check"] == "account_pressure")
    assert account_check["limit"] == 200.0


def test_insufficient_data_never_leaves_new_risk_executable():
    config = bs.teaching_fixture()
    bundle = bs.evaluate_account_policy(_context(
        config=config, candidates=(_candidate(),),
        scenarios=bs.ScenarioSet(paths=(),
                                 insufficiency=("insufficient_data",))))
    assert bundle.status == "insufficient_data"
    assert all(not plan.executable for plan in bundle.plans
               if plan.plan_kind in (bs.PLAN_BREAKOUT, bs.PLAN_PROBE,
                                     bs.PLAN_ADD, bs.PLAN_CONFIRM))
    assert all("insufficient_data_no_validated_scenarios"
               in plan.blocked_reasons for plan in bundle.plans
               if plan.plan_kind in (bs.PLAN_BREAKOUT, bs.PLAN_PROBE))


def test_reinvest_cash_false_cannot_redeploy_sell_proceeds():
    config = bs.teaching_fixture(scenario={
        **bs.teaching_fixture().scenario.to_dict(), "reinvest_cash": False})
    position = _position(code="600001", mark=10.0, trend="flat")
    candidate = _candidate(code="600002", price=10.0, theme_ids=("T2",),
                           structure={"confirm_level": 9.9},
                           evidence_checks=_checks(
                               "entry:breakout:600002",
                               "entry:probe:600002", "confirm:600002"))
    account = _account(cash=0.0, positions=(position,))
    scenarios = bs.scenario_set_from_records(
        _steps({"flat": {"600001": [10.0] * 5, "600002": [10.0] * 5}}),
        spec=config.scenario)
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=account, candidates=(candidate,),
        scenarios=scenarios))
    switch = next(plan for plan in bundle.plans
                  if plan.plan_kind == bs.PLAN_SWITCH)
    outcome = bundle.outcome_matrix[switch.plan_id]["flat"]
    assert any("insufficient_cash" in item for item in outcome["unfilled"])


def test_config_rejects_non_finite_evaluation_parameters():
    payload = bs.teaching_fixture().to_dict()
    payload["evaluation"]["min_probability_samples"] = float("inf")
    with pytest.raises(bs.PolicyConfigError):
        bs.BSPolicyConfig.from_dict(payload)


def test_decision_bundle_is_plain_json_serialisable():
    bundle = bs.evaluate_account_policy(_context(candidates=(_candidate(),)))
    payload = bundle.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    decoded = json.loads(encoded)
    assert decoded["policy_version"] == bundle.policy_version
    assert decoded["outcome_matrix"]
    assert decoded["audit"]["config_hash"] == bundle.config_hash
