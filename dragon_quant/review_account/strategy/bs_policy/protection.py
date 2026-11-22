"""B1/B2 账户强制保护义务、持仓退出政策与回吐预算。"""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Optional

from .account import RiskEnvelope

from .account import AccountView
from .config import BSPolicyConfig
from .plans import (
    HoldExit,
    PlanAction,
    PolicyCondition,
    PolicyPlan,
    ProtectionObligation,
)
from .sizing import _disaster_price, _half_qty, _round_price, _stop_price

def _giveback_decision(config: BSPolicyConfig, account: Optional[AccountView],
                       envelope: RiskEnvelope) -> dict:
    if account is None:
        return {"authorized": False, "required": None, "remaining": 0.0,
                "reasons": ["account_state_missing"]}
    remaining = max(0.0, config.budgets.giveback - float(envelope.giveback_used))
    required = 0.0
    reasons: list[str] = []
    for position in account.aggregated_positions():
        if position.qty <= 0:
            continue
        pnl = position.unrealized_pnl()
        if pnl is None:
            reasons.append(f"valuation_missing:{position.code}")
            continue
        if pnl <= 0:
            continue
        if position.pressure_pct is None:
            reasons.append(f"giveback_pressure_unknown:{position.code}")
            required += pnl
            continue
        required += pnl * float(position.pressure_pct)
    authorized = not reasons and required <= remaining + 1e-9
    if required > remaining + 1e-9:
        reasons.append("giveback_budget_exceeded")
    return {"authorized": authorized, "required": required,
            "remaining": remaining, "reasons": reasons}


# ─── B1/B2：强制保护义务与持仓退出政策 ─────────────────────────────────────



def _budget_reduce(position, covered: Mapping, lot: int,
                   since_date: str = ""
                   ) -> tuple[int, tuple[str, ...], bool, list[str]]:
    """预算类减仓（回吐/收缩）的**剩余目标**与未覆盖批次 scope。

    目标按原始买入量的一半（不是每次用剩余量重新减半）；已完成减仓与应用强
    失效义务的覆盖量都可抵扣，但只抵扣其真正覆盖的数量。已明确退出的批次不会
    让同 code 其余批次的收缩义务被整项豁免（O1）。

    ``since_date`` 非空时只统计**收缩触发日当时已在册**的批次（``entry_date <=
    触发日``）：恢复期间新获授权的批次不因处于收缩态被立即再半仓，也不抬高
    原始目标。
    """
    all_batches = tuple(position.batches)
    batches = tuple(
        batch for batch in all_batches
        if not since_date or (batch.entry_date
                              and batch.entry_date <= since_date))
    if not batches:
        # 只剩触发日之后的新批次：旧 scope 的收缩义务已无剩余，不重复半仓。
        return 0, (), False, []
    if covered is None:
        qty = _half_qty(position.qty, lot)
        return qty, (), position.sellable() > 0, (
            [] if position.sellable() > 0 else ["no_sellable_quantity"])
    total_original = sum(int(batch.original_qty or batch.qty) for batch in batches)
    total_remaining = sum(batch.qty for batch in batches)
    reduced = max(0, total_original - total_remaining)
    target = _half_qty(total_original, lot)
    covered_total = 0
    uncovered = []
    for batch in batches:
        covered_qty = min(batch.qty, int((covered or {}).get(batch.batch_id, 0)))
        covered_total += covered_qty
        if batch.qty - covered_qty > 0:
            uncovered.append(batch)
    remaining_target = max(0, target - reduced - covered_total)
    scope = tuple(batch.batch_id for batch in uncovered)
    sellable = sum(batch.sellable_qty for batch in uncovered)
    if remaining_target <= 0 or not scope:
        return 0, (), False, []
    qty = min(remaining_target, sum(batch.qty for batch in uncovered))
    return qty, scope, sellable > 0, (
        [] if sellable > 0 else ["no_sellable_quantity"])


