"""存量持有/减仓/退出与换旧买新政策构造（G4/G5/G7）。"""

from __future__ import annotations

from typing import Iterable

from .account import PositionView
from .base import (
    ACTION_EXIT,
    ACTION_HOLD,
    ACTION_REDUCE,
    ACTION_SWITCH,
    ENTRY_RULE_PREFIX,
    PLAN_BREAKOUT,
    PLAN_CONFIRM,
    PLAN_FULL_EXIT,
    PLAN_HOLD,
    PLAN_PARTIAL_REDUCE,
    PLAN_SWITCH,
)
from .candidates import CandidateView
from .config import BSPolicyConfig
from .plans import (
    HoldExit,
    PlanAction,
    PlanLeg,
    PolicyCondition,
    PolicyPlan,
    ProtectionObligation,
)
from .state import _plan_id
from .sizing import (
    _cap_by_pressure,
    _disaster_price,
    _half_qty,
    _risk_quantity,
    _stop_price,
    _structure_price,
)

def _position_plans(*, position: PositionView, config: BSPolicyConfig,
                    step: int, recovery: dict, giveback: dict,
                    hold_exits: list[HoldExit],
                    obligations: list[ProtectionObligation]
                    ) -> tuple[list[PolicyPlan], list[dict]]:
    """存量持有/减仓/退出（G4/G5）：**必须保护**与**自主比较**分离。

    - 必须保护（价/时间/灾难/冻结退出、回吐/收缩预算）只按**本持仓完整 owner**
      匹配，并按 batch scope/数量从可用于自主决策的剩余量中抵扣；
    - 自主 hold/partial_reduce/full_exit 只作用于**抵扣后的剩余 scope**，动作与
      legs 携带明确 batch_ids/数量，场景模拟与账本同一解释；剩余量为 0 时这些
      方案不再冒充可自主全退，而是明确只保留必需保护；
    - 其它 owner 同 code 的义务不会阻断本 owner 的合法 hold（R6 身份隔离）。
    """
    from dragon_quant.review_account.execution_core.identity import ref_identity

    spec = config.scenario
    lot = spec.lot_size
    owner = dict(position.strategy_ref or {})
    owner_key = ref_identity(owner)
    own = [item for item in obligations
           if item.kind != "restricted" and int(item.qty or 0) > 0
           and ref_identity(item.owner) == owner_key]
    covered = {batch.batch_id: 0 for batch in position.batches}
    for item in own:
        left = int(item.qty)
        ids = list(item.batch_ids or ()) or [b.batch_id
                                             for b in position.batches]
        for batch in position.batches:
            if left <= 0:
                break
            if batch.batch_id not in ids:
                continue
            free = int(batch.qty) - covered[batch.batch_id]
            take = min(left, max(0, free))
            if take > 0:
                covered[batch.batch_id] += take
                left -= take
    remaining = []
    for batch in position.batches:
        left = int(batch.qty) - covered[batch.batch_id]
        if left <= 0:
            continue
        sellable = min(max(0, int(batch.sellable_qty)
                           - covered[batch.batch_id]), left)
        remaining.append((batch, left, sellable))
    remaining_qty = sum(item[1] for item in remaining)
    remaining_ids = tuple(item[0].batch_id for item in remaining)
    remaining_sellable = sum(item[2] for item in remaining)
    own_exit = [item for item in own if item.action == "exit"]
    has_mandatory = bool(own)

    plans: list[PolicyPlan] = []
    gates: list[dict] = []
    price = position.mark
    cost = position.cost_price
    common: list[str] = []
    if price is None:
        common.append("valuation_missing")
    if cost <= 0:
        common.append("cost_basis_missing")
    stop = _stop_price(cost, config)
    disaster = _disaster_price(cost, config)
    invalidation_hit = bool(price is not None and stop is not None
                            and price <= stop)
    disaster_hit = bool(price is not None and disaster is not None
                        and price <= disaster)
    time_hit = bool(position.held_days is not None and position.held_days
                    >= config.invalidation.time_limit_days)
    legacy_forced = disaster_hit or invalidation_hit or time_hit
    forced = bool(own_exit) or (not has_mandatory and legacy_forced)

    hold_reasons = list(common)
    if position.trend != "up":
        hold_reasons.append("trend_unverified" if position.trend == "unknown"
                            else "trend_invalid")
    if not giveback["authorized"]:
        hold_reasons.extend(giveback["reasons"])
    if remaining_qty <= 0:
        # 必须保护覆盖全部剩余 scope：hold 没有可保留的自主余量；同时保留
        # 既有失效原因命名（价/灾难/时间），不新造第二套口径。
        kinds = {item.kind for item in own_exit}
        if "disaster" in kinds:
            hold_reasons.append("disaster_protection_triggered")
        elif "price_invalidation" in kinds:
            hold_reasons.append("price_invalidation_triggered")
        elif "time_invalidation" in kinds:
            hold_reasons.append("time_invalidation_triggered")
        hold_reasons.append("protection_obligation_requires_exit")
    elif not has_mandatory:
        # 纯核兼容路径（无逐批义务）：聚合失效判定仍作为强制来源。
        if disaster_hit:
            hold_reasons.append("disaster_protection_triggered")
        elif invalidation_hit:
            hold_reasons.append("price_invalidation_triggered")
        elif time_hit:
            hold_reasons.append("time_invalidation_triggered")
        elif forced:
            hold_reasons.append("protection_obligation_requires_exit")
    if int(recovery.get("stage") or 0) and recovery.get("active"):
        hold_reasons.append("contraction_requires_reduction")
    hold_executable = remaining_qty > 0 and not hold_reasons
    hold_conditions = tuple(
        PolicyCondition(kind="price_below", code=position.code, value=value,
                        unit="cny_share")
        for value in (disaster, stop) if value is not None)
    hold = PolicyPlan(
        plan_id=_plan_id(PLAN_HOLD, position.code),
        plan_kind=PLAN_HOLD, code=position.code,
        actions=(PlanAction(action=ACTION_HOLD, code=position.code,
                            target_qty=remaining_qty, delta_qty=0,
                            risk_amount=0.0,
                            condition="trend_valid_and_giveback_within_budget",
                            condition_ref=PolicyCondition(
                                kind="trend", code=position.code,
                                unit="bool", detail=position.trend),
                            owner=owner, batch_ids=remaining_ids),
                  ) if hold_executable else (),
        invalidation=hold_conditions,
        cancel_conditions=(PolicyCondition(kind="trend_invalid",
                                           code=position.code),),
        risk_amount=0.0,
        target_qty=remaining_qty,
        blocked_reasons=tuple(dict.fromkeys(hold_reasons)),
        executable=hold_executable,
        hold_exits=tuple(hold_exits),
        notes=((("remaining scope fully covered by mandatory protections; "
                 "nothing left to hold") if remaining_qty <= 0 else
                "mandatory protections cover triggered batches; remaining "
                "scope may still be held") if has_mandatory else
               "holding keeps existing risk in the account budget",),
    )
    plans.append(hold)
    gates.append({"plan_id": hold.plan_id, "gate": "G5_hold",
                  "passed": hold.executable,
                  "reasons": list(hold.blocked_reasons)})

    reduce_reasons = list(common)
    mandatory_reduce = any(item.executable for item in own
                           if item.action != "exit")
    if remaining_qty <= 0:
        # 剩余量全部被必需保护覆盖：只有存在可执行的**减仓类**义务时，
        # 该方案才以「仅必需减仓」的真实 scope 可执行；否则明确不可执行。
        if not mandatory_reduce:
            if any(item.kind == "disaster" for item in own_exit):
                reduce_reasons.append(
                    "disaster_protection_requires_full_exit")
            else:
                reduce_reasons.append("remaining_scope_covered_by_protection")
    elif remaining_sellable <= 0:
        reduce_reasons.append("no_sellable_quantity")
    elif not has_mandatory and disaster_hit:
        reduce_reasons.append("disaster_protection_requires_full_exit")
    half = _half_qty(remaining_sellable, lot)
    reduce_executable = bool(not reduce_reasons
                             and (remaining_qty > 0 and half > 0
                                  or mandatory_reduce))
    reduce = PolicyPlan(
        plan_id=_plan_id(PLAN_PARTIAL_REDUCE, position.code),
        plan_kind=PLAN_PARTIAL_REDUCE, code=position.code,
        actions=(PlanAction(action=ACTION_REDUCE, code=position.code,
                            target_qty=remaining_qty - half, delta_qty=-half,
                            risk_amount=0.0, sequence=0,
                            condition="partial_exit; remaining sellable scope",
                            fill_requirement="sellable_and_tradable",
                            owner=owner, batch_ids=remaining_ids),
                  ) if reduce_executable and remaining_qty > 0 else (),
        legs=(PlanLeg(step=step, action="sell", code=position.code,
                      qty_fraction=0.5, reason="partial_reduce",
                      batch_ids=remaining_ids, qty=half),)
        if reduce_executable and remaining_qty > 0 else (),
        invalidation=(PolicyCondition(kind="reduce_exposure",
                                      code=position.code),),
        risk_amount=0.0,
        target_qty=(remaining_qty - half) if reduce_executable else remaining_qty,
        blocked_reasons=tuple(dict.fromkeys(reduce_reasons)),
        executable=reduce_executable,
        hold_exits=tuple(hold_exits),
        notes=("risk reduction is allowed even when new risk is restricted; "
               "target is remaining scope after mandatory coverage"
               if remaining_qty > 0 else
               "remaining scope covered by mandatory reduce obligations; "
               "no autonomous reduce target",),
    )
    plans.append(reduce)
    gates.append({"plan_id": reduce.plan_id, "gate": "G8_reduce",
                  "passed": reduce.executable,
                  "reasons": list(reduce.blocked_reasons)})

    exit_reasons = list(common)
    mandatory_exit = any(item.executable for item in own_exit)
    if remaining_qty <= 0:
        # 全退的真实 scope 已由必需逐批退出来表达：可执行时动作只来自这些
        # 必需保护（注入到本方案），不冒充另一条自主全退规则。
        if not mandatory_exit:
            exit_reasons.append("remaining_scope_covered_by_protection")
    elif remaining_sellable <= 0:
        exit_reasons.append("no_sellable_quantity")
    exit_executable = bool(not exit_reasons
                           and (remaining_qty > 0 or mandatory_exit))
    if remaining_qty <= 0:
        exit_note = ("remaining scope fully covered by mandatory per-batch "
                     "exit protections; no autonomous full exit")
    elif forced:
        exit_note = ("mandatory per-batch protections cover triggered batches; "
                     "autonomous full exit applies to remaining owner scope")
    else:
        exit_note = "compare with hold/reduce under common paths"
    exit_plan = PolicyPlan(
        plan_id=_plan_id(PLAN_FULL_EXIT, position.code),
        plan_kind=PLAN_FULL_EXIT, code=position.code,
        actions=(PlanAction(action=ACTION_EXIT, code=position.code,
                            target_qty=0, delta_qty=-remaining_qty,
                            risk_amount=0.0, sequence=0,
                            condition="full_exit; remaining owner scope",
                            fill_requirement="sellable_and_tradable",
                            owner=owner, batch_ids=remaining_ids),
                  ) if exit_executable and remaining_qty > 0 else (),
        legs=(PlanLeg(step=step, action="sell", code=position.code,
                      qty_fraction=1.0,
                      reason="forced_exit" if forced else "full_exit",
                      batch_ids=remaining_ids, qty=remaining_qty),)
        if exit_executable and remaining_qty > 0 else (),
        invalidation=(PolicyCondition(kind="exit_pending",
                                      code=position.code),),
        risk_amount=0.0,
        target_qty=0 if exit_executable else remaining_qty,
        blocked_reasons=tuple(dict.fromkeys(exit_reasons)),
        executable=exit_executable,
        hold_exits=tuple(hold_exits),
        notes=(exit_note,),
    )
    plans.append(exit_plan)
    gates.append({"plan_id": exit_plan.plan_id, "gate": "G4_exit",
                  "passed": exit_plan.executable,
                  "reasons": list(exit_plan.blocked_reasons)})
    return plans, gates


