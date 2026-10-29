"""auction.dimensions — 五维打分（Strategy 权重 / 分桶，纯函数）。

维度键名保留旧名（``direction`` / ``liquidity`` / ``supply`` / ``vol_price`` /
``tail_grab``），语义见方案 §9。维内子项缺失时按剩余权重重算并标 ``degraded``；
整维不可算时 ``score=None``，绝不写 0 / 50。
"""

from __future__ import annotations

import math

from . import registry as R
from .types import DimensionScore


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def upper_score(value, buckets) -> float:
    """上界分桶：返回第一个 ``value <= upper`` 的分数。"""
    if value is None:
        return None
    for upper, score in buckets:
        if value <= upper:
            return float(score)
    return float(buckets[-1][1])


def lower_score(value, buckets) -> float:
    """下界分桶：返回最后一个 ``value >= lower`` 的分数。"""
    if value is None:
        return None
    result = float(buckets[0][1])
    for lower, score in buckets:
        if value >= lower:
            result = float(score)
    return result


def _combine(subs: dict, weights: dict):
    """按可用子项权重重算；返回 ``(score, sub_missing)``。"""
    available = {name: subs[name] for name in subs if subs[name] is not None}
    missing = [name for name in subs if subs[name] is None]
    if not available:
        return None, missing
    total = sum(weights[name] for name in available)
    if total <= 0:
        return None, missing
    score = sum(weights[name] / total * available[name] for name in available)
    return round(_clip(score), 2), missing


def _dim(name, score, missing, details) -> DimensionScore:
    status = "scored" if score is not None else "missing"
    if score is not None and missing:
        status = "degraded"
    return DimensionScore(
        dim=name, label=R.DIM_LABELS[name], score=score,
        weight=R.WEIGHTS[name], effective_weight=None, status=status,
        details={**details, "sub_missing": missing},
    )


def _direction(raw):
    open_score = upper_score(raw.get("effective_open_gap_pct"),
                             R.BUCKETS["upper"]["open_gap"])
    post20 = upper_score(raw.get("post20_price_return_pct"),
                         R.BUCKETS["upper"]["post20_return"])
    slippage = upper_score(raw.get("final_indicative_slippage_pct"),
                           R.BUCKETS["upper"]["slippage"])
    score, missing = _combine(
        {"open": open_score, "post20": post20, "slippage": slippage},
        R.DIRECTION_SUB_WEIGHTS)
    return _dim("direction", score, missing,
                {"open": open_score, "post20": post20, "slippage": slippage})


def _liquidity(raw):
    turnover = lower_score(raw.get("auction_turnover_pct"),
                           R.BUCKETS["lower"]["turnover"])
    amount = raw.get("auction_amount_estimated")
    amount_score = None
    if amount is not None and amount > 0:
        amount_score = lower_score(math.log10(amount),
                                   R.BUCKETS["lower"]["amount_log10"])
    score, missing = _combine({"turnover": turnover, "amount": amount_score},
                              R.LIQUIDITY_SUB_WEIGHTS)
    return _dim("liquidity", score, missing,
                {"turnover": turnover, "amount": amount_score})


def _supply(raw, flags, events):
    ratio = upper_score(raw.get("final_unmatched_matched_ratio"),
                        R.BUCKETS["upper"]["supply_ratio"])
    change = None
    change_ratio = None
    last_matched = raw.get("last_matched_lots")
    delta = raw.get("post20_unmatched_change")
    if (delta is not None and last_matched is not None
            and last_matched >= R.MIN_MATCHED_LOTS_FOR_RATIO and last_matched > 0):
        change_ratio = delta / last_matched
        change = _clip(50.0 + 100.0 * change_ratio)
    weak_to_strong = flags.get("weak_to_strong")
    strong_to_weak = flags.get("strong_to_weak")
    if weak_to_strong is None and strong_to_weak is None:
        conversion = None
    elif weak_to_strong:
        conversion = 100.0
    elif strong_to_weak:
        conversion = 0.0
    else:
        conversion = 50.0
    score, missing = _combine(
        {"ratio": ratio, "change": change, "conversion": conversion},
        R.SUPPLY_SUB_WEIGHTS)
    capped = False
    if score is not None and any(
            event.event_id.split("@")[0] == "sell_pressure_acceleration"
            for event in events):
        score = min(score, R.SUPPLY_SELL_PRESSURE_CAP)
        capped = True
    return _dim("supply", score, missing,
                {"ratio": ratio, "change": change, "change_ratio": change_ratio,
                 "conversion": conversion, "sell_pressure_capped": capped})


def _vol_price(raw):
    agree_ratio = raw.get("vol_price_agree_ratio")
    agree = None
    if agree_ratio is not None:
        agree = 100.0 * agree_ratio if agree_ratio >= 0.6 else 50.0 * agree_ratio
    divergence = raw.get("volume_price_divergence")
    divergence_score = None if divergence is None else (0.0 if divergence else 100.0)
    score, missing = _combine({"agree": agree, "divergence": divergence_score},
                              R.VOL_PRICE_SUB_WEIGHTS)
    return _dim("vol_price", score, missing,
                {"agree": agree, "divergence": divergence_score})


def _tail(raw):
    accel = raw.get("tail_matched_acceleration")
    accel_score = None
    if accel is not None and accel > 0:
        accel_score = _clip(50.0 + 25.0 * math.log2(accel))
    ret = upper_score(raw.get("tail_price_return_pct"),
                      R.BUCKETS["upper"]["post20_return"])
    change = raw.get("tail_unmatched_change")
    if change is None:
        unmatched_score = None
    elif change > 0:
        unmatched_score = 100.0
    elif change == 0:
        unmatched_score = 50.0
    else:
        unmatched_score = 0.0
    score, missing = _combine(
        {"accel": accel_score, "return": ret, "unmatched": unmatched_score},
        R.TAIL_SUB_WEIGHTS)
    return _dim("tail_grab", score, missing,
                {"accel": accel_score, "return": ret, "unmatched": unmatched_score})


def score_dimensions(raw: dict, flags: dict, events: list) -> dict[str, DimensionScore]:
    return {
        "direction": _direction(raw),
        "liquidity": _liquidity(raw),
        "supply": _supply(raw, flags, events),
        "vol_price": _vol_price(raw),
        "tail_grab": _tail(raw),
    }
