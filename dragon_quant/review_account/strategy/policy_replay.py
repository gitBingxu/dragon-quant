"""公共账户政策回放（产品/研究可调用，纯内存，不访问 DB/网络）。

输入一份**完整期初账户**、冻结配置/归属与受支持的事件序列，输出逐事件
DecisionRecord/成交/残余（批次/挂单/预留/资金 credit）与状态时间线（现金/权益/
恢复阶段）。它复用 :class:`AccountEventProcessor` —— 与 :class:`AccountEngine`
**同一**政策决策、撤单、恢复与成交 Reducer，只是把 CAS 提交换成内存状态推进。

纪律：

- 只在给定输入上计算，不自动补数据、不读真实库、不读时钟；
- 无决定事件不等于撤全部 pending；已承诺订单完成剩余不重新授权/消费确认；
- 期末最后事件新下的单不保证成交，未成交留在残余里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from dragon_quant.review_account.execution_core.matching import (
    ExecutionCostModel,
)
from dragon_quant.review_account.execution_core.models import (
    LedgerError, LedgerState,
)
from dragon_quant.review_account.execution_core.risk import RiskBook, RiskCaps
from dragon_quant.review_account.strategy.account_event import (
    AccountEventProcessor,
)
from dragon_quant.review_account.strategy.account_runtime import (
    AccountPolicyRuntime,
)
from dragon_quant.review_account.strategy.bs_policy import (
    AccountView, BSPolicyConfig,
)
from dragon_quant.review_account.strategy.bs_policy.scenario_contracts import (
    RecoveryState,
)
from dragon_quant.review_account.strategy.bs_policy.scenario_execution import (
    seed_ledger_state,
)


@dataclass(frozen=True)
class ReplayEvent:
    """一个受支持的真实事件（与账户引擎的行情行结构一致）。"""

    timestamp: int
    trade_date: str
    rows: Mapping = field(default_factory=dict)
    candidates: tuple = ()
    decision_kwargs: Mapping = field(default_factory=dict)
    decide: bool = False
    capacity_override: Optional[Mapping] = None
    evidence_available: tuple = ()
    unavailable: Mapping = field(default_factory=dict)
    last_closes: Mapping = field(default_factory=dict)
    phase: str = "bar"


@dataclass(frozen=True)
class ReplayInput:
    """一次回放的完整冻结输入。"""

    ledger_id: str
    account: AccountView
    config: BSPolicyConfig
    strategy_ref: Mapping
    events: tuple
    calendar: tuple = ()
    order_ttl_ms: Optional[int] = 86_400_000
    risk_caps: Optional[RiskCaps] = None
    allow_unknown_risk: bool = False
    max_quote_age_ms: Optional[int] = None
    cost_model: ExecutionCostModel = field(default_factory=ExecutionCostModel)
    #: 只读账户检查点（恢复锁存/政策状态/未成订单/预留/标记与逐批事实/资金归属）。
    #: 由调用方从已验证账户文件解析；非空即真正进入工作状态，不接受后丢弃。
    recovery: Optional[Mapping] = None
    policy_states: tuple = ()
    pending_orders: tuple = ()
    reservations: tuple = ()
    mark_facts: Optional[Mapping] = None
    batch_facts: Optional[Mapping] = None
    fund_credits: Optional[Mapping] = None


@dataclass(frozen=True)
class ReplayResult:
    events: tuple[dict, ...]
    final_state: dict

    def to_dict(self) -> dict:
        return {"events": [dict(item) for item in self.events],
                "final_state": dict(self.final_state)}


def _state_snapshot(state: LedgerState, trade_date: str) -> dict:
    batches = []
    for batch in sorted(state.batches, key=lambda item: item.batch_id):
        if batch.remaining <= 0:
            continue
        mark = state.marks.get(batch.code)
        batches.append({
            "batch_id": batch.batch_id, "code": batch.code,
            "qty": batch.remaining, "sellable": batch.sellable(trade_date),
            "entry_date": batch.entry_date, "cost_price": batch.cost_price,
            "plan_id": batch.plan_id, "family_id": batch.family_id,
            "initial_risk": batch.initial_risk,
            "risk_themes": list(batch.risk_themes or ()),
            "mark": mark,
            "market_value": (None if mark is None
                             else float(mark) * batch.remaining),
            "owner": _thaw(batch.strategy_ref)})
    orders = [
        {"order_id": order.order_id, "code": order.code, "side": order.side,
         "qty": order.qty, "filled_qty": order.filled_qty,
         "status": order.status, "plan_id": order.plan_id,
         "family_id": order.family_id,
         "depends_on": list(order.depends_on or ()),
         "reserved_amount": order.reserved_amount,
         "reserved_risk": order.reserved_risk,
         "risk_amount": order.risk_amount,
         "risk_themes": list(order.risk_themes or ()),
         "reason": order.reason}
        for order in sorted(state.orders, key=lambda item: item.order_id)]
    reservations = [
        item.to_row() for item in sorted(
            state.reservations, key=lambda item: item.order_id)
        if item.status == "reserved"]
    book = RiskBook.from_state(state)
    return {
        "cash": state.cash,
        "equity": state.marked_equity(),
        "drawdown_pct": state.drawdown_pct,
        "consecutive_losses": state.consecutive_losses,
        "batches": batches, "orders": orders, "reservations": reservations,
        "fund_credits": dict(state.fund_credits or {}),
        "recovery": dict(state.recovery or {}),
        "recovery_stage": state.recovery_stage,
        "policy_states": list(state.policy_states or ()),
        "closed_trades": list(state.closed_trades or ()),
        "trade_stats": dict(state.trade_stats or {}),
        "marks": {code: state.marks.get(code) for code in
                  {batch.code for batch in state.batches}},
        "risk_book": {"total": book.total, "unknown": book.unknown,
                      "unknown_holdings": book.unknown_holdings,
                      "themes": dict(book.themes)},
    }


def _thaw(value):
    from dragon_quant.review_account.evidence import thaw
    return thaw(dict(value or {}))


def replay_account_policy(payload: ReplayInput) -> ReplayResult:
    """在给定输入上逐事件回放账户政策；返回事件审计与期末残余/状态。"""
    if not isinstance(payload, ReplayInput):
        raise LedgerError("replay 需要 ReplayInput（不自动补数/不读真实库）")
    if payload.account.cash is None:
        raise LedgerError("回放需要完整期初现金；未知现金不伪造账户")
    calendar = tuple(payload.calendar or ())
    asof = calendar[0] if calendar else ""
    state = seed_ledger_state(
        payload.account, asof=asof, recovery=payload.recovery,
        policy_states=payload.policy_states, pending_orders=payload.pending_orders,
        reservations=payload.reservations, mark_facts=payload.mark_facts,
        batch_facts=payload.batch_facts, fund_credits=payload.fund_credits)
    state.trade_date = asof
    runtime = AccountPolicyRuntime(payload.config,
                                   strategy_ref=dict(payload.strategy_ref))
    try:
        processor = AccountEventProcessor(
            runtime, payload.cost_model, calendar=calendar,
            order_ttl_ms=payload.order_ttl_ms, risk_caps=payload.risk_caps,
            allow_unknown_risk=payload.allow_unknown_risk,
            max_quote_age_ms=payload.max_quote_age_ms,
            ledger_id=payload.ledger_id)
        records: list[dict] = []
        seen: dict[int, str] = {}
        for event in payload.events:
            if not isinstance(event, ReplayEvent):
                raise LedgerError("events 必须是 ReplayEvent 序列")
            timestamp = int(event.timestamp)
            source = processor.source_hash(
                event, event.decide, event.decision_kwargs,
                event.capacity_override, event.candidates)
            if timestamp in seen:
                if seen[timestamp] == source:
                    continue
                raise LedgerError("同时间戳事件内容冲突（拒绝静默覆盖）")
            if timestamp <= state.event_cursor:
                raise LedgerError("回放事件必须严格递增")
            seen[timestamp] = source
            outcome = processor.run(
                state, event, decide=event.decide,
                candidates=event.candidates,
                decision_kwargs=event.decision_kwargs,
                capacity_override=event.capacity_override,
                source_hash=source)
            recovery = RecoveryState.from_row(state.recovery or {})
            records.append({
                "event_id": f"evt:{payload.ledger_id}:{timestamp}",
                "timestamp": timestamp, "trade_date": outcome.day,
                "status": "applied", "decided": outcome.decision is not None,
                "decision_record": outcome.decision_record,
                "execution_record": outcome.execution_record,
                "fills": [fill.to_row() for fill in outcome.result.fills],
                "canceled": list(outcome.canceled),
                "revocations": [dict(item) for item in outcome.revocations],
                "coverage": {
                    "used_capabilities": list(getattr(
                        outcome.decision.bundle, "used_capabilities", ())
                        if outcome.decision else ()),
                    "missing_capabilities": list(getattr(
                        outcome.decision.bundle, "missing_capabilities", ())
                        if outcome.decision else ())},
                "state": _state_snapshot(state, outcome.day),
                "recovery": recovery.to_dict(),
            })
        return ReplayResult(events=tuple(records),
                            final_state=_state_snapshot(
                                state, state.trade_date))
    finally:
        runtime.close()


__all__ = [
    "ReplayEvent", "ReplayInput", "ReplayResult", "replay_account_policy",
    "state_snapshot",
]


def state_snapshot(state: LedgerState, trade_date: str) -> dict:
    """公开只读状态投影（持久化查询/审计复用同一形状）。"""
    return _state_snapshot(state, trade_date)
