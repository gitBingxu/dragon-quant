"""策略包（Task 14 canonical）。

公共入口在 :mod:`dragon_quant.review_account.strategy.main`；本包用 **PEP 562
惰性导出**，避免导入纯计算子包（``bs_policy`` / ``runtime`` 等）时顺带加载
``main`` → ``service`` → ``infra.storage`` 的 I/O 链。私有 patch 目标仍在
``strategy.buy.entry_veto`` 等具体子模块。
"""

from __future__ import annotations


def _main():
    from importlib import import_module
    return import_module(".main", __name__)


def __getattr__(name: str):
    module = _main()
    if name == "__all__":
        return list(module.__all__)
    try:
        return getattr(module, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}") from None


def __dir__():
    return sorted(set(globals()) | set(dir(_main())))
