"""``scan.scores`` — 五维「识别真龙」评分器（Task 8 迁入）。

公共入口是 :mod:`dragon_quant.scan.scores.main`，本 ``__init__`` 轻量重导出
该门面的四个公共名；评分算法子模块保持独立，``aggregator`` 不并入 ``main``。
"""

from __future__ import annotations

from dragon_quant.scan.scores.main import (
    SCORERS,
    DragonVerdict,
    evaluate,
    rank_verdicts,
)

__all__ = ["evaluate", "rank_verdicts", "DragonVerdict", "SCORERS"]
