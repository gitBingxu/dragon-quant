"""入场/试探/增配政策构造与独立确认契约（G1–G3/B5）。"""

from __future__ import annotations

from typing import Iterable, Optional

from .base import (
    ACTION_ADD,
    ACTION_ENTER,
    ACTION_PROBE,
    CONFIRM_RULE_PREFIX,
    ENTRY_RULE_PREFIX,
    PLAN_ADD,
    PLAN_PROBE,
)
from .candidates import CandidateView, EvidenceConfirmation
from .config import BSPolicyConfig
from .decision import PolicyContext
from .plans import (
    PlanAction,
    PlanLeg,
    PolicyCondition,
    PolicyPlan,
)
from .sizing import (
    _cap_by_pressure,
    _disaster_price,
    _risk_quantity,
    _structure_price,
    _stop_price,
)
from .scenario_contracts import PositionPolicyState
from .state import _plan_id, _policy_state_for
from .timeutil import to_moment

def _entry_plan(*, kind: str, candidate: CandidateView,
                config: BSPolicyConfig, step: int,
                risk_amount: float, trigger_key: str, trigger_mode: str,
                cash_limit: Optional[float], scale: float,
                blocked: Iterable[str],
                notes: Iterable[str] = ()) -> PolicyPlan:
    spec = config.scenario
    price = candidate.price
    trigger_value = _structure_price(candidate.structure, trigger_key)
    risk = max(0.0, risk_amount * scale)
    qty = _risk_quantity(risk=risk, price=price,
                         stop_pct=config.invalidation.price_stop_pct,
                         spec=spec, cash_limit=cash_limit)
    qty = _cap_by_pressure(qty, price, candidate.pressure_pct,
                           config.budgets.per_trade, spec.lot_size)
    rule_id = f"{ENTRY_RULE_PREFIX}{kind}:{candidate.code}"
    reasons = list(blocked)
    if not candidate.eligible:
        reasons.append("absolute_qualification_failed")
    if not candidate.tradable:
        reasons.append("tradability_unknown")
    if price is None:
        reasons.append("price_missing")
    if qty <= 0 and not reasons:
        reasons.append("risk_quantity_zero")
    check = candidate.check(rule_id)
    if check is None:
        reasons.append(f"evidence_missing:{rule_id}")
    elif not check.usable:
        reasons.append(f"evidence_not_usable:{rule_id}:{check.status}")
    executable = not reasons
    stop = _stop_price(price, config) if price else None
    disaster = _disaster_price(price, config) if price else None
    invalidation = []
    if stop is not None:
        invalidation.append(PolicyCondition(kind="price_below",
                                            code=candidate.code, value=stop,
                                            unit="cny_share"))
    invalidation.append(PolicyCondition(
        kind="time_limit", code=candidate.code,
        value=float(config.invalidation.time_limit_days), unit="trading_day"))
    if disaster is not None:
        invalidation.append(PolicyCondition(kind="price_below",
                                            code=candidate.code, value=disaster,
                                            unit="cny_share"))
    legs = ()
    if executable:
        legs = (PlanLeg(step=step, action="buy", code=candidate.code,
                        qty_fraction=1.0, trigger_price=trigger_value,
                        trigger_mode=trigger_mode, reason=f"{kind}_entry",
                        evidence_rule=rule_id,
                        evidence_ids=tuple(check.matched_evidence or ())),)
    plan_id = _plan_id(kind, candidate.code, step, trigger_value)
    action = PlanAction(
        action=ACTION_PROBE if kind == PLAN_PROBE else ACTION_ENTER,
        code=candidate.code, target_qty=qty, delta_qty=qty, risk_amount=risk,
        sequence=0, condition=f"trigger:{trigger_key}:{trigger_mode}",
        fill_requirement="next_step_tradable; trigger_is_not_a_fill",
        condition_ref=PolicyCondition(kind=f"price_{trigger_mode}",
                                      code=candidate.code, value=trigger_value,
                                      unit="cny_share"),
        evidence_rule=rule_id,
        evidence_ids=tuple(check.matched_evidence or ())
        if check is not None else (),
        family_id=plan_id, pressure_pct=candidate.pressure_pct,
        invalidate_below=stop)
    return PolicyPlan(
        plan_id=plan_id,
        plan_kind=kind,
        code=candidate.code,
        actions=(action,) if executable else (),
        legs=legs,
        entry_rules=(rule_id,),
        data_requirements=(check.capability,) if check is not None else (),
        invalidation=tuple(invalidation),
        cancel_conditions=(
            PolicyCondition(kind="price_below", code=candidate.code,
                            value=stop, unit="cny_share")
            if stop else PolicyCondition(kind="price_invalid",
                                         code=candidate.code),
        ),
        evidence_dependencies=(rule_id,),
        risk_amount=risk if executable else None,
        target_qty=qty,
        blocked_reasons=tuple(dict.fromkeys(reasons)),
        executable=executable,
        notes=tuple(notes),
        stop_price=stop,
        disaster_price=disaster,
        time_limit_steps=config.invalidation.time_limit_days,
        entry_evidence_ids=tuple(check.matched_evidence or ())
        if check is not None else (),
        entry_version=candidate.entry_version,
    )


