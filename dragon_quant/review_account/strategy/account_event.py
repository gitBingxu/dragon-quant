"""账户事件处理核心（W3 引擎与公共 replay 共用的一份编排）。

把「一次真实事件」的完整处理从 :class:`AccountEngine` 抽出：估值/报价适配 →
（可选）政策决策 → 明确授权撤销/合法余量继续 → 共享 :func:`transition_event`
→ 运行事实/政策状态/恢复状态推进。它不读数据库、不提交事务：账本引擎负责
CAS 提交，公共 replay 在内存状态上复用**同一份**编排与 Reducer。

数据源字段/新鲜度/身份口径在 :mod:`event_inputs`；本模块只编排业务顺序。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

from dragon_quant.review_account.execution_core.matching import (
    ExecutionCostModel, ExecutionQuote,
)
from dragon_quant.review_account.execution_core.models import (
    LedgerError, LedgerState,
)
from dragon_quant.review_account.execution_core.risk import RiskBook, RiskCaps
from dragon_quant.review_account.execution_core.runtime_facts import (
    apply_fill_facts, reduce_batch_facts, validate_max_age_ms,
)
from dragon_quant.review_account.execution_core.transition import (
    TransitionCommand, TransitionLimits, TransitionResult, transition_event,
)
from dragon_quant.review_account.strategy import event_inputs
from dragon_quant.review_account.strategy.account_runtime import (
    AccountDecision, AccountPolicyRuntime,
)
from dragon_quant.review_account.strategy.bs_policy import BSPolicyConfig
from dragon_quant.review_account.strategy.bs_policy.recovery import (
    advance_recovery_after_decision, advance_recovery_days,
)
from dragon_quant.review_account.strategy.bs_policy.scenario_contracts import (
    RecoveryConditionConfirmation, RecoveryState,
)
from dragon_quant.review_account.strategy.decision_record import (
    build_decision_record, build_execution_record,
)
from dragon_quant.review_account.strategy.event_inputs import (
    SOURCE_HASH_VERSION, iso as _iso, hash_payload as _hash,
)
from dragon_quant.review_account.strategy.policy_events import (
    advance_state_policy_states, apply_revocations,
    authorization_revocations, cancel_stale_buys, command_from_intent,
    contraction_revocations, seed_committed_policy_inputs,
)

def risk_caps_from_config(config) -> RiskCaps:
    budgets = getattr(config, "budgets", None)
    if budgets is None:
        return RiskCaps()
    return RiskCaps(per_trade=float(budgets.per_trade),
                    account=float(budgets.account),
                    unknown_group=float(budgets.unknown_group),
                    theme_default=float(budgets.per_theme))


@dataclass
class EventOutcome:
    """一次事件处理的完整结果（账本提交与 replay 时间线共用）。"""

    timestamp: int
    day: str
    source_hash: str
    content_hash: str
    result: TransitionResult
    decision: Optional[AccountDecision]
    decision_record: dict
    execution_record: dict
    canceled: list[str] = field(default_factory=list)
    revocations: list[dict] = field(default_factory=list)
    already_processed: bool = False


class AccountEventProcessor:
    """事件编排与 Reducer 组合（无 I/O）。"""

    def __init__(self, runtime: AccountPolicyRuntime,
                 cost_model: ExecutionCostModel, *,
                 calendar: Iterable[str] = (),
                 order_ttl_ms: Optional[int] = 86_400_000,
                 risk_caps: Optional[RiskCaps] = None,
                 allow_unknown_risk: bool = False,
                 max_quote_age_ms: Optional[int] = None,
                 ledger_id: str = "") -> None:
        self.runtime = runtime
        self.cost_model = cost_model
        self.calendar = sorted(calendar or ())
        self.order_ttl_ms = order_ttl_ms
        self.risk_caps = risk_caps
        self.allow_unknown_risk = allow_unknown_risk
        self.max_quote_age_ms = validate_max_age_ms(max_quote_age_ms)
        self.ledger_id = ledger_id

    def next_trade_date(self, day: str) -> Optional[str]:
        for item in self.calendar:
            if item > day:
                return item
        return None

    # ── 事件 ────────────────────────────────────────────────────────────────
    def run(self, state: LedgerState, event, *, decide: bool = False,
            candidates: Iterable = (),
            decision_kwargs: Optional[Mapping] = None,
            capacity_override: Optional[Mapping[str, int]] = None,
            source_hash: str = "") -> EventOutcome:
        candidates = tuple(candidates or ())
        timestamp = int(event.timestamp)
        event_time = _iso(timestamp)
        if not source_hash:
            source_hash = self.source_hash(event, decide, decision_kwargs,
                                           capacity_override, candidates)
        day = self.event_day(event, state)
        state.trade_date = day
        state.event_time = event_time
        self._update_marks(state, event, timestamp)
        pre_equity = event_inputs.current_equity(
            state, timestamp, max_age_ms=self.max_quote_age_ms)
        self._mark_equity(state, pre_equity)
        valuation_known = pre_equity is not None
        quotes = self.quotes(event, day)
        latch = advance_recovery_days(
            RecoveryState.from_row(getattr(state, "recovery", None) or {}),
            trade_date=day, calendar=self.calendar)
        commands: list[TransitionCommand] = []
        decision = None
        canceled: list[str] = []
        revocations: list[dict] = []
        confirmations: tuple = ()
        runtime_audit: dict = {}
        if decide:
            kwargs = dict(decision_kwargs or {})
            kwargs.setdefault("decision_time", event_time)
            kwargs.setdefault("rows", getattr(event, "rows", {}) or {})
            kwargs.setdefault("trade_date", day)
            kwargs.setdefault("calendar", tuple(self.calendar))
            kwargs.setdefault("event_time", timestamp)
            from .owner_protection import _held_days

            checkpoints = dict(getattr(state, "position_facts", {}) or {})
            provided = dict(kwargs.get("held_days_by_batch") or {})
            mapped = {}
            for batch in getattr(state, "batches", ()) or ():
                if int(getattr(batch, "remaining", 0) or 0) <= 0:
                    continue
                checkpoint = dict(checkpoints.get(str(batch.batch_id)) or {})
                override = (checkpoint.get("held_days")
                            if str(checkpoint.get("as_of") or "")[:10]
                            == str(day)[:10] else None)
                value = _held_days(
                    str(batch.entry_date or ""), day,
                    tuple(self.calendar), override)
                if value is not None:
                    mapped[str(batch.batch_id)] = int(value)
                elif provided.get(str(batch.batch_id)) is not None:
                    mapped[str(batch.batch_id)] = int(
                        provided[str(batch.batch_id)])
            # 以当前 state 活跃批次为准覆盖旧/空表，避免新买入批次无 fallback。
            kwargs["held_days_by_batch"] = mapped
            # 本事件 PIT 可执行估值是否可得（覆盖调用方，不允许用旧估值授权）。
            kwargs["current_valuation_known"] = valuation_known
            confirmations = tuple(
                kwargs.pop("recovery_confirmations", ()) or ())
            # 适配器审计（趋势窗口/主题/压力来源）只作记录，不进入决策参数；
            # 与**本事件实际使用**的逐批持有时长一起落入 DecisionRecord。
            runtime_audit = {
                "trend_audit": dict(kwargs.pop("trend_audit", None) or {}),
                "held_days_by_batch": dict(mapped)}
            provided_recovery = kwargs.get("recovery")
            if provided_recovery is not None \
                    and not getattr(state, "recovery", None):
                # 空账本允许调用方显式提供**初始**恢复锁存（研究/replay 种子）；
                # 已有真实状态时下面的 seed 会拒绝静默覆盖。
                latch = (provided_recovery
                         if isinstance(provided_recovery, RecoveryState)
                         else RecoveryState.from_row(provided_recovery))
            latch = self._merge_confirmations(latch, confirmations)
            # 只有已提交过恢复状态时才把它作为权威输入（显式 kwarg 不得静默覆盖
            # 真实状态）；空状态允许调用方提供初始锁存（研究/replay 种子）。
            seed_committed_policy_inputs(
                state, kwargs,
                recovery=(latch if getattr(state, "recovery", None) else None))
            decision = self.runtime.decide(
                state, candidates=candidates, **kwargs)
            revocations = authorization_revocations(
                state, prices=self._prices(quotes, event_ts=timestamp,
                                           event_time=event_time),
                market_allowed=self._market_allowed(kwargs),
                envelope_halted=self._envelope_halted(kwargs))
            # 收缩期内，触发前已存在的买单剩余同属新增风险：按本事件新授权
            # 保守撤余量（已成交保护保留），不能因订单 partial/市场仍 True 绕过。
            merged = {str(item["order_id"]): item for item in revocations}
            for item in contraction_revocations(
                    state,
                    recovery=getattr(decision.bundle, "recovery", {}) or {},
                    event_ts=timestamp):
                merged.setdefault(str(item["order_id"]), item)
            revocations = list(merged.values())
            canceled = (cancel_stale_buys(state, decision.intents)
                        + apply_revocations(state, revocations))
            commands = [command_from_intent(i) for i in decision.intents]
            state.last_decision = self.decision_audit(decision)
        else:
            # 无新决定的事件也必须先按**已知冻结价格失效线**撤未成余量再撮合
            # （main 决策：冻结硬失效/有效期在每个真实支持行情事件先判）。
            revocations = authorization_revocations(
                state, prices=self._prices(quotes, event_ts=timestamp,
                                           event_time=event_time),
                market_allowed=None, envelope_halted=None)
            canceled = apply_revocations(state, revocations)
        batches_before = {batch.batch_id: batch for batch in state.batches}
        result = transition_event(
            state, event_time=timestamp, trade_date=day, quotes=quotes,
            commands=commands, limits=self.limits(state),
            evidence_available=getattr(event, "evidence_available", ()) or (),
            capacity_override=capacity_override)
        canceled = canceled + result.canceled
        self._update_batch_facts(state, timestamp, day, batches_before, result)
        advance_state_policy_states(state, fills=result.fills,
                                    event_time_iso=event_time,
                                    event_ts=timestamp,
                                    version=self.runtime_version())
        latch = self._advance_latch(latch, decision, day, timestamp,
                                    confirmations)
        state.recovery = latch.to_dict()
        state.recovery_stage = int(latch.stage)
        self._mark_equity(
            state, event_inputs.current_equity(
                state, timestamp, max_age_ms=self.max_quote_age_ms))
        state.event_cursor = timestamp
        content = _hash({
            "event": source_hash,
            "fills": [fill.content_hash for fill in result.fills],
            "orders": [[o.order_id, o.status, o.filled_qty, o.reserved_amount,
                        o.locked_qty] for o in state.orders],
            "canceled": canceled,
            "revocations": revocations,
            "fund_credits": dict(state.fund_credits or {}),
            "recovery": dict(state.recovery or {}),
        })
        event_id = f"evt:{self.ledger_id}:{timestamp}"
        return EventOutcome(
            timestamp=timestamp, day=day, source_hash=source_hash,
            content_hash=content,
            result=result, decision=decision,
            decision_record=build_decision_record(
                decision, source=source_hash, event_id=event_id,
                event_time=event_time, day=day, timestamp=timestamp,
                decision_kwargs=decision_kwargs,
                frozen_ref=getattr(self.runtime, "strategy_ref", {}),
                runtime_audit=runtime_audit),
            execution_record=build_execution_record(
                result=result, orders=list(state.orders),
                mark_facts=getattr(state, "mark_facts", {}) or {},
                batch_facts=getattr(state, "batch_facts", {}) or {},
                canceled=canceled, revocations=revocations,
                day=day, timestamp=timestamp),
            canceled=canceled, revocations=revocations)

    # ── 恢复状态（G8）─────────────────────────────────────────────────────
    @staticmethod
    def _merge_confirmations(latch: RecoveryState, confirmations) -> RecoveryState:
        if not confirmations:
            return latch
        merged = list(latch.confirmations)
        seen = {(item.rule_id, item.available_at, item.version, item.snapshot_id)
                for item in merged}
        for item in confirmations:
            if not isinstance(item, RecoveryConditionConfirmation):
                raise LedgerError("recovery_confirmations 必须是结构化确认")
            key = (item.rule_id, item.available_at, item.version,
                   item.snapshot_id)
            if key not in seen:
                merged.append(item)
                seen.add(key)
        from dataclasses import replace
        return replace(latch, confirmations=tuple(merged))

    def _advance_latch(self, latch: RecoveryState, decision, day: str,
                       timestamp: int, confirmations) -> RecoveryState:
        if decision is None:
            return latch
        config = getattr(self.runtime, "config", None)
        if not isinstance(config, BSPolicyConfig):
            return latch
        return advance_recovery_after_decision(
            latch, recovery=getattr(decision.bundle, "recovery", {}) or {},
            config=config, trade_date=day, event_ts=timestamp,
            calendar=tuple(self.calendar), confirmations=confirmations)

    # ── 适配：限额/报价/来源 ───────────────────────────────────────────────
    def limits(self, state: LedgerState) -> TransitionLimits:
        caps = self.risk_caps
        if caps is None and getattr(self.runtime, "config", None) is not None:
            caps = risk_caps_from_config(self.runtime.config)
        return TransitionLimits(
            cost_model=self.cost_model,
            next_trade_date=self.next_trade_date, ttl_ms=self.order_ttl_ms,
            risk_caps=caps, risk_book=RiskBook.from_state(state),
            allow_unknown_risk=self.allow_unknown_risk,
            max_age_ms=self.max_quote_age_ms)

    @staticmethod
    def _market_allowed(kwargs) -> Optional[bool]:
        market = kwargs.get("market")
        return None if market is None else getattr(market, "new_risk_allowed",
                                                   None)

    @staticmethod
    def _envelope_halted(kwargs) -> Optional[bool]:
        envelope = kwargs.get("envelope")
        return None if envelope is None else bool(getattr(envelope, "halted",
                                                          False))

    @staticmethod
    def _prices(quotes: Mapping[str, ExecutionQuote], *, event_ts: int,
                event_time: str) -> dict[str, float]:
        """只采纳**本事件时点已可得**的报价（不拿未来/迟到报价撤销）。

        撤销是真实授权动作：未来执行时点或可得时点晚于本事件的报价不参与
        冻结价格线判定；已知历史真实价格（未过期）可触发保护性撤余量。
        """
        prices: dict[str, float] = {}
        for code, quote in quotes.items():
            if quote.price is None or not quote.available_at:
                continue
            if quote.reason in ("quote_from_future", "quote_time_missing",
                                "quote_available_before_source"):
                continue
            if int(quote.event_ts) > int(event_ts):
                continue
            if quote.available_at > event_time:
                continue
            prices[code] = quote.price
        return prices

    def runtime_version(self) -> str:
        config = getattr(self.runtime, "config", None)
        return str(getattr(config, "content_hash", "") or "")

    def quotes(self, event, day: str) -> dict[str, ExecutionQuote]:
        quotes: dict[str, ExecutionQuote] = {}
        rows = getattr(event, "rows", {}) or {}
        for code in rows:
            quotes[code] = self.quote(event, code, day)
        return quotes

    # ── 估值/日/来源/审计 ───────────────────────────────────────────────────
    def _update_marks(self, state: LedgerState, event, timestamp: int) -> None:
        """标记事实（A1）：源时点/可得时点不被事件时钟覆盖，旧收盘不冒充可执行价。"""
        event_inputs.update_marks(
            state, event, timestamp, max_quote_age_ms=self.max_quote_age_ms)

    def _update_batch_facts(self, state: LedgerState, timestamp: int,
                            day: str, batches_before, result) -> None:
        """逐批运行事实（A1/A2）：峰值只由先前已知可执行行情抬升，半仓由真实成交推进。"""
        state.batch_facts = reduce_batch_facts(
            getattr(state, "batch_facts", None) or {}, batches=state.batches,
            marks=getattr(state, "mark_facts", None) or {},
            event_ts=int(timestamp), trade_date=day, calendar=tuple(self.calendar))
        state.batch_facts = apply_fill_facts(
            state.batch_facts, fills=result.fills,
            batches_before=batches_before, event_ts=int(timestamp))

    @staticmethod
    def _mark_equity(state: LedgerState, equity) -> None:
        """用本事件 PIT 可执行估值更新峰值/回撤；未知估值不改旧值（受限）。"""
        if equity is None:
            return
        state.peak_equity = max(state.peak_equity, equity)
        peak = state.peak_equity
        state.drawdown_pct = ((peak - equity) / peak * 100.0) if peak else 0.0

    @staticmethod
    def event_day(event, state: LedgerState) -> str:
        day = getattr(event, "trade_date", None)
        if day:
            return day
        from dragon_quant.review_account.market import event_date
        return event_date(int(event.timestamp))

    def source_hash(self, event, decide: bool, decision_kwargs,
                    capacity_override, candidates=()) -> str:
        return event_inputs.source_hash(
            event, decide=decide, decision_kwargs=decision_kwargs,
            capacity_override=capacity_override, candidates=candidates,
            fingerprint=self.runtime_fingerprint())

    def runtime_fingerprint(self) -> dict:
        return event_inputs.runtime_fingerprint(
            self.runtime, self.cost_model,
            max_quote_age_ms=self.max_quote_age_ms,
            order_ttl_ms=self.order_ttl_ms, calendar=self.calendar,
            allow_unknown_risk=self.allow_unknown_risk)

    @staticmethod
    def decision_audit(decision: AccountDecision) -> dict:
        bundle = decision.bundle
        limits = dict(getattr(bundle, "action_limits", {}) or {})
        from dragon_quant.review_account.evidence import thaw

        return {
            "status": getattr(bundle, "status", ""),
            "selected_plan_id": getattr(bundle, "selected_plan_id", None),
            "policy_version": getattr(bundle, "policy_version", ""),
            "config_hash": getattr(bundle, "config_hash", None),
            "snapshot_id": getattr(bundle, "snapshot_id", None),
            "missing_capabilities": list(
                getattr(bundle, "missing_capabilities", ()) or ()),
            "protection_obligations": thaw(
                list(limits.get("protection_obligations") or ())),
            "owner_restrictions": thaw(
                list(limits.get("owner_restrictions") or ())),
            "recovery": thaw(dict(getattr(bundle, "recovery", {}) or {})),
            "intents": [[i.action, i.code, i.qty, i.plan_id, i.obligation_id,
                         dict(i.strategy_ref or {}), list(i.batch_ids or ())]
                        for i in decision.intents],
        }

    def quote(self, event, code: str, day: str) -> ExecutionQuote:
        return event_inputs.quote(
            event, code, day, max_quote_age_ms=self.max_quote_age_ms)


__all__ = [
    "AccountEventProcessor", "EventOutcome", "SOURCE_HASH_VERSION",
    "risk_caps_from_config",
]
