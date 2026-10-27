"""策略包（Task 14 canonical）。

公共入口在 :mod:`dragon_quant.review_account.strategy.main`；本包只做轻量显式
重导出，不承载规则实现。14a 整搬期的名称代理已退役：私有 patch 目标迁移为
``strategy.buy.entry_veto``（包根 setattr 不再转发到 ``buy`` 全局）。
"""

from __future__ import annotations

from dragon_quant.review_account.strategy import main as _main
from dragon_quant.review_account.strategy.main import *  # noqa: F403

__all__ = list(_main.__all__)
