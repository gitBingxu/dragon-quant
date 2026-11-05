"""scan 原始候选质量研究（A9/A10 的只读入口）。

**不改 dragons 去重 / 生产候选池**：只读扫描日志里的原始候选，按可成交口径
（T+1 开盘买入、固定持有期收盘卖出、扣固定成本）计算事后净收益，并按
「通过 / 否决」「分数区间」「rank」「首次 / 重复入榜」分组。

边界与口径：
  - 每天同一 code 多次扫描按 canonical 规则去重：rank 最小 → 分数最高 →
    ``scan_created_at`` 最新；``raw_contributions`` 仍保留原始贡献数；
  - 一字板（``high == low``）无法确认开盘可成交 → 排除并计数；
  - 未成熟（持有期未走完）单独计数，不填 0；
  - 没有历史否决候选时明确 ``coverage_gap``，不虚造召回率；
  - 信号事后机会只能称「错失机会描述」，不是策略效果。
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any, Iterable, Mapping, Optional, Sequence

from dragon_quant.review.strict_metrics import is_valid_bar

from .accuracy import BOOTSTRAP_MIN_SAMPLES, BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED
from .accuracy import _bootstrap_interval, _mean

__all__ = [
    "SCAN_QUALITY_VERSION",
    "build_scan_quality",
    "canonical_candidates",
]

SCAN_QUALITY_VERSION = "scan_quality_v1"
DEFAULT_COST_BPS = 13.0
DEFAULT_HOLD_DAYS = (1, 3, 5)
MISSED_OPPORTUNITY_PCT = 5.0

_SCORE_BANDS = ((80.0, ">=80"), (70.0, "70-80"), (60.0, "60-70"),
                (float("-inf"), "<60"))
_RANK_BANDS = ((5, "1-5"), (10, "6-10"), (20, "11-20"),
               (float("inf"), "21+"))


def _score_band(score: float | None) -> str:
    if score is None:
        return "unknown"
    for threshold, label in _SCORE_BANDS:
        if score >= threshold:
            return label
    return "<60"


def _rank_band(rank: int | None) -> str:
    if rank is None:
        return "unknown"
    for upper, label in _RANK_BANDS:
        if rank <= upper:
            return label
    return "21+"


def canonical_candidates(rows: Iterable[Mapping[str, Any]]) -> tuple[list[dict], int]:
    """按天去重：同 code 取 rank 最小 → 分数最高 → **最新** created_at。

    main R5：时间比较方向必须以「最新」为准，不能用字符串升序选中旧记录。
    """
    raw = [dict(row) for row in rows if row.get("code")]
    best: dict[str, dict] = {}
    for row in raw:
        code = str(row["code"])
        current = best.get(code)
        if current is None:
            best[code] = row
            continue
        rank = row.get("rank") if row.get("rank") is not None else 9999
        cur_rank = (current.get("rank")
                    if current.get("rank") is not None else 9999)
        score = float(row.get("composite_score") or 0)
        cur_score = float(current.get("composite_score") or 0)
        created = row.get("scan_created_at") or ""
        cur_created = current.get("scan_created_at") or ""
        if (rank < cur_rank
                or (rank == cur_rank and score > cur_score)
                or (rank == cur_rank and score == cur_score
                    and created > cur_created)):
            best[code] = row
    return list(best.values()), len(raw)


def _bars_by_date(bars: Mapping[str, Sequence[Mapping]]) -> dict[str, dict]:
    return {str(bar.get("date")): bar
            for bar in bars or []
            if isinstance(bar, Mapping) and bar.get("date")}


def _horizon_metrics(entry: float, hold_days: int,
                     days_after: Sequence[str], bars_by_date: dict[str, dict],
                     cost_bps: float) -> dict:
    if len(days_after) < hold_days:
        return {"status": "immature"}
    exit_day = days_after[hold_days - 1]
    exit_bar = bars_by_date.get(exit_day)
    if not is_valid_bar(exit_bar):
        return {"status": "missing_exit_bar"}
    exit_price = _finite(exit_bar.get("close"))
    entry_price = _finite(entry)
    if exit_price is None or entry_price is None or entry_price == 0:
        # 有效 bar 但价格非有限值：该窗口无有效收益，不填 0。
        return {"status": "missing_exit_bar"}
    net = (exit_price / entry_price - 1) * 100 - cost_bps / 100
    if not math.isfinite(net):
        return {"status": "missing_exit_bar"}
    return {"status": "ok", "exit_date": exit_day, "net_return_pct": net}


def _finite(value: Any) -> float | None:
    """只接受有限数值：``None`` / 非数值 / inf / nan 一律视为无有效样本。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _group_block(records: list[dict]) -> dict:
    returns = [value for value in (_finite(r.get("net_return_pct"))
                                   for r in records) if value is not None]
    opps = [value for value in (_finite(r.get("max_opportunity_pct"))
                                for r in records) if value is not None]
    by_day: dict[str, list[float]] = {}
    for record in records:
        value = _finite(record.get("net_return_pct"))
        if value is not None:
            by_day.setdefault(record["snapshot_date"], []).append(value)
    daily = [_mean(values) for values in by_day.values()]
    daily = [value for value in daily if value is not None]
    wins = sum(1 for value in returns if value > 0)
    by_horizon: dict[str, dict] = {}
    horizon_keys = sorted({
        key for record in records
        for key, value in (record.get("horizons") or {}).items()
        if value.get("status") == "ok"
    }, key=lambda value: int(value))
    for key in horizon_keys:
        values = [value for value in (
            _finite((record.get("horizons") or {}).get(key, {}).get(
                "net_return_pct"))
            for record in records
            if (record.get("horizons") or {}).get(key, {}).get("status") == "ok"
        ) if value is not None]
        by_horizon[key] = {
            "valid": len(values),
            "mean_net_return_pct": _mean(values),
            "win_rate": (sum(1 for value in values if value > 0) / len(values)
                         if values else None),
        }
    return {
        "samples": len(records),
        "valid": len(returns),
        "stocks": len({r["code"] for r in records}),
        "days": len({r["snapshot_date"] for r in records}),
        "mean_net_return_pct": _mean(returns),
        "median_net_return_pct": round(median(returns), 4) if returns else None,
        "date_equal_mean_return_pct": _mean(daily),
        "win_rate": (wins / len(returns)) if returns else None,
        "mean_max_opportunity_pct": _mean(opps),
        "missed_opportunity_rate": (
            sum(1 for value in opps if value >= MISSED_OPPORTUNITY_PCT) / len(opps)
            if opps else None),
        "by_horizon": by_horizon,
    }