def _budget_reduce_snapshot(position, snapshot: Mapping, covered: Mapping,
                            lot: int
                            ) -> tuple[int, tuple[str, ...], bool, list[str]]:
    """按**触发时点快照**计算收缩剩余目标与 scope（M5）。

    快照保存触发撮合前的批次 ID、目标与逐批剩余量。后续只按真实完成量（快照
    剩余 − 当前剩余；已关闭批次按快照剩余全计）与更强保护覆盖抵扣；不在快照
    内的新批次（含同日恢复买入）不参与旧 episode，也不改变原目标。
    """
    batch_ids = [str(item) for item in (snapshot.get("batch_ids") or ())]
    if not batch_ids:
        return 0, (), False, []
    baseline = {str(key): int(value) for key, value
                in dict(snapshot.get("remaining") or {}).items()}
    target = int(snapshot.get("target_qty") or 0)
    if target <= 0:
        return 0, (), False, []
    current = {batch.batch_id: batch for batch in position.batches}
    reduced = 0
    for batch_id in batch_ids:
        original_remaining = baseline.get(batch_id)
        if original_remaining is None:
            continue
        batch = current.get(batch_id)
        current_qty = int(batch.qty) if batch is not None else 0
        reduced += max(0, original_remaining - current_qty)
    covered_total = 0
    uncovered = []
    for batch_id in batch_ids:
        batch = current.get(batch_id)
        if batch is None:
            continue
        covered_qty = min(int(batch.qty),
                          int((covered or {}).get(batch_id, 0)))
        covered_total += covered_qty
        if int(batch.qty) - covered_qty > 0:
            uncovered.append(batch)
    remaining_target = max(0, target - reduced - covered_total)
    scope = tuple(batch.batch_id for batch in uncovered)
    sellable = sum(batch.sellable_qty for batch in uncovered)
    if remaining_target <= 0 or not scope:
        return 0, (), False, []
    qty = min(remaining_target, sum(batch.qty for batch in uncovered))
    return qty, scope, sellable > 0, (
        [] if sellable > 0 else ["no_sellable_quantity"])


def _protection_obligations(config: BSPolicyConfig, account: Optional[AccountView],
                            giveback: Mapping, recovery: Mapping,
                            step: int, *, include_invalidation: bool = True,
                            owner: Optional[Mapping] = None,
                            covered_by_batch: Optional[Mapping] = None
                            ) -> list[ProtectionObligation]:
    """账户级保护义务。

    ``owner`` 非空时这些义务只作用于该冻结归属（Caller 已把 account 限定到该
    owner 的持仓）；``include_invalidation=False`` 只产出账户预算类义务
    （giveback/contraction），价/灾难/时间失效由逐批 owner 解析器提供，避免把
    同 code 的不同归属先聚合再触发（R6/L4）。``covered_by_batch`` 给出已被强
    失效义务覆盖的批次数量：预算目标按剩余目标扣减，但不整项豁免同 code 的
    未覆盖批次（O1）。
    """
    if account is None:
        return []
    obligations: list[ProtectionObligation] = []
    covered_exit: set[str] = set()
    sequence = 0
    for position in account.aggregated_positions():
        if position.qty <= 0:
            continue
        sellable = position.sellable()
        base_reasons: list[str] = []
        executable = True
        if position.mark is None:
            base_reasons.append("valuation_missing")
            executable = False
        if sellable <= 0:
            base_reasons.append("no_sellable_quantity")
            executable = False
        stop = _stop_price(position.cost_price, config)
        disaster = _disaster_price(position.cost_price, config)
        kind: Optional[str] = None
        condition: Optional[PolicyCondition] = None
        action = "exit"
        batch_ids: tuple[str, ...] = ()
        # 义务数量用总持仓（而非当前可卖量）：T+1/批次释放后仍应继续尝试退出。
        qty = position.qty
        if include_invalidation:
            if disaster is not None and position.mark is not None \
                    and position.mark <= disaster:
                kind = "disaster"
                condition = PolicyCondition(kind="price_below", code=position.code,
                                            value=disaster, unit="cny_share")
            elif stop is not None and position.mark is not None \
                    and position.mark <= stop:
                kind = "price_invalidation"
                condition = PolicyCondition(kind="price_below", code=position.code,
                                            value=stop, unit="cny_share")
            elif position.held_days is not None \
                    and position.held_days >= config.invalidation.time_limit_days:
                kind = "time_invalidation"
                condition = PolicyCondition(
                    kind="time_limit", code=position.code,
                    value=float(config.invalidation.time_limit_days),
                    unit="trading_day")
        if kind is None and not giveback.get("authorized", True):
            pnl = position.unrealized_pnl()
            if pnl is not None and pnl > 0:
                kind = "giveback"
                action = "reduce"
                qty, batch_ids, ok, extra = _budget_reduce(
                    position, covered_by_batch, config.scenario.lot_size)
                executable = executable and ok
                base_reasons.extend(extra)
                condition = PolicyCondition(
                    kind="giveback", code=position.code,
                    value=_round_price(float(giveback.get("required") or 0.0)),
                    unit="cny")
        if kind is None and recovery.get("active") and position.code not in covered_exit:
            # 主动存量收缩：分阶段处理存量暴露，不等同于无条件清仓。目标只按
            # **触发日当时**的批次计算，恢复期新批次不再被立即半仓。
            kind = "contraction"
            action = "reduce"
            snapshot = (recovery.get("contraction_targets")
                        or {}).get(position.code)
            if snapshot:
                qty, batch_ids, ok, extra = _budget_reduce_snapshot(
                    position, snapshot, covered_by_batch,
                    config.scenario.lot_size)
            else:
                # 旧 episode 缺快照：退回日期口径（W5 迁移前兼容），新触发总
                # 会在决策时生成快照。
                qty, batch_ids, ok, extra = _budget_reduce(
                    position, covered_by_batch, config.scenario.lot_size,
                    since_date=str(recovery.get("trigger_trade_date") or ""))
            executable = executable and ok
            base_reasons.extend(extra)
            condition = PolicyCondition(kind="contraction", code=position.code,
                                        detail="staged_risk_reduction")
        if kind is None or qty <= 0:
            continue
        if action == "exit":
            covered_exit.add(position.code)
        obligations.append(ProtectionObligation(
            obligation_id=f"{kind}:{position.code}",
            code=position.code, kind=kind, action=action, qty=qty,
            executable=executable, reasons=tuple(dict.fromkeys(base_reasons)),
            condition=condition, sequence=sequence,
            owner=dict(owner or {}), batch_ids=batch_ids))
        sequence += 1
    return obligations


