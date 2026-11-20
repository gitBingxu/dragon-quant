"""执行核心：W2 场景与 W3 账本共用的**纯**执行状态转移（叶子层）。

分层（按变化原因隔离）：

- :mod:`models` 批次/订单/成交/预留/状态机（严格纯值，含风险与资金分离字段）；
- :mod:`matching` 决策后**下一合格执行事件**的保守撮合（T+1/费用/容量/归属）；
- :mod:`risk` 非现金风险上限、全账户占用与同事件并发核算；
- :mod:`transition` 唯一执行转移（订单生命周期/触发/到期/成交/费用/现金/批次）；
- :mod:`identity` 完整冻结归属身份（workspace/strategy/snapshot/params/factors）。

依赖方向：本包是叶子，**不 import** ``review_account.ledger``（含 store/projection）、
``infra.storage``、``infra.assets``、provider 或策略运行时；场景与账本各自用适配器
把外部输入/结果转换成本包类型。导入本包不建库、不读时钟/env。
"""

from __future__ import annotations

from .identity import ref_identity
from .matching import (
    ExecutionCostModel,
    ExecutionQuote,
    MatchResult,
    match_order,
    owner_identity,
)
from .models import (
    LEGAL_TRANSITIONS,
    SELLABLE_AFTER,
    SELLABLE_ON,
    SELLABLE_UNKNOWN,
    Batch,
    Fill,
    LedgerConflictError,
    LedgerError,
    LedgerIntegrityError,
    LedgerRevisionError,
    LedgerState,
    Order,
    OrderStatus,
    Reservation,
)
from .risk import RiskBook, RiskCaps, check_new_risk
from .runtime_facts import (
    MARK_LAST_CLOSE,
    MARK_QUOTE,
    BatchFacts,
    MarkFact,
    RuntimeFactError,
    apply_fill_facts,
    held_days,
    mark_prices,
    observe_last_close,
    observe_row,
    reduce_batch_facts,
    reduce_mark_facts,
)
from .settlement import settle_fills, trade_group_key
from .transition import (
    TransitionCommand,
    TransitionLimits,
    TransitionResult,
    transition_event,
)

__all__ = [
    "ref_identity",
    "owner_identity",
    "SELLABLE_UNKNOWN",
    "SELLABLE_ON",
    "SELLABLE_AFTER",
    "Batch",
    "Order",
    "Fill",
    "Reservation",
    "OrderStatus",
    "LEGAL_TRANSITIONS",
    "LedgerState",
    "LedgerError",
    "LedgerConflictError",
    "LedgerRevisionError",
    "LedgerIntegrityError",
    "ExecutionCostModel",
    "ExecutionQuote",
    "MatchResult",
    "match_order",
    "RiskCaps",
    "RiskBook",
    "check_new_risk",
    "TransitionCommand",
    "TransitionLimits",
    "TransitionResult",
    "transition_event",
    "MARK_QUOTE",
    "MARK_LAST_CLOSE",
    "BatchFacts",
    "MarkFact",
    "RuntimeFactError",
    "apply_fill_facts",
    "held_days",
    "mark_prices",
    "observe_last_close",
    "observe_row",
    "reduce_batch_facts",
    "reduce_mark_facts",
    "settle_fills",
    "trade_group_key",
]
