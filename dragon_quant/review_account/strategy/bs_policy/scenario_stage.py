"""共同场景执行、压力门禁、概率资格与政策比较（阶段 3）。"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Mapping

from .base import PLAN_RECOVERY, PLAN_WAIT
from .compare import (
    build_comparisons,
    qualify_probability,
    select_plan,
)
from .config import BSPolicyConfig
from .decision import PolicyContext
from .output import _capability_trace, _restrict_new_risk
from .plans import PlanComparison, PolicyPlan
from .scenarios import (
    common_scenario_basis,
    require_comparable_scenarios,
    validate_scenarios,
)
from .sensitivity import _sensitivity
from .scenario_execution import execution_from_config
from .simulate import simulate_all
from .stage import PrepareStage, ScenarioStage
from .state import _merged_codes
from .timeutil import to_moment


def _initial_evidence(context: PolicyContext, candidates) -> set[str]:
    evidence: set[str] = set()
    decision = to_moment(context.decision_time)
    for candidate in candidates:
        for check in candidate.evidence_checks:
            if check.usable:
                evidence.add(check.rule_id)
        for confirmation in candidate.confirmations:
            available = to_moment(confirmation.available_at)
            if available is not None and decision is not None \
                    and available <= decision:
                evidence.add(confirmation.rule_id)
    return evidence


def _stress_rejections(matrix, plans, pressure, config: BSPolicyConfig
                       ) -> tuple[dict[str, dict], list[dict]]:
    gates: list[dict] = []
    rejected: dict[str, dict] = {}
    if not pressure:
        return rejected, gates
    baseline = next((plan for plan in plans if plan.plan_kind == PLAN_WAIT), None)
    baseline_pressure = {
        path.scenario_id: (matrix.pnl(baseline.plan_id, path.scenario_id)
                           if baseline else 0.0)
        for path in pressure}
    for plan in plans:
        if (plan.risk_amount or 0.0) <= 0:
            continue
        for path in pressure:
            pnl = matrix.pnl(plan.plan_id, path.scenario_id)
            if pnl is None:
                continue
            increment = pnl - (baseline_pressure.get(path.scenario_id) or 0.0)
            if increment < -config.budgets.per_trade - 1e-9:
                rejected[plan.plan_id] = {
                    "scenario_id": path.scenario_id,
                    "incremental_loss": increment,
                    "limit": config.budgets.per_trade}
                gates.append({"plan_id": plan.plan_id, "gate": "G6_stress_path",
                              "passed": False,
                              "reasons": ["stress_path_breach",
                                          rejected[plan.plan_id]]})
                break
    return rejected, gates


def _apply_stress(plans: list[PolicyPlan], rejected: dict[str, dict]
                  ) -> list[PolicyPlan]:
    if not rejected:
        return plans
    return [replace(plan, executable=False, legs=(),
                    actions=tuple(action for action in plan.actions
                                  if action.obligation_id),
                    blocked_reasons=tuple(dict.fromkeys(
                        list(plan.blocked_reasons) + ["stress_path_breach"])))
            if plan.plan_id in rejected else plan for plan in plans]


def _attach_sensitivity(comparisons, sensitivity: Mapping):
    return tuple(
        replace(item, sensitivity={
            **dict(item.sensitivity),
            **{key: value.get(item.plan_id)
               for key, value in sensitivity["scores"].items()
               if isinstance(value, Mapping)},
            "rank_reversal": sensitivity["rank_reversal"],
        })
        for item in comparisons)


def _run_comparison(context: PolicyContext, config: BSPolicyConfig,
                    stage: PrepareStage, plans: list[PolicyPlan],
                    candidates, gates: list[dict], initial_evidence: set[str]):
    scenarios = context.scenarios
    executable = tuple(plan for plan in plans if plan.executable)
    comparisons: tuple[PlanComparison, ...] = ()
    selected_id = None
    outcome_matrix: dict = {}
    selection_meta: dict = {}
    probability = {"qualified": False, "status": "unqualified",
                   "reasons": ("probability_missing",), "probabilities": {}}
    if stage.account is None or stage.equity is None \
            or stage.available_cash is None:
        return "insufficient_data", plans, comparisons, selected_id, \
            outcome_matrix, probability, selection_meta, gates
    if scenarios is None or not scenarios.paths or scenarios.insufficiency:
        return ("insufficient_data", plans, comparisons,
                _wait_id(plans), outcome_matrix, probability,
                {"selection": "wait_without_scenarios"}, gates)
    comparable = scenarios.comparable()
    pressure = scenarios.pressure()
    if not comparable:
        return ("insufficient_data", plans, comparisons, _wait_id(plans),
                outcome_matrix, probability, {"selection": "pressure_only"},
                gates)
    plans_for_sim = tuple(plan for plan in executable
                          if plan.plan_kind != PLAN_RECOVERY)
    matrix = simulate_all(plans_for_sim, comparable + pressure,
                          account=stage.account, spec=config.scenario,
                          initial_evidence=initial_evidence,
                          execution=execution_from_config(config))
    outcome_matrix = matrix.to_dict()
    rejected, stress_gates = _stress_rejections(
        matrix, plans_for_sim, pressure, config)
    gates = gates + stress_gates
    if rejected:
        plans_for_sim = tuple(plan for plan in plans_for_sim
                              if plan.plan_id not in rejected)
        plans = _apply_stress(plans, rejected)
    probability = qualify_probability(
        context.probability, config=config,
        scenario_ids=[path.scenario_id for path in comparable],
        codes=[code for code in _merged_codes(context)
               if code in {c.code for c in candidates}],
        decision_time=context.decision_time)
    comparisons, selection_meta = build_comparisons(
        plans_for_sim, matrix, scenarios=scenarios, config=config,
        probability_qualified=probability["qualified"],
        probabilities=probability["probabilities"],
        codes=_merged_codes(context))
    selected_id = select_plan(comparisons)
    if selected_id is None:
        selected_id = _wait_id(plans)
        selection_meta["selection"] = "fallback_wait"
    sensitivity = _sensitivity(
        plans_for_sim, comparable, stage.account, config, context,
        probability, selected_id, initial_evidence)
    comparisons = _attach_sensitivity(comparisons, sensitivity)
    status = "ok"
    if stage.reasons:
        status = "partial"
    if matrix.missing_codes:
        stage.reasons.append({"reason": "scenario_missing_code",
                              "codes": list(matrix.missing_codes)})
        if selected_id == _wait_id(plans):
            status = "insufficient_data"
    return (status, plans, comparisons, selected_id, outcome_matrix,
            probability, selection_meta, gates)


def _wait_id(plans: Iterable[PolicyPlan]) -> str:
    return next((plan.plan_id for plan in plans
                 if plan.plan_kind == PLAN_WAIT), "")


def build_scenario_stage(context: PolicyContext, stage: PrepareStage,
                         plans: list[PolicyPlan], gates: list[dict],
                         candidates) -> ScenarioStage:
    config = stage.config
    scenarios = context.scenarios
    if scenarios is not None:
        validate_scenarios(scenarios, config.scenario,
                           decision_time=context.decision_time)
        require_comparable_scenarios(scenarios, config.scenario)
    used_capabilities, missing_capabilities = _capability_trace(candidates)
    scenario_basis = (common_scenario_basis(scenarios) if scenarios is not None
                      else {"scenario_ids": [], "kinds": [],
                            "comparable": [], "pressure": [],
                            "insufficiency": ["scenario_set_missing"]})
    initial_evidence = _initial_evidence(context, candidates)
    (status, plans, comparisons, selected_id, outcome_matrix, probability,
     selection_meta, gates) = _run_comparison(
        context, config, stage, plans, candidates, gates, initial_evidence)
    if status == "insufficient_data":
        plans = list(_restrict_new_risk(plans))
        executable_ids = {plan.plan_id for plan in plans if plan.executable}
        comparisons = tuple(item for item in comparisons
                            if item.plan_id in executable_ids)
        for plan in plans:
            if not plan.executable and (
                    "insufficient_data_no_validated_scenarios"
                    in plan.blocked_reasons):
                gates.append({"plan_id": plan.plan_id,
                              "gate": "data_sufficiency", "passed": False,
                              "reasons": [
                                  "insufficient_data_no_validated_scenarios"]})
        selected_id = _wait_id(plans)
        selection_meta.setdefault("selection", "protection_only")
    return ScenarioStage(
        status=status, plans=plans, comparisons=comparisons,
        selected_id=selected_id, outcome_matrix=outcome_matrix,
        probability=probability, selection_meta=selection_meta,
        reasons=stage.reasons, gates=gates, initial_evidence=initial_evidence,
        scenario_basis=scenario_basis, used_capabilities=used_capabilities,
        missing_capabilities=missing_capabilities)