def build_scan_quality(*,
                       date_from: str,
                       date_to: str,
                       contributions_by_date: Mapping[str, list],
                       calendar: Sequence[str],
                       bars: Mapping[str, Sequence[Mapping]],
                       hold_days: Sequence[int] = DEFAULT_HOLD_DAYS,
                       cost_bps: float = DEFAULT_COST_BPS,
                       read_errors: Optional[list[str]] = None,
                       prior_codes: Optional[Iterable[str]] = None,
                       base_read_failed: bool = False,
                       empty_reason: Optional[str] = None) -> dict:
    days = sorted({str(day) for day in (calendar or []) if day})
    index = {day: position for position, day in enumerate(days)}
    horizons = sorted({int(value) for value in hold_days if int(value) > 0})
    bars_by_code = {str(code): _bars_by_date(rows)
                    for code, rows in (bars or {}).items()}

    dates = sorted(day for day in contributions_by_date
                   if date_from <= day <= date_to)
    if not dates:
        # 基础读取失败（扫描日志/日期列表）不得静默呈现成「区间内没有扫描」。
        reason = empty_reason or (
            "scan_read_error" if base_read_failed else "no_scan_in_range")
        return _empty(date_from, date_to, horizons, cost_bps,
                      reason=reason, calendar=days,
                      read_errors=read_errors or [])

    records: list[dict] = []
    raw_total = 0
    canonical_total = 0
    # 区间前已出现的 code 视为 repeat（首入范围 = 区间前 60 天 + 本期）。
    first_seen: dict[str, str] = {
        str(code): "" for code in (prior_codes or [])
    }
    warnings: list[str] = []
    coverage_gap = 0

    for day in dates:
        canonical, raw_count = canonical_candidates(
            contributions_by_date.get(day) or [])
        raw_total += raw_count
        canonical_total += len(canonical)
        if raw_count > len(canonical):
            coverage_gap += raw_count - len(canonical)
        position = index.get(day)
        if position is None:
            warnings.append(f"calendar_unavailable: {day}")
            continue
        for row in canonical:
            code = str(row["code"])
            if code not in first_seen:
                first_seen[code] = day
            entry_index = position + 1
            if entry_index >= len(days):
                records.append({**_base_record(row, day, None), "status": "immature"})
                continue
            entry_day = days[entry_index]
            entry_bar = bars_by_code.get(code, {}).get(entry_day)
            if not is_valid_bar(entry_bar):
                records.append({**_base_record(row, day, None),
                                "status": "entry_bar_invalid"})
                continue
            if entry_bar["high"] == entry_bar["low"]:
                records.append({**_base_record(row, day, entry_day),
                                "status": "one_word_board_unfillable"})
                continue
            entry_price = float(entry_bar["open"])
            days_after = days[entry_index + 1:]
            per_horizon = {
                str(h): _horizon_metrics(entry_price, h, days_after,
                                         bars_by_code.get(code, {}), cost_bps)
                for h in horizons
            }
            mature_horizons = [h for h in horizons
                               if per_horizon[str(h)]["status"] == "ok"]
            primary = horizons[len(horizons) // 2]
            primary_metrics = per_horizon[str(primary)]
            # 机会/风险只用**该 horizon 已成熟**的窗口；未成熟不得用 partial 高点。
            max_opportunity = None
            if primary_metrics["status"] == "ok":
                window = days[entry_index:entry_index + primary]
                max_high = max(
                    (float(bars_by_code.get(code, {}).get(d, {}).get("high") or 0)
                     for d in window),
                    default=0.0)
                if entry_price:
                    max_opportunity = (max_high / entry_price - 1) * 100
            record = {
                **_base_record(row, day, entry_day),
                "status": "ok" if mature_horizons else "immature",
                "entry_price": entry_price,
                "horizons": per_horizon,
                "mature_horizons": mature_horizons,
                "net_return_pct": primary_metrics.get("net_return_pct"),
                "max_opportunity_pct": max_opportunity,
                "first_or_repeat": ("first" if first_seen.get(code) == day
                                    else "repeat"),
            }
            records.append(record)

    ok = [r for r in records if r["status"] == "ok"]
    baseline = _group_block(ok)
    horizon_counts = {
        str(h): {
            "valid": sum(1 for r in ok
                         if (r.get("horizons") or {}).get(str(h), {}).get("status") == "ok"),
            "immature": sum(1 for r in records
                            if (r.get("horizons") or {}).get(str(h), {}).get("status") == "immature"),
            "missing_exit_bar": sum(
                1 for r in records
                if (r.get("horizons") or {}).get(str(h), {}).get("status")
                == "missing_exit_bar"),
        }
        for h in horizons
    }

    def grouped(key: str) -> dict[str, dict]:
        buckets: dict[str, list[dict]] = {}
        for record in records:
            buckets.setdefault(str(record.get(key) or "unknown"), []).append(record)
        return {name: _group_block(items) for name, items in sorted(buckets.items())}

    result_groups = {
        "by_gate": grouped("gate"),
        "by_score_band": grouped("score_band"),
        "by_rank_band": grouped("rank_band"),
        "by_first_repeat": grouped("first_or_repeat"),
        "by_scan_top_n": grouped("scan_top_n"),
    }
    by_gate = result_groups["by_gate"]
    passed = by_gate.get("passed")
    rejected = by_gate.get("rejected")

    def diff(block: Optional[dict]) -> Optional[float]:
        if not block or block.get("mean_net_return_pct") is None \
                or baseline.get("mean_net_return_pct") is None:
            return None
        return round(block["mean_net_return_pct"]
                     - baseline["mean_net_return_pct"], 4)

    # bootstrap 只接收**主 horizon 有效且有限**的收益：主 horizon 未成熟的
    # 记录保留在 samples/其他 horizon 统计里，但不得把 None 当 0 送进重采样。
    def pairs(gate: str) -> list[tuple[str, float]]:
        return [(record["snapshot_date"], value) for record in ok
                if record.get("gate") == gate
                and (value := _finite(record.get("net_return_pct"))) is not None]

    status = ("ok" if baseline["valid"] > 0
              else "insufficient_data")
    return {
        "status": status,
        "reason": None if baseline["valid"] else "no_mature_samples",
        "evaluation_version": SCAN_QUALITY_VERSION,
        "universe": "raw_scan_candidates",
        "sample_unit": "stock_day",
        "date_from": date_from,
        "date_to": date_to,
        "as_of": days[-1] if days else None,
        "window": {"entry_rule": "T+1 open (one-word board excluded)",
                   "exit_rule": "nth trading day close",
                   "hold_days": horizons, "cost_bps": cost_bps},
        "dedupe_rule": "per_day_code: min rank -> max score -> latest created_at",
        "coverage": {
            "days_with_scan": len(dates),
            "raw_contributions": raw_total,
            "canonical_candidates": canonical_total,
            "duplicate_contributions": coverage_gap,
            "mature_samples": baseline["valid"],
            "by_horizon": horizon_counts,
            "excluded": {
                "immature": sum(1 for r in records if r["status"] == "immature"),
                "entry_bar_invalid": sum(
                    1 for r in records if r["status"] == "entry_bar_invalid"),
                "one_word_board_unfillable": sum(
                    1 for r in records
                    if r["status"] == "one_word_board_unfillable"),
            },
        },
        "baseline": baseline,
        "groups": result_groups,
        "diffs_vs_baseline": {
            "passed": diff(passed), "rejected": diff(rejected),
        },
        "spread": ({"pair": ["passed", "rejected"],
                    "basis": "mean_net_return_pct",
                    "value": (round(passed["mean_net_return_pct"]
                                    - rejected["mean_net_return_pct"], 4)
                              if passed and rejected
                              and passed.get("mean_net_return_pct") is not None
                              and rejected.get("mean_net_return_pct") is not None
                              else None),
                    "bootstrap": _bootstrap_interval(
                        pairs("passed"), pairs("rejected"),
                        seed=BOOTSTRAP_SEED, resamples=BOOTSTRAP_RESAMPLES)}
                   if passed and rejected else None),
        "coverage_gap": (
            "no_rejected_candidates: 历史扫描未持久化否决候选，"
            "无法估计召回率；本次只描述已持久化候选的事后机会"
            if rejected is None else None),
        "first_or_repeat_scope": "loaded_range（本期首次/重复；非全历史首入）",
        "read_errors": read_errors,
        "warnings": warnings + (
            ["duplicate_scan_contributions: 同一天同 code 多次扫描已按 canonical"
             " 规则去重，原始贡献数见 coverage"] if coverage_gap else []),
        "caveats": [
            "事后机会描述 ≠ 策略有效性；不改 dragons 去重与生产候选池",
            "固定持有期净收益为可成交估算（T+1 开盘 / 第 n 日收盘 / 扣成本），"
            "不宣称真实成交",
            "区间为描述性 bootstrap，不宣称独立样本显著性",
            f"至少 {BOOTSTRAP_MIN_SAMPLES} 个有效样本才出区间；"
            "不足时 status=insufficient_samples",
        ],
    }


def _base_record(row: Mapping[str, Any], day: str,
                 entry_day: Optional[str]) -> dict:
    gate_value = row.get("is_true_dragon")
    gate = ("passed" if gate_value is True
            else "rejected" if gate_value is False else "unknown")
    reject_reason = row.get("reject_reason") or (
        "unspecified" if gate == "rejected" else None)
    return {
        "snapshot_date": day,
        "entry_date": entry_day,
        "code": str(row.get("code")),
        "name": row.get("name") or "",
        "rank": row.get("rank"),
        "composite_score": row.get("composite_score"),
        "scan_top_n": row.get("scan_top_n"),
        "scan_id": row.get("scan_id"),
        "gate": gate,
        "reject_reason": reject_reason,
        "score_band": _score_band(row.get("composite_score")),
        "rank_band": _rank_band(row.get("rank")),
        "first_or_repeat": None,
    }


def _empty(date_from: str, date_to: str, horizons: list[int],
           cost_bps: float, *, reason: str, calendar: Sequence[str],
           read_errors: Optional[list[str]] = None) -> dict:
    return {
        "status": "insufficient_data",
        "reason": reason,
        "evaluation_version": SCAN_QUALITY_VERSION,
        "universe": "raw_scan_candidates",
        "sample_unit": "stock_day",
        "date_from": date_from,
        "date_to": date_to,
        "as_of": calendar[-1] if calendar else None,
        "window": {"entry_rule": "T+1 open (one-word board excluded)",
                   "exit_rule": "nth trading day close",
                   "hold_days": horizons, "cost_bps": cost_bps},
        "coverage": {"days_with_scan": 0, "raw_contributions": 0,
                     "canonical_candidates": 0, "mature_samples": 0,
                     "excluded": {}},
        "baseline": _group_block([]),
        "groups": {},
        "diffs_vs_baseline": {},
        "spread": None,
        "coverage_gap": None,
        "first_or_repeat_scope": "loaded_range",
        "read_errors": list(read_errors or []),
        "warnings": [],
        "caveats": [],
    }
