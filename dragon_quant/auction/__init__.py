"""集合竞价强度评分器。

对单只个股的 TDX 竞价盘口明细 `(time, price, matched, unmatched)` + 腾讯 Quote
做「一票否决闸门 + 四维加权」，产出 AuctionVerdict（强度等级/方向/诊断）。
纯函数、无副作用、不发请求；数据语义（符号/累计）待实测，见 registry 注释。
"""

from dragon_quant.auction.types import AuctionOutcome, AuctionVerdict
from dragon_quant.auction.strength import evaluate, rank_auctions

__all__ = [
    "AuctionOutcome", "AuctionVerdict", "evaluate", "rank_auctions",
    "run_auction", "run_auction_recompute", "format_report",
]


def format_report(*args, **kwargs):
    """公共展示门面：转发到当前 ``auction.report`` 实现（保持 patch 点有效）。"""
    from dragon_quant.auction.report import format_auction_report
    return format_auction_report(*args, **kwargs)


def run_auction(*args, **kwargs):
    """公共采集入口：转发到 :mod:`dragon_quant.auction.collect`（运行期解析）。"""
    from dragon_quant.auction.collect import run_auction as _run
    return _run(*args, **kwargs)


def run_auction_recompute(*args, **kwargs):
    """公共重算入口：转发到 :mod:`dragon_quant.auction.recompute`（运行期解析）。"""
    from dragon_quant.auction.recompute import run_auction_recompute as _run
    return _run(*args, **kwargs)
