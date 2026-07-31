"""scorers —「识别真龙」五维评分体系（带动/领涨/抗跌/流动 + 资金承接）。

当前 scan 主流程唯一使用的评分体系，SQLite 仍沿用 `*_v2` 分表以兼容历史数据。
依据《评分器Refactor.md》。
"""

from dragon_quant.scorers import registry as R
from dragon_quant.scorers.aggregator import evaluate, rank_verdicts
from dragon_quant.scorers.base import DragonVerdict

# 维度 → (score 函数模块名, 权重)，便于外部内省
SCORERS = dict(R.DIM_WEIGHTS)

__all__ = ["evaluate", "rank_verdicts", "DragonVerdict", "SCORERS"]
