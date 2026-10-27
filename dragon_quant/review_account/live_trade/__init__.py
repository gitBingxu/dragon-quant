"""实盘辅助交易（buy/sell 纯信号记账，Task 13 canonical）。

公共入口在 :mod:`dragon_quant.review_account.live_trade.main`；本包只做轻量显式
重导出。策略与成交完全复用 :mod:`dragon_quant.review_account`。
"""

from dragon_quant.review_account.live_trade.main import run_buy, run_sell

__all__ = ["run_buy", "run_sell"]