def _has_fill_proof(state: Optional[PositionPolicyState]) -> bool:
    """主运行链要求真实 probe 成交证明：父买单身份 + 真实量 + 计划 target。"""
    if state is None:
        return False
    return (bool(state.probe_order_id)
            and int(state.probe_filled_qty) > 0
            and state.pending_target_qty is not None
            and int(state.pending_target_qty) > 0)


def _probe_realized_ratio(state: Optional[PositionPolicyState], *,
                          require_proof: bool = False) -> float:
    """真实试探成交量 / 计划试探量。

    主运行链（``require_proof``）缺真实成交证明时返回 0（不授权新 add，也不把
    未知比例默认成 1）；纯核教学 fixture 的旧状态无 target 时按 1.0 兼容（显式
    隔离，不推广到正式路径）。
    """
    if state is None:
        return 0.0 if require_proof else 1.0
    if require_proof and not _has_fill_proof(state):
        return 0.0
    if not state.pending_target_qty:
        return 1.0
    target = int(state.pending_target_qty)
    if target <= 0:
        return 1.0
    return max(0.0, min(1.0, int(state.probe_filled_qty) / target))


def _confirmation_problem(candidate: CandidateView, rule_id: str,
                          context: PolicyContext, state: PositionPolicyState,
                          config: BSPolicyConfig) -> tuple[Optional[EvidenceConfirmation],
                                                           list[str]]:
    confirmation = candidate.confirmation(rule_id)
    reasons: list[str] = []
    decision = to_moment(context.decision_time)
    if confirmation is None:
        return None, [f"confirmation_missing:{rule_id}"]
    available = to_moment(confirmation.available_at)
    if available is None:
        reasons.append("confirmation_time_invalid")
    elif decision is not None and available > decision:
        reasons.append("confirmation_not_available")
    probe_filled = to_moment(state.probe_filled_at)
    if probe_filled is not None and available is not None \
            and available <= probe_filled:
        reasons.append("confirmation_not_after_probe")
    if context.snapshot is not None:
        if not confirmation.snapshot_id:
            reasons.append("confirmation_snapshot_missing")
        elif confirmation.snapshot_id != context.snapshot.snapshot_id:
            reasons.append("confirmation_snapshot_mismatch")
    if probe_filled is not None and decision is not None \
            and probe_filled > decision:
        reasons.append("probe_filled_after_decision")
    probe_ids = set(state.probe_evidence_ids)
    used = set(state.confirmations_used)
    if not probe_ids:
        reasons.append("probe_evidence_missing")
    if set(confirmation.evidence_ids) & (probe_ids | used):
        reasons.append("confirmation_not_independent")
    if not state.version:
        reasons.append("probe_version_missing")
    if state.version and candidate.entry_version \
            and state.version != candidate.entry_version:
        reasons.append("probe_version_mismatch")
    if state.adds_done >= config.sizing.max_adds:
        reasons.append("add_limit_reached")
    return confirmation, reasons


