"""集合竞价公共门面（Task 13 canonical）。

职责边界与旧 ``auction/service.py`` 一致：只做**稳定重导出**与展示转发，不承载
采集/重算/评分编排：

- :mod:`dragon_quant.review_account.auction.collect`：候选池 / 日历 / 行情 / 采集；
- :mod:`dragon_quant.review_account.auction.recompute`：已存资产离线重算；
- :mod:`dragon_quant.review_account.auction.scoring`：采集与重算共用的评分摘要；
- :mod:`dragon_quant.review_account.auction.strength`：纯函数强度评分。

``commands/auction.py`` 与测试只从这里取公共入口。
"""

from __future__ import annotations

from dragon_quant.review_account.auction.collect import (
    _trade_calendar,  # noqa: F401  (approved public re-export)
    run_auction,
)
from dragon_quant.review_account.auction.recompute import run_auction_recompute
from dragon_quant.review_account.auction.scoring import (  # noqa: F401
    _score_auction_assets,  # approved public re-export / test seam
)
from dragon_quant.review_account.auction.strength import evaluate, rank_auctions
from dragon_quant.review_account.auction.types import AuctionOutcome, AuctionVerdict

__all__ = [
    "AuctionOutcome",
    "AuctionVerdict",
    "evaluate",
    "format_report",
    "rank_auctions",
    "run_auction",
    "run_auction_recompute",
]


def format_report(*args, **kwargs):
    """公共展示门面：运行期解析 ``report.format_auction_report``（保持 patch 点有效）。"""
    from dragon_quant.review_account.auction.report import format_auction_report

    return format_auction_report(*args, **kwargs)
