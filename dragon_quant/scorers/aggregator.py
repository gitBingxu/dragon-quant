"""scorers.aggregator — 门槛+加权聚合，产出 DragonVerdict。

四大特征（drive/leadership/anti_drop/liquidity）任一低于门槛 → 一票否决；
absorption 不否决，仅加权贡献。通过者按 composite 降序排名。
"""

from __future__ import annotations

from typing import Optional

from dragon_quant.cache.data_cache import DataCache
from dragon_quant.models.types import Candidate, ScoreResult
from dragon_quant.scorers import registry as R
from dragon_quant.scorers.base import DragonVerdict
from dragon_quant.scorers import (
    drive, leadership, anti_drop, liquidity, absorption)

# 维度 → score 函数
_SCORERS = {
    "drive": drive.score,
    "leadership": leadership.score,
    "anti_drop": anti_drop.score,
    "liquidity": liquidity.score,
    "absorption": absorption.score,
}
# 四大特征（设门槛），absorption 不在其列
_HARD_DIMS = ("drive", "leadership", "anti_drop", "liquidity")


def evaluate(code: str, cache: DataCache, *,
             candidate_pool: Optional[list[Candidate]] = None,
             primary_sector: str = "",
             all_sector_codes: Optional[list[str]] = None,
             sector_name_map: Optional[dict[str, str]] = None) -> DragonVerdict:
    """对单只候选股做五维评分 + 门槛/加权聚合。"""
    dims: dict[str, ScoreResult] = {}
    for dim, fn in _SCORERS.items():
        try:
            dims[dim] = fn(
                code, cache,
                primary_sector=primary_sector,
                candidate_pool=candidate_pool,
                all_sector_codes=all_sector_codes,
                sector_name_map=sector_name_map,
            )
        except Exception as e:
            dims[dim] = ScoreResult(
                dim=dim, score=R.ABS_NEUTRAL if dim == "absorption" else 0.0,
                weight=R.DIM_WEIGHTS[dim],
                details={"error": str(e), "degraded": True,
                         "fallback": True, "fallback_reason": "评分执行异常"},
            )

    # ── Step 1 硬门槛 ──
    reject = None
    for dim in _HARD_DIMS:
        floor = R.DIM_FLOORS.get(dim)
        if "error" in dims[dim].details:
            reject = f"{dim} 评分异常: {dims[dim].details['error']}"
            break
        if floor is not None and dims[dim].score < floor:
            reject = f"{dim}={dims[dim].score:.1f} < floor {floor:.0f}"
            break

    # ── Step 2 加权综合 ──
    composite = sum(dims[d].score * R.DIM_WEIGHTS[d] for d in dims)
    return DragonVerdict(
        code=code, is_true_dragon=(reject is None),
        composite=round(composite, 2), rank=None,
        dims=dims, reject_reason=reject,
    )


def rank_verdicts(verdicts: list[DragonVerdict]) -> list[DragonVerdict]:
    """对通过门槛的真龙按 composite 降序赋 rank（被否决者 rank=None）。"""
    for verdict in verdicts:
        verdict.rank = None
    dragons = [v for v in verdicts if v.is_true_dragon]
    dragons.sort(key=lambda v: (-v.composite, v.code))
    for i, v in enumerate(dragons, 1):
        v.rank = i
    return verdicts