def _add_plan(*, candidate: CandidateView, config: BSPolicyConfig, step: int,
              cash_limit: Optional[float], scale: float,
              blocked: Iterable[str], context: PolicyContext) -> PolicyPlan:
    """G3/B5：已成交试探的后续增配，或仅声明未来确认条件的不可执行计划。"""
    spec = config.scenario
    rule_id = f"{CONFIRM_RULE_PREFIX}{candidate.code}"
    # 完整 owner + code scope：同 code 不同 owner 的真实试探状态不串用。
    state = _policy_state_for(context, candidate.code,
                              owner=context.active_owner)
    reasons = list(blocked)
    if not candidate.eligible:
        reasons.append("absolute_qualification_failed")
    if not candidate.tradable:
        reasons.append("tradability_unknown")
    if candidate.price is None:
        reasons.append("price_missing")
    check = candidate.check(rule_id)
    if check is None:
        reasons.append(f"evidence_missing:{rule_id}")
    elif not check.usable:
        reasons.append(f"evidence_not_usable:{rule_id}:{check.status}")
    if state is None:
        reasons.append("probe_not_filled")
        return PolicyPlan(
            plan_id=_plan_id(PLAN_ADD, candidate.code, step, None),
            plan_kind=PLAN_ADD, code=candidate.code,
            entry_rules=(rule_id,),
            add_evidence_rule=rule_id,
            invalidation=(PolicyCondition(
                kind="time_limit", code=candidate.code,
                value=float(config.invalidation.confirm_deadline_days),
                unit="trading_day"),),
            cancel_conditions=(PolicyCondition(kind="probe_invalidated",
                                               code=candidate.code),),
            evidence_dependencies=(rule_id,),
            blocked_reasons=tuple(dict.fromkeys(reasons)),
            executable=False,
            notes=("future condition only: no filled probe state, so no add now",
                   "a failed probe never triggers averaging down"),
        )
    if context.require_fill_proof and not _has_fill_proof(state):
        # 旧口径/导入状态缺真实父单+成交量+target：只读展示与既有保护可用，
        # 但不新增 add（不补父单、不按 owner+code 宽松绑定别 family）。
        reasons.append("probe_fill_proof_missing")
    if context.account is not None \
            and context.account.total_qty(candidate.code) <= 0:
        reasons.append("probe_position_missing")
    confirmation, confirmation_reasons = _confirmation_problem(
        candidate, rule_id, context, state, config)
    reasons.extend(confirmation_reasons)
    add_risk = max(0.0, config.sizing.add_step_risk * scale)
    qty = _risk_quantity(risk=add_risk, price=candidate.price,
                         stop_pct=config.invalidation.price_stop_pct,
                         spec=spec, cash_limit=cash_limit)
    qty = _cap_by_pressure(qty, candidate.price, candidate.pressure_pct,
                           config.budgets.per_trade, spec.lot_size)
    # partial probe 不能按计划全量产生 add 权限：按**真实已成交占比**封顶加仓量。
    realized = _probe_realized_ratio(state,
                                     require_proof=context.require_fill_proof)
    if realized < 1.0:
        qty = int(qty * realized // spec.lot_size) * spec.lot_size
    if qty <= 0 and not reasons:
        reasons.append("risk_quantity_zero")
    confirm_price = _structure_price(candidate.structure, "confirm_level")
    executable = not reasons
    # G3：add 依赖**真实 probe 父买单**的持久化身份（不是任意同 code 卖单/意图）；
    # 未绑定真实父腿（如手工注入状态）时不给依赖，但真实派生状态必有父腿身份。
    parent_orders = (state.probe_order_id,) if state.probe_order_id else ()
    family_id = state.family_id or state.probe_plan_id
    # 加仓腿冻结价格失效线（低于即撤销未成余量，与「合法余量继续」分开判定）。
    add_stop = _stop_price(candidate.price, config) if candidate.price else None
    legs = ()
    if executable and confirmation is not None:
        legs = (PlanLeg(step=step, action="buy", code=candidate.code,
                        qty_fraction=1.0, trigger_price=confirm_price,
                        trigger_mode="above", reason="confirmed_add",
                        evidence_rule=rule_id,
                        evidence_ids=tuple(confirmation.evidence_ids),
                        requires_prior_fill=bool(parent_orders),
                        prior_plan_id=state.probe_plan_id,
                        family_id=family_id),)
    return PolicyPlan(
        plan_id=_plan_id(PLAN_ADD, candidate.code, step, confirm_price),
        plan_kind=PLAN_ADD,
        code=candidate.code,
        actions=(PlanAction(action=ACTION_ADD, code=candidate.code,
                            target_qty=qty, delta_qty=qty,
                            risk_amount=add_risk, sequence=0,
                            condition=f"confirm_evidence:{rule_id}",
                            fill_requirement="new independent evidence; no averaging down",
                            condition_ref=PolicyCondition(
                                kind="evidence", code=candidate.code,
                                unit="rule_id", detail=rule_id),
                            evidence_rule=rule_id,
                            evidence_ids=tuple(confirmation.evidence_ids),
                            depends_on=parent_orders,
                            family_id=family_id,
                            pressure_pct=candidate.pressure_pct,
                            invalidate_below=add_stop),
                  ) if executable else (),
        legs=legs,
        entry_rules=(rule_id,),
        add_evidence_rule=rule_id,
        invalidation=(PolicyCondition(
            kind="time_limit", code=candidate.code,
            value=float(config.invalidation.confirm_deadline_days),
            unit="trading_day"),),
        cancel_conditions=(PolicyCondition(kind="probe_invalidated",
                                           code=candidate.code),),
        evidence_dependencies=(rule_id,),
        risk_amount=add_risk if executable else None,
        target_qty=qty,
        blocked_reasons=tuple(dict.fromkeys(reasons)),
        executable=executable,
        notes=(f"consumes probe state {state.probe_plan_id}; adds_done="
               f"{state.adds_done}; max_adds={config.sizing.max_adds}",
               "confirmation identity/time/version checked; never reuse probe evidence"),
        probe_plan_id=state.probe_plan_id,
        initial_entered=True,
        initial_adds=state.adds_done,
        initial_confirmations=state.confirmations_used,
        entry_evidence_ids=tuple(confirmation.evidence_ids)
        if confirmation else (),
    )
