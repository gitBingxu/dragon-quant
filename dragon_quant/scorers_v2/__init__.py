"""scorers_v2 —「识别真龙」五维评分体系（带动/领涨/抗跌/流动 + 资金承接）。

与旧 scorers/ 并存、互不影响，由编排器/CLI `--scorers v2` 开关切换。
依据《评分器Refactor.md》。
"""

from dragon_quant.scorers_v2 import registry as R
from dragon_quant.scorers_v2.aggregator import evaluate, rank_verdicts
from dragon_quant.scorers_v2.base import DragonVerdict

# 维度 → (score 函数模块名, 权重)，便于外部内省
SCORERS_V2 = dict(R.DIM_WEIGHTS)

__all__ = ["evaluate", "rank_verdicts", "DragonVerdict", "SCORERS_V2"]
