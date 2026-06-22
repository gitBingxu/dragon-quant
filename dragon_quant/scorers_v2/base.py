"""scorers_v2.base — 共享数据模型与工具函数。

ScoreResult 复用 models/types.py；本模块新增 DragonVerdict（聚合产物）
及评分器共享的 1分K对齐、归一化涨幅曲线、板块内排名分位等纯函数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from dragon_quant.models.types import KBar, ScoreResult  # noqa: F401  (re-export)

MIN_BUCKET_MS = 60_000     # 1 分钟
FIVE_BUCKET_MS = 300_000   # 5 分钟


@dataclass
class DragonVerdict:
    """真龙判定（门槛+加权聚合产物）。"""
    code: str
    is_true_dragon: bool                  # 是否通过四大特征门槛
    composite: float                      # 加权综合分 0-100
    rank: Optional[int] = None            # 真龙池内排名
    dims: dict[str, ScoreResult] = field(default_factory=dict)  # 五维独立分
    reject_reason: Optional[str] = None   # 若被否决，卡在哪一维


def clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def align_1min(bars: list[KBar]) -> dict[int, KBar]:
    """按 1 分钟 bucket（ts//60000）索引 KBar。"""
    return {bar.timestamp // MIN_BUCKET_MS: bar for bar in (bars or [])}


def common_minute_axis(*bar_lists: list[KBar]) -> list[int]:
    """多条 1分K 的公共分钟轴（升序 bucket 列表）。

    取所有序列 bucket 的并集，便于按统一轴前向填充对齐。
    """
    buckets: set[int] = set()
    for bl in bar_lists:
        for bar in (bl or []):
            buckets.add(bar.timestamp // MIN_BUCKET_MS)
    return sorted(buckets)


def gain_curve(bars: list[KBar], axis: list[int]) -> list[Optional[float]]:
    """归一化涨幅曲线 g[t] = close[t]/preclose − 1，按 axis 对齐并前向填充。

    直接取 KBar.pct（provider 已按昨收正确填充：雪球 percent / 同花顺相对 node.pre），
    避免用「首分钟价」误当昨收导致高开股量级被压缩。缺口前向填充，轴起点前无数据填 None。
    """
    if not bars:
        return [None] * len(axis)
    m = align_1min(bars)
    out: list[Optional[float]] = []
    last: Optional[float] = None
    for b in axis:
        bar = m.get(b)
        if bar is not None:
            last = bar.pct / 100.0
        out.append(last)
    return out


def desc_rank_score(value: float, sample: list[float]) -> tuple[float, int, int]:
    """板块内降序排名分位：s = (1 − r/n) × 100。

    value 在 sample（含自身）中按降序名次 r（最大者 r=1），样本数 n。
    返回 (score, r, n)。n<=1 → score=0（单样本无相对优势）。
    """
    n = len(sample)
    if n <= 1:
        return 0.0, 1, n
    # 名次 = 严格大于本值的个数 + 1
    r = sum(1 for v in sample if v > value) + 1
    return (1.0 - r / n) * 100.0, r, n


def pre_close_of(bars: list[KBar]) -> float:
    """从一条当日 1分K 推断昨收：首根 open（雪球 minute open=上一分钟 close，
    首根 open≈昨收）。无数据返回 0。"""
    if not bars:
        return 0.0
    first = bars[0]
    return first.open if first.open > 0 else first.close
