"""rho/成本/延迟邻域敏感性与排序反转（纯计算）。"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Mapping, Optional

from .account import AccountView
from .compare import build_comparisons, rank_plans, rank_reversal
from .config import BSPolicyConfig
from .decision import PolicyContext
from .scenario_contracts import ScenarioSet
from .simulate import simulate_all

def _sensitivity(plans, comparable, account: AccountView,
                 config: BSPolicyConfig, context: PolicyContext,
                 probability: Mapping, selected_id: Optional[str],
                 initial_evidence: Iterable[str]) -> dict:
    spec = config.scenario
    plans = tuple(plans)
    scenario_ids = [path.scenario_id for path in comparable]
    base_matrix = simulate_all(plans, comparable, account=account, spec=spec,
                               initial_evidence=initial_evidence)
    base_rank = rank_plans(build_comparisons(
        plans, base_matrix, scenarios=ScenarioSet(paths=tuple(comparable)),
        config=config,
        probability_qualified=probability["qualified"],
        probabilities=probability["probabilities"])[0])
    scores: dict[str, dict] = {}
    reversals: dict[str, bool] = {}

    def _rank_for(matrix, *, config_override=None):
        comps, _ = build_comparisons(
            plans, matrix, scenarios=ScenarioSet(paths=tuple(comparable)),
            config=config_override or config,
            probability_qualified=probability["qualified"],
            probabilities=probability["probabilities"])
        return comps, rank_plans(comps)

    for name, rho in {
        "rho_low": max(0.0, config.preference.rho
                       - config.evaluation.rho_sensitivity),
        "rho_high": min(1.0, config.preference.rho
                        + config.evaluation.rho_sensitivity),
    }.items():
        if probability["qualified"]:
            scores[name] = {}
            reversals[name] = False
            continue
        comps, order = _rank_for(base_matrix, config_override=replace(
            config, preference=replace(config.preference, rho=rho)))
        scores[name] = {item.plan_id: item.score for item in comps}
        reversals[name] = rank_reversal(base_rank, order)

    cost_values: dict[str, dict[str, float]] = {}
    for plan in plans:
        row = {}
        for scenario_id in scenario_ids:
            outcome = base_matrix.get(plan.plan_id, scenario_id)
            if outcome is None or outcome.net_pnl is None:
                row = {}
                break
            row[scenario_id] = (outcome.net_pnl
                                - outcome.execution_cost
                                * config.evaluation.cost_sensitivity)
        cost_values[plan.plan_id] = row
    comps, order = _rank_for_values(plans, cost_values, config, probability,
                                    scenario_ids)
    scores["cost_high"] = {item.plan_id: item.score for item in comps}
    reversals["cost_high"] = rank_reversal(base_rank, order)

    shifted = tuple(replace(plan, legs=tuple(
        replace(leg, step=leg.step + config.evaluation.latency_sensitivity_steps)
        for leg in plan.legs
        if leg.step + config.evaluation.latency_sensitivity_steps
        <= spec.horizon_days)) for plan in plans)
    latency_matrix = simulate_all(shifted, comparable, account=account,
                                  spec=spec,
                                  initial_evidence=initial_evidence)
    comps, order = _rank_for(latency_matrix)
    scores["latency_high"] = {item.plan_id: item.score for item in comps}
    reversals["latency_high"] = rank_reversal(base_rank, order)

    return {"scores": scores, "rank_reversal": reversals,
            "base_rank": list(base_rank), "selected": selected_id}


def _rank_for_values(plans, values: Mapping[str, Mapping[str, float]],
                     config: BSPolicyConfig, probability: Mapping,
                     scenario_ids: list[str]):
    from .plans import PlanOutcome
    from .scenario_contracts import ScenarioPath, ScenarioStep
    from .simulate import ScenarioMatrix

    step = ScenarioStep(index=1, at="sensitivity", prices={}, tradable={})
    outcomes = {}
    for plan in plans:
        row = {}
        payload = values.get(plan.plan_id) or {}
        for scenario_id in scenario_ids:
            pnl = payload.get(scenario_id)
            row[scenario_id] = PlanOutcome(
                plan_id=plan.plan_id, scenario_id=scenario_id, net_pnl=pnl)
        outcomes[plan.plan_id] = row
    matrix = ScenarioMatrix(outcomes=outcomes)
    comps, _ = build_comparisons(
        plans, matrix,
        scenarios=ScenarioSet(paths=tuple(
            ScenarioPath(scenario_id=scenario_id, kind="teaching_fixture",
                         steps=(step,), fixture=True)
            for scenario_id in scenario_ids)),
        config=config,
        probability_qualified=probability["qualified"],
        probabilities=probability["probabilities"])
    return comps, rank_plans(comps)