"""infra.factor — 因子层与因子/财务 SQL。

公共门面在 :mod:`dragon_quant.infra.factor.main`（13 个批准出口 + 25 个 lazy
旧名）。本 ``__init__`` 是它的**惰性代理**：导入本包不加载任何应用子模块，
属性访问时才解析 ``main`` 及其 owner 子模块（PEP 562）。
"""

from __future__ import annotations

import importlib

_MAIN = f"{__package__}.main"


def __getattr__(name: str):
    # ``main`` 本身必须先直接 import：若走 ``from . import main``，import 机制对
    # 包属性 ``main`` 的探测会再次进入本函数，造成无限递归。
    module = importlib.import_module(_MAIN)
    if name == "main":
        return module
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | {"main"})
