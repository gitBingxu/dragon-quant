"""政策比较：支配剔除、概率资格、EV/Hurwicz、敏感性与稳定 tie-break。

纪律（方案 §6–§7）：

- **概率合格才比较 EV**：固定事件/政策/期限、成熟训练样本、样本量与校准依据
  完整、场景集合完全匹配，才允许 ``EV = Σ p_s X(a,s)``；否则退回
  ``Hρ = ρ·L + (1−ρ)·U``，并明确它是偏好评分而非期望收益。
- 压力场景只作门禁，不进入 EV/Hρ 的精确计算。
- 先剔除被支配政策；同分时优先**更少新增风险**、再优先更低换手，最后按稳定
  ``plan_id`` 字典序，保证结果可复现。
- 敏感性覆盖 rho、成本、执行延迟；排名反转必须对调用方可见。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Optional

from .config import BSPolicyConfig
from .plans import PlanComparison, PolicyPlan
from .scenario_contracts import ProbabilityInput, ScenarioSet
from .simulate import ScenarioMatrix

CST = timezone(timedelta(hours=8))
PROBABILITY_QUALIFIED = "qualified"
PROBABILITY_UNQUALIFIED = "unqualified"
_PROBABILITY_TOLERANCE = 1e-6


def qualify_probability(probability: Optional[ProbabilityInput], *,
                        config: BSPolicyConfig,
                        scenario_ids: Iterable[str],
                        codes: Iterable[str],
                        decision_time: Optional[str] = None) -> dict:
    """判定概率输入是否有资格参与 EV 比较；不合格时保留原因。"""
    scenario_ids = tuple(scenario_ids)
    codes = {str(code) for code in codes if str(code)}
    reasons: list[str] = []
    if probability is None:
        return {"qualified": False, "status": PROBABILITY_UNQUALIFIED,
                "reasons": ("probability_missing",), "probabilities": {}}
    if not probability.event_id.strip():
        reasons.append("event_id_missing")
    if probability.policy_id != config.policy_id:
        reasons.append("policy_mismatch")
    if probability.profile_version != config.profile_version:
        reasons.append("profile_version_mismatch")
    if probability.horizon_days != config.scenario.horizon_days:
        reasons.append("horizon_mismatch")
    if not probability.matured:
        reasons.append("sample_not_matured")
    if probability.sample_size < config.evaluation.min_probability_samples:
        reasons.append("sample_too_small")
    if not probability.calibration_ref.strip():
        reasons.append("calibration_ref_missing")
    if not probability.calibration_version.strip():
        reasons.append("calibration_version_missing")
    if not probability.event_definition.strip():
        reasons.append("event_definition_missing")
    for field_name in ("training_end", "label_matured_at",
                       "calibration_available_at"):
        value = getattr(probability, field_name)
        if not value:
            reasons.append(f"{field_name}_missing")
            continue
        try:
            moment = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            reasons.append(f"{field_name}_invalid")
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=CST)
        if decision_time is not None:
            decision = datetime.fromisoformat(str(decision_time))
            if decision.tzinfo is None:
                decision = decision.replace(tzinfo=CST)
            if moment > decision:
                reasons.append(f"{field_name}_future")
    applicability = set(probability.applicability)
    if not applicability:
        reasons.append("applicability_missing")
    else:
        covers = bool({"all", "account"} & applicability)
        if not covers and not codes <= applicability:
            reasons.append("applicability_scope_mismatch")
    probabilities = dict(probability.probabilities)
    keys = set(probabilities)
    if keys != set(scenario_ids):
        reasons.append("scenario_set_mismatch")
    total = 0.0
    for scenario_id, value in probabilities.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            reasons.append(f"probability_not_numeric:{scenario_id}")
            continue
        if not math.isfinite(number) or number < 0:
            reasons.append(f"probability_invalid:{scenario_id}")
            continue
        total += number
    if abs(total - 1.0) > _PROBABILITY_TOLERANCE:
        reasons.append("probability_sum_not_one")
    qualified = not reasons
    return {
        "qualified": qualified,
        "status": PROBABILITY_QUALIFIED if qualified else PROBABILITY_UNQUALIFIED,
        "reasons": tuple(dict.fromkeys(reasons)),
        "probabilities": probabilities if qualified else {},
    }


def _values(matrix: ScenarioMatrix, plan_id: str,
            scenario_ids: tuple[str, ...]) -> Optional[list[float]]:
    values = []
    for scenario_id in scenario_ids:
        value = matrix.pnl(plan_id, scenario_id)
        if value is None:
            return None
        values.append(value)
    return values


def _score(values: list[float], config: BSPolicyConfig,
           scenario_ids: tuple[str, ...],
           probabilities: Mapping, *,
           rho: Optional[float] = None
           ) -> tuple[float, float, float, str, Optional[float]]:
    low, high = min(values), max(values)
    if probabilities:
        ev = sum(float(probabilities[scenario_id]) * value
                 for scenario_id, value in zip(scenario_ids, values))
        return ev, low, high, "ev", ev
    score_rho = config.preference.rho if rho is None else rho
    return score_rho * low + (1 - score_rho) * high, low, high, "hurwicz", None


def _dominated_by(plan_id: str, values: Mapping[str, list[float]],
                  plan_ids: list[str]) -> Optional[str]:
    own = values[plan_id]
    for other in sorted(plan_ids):
        if other == plan_id:
            continue
        candidate = values[other]
        if all(b >= a - 1e-9 for a, b in zip(own, candidate)) and any(
                b > a + 1e-9 for a, b in zip(own, candidate)):
            return other
    return None


def build_comparisons(plans: Iterable[PolicyPlan], matrix: ScenarioMatrix, *,
                      scenarios: ScenarioSet,
                      config: BSPolicyConfig,
                      probability_qualified: bool,
                      probabilities: Mapping,
                      codes: Iterable[str] = (),
                      sensitivity: Optional[Mapping] = None
                      ) -> tuple[tuple[PlanComparison, ...], dict]:
    """构造逐政策比较结果；返回 (comparisons, selection_meta)。"""
    plans = tuple(plans)
    comparable = scenarios.comparable()
    scenario_ids = tuple(path.scenario_id for path in comparable)
    pressure_paths = scenarios.pressure()
    values: dict[str, list[float]] = {}
    unscoreable: dict[str, list[str]] = {}
    for plan in plans:
        if not plan.executable:
            continue
        row = _values(matrix, plan.plan_id, scenario_ids)
        if row is None:
            reasons = []
            for scenario_id in scenario_ids:
                outcome = matrix.get(plan.plan_id, scenario_id)
                if outcome is None or outcome.net_pnl is None:
                    reasons.extend(outcome.reasons if outcome else ("missing_outcome",))
            unscoreable[plan.plan_id] = list(dict.fromkeys(reasons))
            continue
        values[plan.plan_id] = row

    scores: dict[str, tuple[float, float, float, str, Optional[float]]] = {}
    for plan_id, row in values.items():
        scores[plan_id] = _score(
            row, config, scenario_ids,
            probabilities if probability_qualified else {})
    plan_ids = sorted(scores)
    dominance = {plan_id: _dominated_by(plan_id, values, plan_ids)
                 for plan_id in plan_ids}

    comparisons: list[PlanComparison] = []
    sensitivity = dict(sensitivity or {})
    for plan in sorted(plans, key=lambda item: item.plan_id):
        if plan.plan_id not in scores:
            if plan.executable:
                comparisons.append(PlanComparison(
                    plan_id=plan.plan_id, scenario_low=None, scenario_high=None,
                    score=None, score_kind="unscored",
                    probability_status=PROBABILITY_UNQUALIFIED,
                    new_risk=plan.risk_amount or 0.0,
                    sensitivity={"reasons": unscoreable.get(plan.plan_id, [])}))
            continue
        score, low, high, kind, ev = scores[plan.plan_id]
        pressure = {}
        for path in pressure_paths:
            value = matrix.pnl(plan.plan_id, path.scenario_id)
            pressure[path.scenario_id] = value
        turnover = 0.0
        for scenario_id in scenario_ids:
            outcome = matrix.get(plan.plan_id, scenario_id)
            if outcome is not None:
                turnover = max(turnover, outcome.traded_notional)
        comparisons.append(PlanComparison(
            plan_id=plan.plan_id,
            scenario_low=low,
            scenario_high=high,
            score=score,
            score_kind=kind,
            ev=ev,
            probability_status=(PROBABILITY_QUALIFIED if probability_qualified
                                else PROBABILITY_UNQUALIFIED),
            dominated_by=dominance.get(plan.plan_id),
            sensitivity={key: value.get(plan.plan_id)
                         for key, value in sensitivity.items()},
            pressure=pressure,
            new_risk=plan.risk_amount or 0.0,
            turnover=turnover,
        ))
    return tuple(comparisons), {
        "scenario_ids": list(scenario_ids),
        "unscoreable": unscoreable,
        "probability_qualified": probability_qualified,
    }


def rank_plans(comparisons: Iterable[PlanComparison]) -> tuple[str, ...]:
    """可复现排序：分数降序 → 新增风险升序 → 换手升序 → plan_id。"""
    usable = [item for item in comparisons
              if item.score is not None and item.dominated_by is None]
    usable.sort(key=lambda item: (-item.score, item.new_risk, item.turnover,
                                  item.plan_id))
    return tuple(item.plan_id for item in usable)


def select_plan(comparisons: Iterable[PlanComparison]) -> Optional[str]:
    ranked = rank_plans(comparisons)
    return ranked[0] if ranked else None


def rank_reversal(base_order: Iterable[str],
                  other_order: Iterable[str]) -> bool:
    return tuple(base_order) != tuple(other_order)
