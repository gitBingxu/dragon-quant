"""集合竞价强度评分器。

对单只个股的 TDX 竞价盘口明细 `(time, price, matched, unmatched)` + 腾讯 Quote
做「一票否决闸门 + 四维加权」，产出 AuctionVerdict（强度等级/方向/诊断）。
纯函数、无副作用、不发请求；数据语义（符号/累计）待实测，见 registry 注释。
"""

from dragon_quant.auction.types import AuctionVerdict
from dragon_quant.auction.strength import evaluate, rank_auctions

__all__ = ["AuctionVerdict", "evaluate", "rank_auctions"]
