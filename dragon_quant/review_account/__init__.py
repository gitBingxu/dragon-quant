"""账户级 review：模拟真实交易员按候选股、持仓和风控规则交易。

公共入口在 :mod:`dragon_quant.review_account.main`，本包根用 **PEP 562 惰性导出**
（``run_review_account`` / ``run_incremental_review_account``）。这样导入纯计算子包
（如 ``strategy.bs_policy`` / ``execution_core``）不会顺带拉入 ``service`` →
``infra.storage.connection`` 等 I/O 实现。
"""

from __future__ import annotations

_LAZY = ("run_review_account", "run_incremental_review_account")

__all__ = list(_LAZY)


def __getattr__(name: str):
    if name in _LAZY:
        from importlib import import_module
        module = import_module(".main", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
