"""scorers_v2.liquidity — 流动性 (20%)。

txt："没有换手、全是一字板顶板的装死走不远 / 真龙是流动性换手走出来的焦点"。
两个子因子各满分100：换手充沛度 TURNOVER_W .5 + 封板质量 SEAL_W .5。
封单走腾讯 gtimg 收盘盘口 Quote.bid1_volume（手），与 Quote.volume（手）同源同单位。
不设一字板惩罚。
"""

from __future__ import annotations

from dragon_quant.cache.data_cache import DataCache
from dragon_quant.models.types import KBar, Quote, ScoreResult, StockInfo
from dragon_quant.scorers_v2 import registry as R
from dragon_quant.scorers_v2.base import clip

DIM = "liquidity"
WEIGHT = R.DIM_WEIGHTS[DIM]


def score(code: str, cache: DataCache, primary_sector: str = "",
          **kwargs) -> ScoreResult:
    quotes: list[Quote] = cache.get("quotes:batch") or []
    qmap = {q.code: q for q in quotes}
    q = qmap.get(code)
    minute: list[KBar] = cache.get(f"kline:1min:{code}") or []
    components: list[StockInfo] = cache.get(f"sector:components:{primary_sector}") or []

    degraded = False

    # ── 换手充沛度 ──
    to = q.turnover_rate if q else 0.0
    s_to_abs = clip(to / R.TURNOVER_FULL * 100.0) if R.TURNOVER_FULL else 0.0
    # 相对分：板块成分股换手降序排名分位
    peer_to = [qmap[s.code].turnover_rate for s in components if s.code in qmap]
    if to not in peer_to:
        peer_to = peer_to + [to]
    s_to_rel = _desc_rank(to, peer_to)
    s_turnover = s_to_abs * R.TO_ABS_W + s_to_rel * R.TO_REL_W

    # ── 封板质量 ──
    is_limit_up = bool(q and q.pct >= R.LIMIT_UP_PCT)
    # 封单强度：bid1_volume(手) / volume(手)，同源同单位
    if q and q.bid1_volume > 0 and q.volume > 0:
        strength = q.bid1_volume / q.volume
        s_seal_strength = clip(strength / R.SEAL_STRENGTH_REF * 100.0)
    elif is_limit_up:
        s_seal_strength = 60.0  # 已涨停但无盘口 → 近似
        degraded = True
    else:
        s_seal_strength = 0.0
        if not q:
            degraded = True
    # 封板稳定性：当日1分K 触及涨停价后回落（开板）次数
    if minute and q and q.limit_up > 0:
        n_open = _count_open(minute, q.limit_up)
        s_seal_stable = 100.0 if n_open == 0 else (60.0 if n_open <= 2 else 20.0)
    else:
        n_open = -1
        s_seal_stable = 60.0  # 1分K缺失 → 中性
        degraded = True
    s_seal = s_seal_strength * R.SEAL_STRENGTH_W + s_seal_stable * R.SEAL_STABLE_W

    total = clip(s_turnover * R.TURNOVER_W + s_seal * R.SEAL_W)
    return ScoreResult(
        dim=DIM, score=round(total, 2), weight=WEIGHT,
        details={
            "s_turnover": round(s_turnover, 2), "turnover_rate": round(to, 2),
            "s_to_abs": round(s_to_abs, 2), "s_to_rel": round(s_to_rel, 2),
            "s_seal": round(s_seal, 2), "s_seal_strength": round(s_seal_strength, 2),
            "s_seal_stable": round(s_seal_stable, 2), "n_open": n_open,
            "is_limit_up": is_limit_up, "degraded": degraded,
        },
    )


def _desc_rank(value: float, sample: list[float]) -> float:
    n = len(sample)
    if n <= 1:
        return 0.0
    r = sum(1 for v in sample if v > value) + 1
    return (1.0 - r / n) * 100.0


def _count_open(minute: list[KBar], limit_up: float) -> int:
    """统计「触及涨停价后又回落」次数：价回到涨停下方再次离开视为一次开板。"""
    eps = limit_up * 0.001  # 0.1% 容差
    sealed = False
    opens = 0
    for bar in minute:
        touched = bar.high >= limit_up - eps
        below = bar.close < limit_up - eps
        if touched and not below:
            sealed = True
        elif sealed and below:
            opens += 1
            sealed = False
    return opens
