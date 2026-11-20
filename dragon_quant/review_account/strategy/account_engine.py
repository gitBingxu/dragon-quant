"""账户事件引擎（W3）：账本加载 → 共享事件处理 → CAS 原子提交。

所有订单生命周期/触发/到期/成交/费用/现金/批次/T+1/容量/预留/保护时序以及
政策/撤销/恢复 Reducer 都在 :mod:`account_event` + :mod:`execution_core` 的共享
实现里；本模块只负责**持久化边界**：加载 revision/检查点、同事件幂等判定与
CAS 提交。公共 replay 复用同一个事件处理器，不复制业务判断。
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional, Sequence

from dragon_quant.review_account.execution_core.matching import (
    ExecutionCostModel,
)
from dragon_quant.review_account.execution_core.models import LedgerError
from dragon_quant.review_account.execution_core.risk import RiskCaps
from dragon_quant.review_account.execution_core.runtime_facts import (
    validate_max_age_ms,
)
from dragon_quant.review_account.ledger.store import AccountLedger
from dragon_quant.review_account.strategy.account_event import (
    AccountEventProcessor, SOURCE_HASH_VERSION,
)
from dragon_quant.review_account.strategy.account_runtime import (
    AccountPolicyRuntime,
)

__all__ = ["AccountEngine", "SOURCE_HASH_VERSION"]


class AccountEngine:
    """单账户事件执行器：账本 + 政策运行时 + 共享事件处理器。"""

    def __init__(self, ledger: AccountLedger, runtime: AccountPolicyRuntime,
                 cost_model: ExecutionCostModel, *,
                 calendar: Sequence[str] = (),
                 order_ttl_ms: Optional[int] = 86_400_000,
                 risk_caps: Optional[RiskCaps] = None,
                 allow_unknown_risk: bool = False,
                 max_quote_age_ms: Optional[int] = None) -> None:
        self.ledger = ledger
        self.runtime = runtime
        self._processor = AccountEventProcessor(
            runtime, cost_model, calendar=calendar,
            order_ttl_ms=order_ttl_ms, risk_caps=risk_caps,
            allow_unknown_risk=allow_unknown_risk,
            max_quote_age_ms=validate_max_age_ms(max_quote_age_ms),
            ledger_id=ledger.ledger_id)

    # 兼容既有适配器/测试：费用模型可替换（事件处理器与来源身份同步读取）。
    @property
    def cost_model(self) -> ExecutionCostModel:
        return self._processor.cost_model

    @cost_model.setter
    def cost_model(self, value: ExecutionCostModel) -> None:
        self._processor.cost_model = value

    @property
    def risk_caps(self) -> Optional[RiskCaps]:
        return self._processor.risk_caps

    @property
    def calendar(self) -> list[str]:
        return self._processor.calendar

    @property
    def order_ttl_ms(self) -> Optional[int]:
        return self._processor.order_ttl_ms

    @property
    def allow_unknown_risk(self) -> bool:
        return self._processor.allow_unknown_risk

    @property
    def max_quote_age_ms(self) -> Optional[int]:
        return self._processor.max_quote_age_ms

    def state(self):
        return self.ledger.load_state()

    def next_trade_date(self, day: str) -> Optional[str]:
        return self._processor.next_trade_date(day)

    def process_event(self, event, *, decide: bool = False,
                      candidates: Iterable = (),
                      decision_kwargs: Optional[Mapping] = None,
                      capacity_override: Optional[Mapping[str, int]] = None
                      ) -> dict:
        state = self.ledger.load_state()
        if state is None:
            raise LedgerError(f"账本 {self.ledger.ledger_id} 尚未创建")
        candidates = tuple(candidates or ())
        timestamp = int(event.timestamp)
        source = self._processor.source_hash(
            event, decide, decision_kwargs, capacity_override, candidates)
        if timestamp <= state.event_cursor:
            prior = self.ledger.event_source_hash(
                f"evt:{self.ledger.ledger_id}:{timestamp}")
            if prior is not None and prior == source:
                return {"status": "already_processed", "event_ts": timestamp}
            raise LedgerError(
                f"同时间戳事件内容与既有记录不同（source_hash 语义 "
                f"{SOURCE_HASH_VERSION}；新的修订需新事件身份，旧语义账本事件"
                f"重放视为不兼容，不静默覆盖）")
        outcome = self._processor.run(
            state, event, decide=decide, candidates=candidates,
            decision_kwargs=decision_kwargs,
            capacity_override=capacity_override, source_hash=source)
        event_id = f"evt:{self.ledger.ledger_id}:{timestamp}"
        status = self.ledger.commit_event(
            expected_revision=state.revision, event_id=event_id,
            source_hash=outcome.source_hash, content_hash=outcome.content_hash,
            state=state, fills=outcome.result.fills,
            decision_record=outcome.decision_record,
            execution_record=outcome.execution_record)
        decision = outcome.decision
        return {"status": status, "event_ts": timestamp,
                "fills": [fill.fill_id for fill in outcome.result.fills],
                "fill_records": [fill.to_row()
                                 for fill in outcome.result.fills],
                "decided": decision is not None,
                "bundle": None if decision is None else decision.bundle,
                "documented_intents": ([] if decision is None
                                       else list(decision.intents)),
                "canceled": outcome.canceled,
                "revocations": outcome.revocations,
                "matches": outcome.result.matches,
                "rejected": outcome.result.rejected}
