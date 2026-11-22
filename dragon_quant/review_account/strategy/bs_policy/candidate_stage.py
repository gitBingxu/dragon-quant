"""候选入场/试探/增配与等待方案编排（阶段 2a）。"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from .base import (
    ENTRY_RULE_PREFIX,
    PLAN_AUCTION_CONFIRM,
    PLAN_BREAKOUT,
    PLAN_CONFIRM,
    PLAN_PROBE,
    PLAN_PULLBACK,
    PLAN_WAIT,
)
from .candidates import CandidateView
from .decision import PolicyContext
from .entry_plans import _add_plan, _entry_plan
from .plans import PlanAction, PolicyPlan
from .pressure import _pressure_checks
from .stage import PrepareStage
from .state import _plan_id, _policy_state_for

_ENTRY_RULES = {
    PLAN_BREAKOUT: ("breakout_level", "above"),
    PLAN_PULLBACK: ("pullback_level", "below"),
    PLAN_CONFIRM: ("confirm_level", "above"),
    PLAN_AUCTION_CONFIRM: ("confirm_level", "above"),
}


def _candidate_blockers(stage: PrepareStage,
                        candidate: CandidateView) -> list[str]:
    blockers = list(stage.market_blocks)
    if candidate.pressure_pct is None:
        blockers.append(f"pressure_unknown:{candidate.code}")
    if stage.available_risk <= 0:
        blockers.append("risk_budget_exhausted")
    if stage.available_cash is None:
        blockers.append("cash_unknown")
    return blockers


def _pressure_guard(stage: PrepareStage, plan: PolicyPlan,
                    candidate: CandidateView) -> PolicyPlan:
    if not plan.executable:
        return plan
    checks, reasons = _pressure_checks(
        stage.config, stage.account,
        account_limit=stage.envelope.available_risk,
        proposal={"code": candidate.code, "qty": plan.target_qty or 0,
                  "price": candidate.price,
                  "pressure_pct": candidate.pressure_pct,
                  "theme_ids": tuple(candidate.theme_ids)})
    stage.risk_checks.extend(dict(check) for check in checks)
    if not reasons:
        return plan
    return _replace_blocked(plan, reasons)


def _replace_blocked(plan: PolicyPlan, reasons: Iterable[str]) -> PolicyPlan:
    return replace(plan, executable=False, actions=(),
                   blocked_reasons=tuple(dict.fromkeys(
                       list(plan.blocked_reasons) + list(reasons))))


def _entry_candidates(stage: PrepareStage, candidate: CandidateView) -> list[PolicyPlan]:
    config = stage.config
    blockers = _candidate_blockers(stage, candidate)
    plans: list[PolicyPlan] = []
    for kind in config.supported_entries:
        if kind not in _ENTRY_RULES:
            continue
        trigger_key, mode = _ENTRY_RULES[kind]
        blocked = list(blockers)
        if kind == PLAN_AUCTION_CONFIRM:
            rule_id = f"{ENTRY_RULE_PREFIX}{kind}:{candidate.code}"
            check = candidate.check(rule_id)
            if check is None or not check.usable:
                blocked.append("history_auction_path_missing")
        risk_amount = min(config.sizing.confirmed_risk, stage.available_risk,
                          config.budgets.per_trade)
        plan = _entry_plan(
            kind=kind, candidate=candidate, config=config, step=stage.step,
            risk_amount=risk_amount, trigger_key=trigger_key,
            trigger_mode=mode, cash_limit=stage.available_cash,
            scale=stage.recovery["scale"], blocked=blocked)
        plans.append(_pressure_guard(stage, plan, candidate))
    probe_rule = f"{ENTRY_RULE_PREFIX}{PLAN_PROBE}:{candidate.code}"
    probe_blockers = list(blockers)
    probe_check = candidate.check(probe_rule)
    if probe_check is None or not probe_check.usable:
        probe_blockers.append(f"evidence_not_usable:{probe_rule}")
    probe = _entry_plan(
        kind=PLAN_PROBE, candidate=candidate, config=config, step=stage.step,
        risk_amount=min(config.sizing.probe_risk, stage.available_risk),
        trigger_key="probe_level", trigger_mode="above",
        cash_limit=stage.available_cash, scale=stage.recovery["scale"],
        blocked=probe_blockers,
        notes=("limited probe risk; expansion requires new evidence",))
    plans.append(_pressure_guard(stage, probe, candidate))
    return plans


def build_candidate_stage(context: PolicyContext, stage: PrepareStage,
                          candidates: tuple[CandidateView, ...]
                          ) -> tuple[list[PolicyPlan], list[dict], list[dict]]:
    config = stage.config
    plans: list[PolicyPlan] = []
    gates: list[dict] = []
    transitions: list[dict] = []
    for candidate in candidates:
        entry_plans = _entry_candidates(stage, candidate)
        for plan in entry_plans:
            plans.append(plan)
            gate = "G1_G2_entry" if plan.plan_kind != PLAN_PROBE else "G3_probe"
            gates.append({"plan_id": plan.plan_id, "gate": gate,
                          "passed": plan.executable,
                          "reasons": list(plan.blocked_reasons)})
        add = _add_plan(
            candidate=candidate, config=config,
            step=min(stage.step + config.invalidation.confirm_deadline_days,
                     config.scenario.horizon_days),
            cash_limit=stage.available_cash, scale=stage.recovery["scale"],
            blocked=_candidate_blockers(stage, candidate), context=context)
        state = _policy_state_for(context, candidate.code)
        if state is not None:
            transitions.append({
                "plan_id": add.plan_id, "code": candidate.code,
                "from_adds": state.adds_done,
                "to_adds": state.adds_done + (1 if add.executable else 0),
                "confirmations_used": list(state.confirmations_used),
                "executable": add.executable,
                "reasons": list(add.blocked_reasons)})
        plans.append(add)
        gates.append({"plan_id": add.plan_id, "gate": "G3_add",
                      "passed": add.executable,
                      "reasons": list(add.blocked_reasons)})

    wait = PolicyPlan(
        plan_id=_plan_id(PLAN_WAIT, ""), plan_kind=PLAN_WAIT,
        actions=(PlanAction(action="wait", code="",
                            condition="no eligible opportunity meets all gates",
                            fill_requirement="none"),),
        notes=("waiting is a formal alternative but must carry account protection",),
        executable=True)
    plans.append(wait)
    gates.append({"plan_id": wait.plan_id, "gate": "G1_wait",
                  "passed": True, "reasons": []})
    return plans, gates, transitions
