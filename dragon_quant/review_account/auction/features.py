"""auction.features — 原始因子提取（纯函数，无 I/O）。

公式 / 单位 / 窗口 / 缺失语义见方案 §8。约定：
  - 手 = ``lots``，元 = ``yuan``，百分比字段以 ``_pct`` 结尾；
  - 任何缺失都返回 ``None``（绝不写 0/50/中性分）；
  - 分母保护：末点匹配量 < ``MIN_MATCHED_LOTS_FOR_RATIO`` 时，所有以匹配量
    为分母的比率一律 ``None`` 并记录 ``low_matched_base``。
"""

from __future__ import annotations

from statistics import median
from typing import Optional

from . import registry as R
from .normalize import matched_deltas, market_of

PIT_QUOTE_FIELDS = (
    "prev_close", "open_px", "limit_up", "limit_down", "float_market_cap", "price",
)


def _finite(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _pct(numerator, denominator) -> Optional[float]:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return (numerator / denominator - 1.0) * 100.0


def _round(value, digits=4):
    return None if value is None else round(float(value), digits)


def _sign(value) -> int:
    if value is None or value == 0:
        return 0
    return 1 if value > 0 else -1


def _fmt_time(seconds: Optional[int]) -> Optional[str]:
    if seconds is None:
        return None
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _median(values) -> Optional[float]:
    values = [v for v in values if v is not None]
    return float(median(values)) if values else None


def compute_features(norm: dict, quote=None, history=None, code: str = "") -> dict:
    """返回 ``raw`` 原始因子、``flags``、``reasons`` 与派生中间量。"""
    reasons: list[dict] = []
    raw: dict = {}
    derived: dict = {}

    def reason(code: str, severity: str, field_name: Optional[str] = None):
        item = {"code": code, "severity": severity, "field": field_name}
        if item not in reasons:
            reasons.append(item)

    last_idx = norm["last_idx"]
    at20_idx = norm["at20_idx"]
    tail_start_idx = norm["tail_start_idx"]
    prices, matched, unmatched, times = (norm["prices"], norm["matched"],
                                         norm["unmatched"], norm["times"])

    prev_close = _finite(getattr(quote, "prev_close", None)) if quote is not None else None
    if prev_close is not None and prev_close <= 0:
        prev_close = None
    quote_price = _finite(getattr(quote, "price", None)) if quote is not None else None
    open_px = _finite(getattr(quote, "open_px", None)) if quote is not None else None
    if open_px is not None and open_px <= 0:
        open_px = None
    limit_down = _finite(getattr(quote, "limit_down", None)) if quote is not None else None
    float_cap = _finite(getattr(quote, "float_market_cap", None)) if quote is not None else None

    last_price = _finite(prices[last_idx]) if last_idx is not None else None
    if last_price is not None and last_price <= 0:
        last_price = None
    last_matched = _finite(matched[last_idx]) if last_idx is not None else None
    last_unmatched = _finite(unmatched[last_idx]) if last_idx is not None else None

    effective_open = open_px if open_px is not None else last_price
    effective_open_source = "official" if open_px is not None else (
        "last_indicative" if last_price is not None else None)

    raw["official_open_px"] = _round(open_px)
    raw["last_indicative_price"] = _round(last_price)
    raw["official_open_gap_pct"] = _round(_pct(open_px, prev_close))
    raw["last_indicative_gap_pct"] = _round(_pct(last_price, prev_close))
    raw["effective_open_gap_pct"] = _round(_pct(effective_open, prev_close))
    raw["effective_open_source"] = effective_open_source
    raw["final_indicative_slippage_pct"] = _round(
        _pct(open_px, last_price) if open_px is not None and last_price is not None
        else None)

    at20_price = _finite(prices[at20_idx]) if at20_idx is not None else None
    raw["post20_price_return_pct"] = _round(_pct(last_price, at20_price))

    tail_start_price = _finite(prices[tail_start_idx]) if tail_start_idx is not None else None
    raw["tail_price_return_pct"] = _round(_pct(last_price, tail_start_price))

    core_prices = [prices[i] for i in norm["core_idx"]]
    if len(core_prices) >= 2:
        peak = core_prices[0]
        worst = 0.0
        for price in core_prices[1:]:
            peak = max(peak, price)
            if peak > 0:
                worst = min(worst, (price / peak - 1.0) * 100.0)
        raw["post20_max_drawdown_pct"] = _round(worst)
    else:
        raw["post20_max_drawdown_pct"] = None

    if len(core_prices) >= 2:
        flat = sum(1 for i in range(1, len(core_prices))
                   if abs(core_prices[i] - core_prices[i - 1])
                   <= core_prices[i - 1] * R.PRICE_TOLERANCE)
        raw["price_flat_ratio"] = _round(flat / (len(core_prices) - 1))
        raw["unique_price_ratio"] = _round(
            len({round(p, 6) for p in core_prices}) / len(core_prices))
    else:
        raw["price_flat_ratio"] = None
        raw["unique_price_ratio"] = None

    # ── 匹配量与流动性 ──
    raw["last_matched_lots"] = _round(last_matched)
    matched_at_0920 = _finite(matched[at20_idx]) if at20_idx is not None else None
    raw["matched_at_0920_lots"] = _round(matched_at_0920)
    post20_delta = (last_matched - matched_at_0920
                    if last_matched is not None and matched_at_0920 is not None else None)
    raw["post20_matched_delta_lots"] = _round(post20_delta)

    tail_start_matched = _finite(matched[tail_start_idx]) if tail_start_idx is not None else None
    tail_delta = (last_matched - tail_start_matched
                  if last_matched is not None and tail_start_matched is not None else None)
    raw["tail_matched_delta_lots"] = _round(tail_delta)

    if (last_matched is not None and matched_at_0920 is not None
            and last_idx is not None and at20_idx is not None):
        span = max(times[last_idx] - times[at20_idx], 1)
        raw["post20_matched_rate"] = _round((last_matched - matched_at_0920) / span, 4)
    else:
        raw["post20_matched_rate"] = None

    tail_rate = front_rate = None
    if (last_matched is not None and tail_start_matched is not None
            and last_idx is not None and tail_start_idx is not None):
        tail_span = max(times[last_idx] - times[tail_start_idx], 1)
        tail_rate = (last_matched - tail_start_matched) / tail_span
    if (tail_start_matched is not None and matched_at_0920 is not None
            and tail_start_idx is not None and at20_idx is not None):
        front_span = max(times[tail_start_idx] - times[at20_idx], 1)
        front_rate = (tail_start_matched - matched_at_0920) / front_span
    raw["tail_matched_acceleration"] = _round(
        tail_rate / front_rate if tail_rate is not None and front_rate is not None
        and front_rate > 0 else None)
    derived["tail_rate"] = tail_rate
    derived["front_rate"] = front_rate

    amount = None
    amount_source = None
    if effective_open is not None and last_matched is not None:
        amount = effective_open * last_matched * 100.0
        amount_source = "official_open" if open_px is not None else "last_indicative"
    raw["auction_amount_estimated"] = _round(amount, 2)
    raw["amount_source"] = amount_source
    raw["amount_estimated"] = amount is not None
    raw["last_time"] = _fmt_time(times[last_idx]) if last_idx is not None else None

    float_shares = None
    if float_cap is not None and quote_price is not None and quote_price > 0:
        float_shares = float_cap / quote_price
    raw["float_shares"] = _round(float_shares, 4)
    raw["float_shares_source"] = (
        "quote.float_market_cap/quote.price" if float_shares is not None else None)
    if float_shares is not None and last_matched is not None and last_matched > 0:
        raw["auction_turnover_pct"] = _round(last_matched * 100.0 / float_shares * 100.0)
    else:
        raw["auction_turnover_pct"] = None

    # ── 历史量比 ──
    history = history or {}
    prior = [v for v in (history.get("last_matched_lots") or [])
             if _finite(v) is not None and _finite(v) > 0]
    prior = [float(v) for v in prior][: R.AUCTION_PROFILE["history"]["max_days"]]
    history_n = len(prior)
    raw["volume_ratio_history_n"] = history_n
    mean_prior = sum(prior) / history_n if history_n else None
    low_base = last_matched is not None and last_matched < R.MIN_MATCHED_LOTS_FOR_RATIO
    if low_base:
        reason("low_matched_base", "warning", "raw_factors")

    volume_ratio_partial = None
    volume_ratio_5d = None
    if last_matched is not None and last_matched > 0 and mean_prior is not None \
            and mean_prior > 0 and not low_base:
        if history_n < R.AUCTION_PROFILE["history"]["target_days"]:
            volume_ratio_partial = last_matched / mean_prior
        elif history_n == R.AUCTION_PROFILE["history"]["target_days"]:
            volume_ratio_5d = last_matched / mean_prior
    raw["volume_ratio_partial"] = _round(volume_ratio_partial)
    raw["volume_ratio_5d"] = _round(volume_ratio_5d)
    if history_n < R.AUCTION_PROFILE["history"]["target_days"]:
        reason("history_insufficient", "warning", "volume_ratio_5d")

    market = market_of(code or getattr(quote, "code", ""))
    interval = R.MARKET_SAMPLE_SECONDS.get(market)
    actual_core_bars = len(norm["core_idx"])
    if interval:
        expected = 300.0 / interval
        raw["event_density_ratio"] = _round(actual_core_bars / expected, 4)
    else:
        raw["event_density_ratio"] = None
        reason("market_unknown", "warning", "market")

    # ── 未匹配与转换 ──
    raw["final_unmatched_lots_signed"] = _round(last_unmatched)
    unmatched_at_0920 = _finite(unmatched[at20_idx]) if at20_idx is not None else None
    post20_unmatched_change = (last_unmatched - unmatched_at_0920
                               if last_unmatched is not None
                               and unmatched_at_0920 is not None else None)
    raw["post20_unmatched_change"] = _round(post20_unmatched_change)

    tail_start_unmatched = _finite(unmatched[tail_start_idx]) if tail_start_idx is not None else None
    raw["tail_unmatched_change"] = _round(
        last_unmatched - tail_start_unmatched
        if last_unmatched is not None and tail_start_unmatched is not None else None)

    ratio_valid = (last_matched is not None and last_matched >= R.MIN_MATCHED_LOTS_FOR_RATIO
                   and last_unmatched is not None)
    raw["final_unmatched_matched_ratio"] = _round(
        last_unmatched / last_matched if ratio_valid else None)
    derived["post20_unmatched_change_ratio"] = (
        post20_unmatched_change / last_matched
        if ratio_valid and post20_unmatched_change is not None else None)

    pre_idx = norm["pre_idx"]
    if len(pre_idx) >= 2:
        cancel = 0.0
        for position in range(1, len(pre_idx)):
            i, j = pre_idx[position - 1], pre_idx[position]
            decline = max(0.0, unmatched[i] - unmatched[j])
            matched_delta = matched[j] - matched[i]
            if unmatched[i] > 0 and matched_delta <= 0:
                cancel += decline
        raw["pre20_cancel_estimate"] = _round(cancel)
    else:
        raw["pre20_cancel_estimate"] = None

    pre_median = _median(unmatched[i] for i in pre_idx)
    tail_median = _median(unmatched[i] for i in norm["tail_idx"])
    derived["pre_unmatched_median"] = pre_median
    derived["tail_unmatched_median"] = tail_median

    weak_to_strong = strong_to_weak = None
    if (len(pre_idx) >= R.CONVERSION_MIN_BARS
            and len(norm["tail_idx"]) >= R.CONVERSION_MIN_BARS
            and pre_median is not None and tail_median is not None
            and last_matched is not None and last_matched > 0):
        magnitude = abs(tail_median - pre_median)
        enough = magnitude >= R.CONVERSION_MIN_RATIO * last_matched
        weak_to_strong = bool(pre_median < 0 < tail_median and enough)
        strong_to_weak = bool(pre_median > 0 > tail_median and enough)
    raw["weak_to_strong"] = weak_to_strong
    raw["strong_to_weak"] = strong_to_weak

    # ── 量价协同 ──
    core_idx = norm["core_idx"]
    agree = total = 0
    for position in range(1, len(core_idx)):
        i, j = core_idx[position - 1], core_idx[position]
        dp = prices[j] - prices[i]
        span = max(times[j] - times[i], 1)
        dmr = (matched[j] - matched[i]) / span
        if dp == 0 or dmr == 0:
            continue
        total += 1
        if _sign(dp) == _sign(dmr):
            agree += 1
    raw["vol_price_agree_ratio"] = _round(agree / total) if total else None

    tail_return = raw.get("tail_price_return_pct")
    tail_accel = raw.get("tail_matched_acceleration")
    if tail_accel is None or tail_return is None:
        raw["volume_price_divergence"] = None
    else:
        raw["volume_price_divergence"] = bool(
            tail_accel >= R.VOL_PRICE_ACCEL_RATIO and tail_return <= 0)

    if tail_delta is not None and (weak_to_strong or strong_to_weak):
        direction = 1.0 if weak_to_strong else -1.0
        raw["tail_absorption_strength"] = _round(tail_delta * direction, 2)
    else:
        raw["tail_absorption_strength"] = None

    # ── 数据质量原因 ──
    if quote is None:
        reason("quote_missing", "degraded", "quote")
    if open_px is None:
        reason("official_open_missing", "degraded", "quote.open_px")
    if float_shares is None:
        reason("float_cap_missing", "degraded", "quote.float_market_cap")
    if limit_down is None or limit_down <= 0:
        reason("limit_down_missing", "warning", "quote.limit_down")
    if norm["tail_extended"]:
        reason("tail_window_short", "warning", "tail")
    if norm["truncated_after_close"]:
        reason("tail_after_close", "warning", "items")
    if any(matched[i] < matched[i - 1] for i in core_idx[1:]):
        reason("post20_matched_decrease", "warning", "items.matched")

    quote_fields_used = []
    for name in PIT_QUOTE_FIELDS:
        if quote is None:
            break
        value = getattr(quote, name, None)
        if value is None:
            continue
        if name in ("prev_close", "open_px") and (_finite(value) or 0) <= 0:
            continue
        quote_fields_used.append(name)

    raw = {key: value for key, value in raw.items()}   # 固定构造顺序
    return {
        "raw": raw,
        "flags": {"weak_to_strong": weak_to_strong,
                  "strong_to_weak": strong_to_weak},
        "reasons": reasons,
        "derived": derived,
        "float_shares": float_shares,
        "quote_fields_used": quote_fields_used,
        "market": market,
        "low_base": low_base,
    }
