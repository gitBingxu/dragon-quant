"""``dragon_quant.scan`` — 可调用扫描包。

2026-10-25 批准：本包从「包根惰性函数」改为**可调用模块**，接受与旧
``orchestrator.scan`` 的函数身份变化：

- ``from dragon_quant import scan; scan(...)`` 在任意导入顺序下都可调用，
  调用时经 :func:`_resolve_scan` 解析 ``scan.orchestrator.main.scan``；
- 包属性 ``scan`` 由 :meth:`_CallableScanModule.__getattr__` 惰性解析真实函数，
  **不写回** ``globals()``，避免缓存与身份漂移；
- 包属性 ``scan`` 与 ``scan.orchestrator.main.scan`` 是**同一函数对象**；
- 导入本包不加载 orchestrator（B1 零副作用）。
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any

__all__ = ["scan"]

_ORCHESTRATOR_ENTRY = f"{__name__}.orchestrator.main"


def _resolve_scan():
    """调用时解析真实编排入口，返回 ``scan.orchestrator.main.scan``。"""

    return getattr(importlib.import_module(_ORCHESTRATOR_ENTRY), "scan")


class _CallableScanModule(types.ModuleType):
    """``dragon_quant.scan`` 的模块类：实例可调用，``scan`` 属性惰性转发。"""

    def __call__(self, *args: Any, **kwargs: Any):
        return _resolve_scan()(*args, **kwargs)

    def __getattr__(self, name: str) -> object:
        if name == "scan":
            return _resolve_scan()
        raise AttributeError(
            f"module {self.__name__!r} has no attribute {name!r}")


sys.modules[__name__].__class__ = _CallableScanModule
