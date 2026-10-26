"""``scan.scores.main`` —「识别真龙」五维评分体系公共入口。

带动 / 领涨 / 抗跌 / 流动 + 资金承接。当前 scan 主流程唯一使用的评分体系，
SQLite 仍沿用 `*_v2` 分表以兼容历史数据。依据《评分器Refactor.md》。

本模块只做门面重导出，评分算法分散在同包子模块（`aggregator` + 五个维度），
`aggregator` 保持独立，不塞进本文件。
"""

from __future__ import annotations

from . import registry as R
from dragon_quant.scan.scores.aggregator import evaluate, rank_verdicts
from dragon_quant.scan.scores.base import DragonVerdict

# 维度 → (score 函数模块名, 权重)，便于外部内省
SCORERS = dict(R.DIM_WEIGHTS)

__all__ = ["evaluate", "rank_verdicts", "DragonVerdict", "SCORERS"]
