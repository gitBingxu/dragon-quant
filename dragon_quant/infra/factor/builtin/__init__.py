"""内置因子包：注册表模式（方案 §9）。

新增因子 = 在某个 ``builtin/*.py`` 里定义 compute 函数 + 一条 ``FactorSpec``，
然后加入该模块的 ``SPECS``；engine / storage / CLI 零改动。
"""

from __future__ import annotations

from dragon_quant.infra.factor.builtin import auction, financial, five_dim, prep, vpa

BUILTIN_MODULES = (five_dim, vpa, financial, prep, auction)


def all_specs():
    """全部内置因子 spec（顺序稳定：模块声明顺序）。"""
    specs = []
    for module in BUILTIN_MODULES:
        specs.extend(module.SPECS)
    return tuple(specs)


__all__ = ["BUILTIN_MODULES", "all_specs", "five_dim", "vpa", "financial",
           "prep", "auction"]
