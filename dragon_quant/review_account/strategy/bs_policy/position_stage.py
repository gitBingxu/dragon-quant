"""存量保护/退出与换仓方案编排（阶段 2b）。"""

from __future__ import annotations

from .base import PLAN_RECOVERY
from .candidate_stage import _pressure_guard
from .candidates import CandidateView
from .decision import PolicyContext
from .plans import PolicyCondition, PolicyPlan
from .position_plans import _position_plans, _switch_plan
from .stage import PrepareStage
from .state import _plan_id


def build_position_stage(context: PolicyContext, stage: PrepareStage,
                         candidates: tuple[CandidateView, ...]
                         ) -> tuple[list[PolicyPlan], list[dict]]:
    config = stage.config
    account = stage.scoped_account
    plans: list[PolicyPlan] = []
    gates: list[dict] = []
    aggregated = account.aggregated_positions() if account is not None else ()
    for position in aggregated:
        pos_plans, pos_gates = _position_plans(
            position=position, config=config, step=stage.step,
            recovery=stage.recovery, giveback=stage.giveback,
            hold_exits=stage.hold_exits, obligations=stage.obligations)
        plans.extend(pos_plans)
        gates.extend(pos_gates)
        for candidate in candidates:
            if candidate.code == position.code:
                continue
            if not candidate.eligible or not candidate.tradable:
                continue
            if candidate.rank is None:
                continue
            switch = _switch_plan(
                position=position, candidate=candidate, config=config,
                step=min(stage.step + 1, config.scenario.horizon_days),
                buy_step=min(stage.step + 2, config.scenario.horizon_days),
                recovery=stage.recovery, blocked=stage.market_blocks)
            switch = _pressure_guard(stage, switch, candidate)
            plans.append(switch)
            gates.append({"plan_id": switch.plan_id, "gate": "G7_switch",
                          "passed": switch.executable,
                          "reasons": list(switch.blocked_reasons)})
    if stage.recovery["active"]:
        plans.append(_recovery_plan(config, stage.recovery))
    return plans, gates


def _recovery_plan(config, recovery: dict) -> PolicyPlan:
    return PolicyPlan(
        plan_id=_plan_id(PLAN_RECOVERY, ""),
        plan_kind=PLAN_RECOVERY,
        actions=(),
        invalidation=tuple(
            PolicyCondition(kind="evidence", detail=f"condition:{item}")
            for item in config.recovery.conditions),
        notes=(f"stage {recovery['stage']}→{recovery['proposed_stage']}; "
               f"risk scale {recovery['scale']}; missing conditions "
               f"{recovery['conditions_missing']}",
               "no automatic maximum recovery; new risk needs fresh evidence"),
        executable=False,
        blocked_reasons=tuple(
            [f"missing_condition:{item}"
             for item in recovery["conditions_missing"]]
            or ([] if recovery["can_resume"] else ["cooldown_not_elapsed"])),
    )
