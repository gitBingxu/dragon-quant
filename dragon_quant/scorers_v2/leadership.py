"""scorers_v2.leadership — 领涨性 (25%)。

txt："龙头通常是板块里连板数量最多、涨幅最大的股票，对小弟在空间上有领涨优势"。
两个子因子各满分100：连板最多 BOARD_W .50 + 5日涨幅最大 PCT_W .50。
评分器只消费当日数据：board_count / fived_pct 由 Phase C 写入 Candidate。
"""

from __future__ import annotations

from typing import Optional

from dragon_quant.cache.data_cache import DataCache
from dragon_quant.models.types import Candidate, ScoreResult
from dragon_quant.scorers_v2 import registry as R
from dragon_quant.scorers_v2.base import clip, desc_rank_score

DIM = "leadership"
WEIGHT = R.DIM_WEIGHTS[DIM]


def score(code: str, cache: DataCache, primary_sector: str = "",
          candidate_pool: Optional[list[Candidate]] = None,
          **kwargs) -> ScoreResult:
    self_cand = _find_candidate(code, candidate_pool)
    board_count = self_cand.board_count if self_cand else 0
    fived_pct = self_cand.fived_pct if self_cand else 0.0

    # ── 连板最多 BOARD_W：与板块内最高连板对比 ──
    peer_boards = _peer_board_counts(primary_sector, candidate_pool)
    b_max = max([board_count] + peer_boards) if peer_boards else board_count
    s_board = clip(100.0 - (b_max - board_count) * R.BOARD_DECAY)

    # ── 5日涨幅最大 PCT_W：板块内当日涨停候选股之间 fived_pct 降序排名分位 ──
    # 口径说明：成分股 StockInfo.five_day_return 仅对当日拉过日K的涨停股为真实值，
    # 其余成分股为默认 0，会污染分位；故样本取候选池（均已在 Phase C 算真实 fived_pct）。
    sample = _peer_fived(primary_sector, candidate_pool)
    degraded = len(sample) < 2
    if fived_pct not in sample:
        sample = sample + [fived_pct]
    s_pct, rank, n = desc_rank_score(fived_pct, sample)

    total = clip(s_board * R.BOARD_W + s_pct * R.PCT_W)
    return ScoreResult(
        dim=DIM, score=round(total, 2), weight=WEIGHT,
        details={
            "s_board": round(s_board, 2), "board_count": board_count, "b_max": b_max,
            "s_pct": round(s_pct, 2), "fived_pct": round(fived_pct, 2),
            "pct_rank": rank, "pct_n": n, "degraded": degraded,
        },
    )


def _find_candidate(code, pool) -> Optional[Candidate]:
    for c in (pool or []):
        if c.code == code:
            return c
    return None


def _peer_board_counts(sector, pool) -> list[int]:
    return [c.board_count for c in (pool or []) if _same_sector(c, sector)]


def _peer_fived(sector, pool) -> list[float]:
    return [c.fived_pct for c in (pool or []) if _same_sector(c, sector)]


def _same_sector(cand: Candidate, sector: str) -> bool:
    if not sector:
        return True
    return cand.primary_sector == sector or sector in (cand.concepts or [])
