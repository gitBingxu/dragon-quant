"""兼容导出：保守撮合已迁到纯叶子
:mod:`dragon_quant.review_account.execution_core.matching`（唯一实现）。

W2 场景与 W3 账本共用同一撮合；本模块只保留既有 import 路径。
"""

from __future__ import annotations

from dragon_quant.review_account.execution_core.matching import (  # noqa: F401
    ExecutionCostModel,
    ExecutionQuote,
    MatchResult,
    _owner_key,
    _released,
    match_order,
    owner_identity,
)

__all__ = [
    "ExecutionCostModel",
    "ExecutionQuote",
    "MatchResult",
    "match_order",
    "owner_identity",
]
