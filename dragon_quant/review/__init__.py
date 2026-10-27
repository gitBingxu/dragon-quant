"""``dragon_quant.review`` — 龙头回测包（Task 12：模块 → 包）。

公共入口在 :mod:`dragon_quant.review.main`（``run_review`` / ``review_dragon``）。
本包是**历史属性代理**（:mod:`dragon_quant.infra.module_proxy`）：读取按名字惰性
转发到 ``main``，``setattr`` 也转发（因此
``patch("dragon_quant.review.build_trade_calendar")`` /
``patch("dragon_quant.review.XueqiuProvider")`` 仍真实改变运行期行为）。
``main`` 子模块属性写本地，避免导入系统设置子模块时被代理吞掉。

``dragon_quant.run_review`` 根懒导出与旧 ``from dragon_quant.review import
run_review`` 保持不变；新代码应直接 import ``dragon_quant.review.main``。
"""

from __future__ import annotations

import sys

from dragon_quant.infra.module_proxy import NameProxyModule, install_name_proxy

_MAIN = f"{__name__}.main"

# 旧模块属性 patch 面（迁移表：build_trade_calendar / XueqiuProvider 由包代理转发）。
_MAPPING = {
    name: _MAIN
    for name in (
        "run_review",
        "review_dragon",
        "build_trade_calendar",
        "XueqiuProvider",
        "kbar_to_dict",
        "find_entry_day",
        "_run_vpa",
        "main",
    )
}

__all__ = ["run_review", "review_dragon"]

install_name_proxy(
    sys.modules[__name__], _MAPPING, local_submodules={"main"})
sys.modules[__name__].__class__ = NameProxyModule
