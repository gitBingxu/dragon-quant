"""公共研究 Facade 包。

导入本包只解析导出表，不建目录/库/连接。调用具体用例时才 import 对应模块。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "account",
    "datasets",
    "experiments",
    "handle_error",
    "query",
    "scan_quality",
    "schema",
    "signals",
    "snapshots",
    "strategy",
]


def __getattr__(name: str) -> Any:  # PEP 562 惰性导出
    if name == "experiments":
        # ``main.experiments`` 是 CLI 用例命名空间；包级同名必须给出真正的
        # 子模块，否则 ``from dragon_quant.research import experiments`` 会拿到
        # 命名空间类，导致 Web 路由的异常类/入口不可见。
        from importlib import import_module

        return import_module(f"{__name__}.experiments")
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import main as _main

    return getattr(_main, name)


def __dir__() -> list[str]:
    return sorted(__all__)
