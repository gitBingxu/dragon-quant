"""集合竞价公共用例 facade — 薄入口与兼容 re-export（M4）。

实现按变化原因拆分：

- :mod:`dragon_quant.auction.collect`：候选池 / 日历 / 行情 / 采集 / 快照 payload；
- :mod:`dragon_quant.auction.recompute`：已存资产离线重算；
- :mod:`dragon_quant.auction.scoring`：采集与重算共用的评分/摘要（唯一实现）；
- :mod:`dragon_quant.auction.dates`：日期归一与交易日解析；
- :mod:`dragon_quant.auction.types`：``AuctionVerdict`` / ``AuctionOutcome``。

本模块不承载任何编排逻辑，也不反向 import command 层；``commands/auction.py``
与直接 import 的测试经这里取得稳定公共入口。
"""

from __future__ import annotations

from dragon_quant.auction.collect import (  # noqa: F401  (兼容 re-export)
    _trade_calendar,
    run_auction,
)
from dragon_quant.auction.recompute import run_auction_recompute
from dragon_quant.auction.scoring import _score_auction_assets  # noqa: F401
from dragon_quant.auction.types import AuctionOutcome

__all__ = [
    "AuctionOutcome",
    "run_auction",
    "run_auction_recompute",
    "_score_auction_assets",
    "_trade_calendar",
]
