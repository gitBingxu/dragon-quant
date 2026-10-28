"""盘后复盘（prep）包门面 —— 兼容代理（Task 12）。

公共入口与 legacy patch 门面在 :mod:`dragon_quant.review_account.prep.main`。
本包安装 :class:`~dragon_quant.infra.module_proxy.NameProxyModule`，并预先加载 ``main``
以发现完整映射名（旧包原本也 eager 加载全部子模块）：

- ``getattr`` 按映射惰性转发到 ``main``（``run_prep`` / ``PrepError`` /
  ``ASSET_COLLECTORS`` / provider 类 / ``assets_db`` / ``dragons_mirror`` / ``time``
  等旧 patch 目标）；
- ``setattr`` 只对映射名转发，因此 ``patch.object(prep, "TdxProvider")`` 仍改变
  运行期行为；
- ``service`` / ``collectors`` / ``compute`` / ``rebuild`` / ``main`` 子模块属性
  写本地，``from dragon_quant.review_account.prep import service`` 不被代理吞掉。

子模块在调用期经 ``rebuild.legacy`` 反向解析本包属性，所以这里**不能**急切实名绑定：
代理转发是 legacy patch 语义的唯一来源（AGENTS「刻意保留的兼容例外」）。
"""

from __future__ import annotations

import sys

from dragon_quant.infra.module_proxy import NameProxyModule, install_name_proxy

from . import main as _main

_MAIN = f"{__name__}.main"

# main 的公共导出 + 旧包命名空间里可 patch 的非 __all__ 名字。
_MAPPING = {
    name: _MAIN
    for name in (*_main.__all__, *_main._LEGACY_PATCH_TARGETS, "main")
}

__all__ = list(_main.__all__)

install_name_proxy(
    sys.modules[__name__],
    _MAPPING,
    local_submodules={"service", "collectors", "compute", "rebuild", "review", "main"},
)
sys.modules[__name__].__class__ = NameProxyModule
