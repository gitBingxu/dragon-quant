"""premarket.anchors — 三锚投影（短线 / 趋势 / 情绪）。

只做投影：数值与角色全部来自 T-1 ``prep.result.review`` 与 ``sentiment``，
不新增股票、不新增阈值、不做全市场推断。同一 code 的角色卡只保留一张：
``short_term > trend``；正 / 负锚是证据条目，可与角色卡同码共存（与方案示例一致）。
"""

from __future__ import annotations

from typing import Any, Mapping

ANCHOR_METHOD = "prep_review_projection_v1"
ANCHOR_SCOPE = "candidate_and_mainline_scope_not_full_market"
ANCHOR_DEDUPE = "one_card_per_code; short_term > trend > sentiment"

_BREADTH_KEYS = (
    "up_cnt", "down_cnt", "limit_up_cnt", "limit_down_cnt", "broken_rate",
    "yesterday_premium_pct", "promotion_rate", "max_board_height",
)


def _as_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def _anchor_rows(value: Any) -> list[dict]:
    rows = []
    for item in _as_list(value):
        if isinstance(item, Mapping) and isinstance(item.get("code"), str):
            rows.append({"code": item["code"], "reason": item.get("reason")})
    return rows


def _empty_breadth(result: Mapping[str, Any] | None) -> dict:
    trade_date = result.get("trade_date") if isinstance(result, Mapping) else None
    breadth = {"as_of": trade_date}
    for key in _BREADTH_KEYS:
        breadth[key] = None
    return breadth


def build_anchors(prep: Mapping[str, Any]) -> dict:
    """投影三锚；review 不可用时保留空槽位，不补 0 / 中性。"""

    result = prep.get("result") if isinstance(prep, Mapping) else None
    result = result if isinstance(result, Mapping) else {}
    review = _as_dict(result.get("review"))
    review_available = bool(prep.get("available")) and bool(
        prep.get("review_available")) and bool(review)

    short_term = {"mainline_leaders": [], "positive": [], "negative": []}
    trend = {"index": None, "index_missing_reason": "not_in_snapshot",
             "mainstay": [], "capacity": []}
    sentiment = {"positive": [], "negative": [],
                 "breadth": _empty_breadth(result if result else None)}

    if review_available:
        role_codes: set[str] = set()
        for mainline in _as_list(review.get("mainlines")):
            if not isinstance(mainline, Mapping):
                continue
            board = mainline.get("board_name")
            role_map = _as_dict(mainline.get("role_map"))
            for role in ("leader", "vanguard"):
                code = role_map.get(role)
                if not isinstance(code, str) or not code or code in role_codes:
                    continue
                role_codes.add(code)
                short_term["mainline_leaders"].append(
                    {"board_name": board, "code": code, "role": role})

        for mainline in _as_list(review.get("mainlines")):
            if not isinstance(mainline, Mapping):
                continue
            board = mainline.get("board_name")
            role_map = _as_dict(mainline.get("role_map"))
            for role, key in (("mainstay", "mainstay"),
                              ("capacity", "capacity")):
                code = role_map.get(role)
                if not isinstance(code, str) or not code or code in role_codes:
                    continue
                role_codes.add(code)
                trend[key].append({"board_name": board, "code": code})

        anchors = _as_dict(review.get("anchors"))
        short_term["positive"] = _anchor_rows(anchors.get("positive"))
        short_term["negative"] = _anchor_rows(anchors.get("negative"))
        sentiment["positive"] = _anchor_rows(anchors.get("positive"))
        sentiment["negative"] = _anchor_rows(anchors.get("negative"))

        source = _as_dict(result.get("sentiment"))
        breadth = {"as_of": result.get("trade_date")}
        for key in _BREADTH_KEYS:
            breadth[key] = source.get(key)
        sentiment["breadth"] = breadth

    return {
        "method": ANCHOR_METHOD,
        "scope": ANCHOR_SCOPE,
        "dedupe": ANCHOR_DEDUPE,
        "short_term": short_term,
        "trend": trend,
        "sentiment": sentiment,
    }


__all__ = [
    "ANCHOR_DEDUPE",
    "ANCHOR_METHOD",
    "ANCHOR_SCOPE",
    "build_anchors",
]
