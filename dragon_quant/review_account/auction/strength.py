"""auction.strength — 稳定门面：委托 :mod:`auction.scoring` 的唯一实现。

保留 ``evaluate`` / ``rank_auctions`` / ``normalize`` / ``_level_of`` 导入面，
便于既有消费方与测试继续引用；评分骨架只在 ``scoring.py`` 实现，禁止复制。
"""

from __future__ import annotations

from .normalize import normalize  # noqa: F401  (re-export)
from .scoring import _level_of, evaluate, rank_auctions  # noqa: F401

__all__ = ["evaluate", "rank_auctions", "normalize", "_level_of"]
