"""决策包组装：动作限制、下一观察、审计与稳定输出（阶段 4）。"""

from __future__ import annotations

from dragon_quant.review_account.evidence import source_status

from .base import BS_DECISION_SCHEMA_VERSION
from .config import BS_POLICY_VERSION
from .decision import DecisionBundle, PolicyContext
from .output import _next_observation, _rejected_plans
from .stage import PrepareStage, ScenarioStage


def build_bundle(context: PolicyContext, stage: PrepareStage,
                 result: ScenarioStage, transitions: list[dict]
                 ) -> DecisionBundle:
    config = stage.config
    plans = result.plans
    reasons = list(result.reasons)
    reasons.extend(dict(item) for item in stage.owner_restrictions)
    selected = next((plan for plan in plans
                     if plan.plan_id == result.selected_id), None)
    selected_actions = selected.actions if selected else ()
    action_limits = {
        "max_new_risk": max(0.0, min(config.budgets.account,
                                     stage.available_risk)
                            * stage.recovery["scale"]),
        "max_increase_actions": config.sizing.max_adds,
        "allowed_actions": ["wait", "hold", "reduce", "exit", "switch",
                            "enter", "probe", "add"],
        "protection_obligations": [item.to_dict()
                                   for item in stage.obligations],
        "owner_restrictions": [dict(item) for item in stage.owner_restrictions],
        "new_risk_restricted": bool(stage.market_blocks or reasons
                                    or stage.owner_restrictions),
        "restriction_reasons": list(dict.fromkeys(
            stage.market_blocks + [reason["reason"] for reason in reasons]
            + [str(item.get("reason", "")) for item in stage.owner_restrictions
               if item.get("reason")])),
        "recovery_stage": stage.recovery["stage"],
        "recovery_scale": stage.recovery["scale"],
    }
    audit = {
        "decision_schema_version": BS_DECISION_SCHEMA_VERSION,
        "policy_version": BS_POLICY_VERSION,
        "config_hash": config.content_hash,
        "config_fixture": config.fixture,
        "current_valuation_known": context.current_valuation_known,
        "snapshot_id": context.snapshot.snapshot_id if context.snapshot else None,
        "snapshot_schema": (context.snapshot.schema_version
                            if context.snapshot else None),
        "scenario_basis": result.scenario_basis,
        "probability": {key: value for key, value in result.probability.items()
                        if key != "probabilities"},
        "selection_meta": result.selection_meta,
        "recovery": stage.recovery,
        "giveback": stage.giveback,
        "protection_obligations": [item.to_dict()
                                   for item in stage.obligations],
        "policy_state_transitions": transitions,
        "source_status": (source_status(context.snapshot)
                          if context.snapshot else None),
    }
    if config.fixture:
        reasons.append({"reason": "teaching_fixture_parameters",
                        "detail": "固定参数仅用于教学/测试，不构成推荐"})
    return DecisionBundle(
        status=result.status,
        decision_time=context.decision_time,
        policy_version=BS_POLICY_VERSION,
        config_hash=config.content_hash,
        snapshot_id=(context.snapshot.snapshot_id
                     if context.snapshot else None),
        plans=tuple(plans),
        rejected=tuple(_rejected_plans(plans)),
        scenarios=tuple(path.to_dict() for path in
                        (context.scenarios.paths if context.scenarios else ())),
        outcome_matrix=result.outcome_matrix,
        comparisons=result.comparisons,
        selected_plan_id=result.selected_id,
        selected_actions=selected_actions,
        action_limits=action_limits,
        next_observation=_next_observation(selected, config, context,
                                           result.selection_meta),
        risk_checks=tuple(stage.risk_checks),
        gate_trace=tuple(result.gates),
        recovery=stage.recovery,
        used_capabilities=result.used_capabilities,
        missing_capabilities=result.missing_capabilities,
        reasons=tuple(reasons),
        audit=audit,
    )
