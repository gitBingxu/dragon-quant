"""按共同路径**逐事件**模拟完整政策（纯计算，共享执行转移）。

执行不再自带一套买卖逻辑：本模块把保护义务/计划腿/持有退出翻译成
``TransitionCommand``，交给 :class:`scenario_execution.ScenarioRun` 调用
**与 W3 账本相同的** :func:`execution_core.transition_event`（订单生命周期、
下一事件成交、费用/现金、批次 T+1、容量分配、风险预留、触发/依赖/到期一致）。

语义（C1，main 已授权）：

- 在 step N **观察到**触发/失效/退出条件后下单，只在**严格更晚**且有执行支持的
  事件成交；不回到观察当根填单。
- 期末最后事件新下的单不保证成交；残余量保留在挂单/批次里，不倒填。
- 真实交易日与 T+1 用路径声明的日历；未知可卖日不造伪日期。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Optional

from dragon_quant.review_account.execution_core.models import Order
from dragon_quant.review_account.execution_core.transition import (
    TransitionCommand,
)

from .account import AccountView
from .config import ScenarioSpec
from .plans import HoldExit, PlanOutcome, PolicyPlan
from .portfolio import lot_floor, trigger_met
from .scenario_contracts import ScenarioPath, ScenarioStep
from .scenario_execution import (
    ScenarioExecution, ScenarioInputError, ScenarioRun,
)


def _entry_state(plan: PolicyPlan) -> dict:
    return {"stop_price": plan.stop_price, "disaster_price": plan.disaster_price,
            "time_limit_steps": plan.time_limit_steps, "entry_step": None}


def _exit_decision(step: ScenarioStep, code: str, policy_entry: dict,
                   exit_policy: Optional[HoldExit]) -> tuple[bool, str]:
    price = step.price(code)
    stop = policy_entry.get("stop_price")
    disaster = policy_entry.get("disaster_price")
    giveback = exit_policy.giveback_price if exit_policy else None
    if exit_policy is not None:
        stop = stop if stop is not None else exit_policy.stop_price
        disaster = disaster if disaster is not None else exit_policy.disaster_price
    should, reason = False, "pending_exit"
    if price is not None:
        if disaster is not None and price <= disaster:
            should, reason = True, "disaster_exit"
        elif stop is not None and price <= stop:
            should, reason = True, "price_invalidation_exit"
        elif giveback is not None and price <= giveback:
            should, reason = True, "giveback_exit"
    if exit_policy is not None and exit_policy.time_limit_step is not None \
            and step.index >= exit_policy.time_limit_step:
        should, reason = True, "hold_time_exit"
    limit = policy_entry.get("time_limit_steps")
    entry_step = policy_entry.get("entry_step")
    if limit is not None and entry_step is not None \
            and step.index - entry_step >= limit:
        should, reason = True, "entry_time_exit"
    return should, reason


def _leg_risk(plan: PolicyPlan, qty: int) -> float:
    risk = plan.risk_amount
    if risk is None:
        return 0.0
    target = plan.target_qty or 0
    if target > 0 and qty < target:
        return float(risk) * qty / target
    return float(risk)


def _open_sell(run: ScenarioRun, code: str, owner: Mapping,
               batch_ids: tuple = ()):
    """该 code/owner/**批次 scope** 的未终结卖单（避免重复锁量）。"""
    scope = frozenset(batch_ids or ())
    for order in run.open_sell(code):
        if order.side != "sell" or order.is_terminal:
            continue
        if frozenset(order.batch_ids or ()) == scope:
            return order
    return None


def _sell_qty(run: ScenarioRun, step: ScenarioStep, code: str,
              fraction: float, owner: Mapping,
              batch_ids: Iterable[str] = ()) -> int:
    available = run.owned_sellable(code, step.trade_date, owner,
                                   batch_ids=batch_ids or ())
    if fraction >= 1:
        return available
    return lot_floor(available * fraction, run.spec.lot_size) or available


def _obligation_commands(plan: PolicyPlan, run: ScenarioRun, owner: Mapping,
                         obligation_remaining: dict) -> list[TransitionCommand]:
    """保护义务按**剩余量**推进；未成交（不可卖/容量不足）保留待后续事件。

    同 code 已有未终结卖单时不重复下单（``transition_event`` 也会去重），但一旦
    该尝试被拒绝（如当日无可用卖量），下一事件仍会重试，直到可卖量释放或成交。
    """
    commands: list[TransitionCommand] = []
    for obligation in plan.protection:
        remaining = obligation_remaining.get(obligation.obligation_id, 0)
        if remaining <= 0:
            continue
        if _open_sell(run, obligation.code,
                      dict(obligation.owner or owner),
                      tuple(obligation.batch_ids or ())) is not None:
            continue
        commands.append(TransitionCommand(
            action="sell", code=obligation.code, qty=remaining,
            owner=dict(obligation.owner or owner),
            plan_id=f"obligation:{obligation.obligation_id}",
            obligation_id=obligation.obligation_id,
            sequence=int(obligation.sequence or 0),
            batch_ids=tuple(obligation.batch_ids or ())))
    return commands


def _leg_commands(plan: PolicyPlan, run: ScenarioRun, step: ScenarioStep,
                  owner: Mapping,
                  available_rules: set[str]) -> list[TransitionCommand]:
    commands: list[TransitionCommand] = []
    for index, leg in enumerate(plan.legs):
        if leg.step != step.index:
            continue
        if leg.evidence_rule and leg.evidence_rule not in available_rules:
            run.unfilled.append(
                f"{leg.code}:{step.index}:{leg.reason}:evidence_not_available")
            continue
        price = step.price(leg.code)
        if not trigger_met(leg, price):
            run.unfilled.append(
                f"{leg.code}:{step.index}:{leg.reason}:trigger_not_met")
            continue
        if leg.action == "buy":
            qty = lot_floor((plan.target_qty or 0) * leg.qty_fraction,
                            run.spec.lot_size) or (plan.target_qty or 0)
            risk = _leg_risk(plan, qty)
            command = TransitionCommand(
                action="buy", code=leg.code, qty=qty, owner=owner,
                plan_id=plan.plan_id, sequence=index, risk_amount=risk)
            if leg.requires_prior_fill:
                if plan.probe_plan_id:
                    # G3：只依赖**真实 probe 父买腿**（本 run 内已成交订单）或
                    # 账户已持有的同家族真实批次；绝不把任意同 code 卖单当到账。
                    parent = run.filled_buy_for_plan(
                        leg.code, owner,
                        leg.prior_plan_id or plan.probe_plan_id)
                    if parent is not None:
                        command = replace(command, depends_on=(parent.order_id,))
                    elif not run.has_prior_fill(
                            leg.code, owner, prior_plan_id=leg.prior_plan_id,
                            family_id=leg.family_id or plan.probe_plan_id):
                        run.unfilled.append(
                            f"{leg.code}:{step.index}:{leg.reason}:"
                            "prior_fill_missing")
                        continue
                else:
                    # G7 换仓：依赖本计划的真实卖腿（不是任意同 code 卖单）。
                    sell = _existing_sell(plan, run)
                    if sell is None:
                        run.unfilled.append(
                            f"{leg.code}:{step.index}:{leg.reason}:"
                            "prior_fill_missing")
                        continue
                    command = replace(command, depends_on=(sell.order_id,))
            commands.append(command)
        elif leg.action == "sell":
            # 明确批次 scope/数量时按计划的剩余 scope 下单（T+1 不可卖部分
            # 留在挂单 pending，不冒充已退出）；旧腿保持 owner 全量口径。
            if leg.qty is not None:
                qty = int(leg.qty)
                if qty <= 0:
                    run.unfilled.append(
                        f"{leg.code}:{step.index}:{leg.reason}:no_quantity")
                    continue
                batch_ids = tuple(leg.batch_ids or ())
            else:
                qty = _sell_qty(run, step, leg.code, leg.qty_fraction, owner,
                                batch_ids=tuple(leg.batch_ids or ()))
                batch_ids = tuple(leg.batch_ids or ())
                if qty <= 0:
                    run.unfilled.append(
                        f"{leg.code}:{step.index}:{leg.reason}:no_sellable")
                    continue
            if _open_sell(run, leg.code, owner, batch_ids) is not None:
                continue
            commands.append(TransitionCommand(
                action="sell", code=leg.code, qty=qty, owner=owner,
                plan_id=plan.plan_id, sequence=index,
                batch_ids=batch_ids))
        else:
            run.unfilled.append(
                f"{leg.code}:{step.index}:unknown_leg_action:{leg.action}")
    return commands


def _existing_sell(plan: PolicyPlan, run: ScenarioRun) -> Optional[Order]:
    candidates = [order for order in run.state.orders
                  if order.side == "sell" and order.plan_id == plan.plan_id]
    if not candidates:
        return None
    filled = [order for order in candidates if order.filled_qty > 0]
    if filled:
        return filled[0]
    return candidates[0]


def _exit_commands(run: ScenarioRun, step: ScenarioStep, owner: Mapping,
                   entry_policy: dict[str, dict],
                   hold_exits: dict[str, HoldExit],
                   pending: dict[str, list[str]]) -> list[TransitionCommand]:
    commands: list[TransitionCommand] = []
    for code in list(run.positions(step.trade_date)):
        if run.owned_qty(code, owner) <= 0:
            continue
        policy_entry = entry_policy.get(code) or {}
        if policy_entry.get("entry_fill_step") == step.index:
            continue  # 买入当日不反向激活保护
        should, reason = _exit_decision(step, code, policy_entry,
                                        hold_exits.get(code))
        if code in pending:
            should, reason = True, "pending_exit"
        if not should:
            continue
        if _open_sell(run, code, owner) is not None:
            continue
        qty = run.owned_sellable(code, step.trade_date, owner)
        if qty <= 0:
            run.unfilled.append(f"{code}:{step.index}:{reason}:no_sellable")
            continue
        commands.append(TransitionCommand(
            action="sell", code=code, qty=qty, owner=owner,
            plan_id=f"exit:{code}:{reason}", sequence=9000 + step.index))
    return commands


def _apply_fills(plan: PolicyPlan, run: ScenarioRun, step: ScenarioStep,
                 result, policy: dict, entry_policy: dict[str, dict],
                 obligation_remaining: dict,
                 obligation_kind: Mapping[str, str],
                 counted_orders: set) -> None:
    by_id = {order.order_id: order for order in run.state.orders}
    for fill in result.fills:
        order = by_id.get(fill.order_id)
        if order is None:
            continue
        if order.obligation_id:
            remaining = obligation_remaining.get(order.obligation_id, 0)
            obligation_remaining[order.obligation_id] = max(
                0, remaining - fill.qty)
            kind = obligation_kind.get(order.obligation_id, "obligation")
            policy["exits"].append({
                "obligation_id": order.obligation_id, "code": order.code,
                "step": step.index, "kind": kind, "qty": fill.qty,
                "declared_qty": order.qty, "sold_qty": fill.qty,
                "remaining_qty": obligation_remaining[order.obligation_id],
                "trade_date": step.trade_date})
            continue
        if order.plan_id == plan.plan_id and order.side == "buy":
            # 同一买单的多次（部分）成交只计一次增配/确认消费。
            if order.order_id in counted_orders:
                continue
            counted_orders.add(order.order_id)
            leg = _leg_for(plan, order)
            if leg is not None and leg.evidence_rule:
                policy["confirmations_used"].extend(
                    leg.evidence_ids or (leg.evidence_rule,))
            if policy["entered"]:
                policy["adds_done"] += 1
            policy["entered"] = True
            policy["entry_step"] = policy["entry_step"] or step.index
            policy_entry = entry_policy.setdefault(order.code, _entry_state(plan))
            # 首笔真实成交记录为 T+1 保护的起点；补充买入不重置。
            policy_entry.setdefault("entry_step", step.index)
            policy_entry["entry_fill_step"] = step.index
            policy_entry["entry_step"] = policy["entry_step"]
            continue
        if order.plan_id == plan.plan_id and order.side == "sell":
            policy["exits"].append({"code": order.code, "step": step.index,
                                    "reason": order.reason or "exit",
                                    "qty": fill.qty})
            continue
        if order.plan_id.startswith("exit:"):
            parts = order.plan_id.split(":", 2)
            policy["exits"].append({"code": order.code, "step": step.index,
                                    "reason": parts[2] if len(parts) > 2 else "exit",
                                    "qty": fill.qty})


def _leg_for(plan: PolicyPlan, order: Order):
    if 0 <= int(order.sequence) < len(plan.legs):
        return plan.legs[int(order.sequence)]
    return None


def _timeline_entry(run: ScenarioRun, step: ScenarioStep, policy: dict) -> dict:
    return {
        "step": step.index, "at": step.at, "trade_date": step.trade_date,
        "cash": run.state.cash,
        "positions": run.positions(step.trade_date),
        "sellable": run.sellable(step.trade_date),
        "batches": run.batch_state(),
        "reservations": [item.to_row() for item in run.state.reservations
                         if item.status == "reserved"],
        "fills": [fill.to_row() for fill in run.step_fills],
        "policy_state": {
            "entered": policy["entered"], "entry_step": policy["entry_step"],
            "adds_done": policy["adds_done"],
            "confirmations_used": list(policy["confirmations_used"]),
            "exits": list(policy["exits"]),
            "pending_exits": {code: list(items) for code, items
                              in run.pending_exits.items()}},
        "equity": run.equity(),
    }


def _policy_states(run: ScenarioRun, policy: dict,
                   entry_policy: dict[str, dict],
                   last_step: ScenarioStep,
                   tracked_codes: Iterable[str] = ()) -> list[dict]:
    states: list[dict] = []
    seen: set[str] = set()
    for code, qty in run.positions(last_step.trade_date).items():
        if qty <= 0:
            continue
        seen.add(code)
        states.append({
            "code": code, "qty": qty,
            "sellable": run.sellable(last_step.trade_date).get(code, 0),
            "pending_exit": code in run.pending_exits,
            "pending_reasons": list(run.pending_exits.get(code, [])),
            "entry_step": entry_policy.get(code, {}).get("entry_step"),
            "status": "pending_exit" if code in run.pending_exits
            else "held"})
    for code in sorted(set(entry_policy) | set(tracked_codes)):
        if code in seen:
            continue
        if run.state.position_qty(code) > 0:
            continue
        states.append({"code": code, "qty": 0, "sellable": 0,
                       "pending_exit": False, "pending_reasons": [],
                       "entry_step": entry_policy.get(code, {}).get("entry_step"),
                       "status": "exited"})
    return states


def simulate_plan(plan: PolicyPlan, path: ScenarioPath, *,
                  account: AccountView, spec: ScenarioSpec,
                  initial_evidence: Iterable[str] = (),
                  execution: Optional[ScenarioExecution] = None,
                  owner: Optional[Mapping] = None) -> PlanOutcome:
    start_equity = account.marked_equity()
    if start_equity is None or account.cash is None:
        return PlanOutcome(plan_id=plan.plan_id, scenario_id=path.scenario_id,
                           net_pnl=None, reasons=("account_mark_missing",))
    if owner is None:
        owner = dict(getattr(plan, "owner", {}) or {})
    if not owner:
        owner = next((dict(position.strategy_ref)
                      for position in account.positions
                      if position.strategy_ref), {})
    owner = dict(owner or {})
    calendar = tuple((execution.calendar if execution else ())
                     or path.trade_dates())
    if account.positions and not calendar:
        return PlanOutcome(plan_id=plan.plan_id, scenario_id=path.scenario_id,
                           net_pnl=None,
                           reasons=("scenario_calendar_missing",))
    try:
        run = ScenarioRun.build(account, path, spec, execution=execution)
    except ScenarioInputError as exc:
        return PlanOutcome(plan_id=plan.plan_id, scenario_id=path.scenario_id,
                           net_pnl=None, reasons=(exc.reason, exc.code))
    initial_held = set(run.positions(path.steps[0].trade_date)) \
        if path.steps else set()
    available_rules = set(initial_evidence)
    timeline: list[dict] = []
    policy = {"entered": plan.initial_entered, "entry_step": None,
              "adds_done": plan.initial_adds,
              "confirmations_used": list(plan.initial_confirmations),
              "exits": []}
    entry_policy: dict[str, dict] = {}
    hold_exits = {item.code: item for item in plan.hold_exits}
    obligation_remaining = {item.obligation_id: max(0, item.qty)
                            for item in plan.protection}
    obligation_kind = {item.obligation_id: item.kind
                       for item in plan.protection}
    counted_orders: set[str] = set()
    steps = list(path.steps)
    for step in steps:
        # 与 AccountEngine 同序：本事件观察（撮合前状态）→ 命令/撤单 → 撮合
        # 既有挂单；本事件新建的订单留到后续事件成交。
        available_rules.update(step.evidence_available)
        commands = _obligation_commands(plan, run, owner,
                                        obligation_remaining)
        commands.extend(_leg_commands(plan, run, step, owner, available_rules))
        commands.extend(_exit_commands(run, step, owner, entry_policy,
                                       hold_exits, run.pending_exits))
        result = run.execute(step, commands)
        _apply_fills(plan, run, step, result, policy, entry_policy,
                     obligation_remaining, obligation_kind, counted_orders)
        for code in list(run.pending_exits):
            if run.state.position_qty(code) <= 0 and not run.open_sell(code):
                run.pending_exits.pop(code, None)
        for code, qty in run.positions(step.trade_date).items():
            # 只有**退出/保护**类未成交卖单才标记待退出；计划内减仓腿不算。
            open_orders = [order for order in run.open_sell(code)
                           if order.plan_id.startswith(("exit:", "obligation:"))]
            if open_orders:
                reasons = run.pending_exits.setdefault(code, [])
                for order in open_orders:
                    reason = f"{order.plan_id}:unfilled"
                    if reason not in reasons:
                        reasons.append(reason)
        timeline.append(_timeline_entry(run, step, policy))

    final = path.final_step()
    if final is None:
        return PlanOutcome(plan_id=plan.plan_id, scenario_id=path.scenario_id,
                           net_pnl=None, reasons=("scenario_empty",))
    ending = run.state.cash
    for code, qty in run.positions(final.trade_date).items():
        if qty <= 0:
            continue
        truth = final.price(code)
        if truth is None:
            return PlanOutcome(
                plan_id=plan.plan_id, scenario_id=path.scenario_id,
                net_pnl=None, unfilled=tuple(run.unfilled),
                reasons=("final_mark_missing", code),
                timeline=tuple(timeline))
        ending += truth * qty
    states = _policy_states(run, policy, entry_policy, steps[-1],
                            tracked_codes=initial_held)
    held_now = bool(run.positions(final.trade_date))
    if run.pending_exits:
        terminal = "pending_exit"
    elif held_now:
        terminal = "held"
    elif policy["entered"] or policy["exits"] or initial_held:
        terminal = "exited"
    else:
        terminal = "not_entered"
    path_equity = tuple({"step": item["step"], "at": item["at"],
                         "equity": item["equity"]} for item in timeline)
    return PlanOutcome(
        plan_id=plan.plan_id, scenario_id=path.scenario_id,
        net_pnl=ending - start_equity, ending_equity=ending,
        execution_cost=run.execution_cost,
        traded_notional=run.traded_notional,
        unfilled=tuple(run.unfilled), path_equity=path_equity,
        timeline=tuple(timeline), policy_states=tuple(states),
        terminal_status=terminal)


@dataclass(frozen=True)
class ScenarioMatrix:
    """plan_id → scenario_id → PlanOutcome（缺失代码/路径时显式标注）。"""

    outcomes: dict
    missing_codes: tuple[str, ...] = ()

    def get(self, plan_id: str, scenario_id: str) -> Optional[PlanOutcome]:
        return (self.outcomes.get(plan_id) or {}).get(scenario_id)

    def pnl(self, plan_id: str, scenario_id: str) -> Optional[float]:
        outcome = self.get(plan_id, scenario_id)
        return outcome.net_pnl if outcome else None

    def to_dict(self) -> dict:
        return {plan_id: {scenario_id: outcome.to_dict()
                          for scenario_id, outcome in scenarios.items()}
                for plan_id, scenarios in self.outcomes.items()}


def simulate_all(plans, paths, *, account: AccountView, spec: ScenarioSpec,
                 initial_evidence: Iterable[str] = (),
                 execution: Optional[ScenarioExecution] = None) -> ScenarioMatrix:
    outcomes: dict[str, dict[str, PlanOutcome]] = {}
    missing: set[str] = set()
    held = {position.code for position in account.positions
            if position.qty > 0}
    for plan in plans:
        plan_codes = set(held)
        if plan.target_qty:
            plan_codes.add(plan.code)
        plan_codes.update(leg.code for leg in plan.legs)
        plan_codes.update(item.code for item in plan.protection)
        row: dict[str, PlanOutcome] = {}
        for path in paths:
            final = path.final_step()
            lacking = sorted(code for code in plan_codes
                             if final is None or final.price(code) is None)
            if lacking:
                missing.update(lacking)
                row[path.scenario_id] = PlanOutcome(
                    plan_id=plan.plan_id, scenario_id=path.scenario_id,
                    net_pnl=None, reasons=("scenario_missing_code", *lacking))
                continue
            row[path.scenario_id] = simulate_plan(
                plan, path, account=account, spec=spec,
                initial_evidence=initial_evidence, execution=execution)
        outcomes[plan.plan_id] = row
    return ScenarioMatrix(outcomes=outcomes,
                          missing_codes=tuple(sorted(missing)))
