"""决策输出辅助：拒绝原因、动作限制、下一观察与观察模式。"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Mapping, Optional

from .base import (
    BS_DECISION_SCHEMA_VERSION,
    PLAN_WAIT,
    _NEW_RISK_KINDS,
)
from .candidates import CandidateView
from .config import BS_POLICY_VERSION, BSPolicyConfig
from .decision import DecisionBundle, PolicyContext
from .plans import PlanAction, PolicyCondition, PolicyPlan
from .state import _plan_id

def _capability_trace(candidates: Iterable[CandidateView]
                      ) -> tuple[tuple[str, ...], tuple[str, ...]]:
    used: set[str] = set()
    missing: set[str] = set()
    for candidate in candidates:
        for check in candidate.evidence_checks:
            if check.usable:
                used.add(check.capability)
            else:
                missing.add(check.capability)
    return tuple(sorted(used)), tuple(sorted(missing - used))


def _restrict_new_risk(plans: Iterable[PolicyPlan]) -> list[PolicyPlan]:
    """数据不足时只允许观察/持有/减仓：新增风险计划一律不可执行。"""
    result: list[PolicyPlan] = []
    for plan in plans:
        if plan.executable and plan.plan_kind in _NEW_RISK_KINDS:
            result.append(replace(
                plan, executable=False, legs=(),
                actions=tuple(action for action in plan.actions
                              if action.obligation_id),
                blocked_reasons=tuple(dict.fromkeys(
                    list(plan.blocked_reasons)
                    + ["insufficient_data_no_validated_scenarios"]))))
        else:
            result.append(plan)
    return result


def _rejected_plans(plans: Iterable[PolicyPlan]) -> list[dict]:
    return [
        {"plan_id": plan.plan_id, "plan_kind": plan.plan_kind,
         "code": plan.code, "reasons": list(plan.blocked_reasons)}
        for plan in plans
        if not plan.executable and plan.blocked_reasons
    ]


def _next_observation(plan: Optional[PolicyPlan], config: BSPolicyConfig,
                      context: PolicyContext,
                      selection_meta: Mapping) -> tuple[dict, ...]:
    items: list[dict] = []
    if plan is not None:
        for condition in plan.invalidation:
            if isinstance(condition, PolicyCondition):
                items.append({"plan_id": plan.plan_id, "kind": "invalidation",
                              "condition": condition.to_dict()})
            else:
                items.append({"plan_id": plan.plan_id, "kind": "invalidation",
                              "watch": condition})
        for condition in plan.cancel_conditions:
            if isinstance(condition, PolicyCondition):
                items.append({"plan_id": plan.plan_id, "kind": "cancel",
                              "condition": condition.to_dict()})
            else:
                items.append({"plan_id": plan.plan_id, "kind": "cancel",
                              "watch": condition})
        for rule in plan.evidence_dependencies:
            items.append({"plan_id": plan.plan_id, "kind": "evidence",
                          "watch": rule})
    for obligation in (plan.protection if plan is not None else ()):
        if not obligation.executable:
            items.append({"plan_id": plan.plan_id, "kind": "pending_protection",
                          "obligation": obligation.to_dict()})
    if plan is None or plan.plan_kind == PLAN_WAIT:
        items.append({"plan_id": plan.plan_id if plan else None,
                      "kind": "wait",
                      "watch": "eligible_candidate_with_usable_evidence"})
    if selection_meta.get("selection"):
        items.append({"kind": "selection", "detail": selection_meta["selection"]})
    items.append({"kind": "evaluation_horizon",
                  "watch": f"time_limit:{config.invalidation.time_limit_days}d"})
    return tuple(items)


def _observation_bundle(context: PolicyContext,
                        config: Optional[BSPolicyConfig],
                        reasons: Iterable[dict], status: str) -> DecisionBundle:
    wait = PolicyPlan(
        plan_id=_plan_id(PLAN_WAIT, ""), plan_kind=PLAN_WAIT,
        actions=(PlanAction(action="wait", code="",
                            condition="observe only; no new risk"),),
        notes=("observation mode: missing policy/account inputs",),
        executable=True,
    )
    # 缺新策略配置只限制新增风险；既有可确定的 owner 保护不丢失。
    obligations = tuple(context.owner_protections or ())
    if obligations:
        from .protection import _inject_protection, prioritize_obligations

        wait = _inject_protection(wait, prioritize_obligations(
            list(obligations)), [])
    return DecisionBundle(
        status=status,
        decision_time=context.decision_time,
        policy_version=BS_POLICY_VERSION,
        config_hash=config.content_hash if config else None,
        snapshot_id=(context.snapshot.snapshot_id
                     if context.snapshot else None),
        plans=(wait,),
        selected_plan_id=wait.plan_id,
        selected_actions=wait.actions,
        action_limits={"max_new_risk": 0.0,
                       "new_risk_restricted": True,
                       "allowed_actions": ["wait", "observe"],
                       "protection_obligations": [
                           item.to_dict() for item in wait.protection],
                       "owner_restrictions": [
                           dict(item) for item in
                           (context.owner_restrictions or ())]},
        next_observation=({"kind": "missing_input",
                           "watch": "risk_preference_and_budget_config"},
                          {"kind": "missing_input",
                           "watch": "account_state_snapshot"}),
        gate_trace=({"plan_id": wait.plan_id, "gate": "config",
                     "passed": False,
                     "reasons": [reason["reason"] for reason in reasons]},),
        reasons=tuple(reasons),
        audit={"policy_version": BS_POLICY_VERSION,
               "config_present": config is not None,
               "decision_schema_version": BS_DECISION_SCHEMA_VERSION},
    )
