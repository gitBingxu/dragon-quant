"""P/A 优先级：三值语义、排序键与否决的唯一来源。

设计约束（见 archived_md/auction/2026-10-21-auction-prep-buy-priority-design.md）：

- 三值统一为 ``True`` / ``False`` / ``None(Unknown)``，排序序为 True < Unknown < False；
- ``prep_state`` 只把 strong/light 视为 True，ignore/unrated 视为 False，其余（旧快照缺
  verdict）视为 Unknown；
- ``auction_state`` 只信任「有行 ∧ not unverified ∧ not is_weak」；数据缺失、JSON 损坏、
  语义未实测（unverified）与空 items 哨兵一律 Unknown，绝不把「不知道」当成「弱」；
- ``rank_key`` 是 ``TradingEngine`` 与 ``SignalEngine`` 的唯一排序键；``key_components``
  是同一分量的可落库版本，二者不得各写一套；
- 因子分量（P2）插在 ``-composite`` 之前，受 ``factor_ranking_enabled``（默认关）控制；
  关闭时恒为 ``(0, 0.0, "off")``，排序与旧实现逐位等价。缺失 ≠ 中性：缺因子分的
  候选进 ``factor_rank=1`` 桶，整日 ``degraded`` 时完全回退；
- 本模块纯函数、无 I/O，便于单测与跨引擎复用。
"""

from __future__ import annotations

import math
from typing import Optional

from dragon_quant.review_account.auction.registry import AUCTION_ITEMS_MISSING

# 三值排序：True（最优）→ Unknown（中性）→ False（最差）
_ORDER = {True: 0, None: 1, False: 2}
UNKNOWN_ORDER = 1

PREP_RECOMMENDED = frozenset({"strong", "light"})
PREP_NOT_RECOMMENDED = frozenset({"ignore", "unrated"})

TRI_LABELS = {True: "强", False: "弱", None: "未知"}

# 因子分排序分量（P2）：0=有分；1=缺失（排在所有有分者之后）。关闭时恒 0。
FACTOR_RANK_SCORED = 0
FACTOR_RANK_MISSING = 1
FACTOR_MODES = ("off", "on", "degraded")

RANKING_MODE_LABELS = {
    "auction": "A/P 已启用（竞价快照已验证，参与排序与否决）",
    "auction_unverified": "已读竞价快照，但语义未实测 → A 中性（不参与排序/否决）",
    "prep_only": "无当日竞价行 → A 中性",
    "legacy": "无 T-1 prep 快照 → 3 日真龙兜底",
    "mixed": "区间内排序模式混合（见 ranking_modes）",
}


def tri_order(value: Optional[bool]) -> int:
    """三值 → 排序序：True=0、Unknown=1、False=2。"""
    return _ORDER.get(value, UNKNOWN_ORDER)


def prep_state(candidate: dict) -> Optional[bool]:
    """prep 推荐三值：strong/light → True；ignore/unrated → False；缺字段 → None。"""
    verdict = candidate.get("verdict") if isinstance(candidate, dict) else None
    if verdict in PREP_RECOMMENDED:
        return True
    if verdict in PREP_NOT_RECOMMENDED:
        return False
    return None


def auction_state(record: Optional[dict]) -> Optional[bool]:
    """竞价三值：无行/损坏/unverified/空 items → None；verified ∧ weak → False；否则 True。"""
    if not isinstance(record, dict) or record.get("error"):
        return None
    if not isinstance(record.get("unverified"), bool):
        return None          # 字段缺失 → Unknown（不当作「已验证」）
    if record["unverified"]:
        return None
    if record.get("reject_reason") == AUCTION_ITEMS_MISSING:
        return None
    is_weak = record.get("is_weak")
    if not isinstance(is_weak, bool):
        return None
    return not is_weak


def _entry_priority(candidate: dict) -> dict:
    value = candidate.get("entry_priority") if isinstance(candidate, dict) else None
    return value if isinstance(value, dict) else {}


def _side_state(candidate: dict, side: str) -> Optional[bool]:
    block = _entry_priority(candidate).get(side)
    if not isinstance(block, dict):
        return None
    state = block.get("state")
    return state if isinstance(state, bool) else None


