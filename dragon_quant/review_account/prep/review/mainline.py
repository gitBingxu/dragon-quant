"""主线纯计算：状态五态、排名与角色映射（评分/排序见 :mod:`board_rank`）。

输入只有资产 ``dict`` 与上一快照 ``prev``；本模块**无 I/O**。所有数字（Top N、
五态阈值）都来自 :class:`~.config.ReviewConfig`；N/H/C/S 评分与排序由
:func:`board_rank.rank_boards` **唯一实现**，详细池（``pool``）复用同一函数，
避免「采的代码」与「评的主线」漂移。

设计模式：

* **策略（Strategy）** —— 四个子分各是一条"原始值 → 0–100"的封顶归一策略；
* **模板方法（Template Method）** —— 统一走 ``mainline_score_for``（子分 → 缺失归一），
  排名/状态/角色只在其外层组合。
"""

from __future__ import annotations

from typing import Any, Mapping

from .board_rank import mainline_score_for, rank_boards
from .config import CONFIG, ReviewConfig
from .roles import assign_roles


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return None
    return number if number == number and abs(number) != float("inf") else None


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------


def _prev_mainlines(prev: Any) -> list[dict]:
    if not isinstance(prev, dict):
        return []
    review = prev.get("review")
    if isinstance(review, dict) and isinstance(review.get("mainlines"), list):
        return [item for item in review["mainlines"] if isinstance(item, dict)]
    if isinstance(prev.get("mainlines"), list):
        return [item for item in prev["mainlines"] if isinstance(item, dict)]
    return []


def _prev_rank_map(prev: Any, watchlist_n: int) -> dict[str, tuple[int, float | None]]:
    mapping: dict[str, tuple[int, float | None]] = {}
    for index, item in enumerate(_prev_mainlines(prev)):
        name = item.get("board_name") or item.get("name")
        if not isinstance(name, str) or not name:
            continue
        rank = _num(item.get("rank"))
        resolved_rank = int(rank) if rank is not None and rank > 0 else index + 1
        if resolved_rank > watchlist_n:
            continue
        mapping[name] = (resolved_rank, _num(item.get("mainline_score")))
    return mapping


def _state_for(
    board_name: str,
    rank: int,
    score: float | None,
    prev_ranks: Mapping[str, tuple[int, float | None]],
    config: ReviewConfig,
) -> str:
    if not prev_ranks:
        return "unknown"
    if score is None:
        return "unknown"
    previous = prev_ranks.get(board_name)
    if previous is None:
        return "new" if rank <= config.mainline_top_n else "unknown"

    prev_rank, prev_score = previous
    rank_delta = prev_rank - rank  # 正数 = 排名上升
    score_delta = score - prev_score if prev_score is not None else None

    # main 单次验收裁决：负向优先。排名上升但分数大幅下滑、或排名大幅下滑但分数上升，
    # 都按 weakening 处理，避免把「排名 +1 / 分数 -20」误报为强化。
    if rank_delta <= -config.mainline_state_rank_down or (
        score_delta is not None and score_delta < -config.mainline_state_score_delta
    ):
        return "weakening"
    if rank_delta >= config.mainline_state_rank_up or (
        score_delta is not None and score_delta > config.mainline_state_score_delta
    ):
        return "strengthening"
    if (
        score_delta is not None
        and abs(rank_delta) <= 1
        and abs(score_delta) <= config.mainline_state_score_delta
    ):
        return "stable"
    return "unknown"


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def _evidence(item: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    if item.get("limit_up_num") is not None:
        lines.append(f"涨停 {item['limit_up_num']} 家")
    if item.get("high_num") is not None:
        lines.append(f"最高 {item['high_num']} 板")
    if item.get("continuous_plate_num") is not None:
        lines.append(f"连续在榜 {item['continuous_plate_num']} 天")
    if item.get("concentration") is not None:
        lines.append(f"集中度 {float(item['concentration']) * 100:.1f}%")
    return lines


def compute_mainlines(
    assets: dict, prev: dict | None = None, config: ReviewConfig = CONFIG
) -> list[dict]:
    """主线排名 + 五态 + 角色映射（纯函数）。

    * 按主线分降序，取前 ``mainline_watchlist_n`` 名；前 ``mainline_top_n`` 名为当日主线；
    * 并列按（涨停家数 desc、板块名升序）稳定排序；
    * 无 T-1 快照 / T-1 板块榜不可用 → 全部 ``state="unknown"``；
    * 成员缺失时主线分仍展示，``unknown_reason="board_members_missing"``。
    """

    pool_payload = assets.get("ths_limit_up_pool")
    pool_rows = (pool_payload.get("rows") if isinstance(pool_payload, dict) else None)
    pool_rows = [row for row in pool_rows if isinstance(row, dict)] \
        if isinstance(pool_rows, list) else []

    ranked = rank_boards(assets, config)
    prev_ranks = _prev_rank_map(prev, config.mainline_watchlist_n)
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
        item["is_mainline"] = index <= config.mainline_top_n
        item["watchlist"] = (
            config.mainline_top_n < index <= config.mainline_watchlist_n
        )
        item["state"] = _state_for(
            item["board_name"], index, item["mainline_score"], prev_ranks, config
        )
        item["evidence"] = _evidence(item)

    top_blocks = [item for item in ranked if item["is_mainline"]]
    assignments = assign_roles(pool_rows, top_blocks, config) if top_blocks else {}
    for item in top_blocks:
        entries = sorted(
            (
                (
                    record.get("intra_board_rank") or 10**9,
                    record.get("role") or "",
                    code,
                )
                for code, record in assignments.items()
                if record.get("role")
                and isinstance(record.get("mainline"), dict)
                and record["mainline"].get("board_name") == item["board_name"]
            ),
            key=lambda entry: (entry[0], entry[1], entry[2]),
        )
        role_map: dict[str, str] = {}
        for _, role, code in entries:
            role_map.setdefault(role, code)
        item["role_map"] = role_map

    for item in ranked:
        item.pop("members", None)
    return ranked


__all__ = ["compute_mainlines", "mainline_score_for"]
