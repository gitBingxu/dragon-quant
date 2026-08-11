"""账户级 review：模拟真实交易员按候选股、持仓和风控规则交易。"""

from dragon_quant.review_account.service import run_review_account

__all__ = ["run_review_account"]
