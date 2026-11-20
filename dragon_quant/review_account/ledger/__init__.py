"""W3 账户批次账本（真值 + 原子落账 + 保守撮合 + 只读投影）的稳定窄入口。

分层（按变化原因隔离）：

- :mod:`models` 纯值对象（批次/订单/成交腿/预留/状态机）；
- :mod:`matching` 决策后下一事件的保守撮合（T+1/费用/容量/归属）；
- :mod:`projection` 账本 → W2 ``AccountView`` 只读投影（按归属分组）；
- :mod:`store` dragon.db 原子事件提交与只读查询。

不 import CLI/Web/provider/因子；导入本包不建库（首次真实存储访问才惰性建）。
"""

from __future__ import annotations

from .history import HistoryPage, HistoryRecord, LedgerHistory
from .matching import (
    ExecutionCostModel,
    ExecutionQuote,
    MatchResult,
    match_order,
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
from .projection import project_account_view
from .store import AccountLedger, LEDGER_SCHEMA_VERSION

__all__ = [
    "AccountLedger",
    "LEDGER_SCHEMA_VERSION",
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
    "project_account_view",
    "LedgerHistory",
    "HistoryPage",
    "HistoryRecord",
]