def _hold_exits(config: BSPolicyConfig, account: Optional[AccountView],
                step: int, giveback: Mapping) -> list[HoldExit]:
    if account is None:
        return []
    positions = account.aggregated_positions()
    total_profit = sum(
        max(0.0, position.unrealized_pnl() or 0.0) for position in positions)
    remaining = float(giveback.get("remaining") or 0.0)
    exits: list[HoldExit] = []
    for position in positions:
        if position.qty <= 0:
            continue
        pnl = position.unrealized_pnl()
        giveback_price = None
        giveback_amount = None
        if pnl is not None and pnl > 0 and total_profit > 0 and position.mark:
            share = remaining * pnl / total_profit
            giveback_amount = share
            if share > 0:
                giveback_price = _round_price(position.mark - share / position.qty)
        held = position.held_days
        if held is None:
            time_limit_step = None
        else:
            time_limit_step = step + max(
                0, config.invalidation.time_limit_days - held)
        exits.append(HoldExit(
            code=position.code,
            stop_price=_stop_price(position.cost_price, config),
            disaster_price=_disaster_price(position.cost_price, config),
            giveback_price=giveback_price,
            time_limit_step=time_limit_step,
            giveback_amount=giveback_amount))
    return exits


_KIND_PRIORITY = {
    "disaster": 0, "price_invalidation": 1, "time_invalidation": 2,
    "frozen_exit": 3, "giveback": 4, "contraction": 5, "restricted": 9,
}


def prioritize_obligations(obligations: list[ProtectionObligation]
                           ) -> list[ProtectionObligation]:
    """稳定排序 + 重编号：保护动作先于新风险/hold，且事件序列可复现。

    责任链优先级保持既有卖出顺序（灾难 → 结构失效 → 时间失效 → 冻结退出 →
    回吐 → 收缩）；同优先级按 code/义务 ID 排序，不依赖输入遍历顺序。
    """
    ordered = sorted(obligations, key=lambda item: (
        _KIND_PRIORITY.get(item.kind, 8), item.code, item.obligation_id))
    return [replace(item, sequence=index)
            for index, item in enumerate(ordered)]


def _inject_protection(plan: PolicyPlan,
                       obligations: list[ProtectionObligation],
                       hold_exits: list[HoldExit]) -> PolicyPlan:
    """所有候选政策继承同一账户保护义务；等待/入场不得绕开。"""
    if not obligations and plan.protection:
        return plan
    protection_actions = tuple(
        PlanAction(
            action="exit" if item.action == "exit" else "reduce",
            code=item.code,
            target_qty=0 if item.action == "exit" else None,
            delta_qty=-item.qty if item.qty else None,
            risk_amount=0.0,
            condition=f"protection:{item.kind}",
            sequence=item.sequence,
            fill_requirement="next_step_tradable; may stay pending",
            condition_ref=item.condition,
            obligation_id=item.obligation_id,
            executable=item.executable,
            pending_reasons=item.reasons,
            owner=item.owner,
            batch_ids=item.batch_ids,
            plan_id=item.plan_id,
        )
        for item in obligations)
    shift = len(protection_actions)
    actions = protection_actions + tuple(
        replace(action, sequence=action.sequence + shift)
        for action in plan.actions)
    exits = tuple(hold_exits) if hold_exits else plan.hold_exits
    return replace(plan, actions=actions, protection=tuple(obligations),
                   hold_exits=exits)