def _switch_plan(*, position: PositionView, candidate: CandidateView,
                 config: BSPolicyConfig, step: int, buy_step: int,
                 recovery: dict, blocked: Iterable[str]) -> PolicyPlan:
    spec = config.scenario
    rule_id = f"{ENTRY_RULE_PREFIX}{PLAN_CONFIRM}:{candidate.code}"
    reasons = list(blocked)
    if not candidate.eligible:
        reasons.append("absolute_qualification_failed")
    if not candidate.tradable:
        reasons.append("tradability_unknown")
    if candidate.price is None:
        reasons.append("price_missing")
    if position.sellable() <= 0:
        reasons.append("no_sellable_quantity")
    check = candidate.check(rule_id)
    if check is None or not check.usable:
        fallback = f"{ENTRY_RULE_PREFIX}{PLAN_BREAKOUT}:{candidate.code}"
        if candidate.usable(fallback):
            rule_id = fallback
            check = candidate.check(fallback)
    if check is None:
        reasons.append(f"evidence_missing:{rule_id}")
    elif not check.usable:
        reasons.append(f"evidence_not_usable:{rule_id}")
    if position.trend == "up":
        reasons.append("incumbent_trend_still_valid")
    risk = max(0.0, config.sizing.confirmed_risk * recovery["scale"])
    # 默认换仓**只由真实卖出净到账资助**：不把同账户其它自由现金并入买腿
    # （不发明用户风险偏好，也不因 state 里现金多就整仓买满）。真实可部署额度
    # 由执行核心按已实现净卖款与子单预留逐事件限制。
    proceeds = position.sellable() * (position.mark or 0.0) * (
        1 - spec.sell_slippage)
    qty = _risk_quantity(risk=risk, price=candidate.price,
                         stop_pct=config.invalidation.price_stop_pct,
                         spec=spec, cash_limit=max(0.0, proceeds))
    qty = _cap_by_pressure(qty, candidate.price, candidate.pressure_pct,
                           config.budgets.per_trade, spec.lot_size)
    if qty <= 0 and not reasons:
        reasons.append("risk_quantity_zero")
    trigger = _structure_price(candidate.structure, "confirm_level")
    executable = not reasons
    plan_id = _plan_id(PLAN_SWITCH, f"{position.code}->{candidate.code}", step,
                       trigger)
    stop = _stop_price(candidate.price, config)
    owner = dict(position.strategy_ref or {})
    incumbent_scope = tuple(batch.batch_id for batch in position.batches)
    legs = ()
    if executable:
        legs = (
            PlanLeg(step=step, action="sell", code=position.code,
                    qty_fraction=1.0, reason="switch_sell_leg",
                    family_id=plan_id),
            PlanLeg(step=buy_step, action="buy", code=candidate.code,
                    qty_fraction=1.0, trigger_price=trigger,
                    trigger_mode="above", requires_prior_fill=True,
                    reason="switch_buy_leg_after_fill",
                    evidence_rule=rule_id,
                    evidence_ids=tuple(check.matched_evidence or ()),
                    family_id=plan_id),
        )
    return PolicyPlan(
        plan_id=plan_id,
        plan_kind=PLAN_SWITCH,
        code=candidate.code,
        actions=(PlanAction(action=ACTION_SWITCH, code=position.code,
                            target_qty=0, delta_qty=-position.sellable(),
                            risk_amount=risk, sequence=0,
                            condition="sell_first; buy only after real fill",
                            fill_requirement="no_atomic_switch_assumption",
                            owner=owner, batch_ids=incumbent_scope,
                            family_id=plan_id),
                  PlanAction(action=ACTION_SWITCH, code=candidate.code,
                             target_qty=qty, delta_qty=qty, risk_amount=risk,
                             sequence=1,
                             condition="proceeds_available_after_sell_fill",
                             fill_requirement="next_step_tradable",
                             owner=owner, family_id=plan_id,
                             pressure_pct=candidate.pressure_pct,
                             invalidate_below=stop),
                  ) if executable else (),
        legs=legs,
        entry_rules=(rule_id,),
        invalidation=(
            PolicyCondition(kind="price_below", code=candidate.code,
                            value=_stop_price(candidate.price, config),
                            unit="cny_share"),
            PolicyCondition(kind="time_limit", code=candidate.code,
                            value=float(config.invalidation.time_limit_days),
                            unit="trading_day")),
        cancel_conditions=(PolicyCondition(kind="sell_fill_missing",
                                           code=position.code),),
        evidence_dependencies=(rule_id,),
        risk_amount=risk if executable else None,
        target_qty=qty,
        blocked_reasons=tuple(dict.fromkeys(reasons)),
        executable=executable,
        stop_price=_stop_price(candidate.price, config),
        disaster_price=_disaster_price(candidate.price, config),
        time_limit_steps=config.invalidation.time_limit_days,
        entry_evidence_ids=tuple(check.matched_evidence or ())
        if check is not None else (),
        entry_version=candidate.entry_version,
        notes=("both-side fees/slippage and unfilled sell are charged",),
    )
