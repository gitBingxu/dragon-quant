"""auction.events — 事件检测器链（责任链，收集全部证据，不短路）。

硬约束（方案 §7.3 / §9.5 / §11.1）：
  - 链按固定顺序执行，收集全部证据，``veto`` 是事件属性而非中断；
  - 只有 09:20–09:25 核心窗口或静态价格事实的事件才允许 ``veto=True``
    （``limit_down_queue_sell`` / ``high_position_green_increasing`` / ``strong_to_weak``）；
  - ``cancel_spoof_pre20`` / ``early_cancel_estimate`` 等 pre-only 证据一律
    ``veto=False``，不得进入 ``is_weak`` / ``strength`` / ``rank``；
  - 同一事件只有一个 ``owner_dim``，只有 owner 维度可以消费它。
"""

from __future__ import annotations

from typing import Optional

from . import registry as R
from .types import RiskEvent

VETO_REASONS = {
    "limit_down_queue_sell": "跌停价排队卖",
    "high_position_green_increasing": "高位绿柱递增",
    "strong_to_weak": "强转弱",
}
_VETO_CODES = frozenset(VETO_REASONS)


def _fmt(seconds: Optional[int]) -> Optional[str]:
    if seconds is None:
        return None
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _window(norm: dict, first_idx, last_idx) -> list:
    times = norm["times"]
    if first_idx is None or last_idx is None:
        return []
    return [_fmt(times[first_idx]), _fmt(times[last_idx])]


def _max_abs_negative(values) -> float:
    negatives = [abs(v) for v in values if v is not None and v < 0]
    return max(negatives) if negatives else 0.0


def _event(code: str, event_code: str, *, family: str, severity: str,
           veto: bool, window: list, owner: Optional[str],
           evidence: dict, time_seconds: Optional[int]) -> RiskEvent:
    stamp = _fmt(time_seconds) or (window[-1] if window else "")
    return RiskEvent(
        code=code,
        event_id=f"{event_code}@{stamp}" if stamp else event_code,
        family=family,
        severity=severity,
        veto=bool(veto),
        window=list(window),
        owner_dim=owner,
        evidence=dict(evidence),
    )


