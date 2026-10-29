"""主线板块评分与排序（共享纯函数，**唯一实现**）。

``compute_mainlines``（复盘分计算）与 ``derive_pool_codes``（详细池代码解析）必须
使用同一套 N/H/C/S 评分与排序，否则「评分的代码」和「采/评的主线」会漂移——
池按涨停家数排序、计算按主线分排序时，边界上会漏掉真正的主线核心。

本模块只依赖 :mod:`config`：无 I/O、无项目内其它依赖，因而不构成循环。
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from .config import CONFIG, ReviewConfig

_LIANBAN_RE = re.compile(r"(\d+)天(\d+)板")


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
    return number if math.isfinite(number) else None


def _normalize(raw: Any, cap: float) -> float | None:
    number = _num(raw)
    if number is None:
        return None
    return min(max(number, 0.0) / cap, 1.0) * 100.0 if cap > 0 else None


def _row_height(row: Mapping[str, Any]) -> int | None:
    high_num = _num(row.get("high_num"))
    if high_num is not None and high_num > 0:
        return int(high_num)
    match = _LIANBAN_RE.search(str(row.get("high") or ""))
    return int(match.group(2)) if match else None


def _members(row: Mapping[str, Any]) -> list[str] | None:
    """从 ``members`` / ``stock_list`` 取成员代码；结构缺失返回 ``None``。"""

    value = row.get("members")
    if value is None:
        value = row.get("stock_list")
    if not isinstance(value, list):
        return None
    codes: list[str] = []
    seen: set[str] = set()
    for item in value:
        code = item.get("code") if isinstance(item, dict) else item
        if isinstance(code, str) and code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def mainline_score_for(
    limit_up_num: Any,
    high_num: Any,
    continuous_plate_num: Any,
    concentration: Any,
    config: ReviewConfig = CONFIG,
) -> dict:
    """四子分 → 主线分（缺失子分在可用权重内归一，不填 0/中性）。"""

    caps = config.mainline_caps
    sub_scores = {
        "N": _normalize(limit_up_num, float(caps["limit_up_num"])),
        "H": _normalize(high_num, float(caps["high_num"])),
        "C": _normalize(continuous_plate_num, float(caps["continuous_plate_num"])),
        "S": _normalize(concentration, float(caps["concentration"])),
    }
    available_weight = sum(
        weight for key, weight in config.weights_mainline.items()
        if sub_scores[key] is not None
    )
    score: float | None = None
    if available_weight > 0:
        score = round(
            sum(
                weight * float(sub_scores[key])
                for key, weight in config.weights_mainline.items()
                if sub_scores[key] is not None
            )
            / available_weight,
            1,
        )
    return {
        "score": score,
        "sub_scores": {
            key: (None if value is None else round(float(value), 1))
            for key, value in sub_scores.items()
        },
        "partial": any(value is None for value in sub_scores.values()),
    }


def _block_rows(assets: Mapping[str, Any]) -> list[dict]:
    payload = assets.get("ths_block_top")
    rows = payload.get("rows") if isinstance(payload, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _pool_rows(assets: Mapping[str, Any]) -> list[dict]:
    payload = assets.get("ths_limit_up_pool")
    rows = payload.get("rows") if isinstance(payload, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _limit_up_cnt(assets: Mapping[str, Any]) -> int | None:
    payload = assets.get("ths_limit_up_pool")
    pool = payload if isinstance(payload, dict) else {}
    total = _num(pool.get("total"))
    if total is not None and total >= 0:
        return int(total)
    rows = pool.get("rows")
    # 池被分页截断时，行数只是下界，不能冒充全量涨停家数。
    if isinstance(rows, list) and pool.get("truncated") is not True:
        return len(rows)
    return None


def _pool_truncated(assets: Mapping[str, Any]) -> bool:
    payload = assets.get("ths_limit_up_pool")
    return isinstance(payload, dict) and payload.get("truncated") is True


def rank_boards(
    assets: Mapping[str, Any], config: ReviewConfig = CONFIG
) -> list[dict]:
    """板块行 → 主线分排序后的观察位（前 ``mainline_watchlist_n`` 名）。

    返回行含原始 ``members`` 列表（调用方负责在对外输出前移除），其余字段与
    ``compute_mainlines`` 的中间结构一致。排序：主线分降序（缺失最后）、涨停家数
    降序、板块名升序——完全确定性。
    """

    limit_up_cnt = _limit_up_cnt(assets)
    pool_truncated = _pool_truncated(assets)
    pool_rows = _pool_rows(assets)
    pool_codes = {
        row.get("code") for row in pool_rows
        if isinstance(row.get("code"), str) and row.get("code")
    }

    ranked: list[dict] = []
    for row in _block_rows(assets):
        name = row.get("name") or row.get("board_name")
        if not isinstance(name, str) or not name:
            continue
        members = _members(row)
        member_codes = members or []
        candidates = [code for code in member_codes if code in pool_codes]

        limit_up_num = _num(row.get("limit_up_num"))
        if limit_up_num is None and members and not pool_truncated:
            limit_up_num = len(candidates)
        high_num = _row_height(row)
        continuous = _num(row.get("continuous_plate_num"))
        concentration = (
            limit_up_num / limit_up_cnt
            if limit_up_num is not None and limit_up_cnt is not None and limit_up_cnt > 0
            else None
        )

        scored = mainline_score_for(
            limit_up_num, high_num, continuous, concentration, config)
        reasons: list[str] = []
        if members is None or not members:
            reasons.append("board_members_missing")
        if scored["score"] is None:
            reasons.append("mainline_score_missing")

        ranked.append(
            {
                "board_name": name,
                "category": row.get("category"),
                "mainline_score": scored["score"],
                "sub_scores": scored["sub_scores"],
                "partial": scored["partial"],
                "state": "unknown",
                "rank": 0,
                "is_mainline": False,
                "watchlist": False,
                "limit_up_num": None if limit_up_num is None else int(limit_up_num),
                "high_num": high_num,
                "continuous_plate_num": None if continuous is None else int(continuous),
                "concentration": concentration,
                "members_sample": member_codes[: config.mainline_members_sample_limit],
                "members": member_codes,
                "role_map": {},
                "evidence": [],
                "unknown_reason": ";".join(reasons) or None,
            }
        )

    ranked.sort(
        key=lambda item: (
            1 if item["mainline_score"] is None else 0,
            -float(item["mainline_score"] or 0.0),
            -(float(item["limit_up_num"]) if item["limit_up_num"] is not None else 0.0),
            item["board_name"],
        )
    )
    return ranked[: config.mainline_watchlist_n]


__all__ = ["mainline_score_for", "rank_boards"]