def _factor_component(candidate: dict, enabled: bool) -> tuple[int, float, str]:
    """因子分量的**唯一**归零/缺失判定（``rank_key`` 只消费，不另写分支）。

    - 开关关：``(0, 0.0, "off")``，无论候选注解残留什么都强制归零；
    - 有 ``entry_priority.factor`` 且 ``mode == "on"``：
      ``score`` 为有限数值 → ``(0, score, "on")``；否则 → 缺失桶 ``(1, 0.0, "on")``；
    - ``mode == "degraded"``（整日回退）或注解缺失/损坏 → ``(0, 0.0, "degraded")``，
      排序完全回退到无因子层基线。
    """
    if not enabled:
        return FACTOR_RANK_SCORED, 0.0, "off"
    block = _entry_priority(candidate).get("factor")
    if not isinstance(block, dict) or block.get("mode") in (None, "off"):
        return FACTOR_RANK_SCORED, 0.0, "degraded"
    if block.get("mode") != "on":
        return FACTOR_RANK_SCORED, 0.0, "degraded"
    score = block.get("score")
    try:
        number = float(score)
    except (TypeError, ValueError):
        return FACTOR_RANK_MISSING, 0.0, "on"
    if not math.isfinite(number):
        return FACTOR_RANK_MISSING, 0.0, "on"
    return FACTOR_RANK_SCORED, number, "on"


def key_components(signal: dict, candidate: dict,
                   auction_ranking_enabled: bool = False,
                   factor_ranking_enabled: bool = False) -> dict:
    """排序键的可观测分量（落库/展示）；``rank_key`` 由同一份数据派生。"""
    a_state = _side_state(candidate, "auction") if auction_ranking_enabled else None
    p_state = _side_state(candidate, "prep")
    composite = (candidate or {}).get("composite_score") or 0
    try:
        composite = float(composite)
    except (TypeError, ValueError):
        composite = 0.0
    priority = (signal or {}).get("priority") or 0
    try:
        priority = int(priority)
    except (TypeError, ValueError):
        priority = 0
    factor_rank, factor_score, factor_mode = _factor_component(
        candidate, bool(factor_ranking_enabled))
    return {
        "auction_order": tri_order(a_state),
        "prep_order": tri_order(p_state),
        "factor_rank": factor_rank,
        "factor_score": factor_score,
        "factor_mode": factor_mode,
        "composite": composite,
        "priority": priority,
        "code": str((candidate or {}).get("code") or ""),
    }


def rank_key(signal: dict, candidate: dict,
             auction_ranking_enabled: bool = False,
             factor_ranking_enabled: bool = False) -> tuple:
    """全序排序键：tier(A,P) → factor_rank → factor_score desc → composite desc
    → priority desc → code。

    ``auction_ranking_enabled=False`` 时 A 分量恒为 Unknown；``factor_ranking_enabled``
    默认 ``False`` 时因子分量为 ``(0, 0.0)``，插入的两个零分量对所有候选相同，
    排序与旧实现**逐位等价**（去掉第 3、4 位即旧元组）。因子分插在 ``-composite``
    之前（不是之后）：否则退化成几乎不生效的 tie-breaker，A/B 永远测不出增量。
    """
    parts = key_components(signal, candidate, auction_ranking_enabled,
                           factor_ranking_enabled)
    return (
        parts["auction_order"],
        parts["prep_order"],
        parts["factor_rank"],
        -parts["factor_score"],
        -parts["composite"],
        -parts["priority"],
        parts["code"],
    )


def ranking_mode(*, prep_snapshot_date, auction_rows: int,
                 auction_verified_rows: int) -> str:
    """事实驱动的排序模式（不由候选注解反推）。"""
    if auction_verified_rows > 0:
        return "auction"
    if auction_rows > 0:
        return "auction_unverified"
    if prep_snapshot_date:
        return "prep_only"
    return "legacy"


def would_veto(candidate: dict) -> bool:
    """影子标记：若开启否决，该候选是否会被杀（不改变决策）。"""
    return _side_state(candidate, "auction") is False


def auction_veto_reason(cfg, candidate: dict, signal: Optional[dict]) -> Optional[str]:
    """D3 否决的唯一实现；仅 ``auction_veto_enabled`` 时生效。"""
    if not getattr(cfg, "auction_veto_enabled", False):
        return None
    if not signal:
        return None
    if _side_state(candidate, "auction") is not False:
        return None
    exempt = getattr(cfg, "auction_veto_exempt_signals", None) or []
    if signal.get("code") in exempt:
        return None
    block = _entry_priority(candidate).get("auction") or {}
    reason = block.get("reject_reason") or "竞价明确走弱"
    strength = block.get("strength")
    level = block.get("level")
    evidence = "、".join(
        part for part in (
            f"强度 {strength}" if strength is not None else "",
            f"等级 {level}" if level else "",
        ) if part
    )
    return f"竞价否决：{reason}" + (f"（{evidence}）" if evidence else "")
