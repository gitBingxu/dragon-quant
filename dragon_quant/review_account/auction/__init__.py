"""集合竞价强度评分器（Task 13 canonical）。

对单只个股的 TDX 竞价盘口明细 ``(time, price, matched, unmatched)`` + 腾讯 Quote
做「一票否决闸门 + 四维加权」，产出 AuctionVerdict（强度等级/方向/诊断）。
纯函数、无副作用、不发请求；数据语义（符号/累计）待实测，见 registry 注释。

公共入口在 :mod:`dragon_quant.review_account.auction.main`；本包只做轻量显式
重导出，不承载编排逻辑。
"""

from dragon_quant.review_account.auction.main import (
    AuctionOutcome,
    AuctionVerdict,
    evaluate,
    format_report,
    rank_auctions,
    run_auction,
    run_auction_recompute,
)

__all__ = [
    "AuctionOutcome",
    "AuctionVerdict",
    "evaluate",
    "format_report",
    "rank_auctions",
    "run_auction",
    "run_auction_recompute",
]
