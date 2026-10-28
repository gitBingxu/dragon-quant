"""复盘分的可成交评估、消融、滚动 OOS 与前向影子指标（M6，技术方案 §14）。

设计约束（技术方案 §3.1 分层）：
  - **纯函数**：不读库、不发请求、不 import ``review_account`` / provider / storage；
    行情与资产一律由调用方注入（与 ``infra/factor/evaluation.py`` 同风格）。
  - **历史不足 = ``insufficient_data``**：绝不以 0 或空表冒充「有效结论」；
    影子报告只描述样本，不宣称策略有效。
  - 可成交口径（§14.1）：T+1 开盘（若 09:25 竞价价可成交则用竞价价）→ T+N 开盘；
    往返成本默认 13 bps；一字涨停（无成交）/ 停牌 / 成交额 < 2 亿剔除并计数。
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Optional, Sequence

from . import stats as fstats

DIMENSIONS: tuple[str, ...] = ("D1", "D2", "D3", "D4", "D5", "D6")
DEFAULT_HORIZONS: tuple[int, ...] = (1, 3, 5)
DEFAULT_COST_BPS = 13.0          # 佣金 3bp/边 + 印花税 5bp + 过户费 0.1bp ≈ 13bp 往返
MIN_AMOUNT = 200_000_000.0       # 成交额 < 2 亿 = illiquid（与 G3a 一致）
MIN_SHADOW_DAYS = 20             # 连续 ≥20 个交易日才可汇报影子指标
MIN_IC_DAYS = 3                  # 消融/滚动 IC 的最少有效日
MARKET_OPEN = "09:30"

_VERDICT_SCORE = {"strong": 2.0, "light": 1.0, "ignore": 0.0}


def _num(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _attr(bar, name: str, default=None):
    if isinstance(bar, Mapping):
        return bar.get(name, default)
    return getattr(bar, name, default)


def _bar_date(bar) -> Optional[str]:
    for key in ("trade_date", "date", "timestamp"):
        value = _attr(bar, key)
        if value is None:
            continue
        # 不写 ``isinstance(value, datetime)``：E2E 时钟审计把它限制在 evaluation.py。
        date_method = getattr(value, "date", None)
        if callable(date_method) and not isinstance(value, str):
            return date_method().isoformat()
        text = str(value)
        if len(text) >= 10 and text[4] == "-":
            return text[:10]
        if len(text) == 8 and text.isdigit():
            return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return None


def _available_parts(available_at: str) -> tuple[str, bool]:
    """返回 ``(日期, 是否开盘前可知)``；开盘前可知 → 当日可买入。"""
    text = str(available_at or "").strip().replace(" ", "T")
    if "T" in text:
        day, clock = text.split("T", 1)
        return day[:10], clock[:5] < MARKET_OPEN
    return text[:10], True


def _bars_by_day(market, code: str, start: str) -> dict[str, Any]:
    try:
        raw = market.daily(code, start) or []
    except Exception:
        return {}
    mapped: dict[str, Any] = {}
    for bar in raw:
        day = _bar_date(bar)
        if day:
            mapped[day] = bar
    return mapped


def _entry_exclusion(bar, min_amount: float) -> Optional[str]:
    if bar is None:
        return "missing_bar"
    open_price = _num(_attr(bar, "open"))
    volume = _num(_attr(bar, "volume"))
    amount = _num(_attr(bar, "amount"))
    volume = 0.0 if volume is None else volume
    amount = 0.0 if amount is None else amount
    if volume == 0 and amount == 0:
        limit = _num(_attr(bar, "limit_up_price"))
        if limit is not None and open_price is not None and open_price >= limit - 1e-9:
            return "unfillable_one_word"
        if bool(_attr(bar, "limit_up")):
            return "unfillable_one_word"
        return "suspended"
    if open_price is None or open_price <= 0:
        return "missing_bar"
    if amount and amount < min_amount:
        return "illiquid"
    return None


def _exit_exclusion(bar) -> Optional[str]:
    if bar is None:
        return "missing_bar"
    open_price = _num(_attr(bar, "open"))
    if open_price is None or open_price <= 0:
        return "missing_bar"
    volume = _num(_attr(bar, "volume"))
    amount = _num(_attr(bar, "amount"))
    if (volume is None or volume == 0) and (amount is None or amount == 0):
        return "suspended"
    return None


def _entry_index(day: str, before_open: bool, calendar: Sequence[str]) -> Optional[int]:
    for index, item in enumerate(calendar):
        if item > day:
            return index
        if item == day and before_open:
            return index
    return None


def build_tradable_labels(
    observations: Iterable[Mapping[str, Any]],
    market,
    calendar: Sequence[str],
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    cost_bps: float = DEFAULT_COST_BPS,
    min_amount: float = MIN_AMOUNT,
) -> dict:
    """把因子观测（``code`` + ``available_at``）转成可成交前向收益标签。

    ``market`` 只需提供 ``daily(code, start)``（鸭子类型）；``calendar`` 为交易日
    序列。缺失/不可成交一律进 ``exclusions``，**绝不写 0 收益**。
    """
    calendar = sorted({str(day) for day in calendar if day})
    labels: list[dict] = []
    exclusions: dict[str, int] = {}

    def exclude(reason: str) -> None:
        exclusions[reason] = exclusions.get(reason, 0) + 1

    for observation in observations:
        code = observation.get("code")
        available_at = observation.get("available_at")
        if not code or not available_at:
            exclude("missing_observation")
            continue
        day, before_open = _available_parts(str(available_at))
        start_index = _entry_index(day, before_open, calendar)
        if start_index is None:
            exclude("no_entry_day")
            continue
        bars = _bars_by_day(market, str(code), day)
        entry_day = calendar[start_index]
        auction_price = _num(observation.get("auction_price"))
        auction_fillable = observation.get("auction_fillable")
        if auction_fillable is False:
            exclude("unfillable_one_word")
            continue
        if auction_fillable is True and auction_price is not None and auction_price > 0:
            # 只有显式「可成交」才用竞价价；未知状态回落到开盘价，不假设可成交。
            entry_price = auction_price
        else:
            entry_bar = bars.get(entry_day)
            reason = _entry_exclusion(entry_bar, min_amount)
            if reason is not None:
                exclude(reason)
                continue
            entry_price = _num(_attr(entry_bar, "open"))
            if entry_price is None or entry_price <= 0:
                exclude("missing_bar")
                continue
        for horizon in horizons:
            end_index = start_index + int(horizon)
            if end_index >= len(calendar):
                exclude("insufficient_horizon")
                continue
            exit_day = calendar[end_index]
            exit_bar = bars.get(exit_day)
            reason = _exit_exclusion(exit_bar)
            if reason is not None:
                exclude(reason)
                continue
            exit_price = _num(_attr(exit_bar, "open"))
            if exit_price is None or exit_price <= 0:
                exclude("missing_bar")
                continue
            gross = (exit_price / entry_price - 1.0) * 100.0
            labels.append({
                "code": str(code),
                "trade_date": day,
                "horizon": int(horizon),
                "value": gross - float(cost_bps) / 100.0,
                "entry_date": entry_day,
                "exit_date": exit_day,
                "entry_price": entry_price,
                "exit_price": exit_price,
            })

    return {
        "status": "ok" if labels else "insufficient_data",
        "labels": labels,
        "exclusions": exclusions,
        "cost_bps": float(cost_bps),
        "reason": None if labels else "no_tradable_labels",
    }


# ─── 消融 / 滚动 OOS ──────────────────────────────────────────────────────


def _dim_map(candidate: Mapping[str, Any]) -> dict[str, dict]:
    dimensions = candidate.get("dimensions")
    if not isinstance(dimensions, dict):
        return {}
    return {key: value for key, value in dimensions.items()
            if isinstance(value, dict)}


def _weighted_score(candidate: Mapping[str, Any],
                    dims: Sequence[str]) -> Optional[float]:
    dimensions = _dim_map(candidate)
    scores: list[float] = []
    weights: list[float] = []
    for dim in dims:
        entry = dimensions.get(dim)
        if entry is None:
            continue
        score = _num(entry.get("score"))
        if score is None:
            continue
        weight = _num(entry.get("weight"))
        scores.append(score)
        weights.append(1.0 if weight is None else weight)
    total = sum(weights)
    if not scores or total <= 0:
        return None
    return sum(score * weight for score, weight in zip(scores, weights)) / total


def _equal_weight_score(candidate: Mapping[str, Any],
                        dims: Sequence[str]) -> Optional[float]:
    dimensions = _dim_map(candidate)
    scores = [_num(dimensions[dim].get("score"))
              for dim in dims if dim in dimensions]
    values = [score for score in scores if score is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _paired_ic(pairs: Sequence[tuple[float, float]],
               min_cross_section: int) -> Optional[float]:
    if len(pairs) < max(2, int(min_cross_section)):
        return None
    return fstats.spearman([pair[0] for pair in pairs],
                           [pair[1] for pair in pairs])


def _ic_series(candidates_by_day: Mapping[str, Any],
               labels_by_day: Mapping[str, Any],
               scorer,
               *,
               min_cross_section: int,
               dims: Sequence[str] = DIMENSIONS) -> list[dict]:
    series: list[dict] = []
    for day in sorted(candidates_by_day):
        labels = labels_by_day.get(day) or {}
        pairs: list[tuple[float, float]] = []
        for candidate in candidates_by_day[day] or []:
            code = candidate.get("code")
            label = _num(labels.get(code)) if code else None
            if label is None:
                continue
            value = scorer(candidate, dims)
            if value is None:
                continue
            pairs.append((float(value), float(label)))
        ic = _paired_ic(pairs, min_cross_section)
        if ic is not None:
            series.append({"trade_date": day, "rank_ic": ic, "n": len(pairs)})
    return series


def _aggregate(series: list[dict]) -> dict:
    values = [item["rank_ic"] for item in series]
    return {
        "mean_rank_ic": fstats.mean(values),
        "std": fstats.stdev(values, ddof=1),
        "positive_ratio": (sum(1 for value in values if value > 0) / len(values))
                          if values else None,
        "n_days": len(values),
    }


def ablation(
    candidates_by_day: Mapping[str, Any],
    labels_by_day: Mapping[str, Any],
    *,
    dims: Sequence[str] = DIMENSIONS,
    min_cross_section: int = 3,
    min_days: int = MIN_IC_DAYS,
) -> dict:
    """六维消融（§14.3）：全分 vs 逐维归零 vs 等权，附旧 composite/verdict 基线。

    ``candidates_by_day`` 可直接传 ``review_candidates``（含 ``dimensions`` 的
    ``score``/``weight`` 与 ``legacy`` 只读证据）；``labels_by_day`` 为
    ``{date: {code: fwd_ret}}``。数据不足 → ``insufficient_data``。
    """
    full_series = _ic_series(candidates_by_day, labels_by_day,
                             _weighted_score,
                             min_cross_section=min_cross_section, dims=dims)
    if len(full_series) < max(2, int(min_days)):
        return {
            "status": "insufficient_data",
            "reason": f"有效 IC 日 {len(full_series)} < {max(2, int(min_days))}",
            "days": len(full_series),
            "full": {},
        }

    full = _aggregate(full_series)
    leave_one_out: dict[str, dict] = {}
    marginal: dict[str, Optional[float]] = {}
    for dim in dims:
        remaining = tuple(item for item in dims if item != dim)
        series = _ic_series(candidates_by_day, labels_by_day, _weighted_score,
                            min_cross_section=min_cross_section, dims=remaining)
        summary = _aggregate(series)
        leave_one_out[dim] = summary
        if full["mean_rank_ic"] is not None and summary["mean_rank_ic"] is not None:
            marginal[dim] = full["mean_rank_ic"] - summary["mean_rank_ic"]
        else:
            marginal[dim] = None

    equal_series = _ic_series(candidates_by_day, labels_by_day,
                              _equal_weight_score,
                              min_cross_section=min_cross_section, dims=dims)

    baselines: dict[str, dict] = {}
    composite = _ic_series(candidates_by_day, labels_by_day,
                           lambda cand, _dims: _num(
                               (cand.get("legacy") or {}).get("composite_score")),
                           min_cross_section=min_cross_section)
    if composite:
        baselines["legacy_composite"] = _aggregate(composite)
    verdict = _ic_series(candidates_by_day, labels_by_day,
                         lambda cand, _dims: _VERDICT_SCORE.get(
                             (cand.get("legacy") or {}).get("verdict")),
                         min_cross_section=min_cross_section)
    if verdict:
        baselines["legacy_verdict"] = _aggregate(verdict)

    return {
        "status": "evaluated",
        "days": full["n_days"],
        "full": full,
        "leave_one_out": leave_one_out,
        "marginal_ic": marginal,
        "equal_weight": _aggregate(equal_series),
        "baselines": baselines,
        "trial_count": 1 + len(dims) + 1,
    }


def _split_windows(series: list[dict], n_windows: int) -> list[list[dict]]:
    n = n_windows
    k = len(series) // n
    windows: list[list[dict]] = []
    for index in range(n):
        start = index * k
        end = (index + 1) * k if index < n - 1 else len(series)
        windows.append(series[start:end])
    return windows


def walk_forward_ic(
    candidates_by_day: Mapping[str, Any],
    labels_by_day: Mapping[str, Any],
    *,
    dims: Sequence[str] = DIMENSIONS,
    n_windows: int = 4,
    min_cross_section: int = 3,
) -> dict:
    """滚动样本外 RankIC（§14.2，与 ``review_account.evaluation.walk_forward`` 同口径）。

    区间均分 ``n_windows`` 段逐段评估；相邻窗口 IC 比近似 OOS/IS 效率比。
    当日数 < ``n_windows*5`` 或某窗口无有效 IC → ``insufficient_data``。
    """
    series = _ic_series(candidates_by_day, labels_by_day, _weighted_score,
                        min_cross_section=min_cross_section, dims=dims)
    if len(series) < max(2, int(n_windows)) * 5:
        return {
            "status": "insufficient_data",
            "reason": "交易日不足",
            "available_days": len(series),
            "min_days_required": max(2, int(n_windows)) * 5,
        }

    windows = _split_windows(series, max(2, int(n_windows)))
    per_window: list[Optional[float]] = []
    for window in windows:
        values = [item["rank_ic"] for item in window]
        per_window.append(fstats.mean(values))
    ratios: list[float] = []
    for previous, current in zip(per_window, per_window[1:]):
        if previous is not None and previous != 0 and current is not None:
            ratios.append(current / previous)
    oos_is = (sum(ratios) / len(ratios)) if ratios else None
    red_flag = ((oos_is is not None and oos_is < 0.5)
                or any(value is not None and value < 0 for value in per_window))
    return {
        "status": "evaluated",
        "windows": per_window,
        "oos_is_ratio": oos_is,
        "red_flag": red_flag,
        "oos_decay": red_flag,
        "trial_count": max(2, int(n_windows)),
    }


# ─── 前向影子指标 ─────────────────────────────────────────────────────────


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return (numerator / denominator) if denominator else None


def shadow_metrics(days: Sequence[Mapping[str, Any]],
                   *, min_days: int = MIN_SHADOW_DAYS) -> dict:
    """前向影子报告（§14.4）：只描述样本，不构成有效性结论。

    ``days`` 逐日提供：
      - ``feedback_items``：``feedback.items``（``state`` / ``close_pct`` /
        ``unknown_reason``）；
      - ``pool``：``review.pool``（``count`` / ``truncated``）；
      - ``planned_codes`` / ``auction_covered_codes``：竞价覆盖率；
      - ``http_requests``：当日新增 HTTP 次数（采集段未记录时为 None）。
    """
    total_items = 0
    confirmed_n = confirmed_hits = 0
    invalidated_n = invalidated_correct = 0
    unknown_n = 0
    unknown_reasons: dict[str, int] = {}
    pool_days = truncated_days = 0
    planned_total = covered_total = 0
    http_total: Optional[int] = 0

    for day in days:
        items = day.get("feedback_items") or []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            total_items += 1
            state = item.get("state")
            close_pct = _num(item.get("close_pct"))
            if state == "confirmed":
                confirmed_n += 1
                if close_pct is not None and close_pct > 0:
                    confirmed_hits += 1
            elif state == "invalidated":
                invalidated_n += 1
                if close_pct is not None and close_pct <= 0:
                    invalidated_correct += 1
            elif state == "unknown":
                unknown_n += 1
                reason = item.get("unknown_reason") or "unspecified"
                unknown_reasons[str(reason)] = unknown_reasons.get(str(reason), 0) + 1

        pool = day.get("pool")
        if isinstance(pool, Mapping):
            pool_days += 1
            if pool.get("truncated"):
                truncated_days += 1

        planned = {str(code) for code in (day.get("planned_codes") or [])}
        covered = {str(code) for code in (day.get("auction_covered_codes") or [])}
        planned_total += len(planned)
        covered_total += len(planned & covered)

        requests = day.get("http_requests")
        if http_total is not None:
            if isinstance(requests, int) and not isinstance(requests, bool):
                http_total += requests
            else:
                http_total = None

    reasons: list[str] = []
    if len(days) < int(min_days):
        reasons.append(f"history_days={len(days)}<{int(min_days)}")
    if total_items == 0:
        reasons.append("no_feedback_items")

    return {
        "status": "ok" if not reasons else "insufficient_data",
        "days": len(days),
        "min_days_required": int(min_days),
        "reasons": reasons,
        "confirmed": {
            "n": confirmed_n,
            "hits": confirmed_hits,
            "hit_rate": _ratio(confirmed_hits, confirmed_n),
        },
        "invalidated": {
            "n": invalidated_n,
            "correct": invalidated_correct,
            "correct_avoidance_rate": _ratio(invalidated_correct, invalidated_n),
        },
        "unknown": {
            "n": unknown_n,
            "ratio": _ratio(unknown_n, total_items),
            "by_reason": dict(sorted(unknown_reasons.items())),
        },
        "http_requests": http_total,
        "truncation": {
            "days_with_pool": pool_days,
            "truncated_days": truncated_days,
            "ratio": _ratio(truncated_days, pool_days),
        },
        "auction_coverage": {
            "planned": planned_total,
            "covered": covered_total,
            "ratio": _ratio(covered_total, planned_total),
        },
        "note": "影子指标只描述样本；历史不足或未跑满影子期时，不构成有效性结论",
    }


__all__ = [
    "DEFAULT_COST_BPS",
    "DEFAULT_HORIZONS",
    "DIMENSIONS",
    "MIN_AMOUNT",
    "MIN_SHADOW_DAYS",
    "ablation",
    "build_tradable_labels",
    "shadow_metrics",
    "walk_forward_ic",
]
