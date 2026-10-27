"""``review_account`` 公共入口：账户级模拟交易回测（Task 12）。

用例实现仍在 :mod:`dragon_quant.review_account.service`；本模块是公共导入门面，
``review_account/__init__.py`` 与本模块指向同一函数对象。
"""

from __future__ import annotations

# ``service`` first: its import chain finishes loading ``market``/``strategy``
# before ``data`` is bound here, avoiding the pre-existing
# ``market ↔ strategy.buy`` import-order sensitivity (Task 15 re-export).
from dragon_quant.review_account.service import run_review_account
from dragon_quant.review_account.data import MarketData
from dragon_quant.review_account.models import StrategyConfig

__all__ = ["run_review_account", "StrategyConfig", "MarketData"]
