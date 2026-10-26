"""账户级 review：模拟真实交易员按候选股、持仓和风控规则交易。

公共入口在 :mod:`dragon_quant.review_account.main`；本包根做轻量显式重导出。
"""

from dragon_quant.review_account.main import run_review_account

__all__ = ["run_review_account"]