def detect(code: str, norm: dict, feats: dict, quote=None) -> list[RiskEvent]:
    """按固定链顺序检测事件；返回按 ``event_id`` 去重后的事件列表。"""
    raw = feats["raw"]
    flags = feats["flags"]
    times, prices, matched, unmatched = (norm["times"], norm["prices"],
                                         norm["matched"], norm["unmatched"])
    core_idx, pre_idx, tail_idx = norm["core_idx"], norm["pre_idx"], norm["tail_idx"]
    last_idx = norm["last_idx"]
    at20_idx = norm["at20_idx"]
    limit_down = getattr(quote, "limit_down", 0.0) if quote is not None else 0.0
    limit_down = float(limit_down or 0.0)

    events: list[RiskEvent] = []

    # 1. limit_down_queue_sell（veto，静态价格事实 / 核心窗口）
    tail_prices = [prices[i] for i in tail_idx]
    tail_neg = _max_abs_negative(unmatched[i] for i in tail_idx)
    front_neg = _max_abs_negative(unmatched[i] for i in norm["front_idx"])
    near_limit = bool(limit_down > 0 and tail_prices
                      and min(tail_prices) <= limit_down * (1 + R.PRICE_TOLERANCE))
    extreme_sell = bool(tail_neg > 0 and front_neg > 0
                        and tail_neg > front_neg * R.REJECT_TAIL_NEG_RATIO)
    if near_limit or extreme_sell:
        events.append(_event(
            code, "limit_down_queue_sell", family="risk", severity="high", veto=True,
            window=_window(norm, tail_idx[0] if tail_idx else None,
                           last_idx),
            owner="supply",
            evidence={"near_limit": near_limit, "extreme_sell": extreme_sell,
                      "tail_sell_max": round(tail_neg, 2),
                      "front_sell_max": round(front_neg, 2),
                      "limit_down": limit_down or None},
            time_seconds=times[last_idx] if last_idx is not None else None,
        ))

    # 2. high_position_green_increasing（veto，核心窗口）
    gap = raw.get("effective_open_gap_pct")
    neg_series = [abs(unmatched[i]) for i in tail_idx
                  if unmatched[i] is not None and unmatched[i] < 0]
    monotonic = (len(neg_series) >= R.HIGH_GREEN_MIN_BARS
                 and all(neg_series[i] >= neg_series[i - 1]
                         for i in range(1, len(neg_series))))
    if gap is not None and gap > R.REJECT_HIGH_PCT and monotonic:
        events.append(_event(
            code, "high_position_green_increasing", family="risk", severity="high",
            veto=True, window=_window(norm, tail_idx[0] if tail_idx else None, last_idx),
            owner="price",
            evidence={"effective_open_gap_pct": gap, "green_bars": len(neg_series)},
            time_seconds=times[last_idx] if last_idx is not None else None,
        ))

    # 3. strong_to_weak（veto，pre+tail）
    if flags.get("strong_to_weak"):
        events.append(_event(
            code, "strong_to_weak", family="risk", severity="high", veto=True,
            window=_window(norm, pre_idx[0] if pre_idx else None,
                           last_idx),
            owner="supply",
            evidence={"pre_median": round(feats["derived"]["pre_unmatched_median"] or 0.0, 2),
                      "tail_median": round(feats["derived"]["tail_unmatched_median"] or 0.0, 2),
                      "last_matched_lots": raw.get("last_matched_lots")},
            time_seconds=times[last_idx] if last_idx is not None else None,
        ))

    # 4. weak_to_strong（conversion，owner=supply，不 veto）
    if flags.get("weak_to_strong"):
        events.append(_event(
            code, "weak_to_strong", family="conversion", severity="medium", veto=False,
            window=_window(norm, pre_idx[0] if pre_idx else None, last_idx),
            owner="supply",
            evidence={"pre_median": round(feats["derived"]["pre_unmatched_median"] or 0.0, 2),
                      "tail_median": round(feats["derived"]["tail_unmatched_median"] or 0.0, 2)},
            time_seconds=times[last_idx] if last_idx is not None else None,
        ))

    # 5. sell_pressure_acceleration（不 veto，owner=supply）
    expansion_ratio = (tail_neg / front_neg) if front_neg > 0 else None
    tail_delta = raw.get("tail_matched_delta_lots")
    if (expansion_ratio is not None and expansion_ratio >= R.SELL_PRESSURE_RATIO
            and tail_delta is not None and tail_delta > 0):
        events.append(_event(
            code, "sell_pressure_acceleration", family="risk", severity="medium",
            veto=False, window=_window(norm, tail_idx[0] if tail_idx else None, last_idx),
            owner="supply",
            evidence={"expansion_ratio": round(expansion_ratio, 2),
                      "tail_matched_delta_lots": tail_delta},
            time_seconds=times[last_idx] if last_idx is not None else None,
        ))

    # 6. volume_price_divergence（不 veto，owner=vol_price）
    if raw.get("volume_price_divergence") is True:
        events.append(_event(
            code, "volume_price_divergence", family="risk", severity="medium",
            veto=False, window=_window(norm, at20_idx, last_idx), owner="vol_price",
            evidence={"tail_matched_acceleration": raw.get("tail_matched_acceleration"),
                      "tail_price_return_pct": raw.get("tail_price_return_pct")},
            time_seconds=times[last_idx] if last_idx is not None else None,
        ))

    # 7. cancel_spoof_pre20（仅证据，永不 veto，owner=None）
    pre_buy = [unmatched[i] for i in pre_idx if unmatched[i] is not None and unmatched[i] > 0]
    core_buy = [unmatched[i] for i in core_idx if unmatched[i] is not None and unmatched[i] > 0]
    peak = max(pre_buy) if pre_buy else 0.0
    if peak > 0:
        post_peak = max(core_buy) if core_buy else 0.0
        peak_price = prices[pre_idx[-1]] if pre_idx else None
        last_price = prices[last_idx] if last_idx is not None else None
        price_held = (last_price is not None and peak_price is not None
                      and last_price >= peak_price * 0.99)
        if post_peak < peak * R.CANCEL_SPOOF_DROP_RATIO and price_held:
            events.append(_event(
                code, "cancel_spoof_pre20", family="context", severity="medium",
                veto=False,
                window=_window(norm, pre_idx[0] if pre_idx else None,
                               pre_idx[-1] if pre_idx else None),
                owner=None,
                evidence={"pre_peak_buy": round(peak, 2),
                          "post20_peak_buy": round(post_peak, 2),
                          "drop_ratio": round(post_peak / peak, 3)},
                time_seconds=times[pre_idx[-1]] if pre_idx else None,
            ))

    # 8. tail_matched_spike（owner=tail_grab，不 veto）
    accel = raw.get("tail_matched_acceleration")
    if accel is not None and accel >= R.VOL_PRICE_ACCEL_RATIO:
        events.append(_event(
            code, "tail_matched_spike", family="context", severity="info", veto=False,
            window=_window(norm, tail_idx[0] if tail_idx else None, last_idx),
            owner="tail_grab", evidence={"tail_matched_acceleration": accel},
            time_seconds=times[last_idx] if last_idx is not None else None,
        ))

    # 9. post20_matched_decrease（family=data，不 veto）
    if any(matched[i] < matched[i - 1] for i in core_idx[1:]):
        events.append(_event(
            code, "post20_matched_decrease", family="data", severity="warning",
            veto=False, window=_window(norm, at20_idx, last_idx), owner=None,
            evidence={"note": "09:20 后匹配量下降（数据完整性异常）"},
            time_seconds=times[last_idx] if last_idx is not None else None,
        ))

    # 10-12. 同价位转换估计（仅证据）
    for position in range(1, len(core_idx)):
        i, j = core_idx[position - 1], core_idx[position]
        base = prices[i]
        if base <= 0:
            continue
        if abs(prices[j] - base) > base * R.PRICE_TOLERANCE:
            events.append(_event(
                code, "queue_hidden_or_repriced", family="context", severity="info",
                veto=False, window=[_fmt(times[i]), _fmt(times[j])], owner=None,
                evidence={"from_price": prices[i], "to_price": prices[j]},
                time_seconds=times[j]))
            continue
        matched_up = matched[j] > matched[i]
        if not matched_up:
            continue
        if unmatched[i] < 0 and unmatched[j] < 0 and abs(unmatched[j]) < abs(unmatched[i]):
            events.append(_event(
                code, "sell_absorption_estimate", family="absorption", severity="info",
                veto=False, window=[_fmt(times[i]), _fmt(times[j])], owner=None,
                evidence={"sell_queue_before": unmatched[i], "sell_queue_after": unmatched[j],
                          "matched_delta": matched[j] - matched[i]},
                time_seconds=times[j]))
        if unmatched[i] > 0 and unmatched[j] > 0 and unmatched[j] < unmatched[i]:
            events.append(_event(
                code, "buy_queue_hit_estimate", family="absorption", severity="info",
                veto=False, window=[_fmt(times[i]), _fmt(times[j])], owner=None,
                evidence={"buy_queue_before": unmatched[i], "buy_queue_after": unmatched[j],
                          "matched_delta": matched[j] - matched[i]},
                time_seconds=times[j]))

    # 13. early_cancel_estimate（pre-only，仅证据）
    if raw.get("pre20_cancel_estimate") is not None and raw["pre20_cancel_estimate"] > 0:
        events.append(_event(
            code, "early_cancel_estimate", family="context", severity="info",
            veto=False, window=_window(norm, pre_idx[0] if pre_idx else None,
                                       pre_idx[-1] if pre_idx else None),
            owner=None, evidence={"cancel_lots": raw["pre20_cancel_estimate"]},
            time_seconds=times[pre_idx[-1]] if pre_idx else None))

    # 去重：同一 event_code 只保留首个
    seen, unique = set(), []
    for event in events:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        unique.append(event)
    return unique


def veto_events(events) -> list[RiskEvent]:
    return [event for event in events if event.veto]


def first_veto_reason(events) -> Optional[str]:
    for event in events:
        if event.veto:
            return VETO_REASONS.get(event.event_id.split("@")[0], "竞价风险事件")
    return None


def validate_veto_windows(events, core_start: str = "09:20:00") -> list[str]:
    """返回违规事件 id：证据窗口完全落在 09:20 前的 veto 事件（必须为空）。"""
    offenders = []
    for event in events:
        if not event.veto:
            continue
        window = event.window or []
        if not window or all(str(point) < core_start for point in window if point):
            offenders.append(event.event_id)
    return offenders
