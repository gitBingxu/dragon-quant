"""全历史逐股方向准确率（方案 §5；Strategy + Template Method，纯核 + 只读组装）。

边界：
  - 样本单位固定 ``stock_day``（同一股票不同快照日各一样本，再按 code 聚合）；
  - 行情只读调用方注入的本地 ``stock_daily``（不触网、不写库）；
  - 信号选择由两套 :class:`Strategy` 提供，窗口 / 方向 / 聚合由
    :func:`evaluate_accuracy` 模板方法统一完成；
  - 缺数据、未成熟、信号缺失一律显式排除并计数，不写 0、不补算。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from dragon_quant.review.strict_metrics import is_valid_bar

from .bars_loader import BarsTruncated, load_bars_resilient

__all__ = [
    "ACCURACY_CAVEATS",
    "ACCURACY_WINDOW_AUCTION",
    "ACCURACY_WINDOW_PREP",
    "AccuracyWindow",
    "AuctionSignalStrategy",
    "PrepSignalStrategy",
    "SignalSample",
    "build_auction_accuracy",
    "build_prep_accuracy",
    "evaluate_accuracy",
]

ACCURACY_CAVEATS = (
    "方向命中仅描述历史样本，不构成策略有效性结论",
    "样本单位 = 股票 × 快照日；同一股票跨日分别计数后按 code 聚合",
    "交易日按本地日 K 推断，非交易所日历；缺行情/未成熟一律排除并计数",
)

PREP_ACCEPTED_VERSIONS = ("0.7", "0.8")
AUCTION_ACCEPTED_VERSION = "2"
PREP_ADVICE_BUILD = ("重仓", "正常", "轻仓")


# ─── 数据结构 ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AccuracyWindow:
    entry_offset: int
    exit_offset: int
    entry_price: str = "open"
    exit_price: str = "close"
    cost_bps: float = 0.0

    def as_dict(self) -> dict:
        return {
            "entry_offset": self.entry_offset,
            "exit_offset": self.exit_offset,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "cost_bps": self.cost_bps,
        }


ACCURACY_WINDOW_PREP = AccuracyWindow(entry_offset=1, exit_offset=3)
ACCURACY_WINDOW_AUCTION = AccuracyWindow(entry_offset=0, exit_offset=2)


@dataclass
class SignalSample:
    code: str
    name: str | None
    snapshot_date: str
    expected_direction: str  # up | down
    group: str
    exclusion_reason: str | None = None


# ─── 取值 helper ─────────────────────────────────────────────────────────


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _code(item: Mapping[str, Any]) -> str | None:
    return _text(item.get("code"))


def _name(item: Mapping[str, Any]) -> str | None:
    return _text(item.get("name"))


# ─── 信号策略（Strategy） ────────────────────────────────────────────────


class PrepSignalStrategy:
    """prep 口径：按日级「是否建仓」选择推荐 / 不推荐组。"""

    def select(self, payload: Mapping[str, Any] | None) -> dict:
        data = payload if isinstance(payload, Mapping) else {}
        version = str(data.get("version")) if data.get("version") is not None else ""
        if version not in PREP_ACCEPTED_VERSIONS:
            return {
                "samples": [],
                "day_kind": None,
                "snapshot_exclusion": "snapshot_unsupported",
                "warnings": [],
            }

        candidates = data.get("review_candidates")
        if not isinstance(candidates, list):
            return {
                "samples": [],
                "day_kind": None,
                "snapshot_exclusion": "review_candidates_missing",
                "warnings": [],
            }

        day_kind = self._day_kind(data.get("sentiment"))
        snapshot_date = _text(data.get("trade_date")) or ""
        samples: list[SignalSample] = []
        warnings: list[str] = []
        inconsistent = 0

        for item in candidates:
            if not isinstance(item, Mapping):
                continue
            code = _code(item)
            if not code:
                continue
            eligibility = item.get("eligibility")
            position_pct = _num(item.get("position_pct"))
            name = _name(item)

            if day_kind == "signal_missing":
                samples.append(SignalSample(
                    code=code, name=name, snapshot_date=snapshot_date,
                    expected_direction="up", group="data_insufficient",
                    exclusion_reason="market_signal_missing"))
                continue

            if day_kind == "build":
                if eligibility == "eligible" and position_pct is not None \
                        and position_pct > 0:
                    samples.append(SignalSample(
                        code=code, name=name, snapshot_date=snapshot_date,
                        expected_direction="up", group="recommended"))
                elif eligibility in (None, "", "unknown") or position_pct is None:
                    samples.append(SignalSample(
                        code=code, name=name, snapshot_date=snapshot_date,
                        expected_direction="up", group="data_insufficient",
                        exclusion_reason="candidate_data_insufficient"))
                # 其余（明确不推荐）忽略，不进分母。
                continue

            # no_build：position_pct == 0 即明确不推荐（不要求 not_qualified）。
            if position_pct == 0:
                samples.append(SignalSample(
                    code=code, name=name, snapshot_date=snapshot_date,
                    expected_direction="down", group="not_recommended"))
            elif position_pct is None:
                samples.append(SignalSample(
                    code=code, name=name, snapshot_date=snapshot_date,
                    expected_direction="down", group="data_insufficient",
                    exclusion_reason="candidate_data_insufficient"))
            elif position_pct > 0:
                inconsistent += 1

        if inconsistent:
            warnings.append(
                f"inconsistent_group: {snapshot_date} 有 {inconsistent} 只 "
                "position_pct>0 与不建仓日建议冲突，未计入分母")

        samples.sort(key=lambda sample: sample.code)
        return {
            "samples": samples,
            "day_kind": day_kind,
            "snapshot_exclusion": None,
            "warnings": warnings,
        }

    @staticmethod
    def _day_kind(sentiment: Any) -> str:
        data = sentiment if isinstance(sentiment, Mapping) else {}
        position_pct = _num(data.get("position_pct"))
        if position_pct is not None:
            return "build" if position_pct > 0 else "no_build"
        advice = _text(data.get("position_advice"))
        if advice == "空仓":
            return "no_build"
        if advice in PREP_ADVICE_BUILD:
            return "build"
        return "signal_missing"


class AuctionSignalStrategy:
    """auction 口径：等级弱优先看跌；非弱偏空看跌，偏多/中性看涨。"""

    def select(self, rows: Iterable[Any],
               names: Mapping[str, str] | None = None) -> dict:
        name_map = names if isinstance(names, Mapping) else {}
        samples: list[SignalSample] = []
        exclusions = Counter({"snapshot_corrupt": 0,
                              "snapshot_unsupported": 0})
        warnings: list[str] = []

        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            code = _code(row)
            if not code:
                continue
            snapshot_date = _text(row.get("trade_date")) or ""
            name = _text(name_map.get(code)) or _name(row)

            if row.get("error"):
                exclusions["snapshot_corrupt"] += 1
                continue
            if str(row.get("version")) != AUCTION_ACCEPTED_VERSION:
                exclusions["snapshot_unsupported"] += 1
                continue

            level = row.get("level")
            direction = row.get("direction")
            data_quality = row.get("data_quality")
            status = (data_quality.get("status")
                      if isinstance(data_quality, Mapping) else None)

            if level is None or status == "unavailable" \
                    or row.get("unverified") is True:
                samples.append(self._excluded(code, name, snapshot_date,
                                              "signal_missing"))
                continue

            if level == "弱":
                expected = "down"
            elif level in ("强", "中"):
                if direction == "bear":
                    expected = "down"
                elif direction in ("bull", "neutral"):
                    expected = "up"
                else:
                    samples.append(self._excluded(code, name, snapshot_date,
                                                  "signal_missing"))
                    continue
            else:
                samples.append(self._excluded(code, name, snapshot_date,
                                              "signal_missing"))
                continue

            samples.append(SignalSample(
                code=code, name=name, snapshot_date=snapshot_date,
                expected_direction=expected, group="auction"))

        samples.sort(key=lambda sample: (sample.snapshot_date, sample.code))
        return {"samples": samples, "exclusions": dict(exclusions),
                "warnings": warnings}

    @staticmethod
    def _excluded(code: str, name: str | None, snapshot_date: str,
                  reason: str) -> SignalSample:
        return SignalSample(code=code, name=name, snapshot_date=snapshot_date,
                            expected_direction="up", group="data_insufficient",
                            exclusion_reason=reason)


# ─── 模板方法：窗口 / 方向 / 聚合 ────────────────────────────────────────


def _empty_summary() -> dict:
    return {"correct": 0, "valid": 0, "excluded": 0, "hit_rate": None,
            "excluded_by_reason": {}}


def evaluate_accuracy(
    samples: Sequence[SignalSample],
    bars: Mapping[str, Sequence[Mapping[str, Any]]] | None,
    calendar: Sequence[str],
    window: AccuracyWindow,
    cutoff: str | None,
) -> dict:
    """固定「截止 → 定位交易日 → 取 bar → 方向 → 聚合」骨架。"""

    days = sorted({str(day) for day in (calendar or []) if day})
    index = {day: position for position, day in enumerate(days)}
    bars_by_code = bars if isinstance(bars, Mapping) else {}

    correct = 0
    valid = 0
    excluded = 0
    reasons: Counter[str] = Counter()
    per_code: dict[str, dict] = {}

    for sample in sorted(samples, key=lambda item: (item.code,
                                                    item.snapshot_date)):
        stock = per_code.setdefault(sample.code, {
            "code": sample.code, "name": sample.name,
            "correct": 0, "valid": 0, "excluded": 0})
        if stock.get("name") is None and sample.name:
            stock["name"] = sample.name

        if sample.exclusion_reason:
            excluded += 1
            reasons[sample.exclusion_reason] += 1
            stock["excluded"] += 1
            continue

        if cutoff is None or sample.snapshot_date > cutoff:
            excluded += 1
            reasons["immature"] += 1
            stock["excluded"] += 1
            continue

        position = index.get(sample.snapshot_date)
        if position is None:
            excluded += 1
            reasons["calendar_unavailable"] += 1
            stock["excluded"] += 1
            continue

        entry_index = position + window.entry_offset
        exit_index = position + window.exit_offset
        if entry_index >= len(days) or exit_index >= len(days):
            excluded += 1
            reasons["immature"] += 1
            stock["excluded"] += 1
            continue

        entry_date = days[entry_index]
        exit_date = days[exit_index]
        by_date = {
            str(bar.get("date") or ""): bar
            for bar in (bars_by_code.get(sample.code) or [])
            if isinstance(bar, Mapping)
        }
        entry_bar = by_date.get(entry_date)
        exit_bar = by_date.get(exit_date)
        if entry_bar is None or exit_bar is None:
            excluded += 1
            reasons["missing_bar"] += 1
            stock["excluded"] += 1
            continue
        if not is_valid_bar(entry_bar) or not is_valid_bar(exit_bar):
            excluded += 1
            reasons["invalid_bar"] += 1
            stock["excluded"] += 1
            continue

        entry_px = _num(entry_bar.get(window.entry_price))
        exit_px = _num(exit_bar.get(window.exit_price))
        if entry_px is None or exit_px is None or entry_px <= 0:
            excluded += 1
            reasons["invalid_bar"] += 1
            stock["excluded"] += 1
            continue

        if exit_px > entry_px:
            actual = "up"
        elif exit_px < entry_px:
            actual = "down"
        else:
            actual = "flat"

        valid += 1
        stock["valid"] += 1
        if actual == sample.expected_direction:
            correct += 1
            stock["correct"] += 1

    stocks: list[dict] = []
    for stock in per_code.values():
        denominator = stock["valid"]
        stocks.append({
            **stock,
            "hit_rate": (round(stock["correct"] / denominator, 4)
                         if denominator else None),
        })
    stocks.sort(key=lambda item: (-item["valid"], item["code"]))

    return {
        "summary": {
            "correct": correct,
            "valid": valid,
            "excluded": excluded,
            "hit_rate": round(correct / valid, 4) if valid else None,
            "excluded_by_reason": dict(sorted(reasons.items())),
        },
        "stocks": stocks,
        "as_of": cutoff,
        "latest_bar_date": days[-1] if days else None,
    }


# ─── 全历史组装（只读 Facade） ───────────────────────────────────────────


def _latest_bar_date(bar_dates: Sequence[str], today: str | None) -> str | None:
    days = [day for day in bar_dates if day and (today is None or day <= today)]
    return max(days) if days else None


def _cutoff(bar_dates: Sequence[str], trading_days_after: int) -> str | None:
    """可验证截止日：其后仍有 ``trading_days_after`` 个本地确认交易日。"""

    days = sorted(bar_dates)
    index = len(days) - 1 - trading_days_after
    return days[index] if index >= 0 else None


def _response(
    *,
    status: str,
    reason: str | None,
    bar_dates: Sequence[str],
    today: str | None,
    cutoff: str | None,
    window: AccuracyWindow,
    snapshot_count: int,
    day_kind_counts: Mapping[str, int],
    summary: Mapping[str, Any],
    stocks: Sequence[Mapping[str, Any]],
    excluded_snapshot_dates: Mapping[str, int],
    warnings: Sequence[str],
) -> dict:
    return {
        "status": status,
        "reason": reason,
        "as_of": cutoff,
        "latest_bar_date": _latest_bar_date(bar_dates, today),
        "snapshot_count": snapshot_count,
        "data_source": "local_stock_daily",
        "calendar_basis": "local_stock_daily_bars_union",
        "sample_unit": "stock_day",
        "window": window.as_dict(),
        "summary": dict(summary),
        "day_kind_counts": dict(day_kind_counts),
        "stocks": [dict(stock) for stock in stocks],
        "excluded_snapshot_dates": dict(excluded_snapshot_dates),
        "warnings": list(warnings),
        "caveats": list(ACCURACY_CAVEATS),
    }


def _empty_prep_day_kinds() -> dict[str, int]:
    return {"build": 0, "no_build": 0, "signal_missing": 0}


def _load_range(samples: Sequence[SignalSample], today: str | None) -> tuple[
        str, str] | None:
    active = [sample for sample in samples if sample.exclusion_reason is None]
    if not active:
        return None
    date_from = min(sample.snapshot_date for sample in active)
    date_to = max(today or "", max(sample.snapshot_date for sample in active))
    return date_from, date_to


def _load_bars(
    load_bars: Callable[[list[str], str, str], Mapping],
    samples: Sequence[SignalSample],
    bar_dates: Sequence[str],
    today: str | None,
    warnings: list[str],
) -> tuple[dict, str | None]:
    """返回 ``(rows, fatal_reason)``；fatal 时调用方不输出部分统计。"""

    active = [sample for sample in samples if sample.exclusion_reason is None]
    codes = sorted({sample.code for sample in active})
    window = _load_range(samples, today)
    if not codes or window is None:
        return {}, None
    date_from, date_to = window
    # 已确认日历可能包含相对注入时钟「未来」的 bar（回放/夹具）；加载到日历
    # 末尾才能取到 T+1/T+3，不用注入时钟截断已存在的行情。
    if bar_dates:
        date_to = max(date_to, max(bar_dates))
    try:
        loaded = load_bars_resilient(load_bars, codes, date_from, date_to)
    except BarsTruncated:
        return {}, "local_bars_truncated"
    except Exception as exc:      # 行情故障不当 0 收益
        warnings.append(f"local_bars_error: {type(exc).__name__}: {exc}")
        return {}, None
    return dict(loaded.get("rows") or {}), None


def build_prep_accuracy(
    *,
    snapshot_dates: Sequence[str],
    load_snapshot: Callable[[str], Mapping | None],
    bar_dates: Sequence[str],
    load_bars: Callable[[list[str], str, str], Mapping],
    today: str | None = None,
) -> dict:
    """prep 全历史准确率（T+1 开盘 → T+3 收盘，原始价格不扣费）。"""

    dates = sorted({str(day) for day in snapshot_dates if day})
    cutoff = _cutoff(bar_dates, 3)
    excluded_dates = {"snapshot_unsupported": 0, "snapshot_corrupt": 0,
                      "review_candidates_missing": 0}
    day_kinds = _empty_prep_day_kinds()
    warnings: list[str] = []

    if not dates:
        return _response(
            status="insufficient_data", reason="no_snapshots",
            bar_dates=bar_dates, today=today, cutoff=cutoff,
            window=ACCURACY_WINDOW_PREP, snapshot_count=0,
            day_kind_counts=day_kinds, summary=_empty_summary(), stocks=[],
            excluded_snapshot_dates=excluded_dates, warnings=warnings)

    strategy = PrepSignalStrategy()
    samples: list[SignalSample] = []
    for day in dates:
        try:
            payload = load_snapshot(day)
        except Exception as exc:
            payload = None
            warnings.append(
                f"snapshot_corrupt: {day} {type(exc).__name__}: {exc}")
        if not isinstance(payload, Mapping):
            excluded_dates["snapshot_corrupt"] += 1
            continue
        selection = strategy.select(payload)
        warnings.extend(selection["warnings"])
        exclusion = selection["snapshot_exclusion"]
        if exclusion:
            excluded_dates[exclusion] += 1
            continue
        day_kinds[selection["day_kind"]] += 1
        samples.extend(selection["samples"])

    rows, fatal_reason = _load_bars(load_bars, samples, bar_dates, today, warnings)
    if fatal_reason:
        return _response(
            status="insufficient_data", reason=fatal_reason,
            bar_dates=bar_dates, today=today, cutoff=cutoff,
            window=ACCURACY_WINDOW_PREP, snapshot_count=len(dates),
            day_kind_counts=day_kinds, summary=_empty_summary(), stocks=[],
            excluded_snapshot_dates=excluded_dates, warnings=warnings)

    evaluated = evaluate_accuracy(
        samples, rows, bar_dates, ACCURACY_WINDOW_PREP, cutoff)
    if not bar_dates:
        status, reason = "insufficient_data", "local_bars_missing"
    else:
        status, reason = "ok", None
    return _response(
        status=status, reason=reason, bar_dates=bar_dates, today=today,
        cutoff=evaluated["as_of"], window=ACCURACY_WINDOW_PREP,
        snapshot_count=len(dates), day_kind_counts=day_kinds,
        summary=evaluated["summary"], stocks=evaluated["stocks"],
        excluded_snapshot_dates=excluded_dates, warnings=warnings)


def build_auction_accuracy(
    *,
    snapshot_dates: Sequence[str],
    load_snapshot_rows: Callable[[str], Iterable[Any]],
    load_names: Callable[[str], Mapping[str, str]],
    bar_dates: Sequence[str],
    load_bars: Callable[[list[str], str, str], Mapping],
    today: str | None = None,
) -> dict:
    """auction 全历史准确率（T 开盘 → T+2 收盘，原始价格不扣费）。"""

    dates = sorted({str(day) for day in snapshot_dates if day})
    cutoff = _cutoff(bar_dates, 2)
    excluded_dates = {"snapshot_unsupported": 0, "snapshot_corrupt": 0}
    warnings: list[str] = []

    if not dates:
        return _response(
            status="insufficient_data", reason="no_snapshots",
            bar_dates=bar_dates, today=today, cutoff=cutoff,
            window=ACCURACY_WINDOW_AUCTION, snapshot_count=0,
            day_kind_counts={}, summary=_empty_summary(), stocks=[],
            excluded_snapshot_dates=excluded_dates, warnings=warnings)

    strategy = AuctionSignalStrategy()
    samples: list[SignalSample] = []
    for day in dates:
        try:
            rows = list(load_snapshot_rows(day) or [])
        except Exception as exc:
            excluded_dates["snapshot_corrupt"] += 1
            warnings.append(
                f"snapshot_corrupt: {day} {type(exc).__name__}: {exc}")
            continue
        try:
            names = load_names(day) or {}
        except Exception as exc:
            names = {}
            warnings.append(
                f"names_error: {day} {type(exc).__name__}: {exc}")
        prepared = [
            {**row, "trade_date": row.get("trade_date") or day}
            if isinstance(row, Mapping) else row
            for row in rows
        ]
        selection = strategy.select(prepared, names)
        warnings.extend(selection["warnings"])
        for reason, count in selection["exclusions"].items():
            excluded_dates[reason] = excluded_dates.get(reason, 0) + count
        samples.extend(selection["samples"])

    rows_by_code, fatal_reason = _load_bars(load_bars, samples, bar_dates, today, warnings)
    if fatal_reason:
        return _response(
            status="insufficient_data", reason=fatal_reason,
            bar_dates=bar_dates, today=today, cutoff=cutoff,
            window=ACCURACY_WINDOW_AUCTION, snapshot_count=len(dates),
            day_kind_counts={}, summary=_empty_summary(), stocks=[],
            excluded_snapshot_dates=excluded_dates, warnings=warnings)

    evaluated = evaluate_accuracy(
        samples, rows_by_code, bar_dates, ACCURACY_WINDOW_AUCTION, cutoff)
    if not bar_dates:
        status, reason = "insufficient_data", "local_bars_missing"
    else:
        status, reason = "ok", None
    return _response(
        status=status, reason=reason, bar_dates=bar_dates, today=today,
        cutoff=evaluated["as_of"], window=ACCURACY_WINDOW_AUCTION,
        snapshot_count=len(dates), day_kind_counts={},
        summary=evaluated["summary"], stocks=evaluated["stocks"],
        excluded_snapshot_dates=excluded_dates, warnings=warnings)
