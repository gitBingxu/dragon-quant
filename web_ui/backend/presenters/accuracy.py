"""全历史逐股方向准确率（方案 §5；Strategy + Template Method，纯核 + 只读组装）。

边界：
  - 样本单位固定 ``stock_day``（同一股票不同快照日各一样本，再按 code 聚合）；
  - 行情只读调用方注入的本地 ``stock_daily``（不触网、不写库）；
  - 信号选择由两套 :class:`Strategy` 提供，窗口 / 方向 / 聚合由
    :func:`evaluate_accuracy` 模板方法统一完成；
  - 缺数据、未成熟、信号缺失一律显式排除并计数，不写 0、不补算。
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Any, Callable, Iterable, Mapping, Sequence

from dragon_quant.infra.factor.review_shadow import (
    DEFAULT_COST_BPS as TRADABLE_DEFAULT_COST_BPS,
    build_tradable_labels,
)
from dragon_quant.infra.factor.signal_adapter import (
    AUCTION_ACCEPTED_VERSIONS,
    PREP_ACCEPTED_VERSIONS,
    auction_expected_direction,
    auction_signal,
    prep_expected_direction,
    prep_group,
)
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
    "prep「不推荐」不是看跌预测，主指标为两组同窗口净收益与差值；"
    "市场空仓效果按日期等权另列",
    "auction「中性」不是看涨：只有 bull/bear 进入方向命中分母，"
    "强弱档位与风险否决独立成轴",
    "0.7/0.8 与 2.x 快照按版本分别计数，不假设跨版本可比；"
    "区间为描述性，不宣称独立样本显著性",
)

# 版本白名单的唯一来源是共享信号适配叶子；这里保留别名供既有调用方使用。
AUCTION_ACCEPTED_VERSION = AUCTION_ACCEPTED_VERSIONS[0]
PREP_ADVICE_BUILD = ("重仓", "正常", "轻仓")

#: 组间差 bootstrap 的固定 seed 与最少样本（不足不出区间，不伪造显著性）。
BOOTSTRAP_SEED = 20261105
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_MIN_SAMPLES = 5


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


# 主研究合同与 ``decision_review`` 同源：可成交开盘 → 第 N 交易日开盘、扣 13bp。
# 入口时点：prep T 日 16:40（次日开盘可买）→ T+1；auction T 日 09:25 → T 日。
ACCURACY_HORIZONS = (1, 3, 5)
ACCURACY_WINDOW_PREP = AccuracyWindow(
    entry_offset=1, exit_offset=3, entry_price="open", exit_price="open",
    cost_bps=TRADABLE_DEFAULT_COST_BPS)
ACCURACY_WINDOW_AUCTION = AccuracyWindow(
    entry_offset=0, exit_offset=2, entry_price="open", exit_price="open",
    cost_bps=TRADABLE_DEFAULT_COST_BPS)


@dataclass
class SignalSample:
    code: str
    name: str | None
    snapshot_date: str
    expected_direction: str | None  # up | down | None（中性/无方向预期）
    group: str
    exclusion_reason: str | None = None
    version: str | None = None      # 快照版本（用于版本分层，不混合计算）


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
    """prep 口径：固定完整 ``review_candidates`` 母集（A4）。

    推荐 / 不推荐分组**不随市场建仓日型变化**：不建仓日也不会丢弃「不推荐」
    组；不推荐不是看跌（``expected_direction=None``），组间比较用同窗口净收益。
    市场仓位为 0 的「空仓风险效果」由 builder 按日期等权另列。
    """

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

        for item in candidates:
            if not isinstance(item, Mapping):
                continue
            code = _code(item)
            if not code:
                continue
            group = prep_group(item)
            excluded = ("candidate_data_insufficient"
                        if group == "data_insufficient" else None)
            samples.append(SignalSample(
                code=code, name=_name(item), snapshot_date=snapshot_date,
                expected_direction=prep_expected_direction(group),
                group=group, exclusion_reason=excluded, version=version))

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
    """auction 口径：方向 bull/bear/neutral/unknown，风险否决独立成轴（A5）。

    * ``neutral`` 保留中性，**不再当成看涨**；
    * 只有 ``bull`` / ``bear`` 进入方向命中分母，``neutral`` 单列计数；
    * 缺 ``unverified`` / ``level`` / ``strength`` / ``direction`` 等关键键
      fail-closed 为 ``unknown``（数据不足），不猜方向；
    * ``is_weak`` 是真实风险否决，单独保留在 ``veto`` 轴。
    """

    def select(self, rows: Iterable[Any],
               names: Mapping[str, str] | None = None) -> dict:
        name_map = names if isinstance(names, Mapping) else {}
        samples: list[SignalSample] = []
        exclusions = Counter({"snapshot_corrupt": 0,
                              "snapshot_unsupported": 0})
        warnings: list[str] = []
        axes = Counter({"bull": 0, "bear": 0, "neutral": 0, "unknown": 0,
                        "veto": 0, "strong": 0, "medium": 0, "weak": 0,
                        "level_unknown": 0})
        versions_seen: Counter[str] = Counter()

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
            version = str(row.get("version"))
            versions_seen[version] += 1
            if version not in AUCTION_ACCEPTED_VERSIONS:
                exclusions["snapshot_unsupported"] += 1
                continue

            signal = auction_signal(row)
            direction = signal["direction"]
            axes[direction] += 1
            level = signal["level"]
            axes[{"强": "strong", "中": "medium", "弱": "weak"}.get(
                level, "level_unknown")] += 1
            if signal["veto"]:
                axes["veto"] += 1

            if not signal["data_available"]:
                samples.append(self._excluded(code, name, snapshot_date,
                                              signal["reason"] or "signal_missing"))
                continue

            expected = auction_expected_direction(direction)
            samples.append(SignalSample(
                code=code, name=name, snapshot_date=snapshot_date,
                expected_direction=expected,
                group="auction_neutral" if expected is None else "auction",
                version=version))

        samples.sort(key=lambda sample: (sample.snapshot_date, sample.code))
        return {"samples": samples, "exclusions": dict(exclusions),
                "axes": dict(axes), "versions_seen": dict(versions_seen),
                "warnings": warnings}

    @staticmethod
    def _excluded(code: str, name: str | None, snapshot_date: str,
                  reason: str) -> SignalSample:
        return SignalSample(code=code, name=name, snapshot_date=snapshot_date,
                            expected_direction=None, group="data_insufficient",
                            exclusion_reason=reason)


# ─── 模板方法：窗口 / 方向 / 聚合 ────────────────────────────────────────


def _empty_summary() -> dict:
    return {"correct": 0, "valid": 0, "direction_valid": 0, "neutral": 0,
            "excluded": 0, "hit_rate": None, "excluded_by_reason": {},
            "direction_denominator": "bull_or_bear_only"}


def _finite_values(values: Sequence[Any]) -> list[float]:
    """只保留有限数值：``None`` / 非数值 / inf / nan 不是有效收益。"""
    result: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result.append(number)
    return result


def _mean(values: Sequence[float]) -> float | None:
    numbers = _finite_values(values)
    return round(sum(numbers) / len(numbers), 4) if numbers else None


def _date_equal_mean(records: Sequence[Mapping[str, Any]]) -> float | None:
    """按交易日等权：先算每日均值，再对日期取均值（A11）。"""
    by_day: dict[str, list[float]] = {}
    for record in records:
        value = _finite_values([record.get("net_return_pct")])
        if value:
            by_day.setdefault(record["snapshot_date"], []).append(value[0])
    daily = [_mean(values) for values in by_day.values()]
    daily = [value for value in daily if value is not None]
    return _mean(daily)


def _group_stats(records: Sequence[Mapping[str, Any]]) -> dict:
    returns = _finite_values([record.get("net_return_pct")
                              for record in records])
    wins = sum(1 for value in returns if value > 0)
    losses = sum(1 for value in returns if value < 0)
    flats = sum(1 for value in returns if value == 0)
    return {
        "samples": len(records),
        "stocks": len({record["code"] for record in records}),
        "days": len({record["snapshot_date"] for record in records}),
        "mean_return_pct": _mean(returns),
        "median_return_pct": round(median(returns), 4) if returns else None,
        "date_equal_mean_return_pct": _date_equal_mean(records),
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "win_rate": round(wins / len(returns), 4) if returns else None,
        "avoided_loss_rate": round(losses / len(returns), 4) if returns else None,
        "missed_gain_rate": round(wins / len(returns), 4) if returns else None,
    }


def _bootstrap_interval(
        left: Sequence[tuple[str, float]],
        right: Sequence[tuple[str, float]], *,
        seed: int = BOOTSTRAP_SEED,
        resamples: int = BOOTSTRAP_RESAMPLES) -> dict:
    """**日期级成组**重抽样的组间均值差区间（固定 seed，可复现）。

    输入是 ``(日期, 数值)``：每次有放回抽取**日期**，再把该日全部样本一起纳入，
    保留同日样本相关性；不是对股票收益值做独立重抽样。样本/独立日不足不出区间。
    ``None`` / 非有限值不是有效收益，进入重采样前剔除（不当作 0）。
    """
    left = [(day, values[0]) for day, value in left
            if (values := _finite_values([value]))]
    right = [(day, values[0]) for day, value in right
             if (values := _finite_values([value]))]
    left_days = sorted({day for day, _ in left})
    right_days = sorted({day for day, _ in right})
    if (len(left) < BOOTSTRAP_MIN_SAMPLES
            or len(right) < BOOTSTRAP_MIN_SAMPLES
            or len(left_days) < 2 or len(right_days) < 2):
        return {"status": "insufficient_samples", "seed": seed,
                "resamples": resamples, "low": None, "high": None,
                "left_days": len(left_days), "right_days": len(right_days),
                "caveat": "独立日样本不足，不提供区间"}

    def by_day(items: Sequence[tuple[str, float]]) -> dict[str, list[float]]:
        grouped: dict[str, list[float]] = {}
        for day, value in items:
            grouped.setdefault(day, []).append(value)
        return grouped

    left_map, right_map = by_day(left), by_day(right)
    rng = random.Random(seed)
    diffs = []
    for _ in range(resamples):
        sampled_left = [rng.choice(left_days) for _ in left_days]
        sampled_right = [rng.choice(right_days) for _ in right_days]
        l_values = [v for day in sampled_left for v in left_map[day]]
        r_values = [v for day in sampled_right for v in right_map[day]]
        diffs.append(sum(l_values) / len(l_values)
                     - sum(r_values) / len(r_values))
    diffs.sort()
    low = diffs[int(0.025 * (len(diffs) - 1))]
    high = diffs[int(0.975 * (len(diffs) - 1))]
    return {"status": "ok", "seed": seed, "resamples": resamples,
            "low": round(low, 4), "high": round(high, 4),
            "left_days": len(left_days), "right_days": len(right_days),
            "method": "date_block_bootstrap",
            "caveat": "日期级重抽样的描述性区间，非独立样本显著性"}


def _classify_net(value: float) -> str:
    rounded = round(value, 6)
    if rounded > 0:
        return "up"
    if rounded < 0:
        return "down"
    return "flat"


class _MappingBarsMarket:
    """把 ``{code: [bar dict]}`` 适配为 ``build_tradable_labels`` 的 market 鸭子类型。"""

    def __init__(self, bars: Mapping[str, Sequence[Mapping[str, Any]]] | None):
        self._bars = {str(code): list(rows or [])
                      for code, rows in (bars or {}).items()}

    def daily(self, code: str, start: str | None = None) -> list:
        rows = self._bars.get(str(code), [])
        if not start:
            return list(rows)
        return [row for row in rows
                if str(row.get("date") or "") >= start]


def evaluate_accuracy(
    samples: Sequence[SignalSample],
    bars: Mapping[str, Sequence[Mapping[str, Any]]] | None,
    calendar: Sequence[str],
    window: AccuracyWindow,
    cutoff: str | None,
    *,
    scope: str = "prep",
    horizons: Sequence[int] = ACCURACY_HORIZONS,
    cost_bps: float | None = None,
) -> dict:
    """主研究合同：可成交开盘 → 第 N 交易日开盘、扣成本。

    直接复用 ``infra.factor.review_shadow.build_tradable_labels``（与
    ``decision_review`` 同一纯核），因此同输入/同 cost 下 Web 与 CLI 口径一致。
    ``scope="prep"`` 的快照 T 日 16:40 才可用 → 次日开盘入场；``auction`` 09:25
    → 当日开盘入场。方向判断基于**净收益**（含成本），不是毛价差。
    """
    days = sorted({str(day) for day in (calendar or []) if day})
    horizon_list = sorted({int(h) for h in horizons if int(h) > 0})
    cost = TRADABLE_DEFAULT_COST_BPS if cost_bps is None else float(cost_bps)
    clock = "16:40" if scope == "prep" else "09:25"
    observations = [
        {"code": sample.code,
         "available_at": f"{sample.snapshot_date}T{clock}:00+08:00"}
        for sample in samples if sample.exclusion_reason is None
    ]
    label_result = build_tradable_labels(
        observations, _MappingBarsMarket(bars), days,
        horizons=horizon_list, cost_bps=cost)
    label_index = {
        (str(label["trade_date"]), str(label["code"]), int(label["horizon"])): label
        for label in label_result["labels"]
    }
    exclusion_index = {
        (str(item["trade_date"]), str(item["code"]), int(item["horizon"])):
            item["reason"]
        for item in label_result.get("exclusion_items", [])
    }

    per_code: dict[str, dict] = {}
    records_by_h: dict[int, list[dict]] = {h: [] for h in horizon_list}
    reasons_by_h: dict[int, Counter[str]] = {
        h: Counter() for h in horizon_list}
    sample_reasons: Counter[str] = Counter()
    excluded_samples = 0

    for sample in sorted(samples, key=lambda item: (item.code,
                                                    item.snapshot_date)):
        stock = per_code.setdefault(sample.code, {
            "code": sample.code, "name": sample.name,
            "correct": 0, "valid": 0, "excluded": 0,
            "direction_valid": 0})
        if stock.get("name") is None and sample.name:
            stock["name"] = sample.name
        if sample.exclusion_reason:
            excluded_samples += 1
            sample_reasons[sample.exclusion_reason] += 1
            stock["excluded"] += 1
            continue
        if cutoff is None or sample.snapshot_date > cutoff:
            excluded_samples += 1
            sample_reasons["immature"] += 1
            stock["excluded"] += 1
            continue
        sample_valid = False
        for horizon in horizon_list:
            key = (sample.snapshot_date, sample.code, horizon)
            label = label_index.get(key)
            if label is None:
                reason = exclusion_index.get(key, "immature")
                if reason == "insufficient_horizon":
                    reason = "immature"
                reasons_by_h[horizon][reason] += 1
                continue
            net_values = _finite_values([label["value"]])
            if not net_values:
                # 该 horizon 标签存在但收益非有限：按无效样本排除，不填 0。
                reasons_by_h[horizon]["invalid_value"] += 1
                continue
            net = net_values[0]
            actual = _classify_net(net)
            record = {
                "code": sample.code,
                "snapshot_date": sample.snapshot_date,
                "group": sample.group,
                "expected_direction": sample.expected_direction,
                "actual_direction": actual,
                "net_return_pct": round(net, 6),
                "entry_date": label["entry_date"],
                "exit_date": label["exit_date"],
                "version": sample.version,
            }
            records_by_h[horizon].append(record)
            sample_valid = True
            if horizon == _primary_horizon(horizon_list):
                stock["valid"] += 1
                if sample.expected_direction in ("up", "down"):
                    stock["direction_valid"] += 1
                    if actual == sample.expected_direction:
                        stock["correct"] += 1
        if not sample_valid:
            excluded_samples += 1
            stock["excluded"] += 1

    primary = _primary_horizon(horizon_list)
    horizon_payloads: dict[str, dict] = {}
    for horizon in horizon_list:
        records = records_by_h[horizon]
        groups: dict[str, list[dict]] = {}
        for record in records:
            groups.setdefault(record["group"], []).append(record)
        group_stats = {name: _group_stats(items)
                       for name, items in groups.items()}
        spread = None
        if "recommended" in groups and "not_recommended" in groups:
            left = [(r["snapshot_date"], r["net_return_pct"])
                    for r in groups["recommended"]]
            right = [(r["snapshot_date"], r["net_return_pct"])
                     for r in groups["not_recommended"]]
            left_values = [value for _, value in left]
            right_values = [value for _, value in right]
            spread = {
                "pair": ["recommended", "not_recommended"],
                "basis": "mean_net_return_pct",
                "value": (round(sum(left_values) / len(left_values)
                                - sum(right_values) / len(right_values), 4)
                          if left_values and right_values else None),
                "bootstrap": _bootstrap_interval(left, right),
            }
        direction_valid = sum(
            1 for record in records
            if record["expected_direction"] in ("up", "down"))
        correct = sum(
            1 for record in records
            if record["expected_direction"] in ("up", "down")
            and record["actual_direction"] == record["expected_direction"])
        horizon_payloads[str(horizon)] = {
            "horizon": horizon,
            "valid": len(records),
            "direction_valid": direction_valid,
            "neutral": len(records) - direction_valid,
            "correct": correct,
            "hit_rate": (round(correct / direction_valid, 4)
                         if direction_valid else None),
            "excluded_by_reason": dict(sorted(reasons_by_h[horizon].items())),
            "groups": group_stats,
            "spread": spread,
            "independent_dates": len({r["snapshot_date"] for r in records}),
        }

    primary_payload = horizon_payloads[str(primary)]
    stocks: list[dict] = []
    for stock in per_code.values():
        denominator = stock.get("direction_valid", 0)
        stocks.append({
            **stock,
            "hit_rate": (round(stock["correct"] / denominator, 4)
                         if denominator else None),
        })
    stocks.sort(key=lambda item: (-item["valid"], item["code"]))

    versions = sorted({str(r["version"]) for records in records_by_h.values()
                       for r in records if r.get("version") is not None})
    stratification = {
        "basis": "snapshot_version",
        "versions": versions,
        "comparable": len(versions) <= 1,
        "by_version": {
            version: {
                h: {
                    "valid": sum(1 for r in records_by_h[int(h)]
                                 if str(r.get("version")) == version),
                    "mean_net_return_pct": _mean([
                        r["net_return_pct"] for r in records_by_h[int(h)]
                        if str(r.get("version")) == version]),
                }
                for h in horizon_payloads
            }
            for version in versions
        },
        "note": ("跨版本默认不可比；混合计算仅作为描述性合计"
                 if len(versions) > 1 else "单一版本"),
    }
    summary = {
        "correct": primary_payload["correct"],
        "valid": primary_payload["valid"],
        "direction_valid": primary_payload["direction_valid"],
        "neutral": primary_payload["neutral"],
        "excluded": excluded_samples,
        "hit_rate": primary_payload["hit_rate"],
        "direction_denominator": "bull_or_bear_or_up_only",
        "excluded_by_reason": dict(sorted(
            (sample_reasons + reasons_by_h[primary]).items())),
        "primary_horizon": primary,
    }
    return {
        "summary": summary,
        "groups": primary_payload["groups"],
        "spread": primary_payload["spread"],
        "horizons": horizon_payloads,
        "stocks": stocks,
        "as_of": cutoff,
        "latest_bar_date": days[-1] if days else None,
        "independent_dates": primary_payload["independent_dates"],
        "independent_codes": len({record["code"]
                                  for record in records_by_h[primary]}),
        "sample_unit": "stock_day",
        "contract": {
            "id": "decision_review_open_to_open_net",
            "entry_rule": "available_at -> first tradable open",
            "exit_rule": "nth trading day open",
            "cost_bps": cost,
            "horizons": horizon_list,
            "legacy_raw_open_to_close": False,
        },
        "stratification": stratification,
        "day_returns": [
            {"snapshot_date": day,
             "mean_return_pct": _mean([
                 r["net_return_pct"] for r in records_by_h[primary]
                 if r["snapshot_date"] == day])}
            for day in sorted({r["snapshot_date"]
                               for r in records_by_h[primary]})
        ],
    }


def _primary_horizon(horizons: Sequence[int]) -> int:
    ordered = sorted(horizons)
    return ordered[len(ordered) // 2]


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
    groups: Mapping[str, Any] | None = None,
    spread: Any = None,
    independent_dates: int = 0,
    independent_codes: int = 0,
    versions_seen: Mapping[str, int] | None = None,
    axes: Mapping[str, int] | None = None,
    market_day_effect: Mapping[str, Any] | None = None,
    horizons: Mapping[str, Any] | None = None,
    contract: Mapping[str, Any] | None = None,
    stratification: Mapping[str, Any] | None = None,
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
        "groups": dict(groups or {}),
        "spread": spread,
        "independent_dates": independent_dates,
        "independent_codes": independent_codes,
        "versions_seen": dict(versions_seen or {}),
        "horizons": dict(horizons or {}),
        "contract": dict(contract or {}),
        "stratification": dict(stratification or {}),
        "axes": dict(axes or {}),
        "market_day_effect": dict(market_day_effect or {}),
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
    horizons: Sequence[int] = ACCURACY_HORIZONS,
    cost_bps: float | None = None,
) -> dict:
    """prep 全历史准确率（可成交开盘 → 第 N 交易日开盘，默认扣 13bp）。"""

    dates = sorted({str(day) for day in snapshot_dates if day})
    cutoff = _cutoff(bar_dates, max(horizons))
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
    versions_seen: Counter[str] = Counter()
    day_kind_by_date: dict[str, str] = {}
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
        versions_seen[str(payload.get("version"))] += 1
        selection = strategy.select(payload)
        warnings.extend(selection["warnings"])
        exclusion = selection["snapshot_exclusion"]
        if exclusion:
            excluded_dates[exclusion] += 1
            continue
        day_kinds[selection["day_kind"]] += 1
        day_kind_by_date[day] = selection["day_kind"]
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
        samples, rows, bar_dates, ACCURACY_WINDOW_PREP, cutoff,
        scope="prep", horizons=horizons, cost_bps=cost_bps)
    any_valid = any(
        payload.get("valid", 0) > 0
        for payload in evaluated["horizons"].values())
    if not bar_dates:
        status, reason = "insufficient_data", "local_bars_missing"
    elif not any_valid:
        status, reason = "insufficient_data", "no_mature_samples"
    else:
        status, reason = "ok", None

    # 市场空仓风险效果：按「日型」分组、日期等权（A4）。
    per_kind: dict[str, list[float]] = {}
    for row in evaluated.get("day_returns") or []:
        kind = day_kind_by_date.get(row["snapshot_date"])
        if kind and row["mean_return_pct"] is not None:
            per_kind.setdefault(kind, []).append(row["mean_return_pct"])
    market_day_effect = {
        kind: {"days": len(values),
               "date_equal_mean_return_pct": _mean(values)}
        for kind, values in per_kind.items()
    }

    return _response(
        status=status, reason=reason, bar_dates=bar_dates, today=today,
        cutoff=evaluated["as_of"], window=ACCURACY_WINDOW_PREP,
        snapshot_count=len(dates), day_kind_counts=day_kinds,
        summary=evaluated["summary"], stocks=evaluated["stocks"],
        excluded_snapshot_dates=excluded_dates, warnings=warnings,
        groups=evaluated["groups"], spread=evaluated["spread"],
        independent_dates=evaluated["independent_dates"],
        independent_codes=evaluated["independent_codes"],
        versions_seen=versions_seen, market_day_effect=market_day_effect,
        horizons=evaluated["horizons"], contract=evaluated["contract"],
        stratification=evaluated["stratification"])


def build_auction_accuracy(
    *,
    snapshot_dates: Sequence[str],
    load_snapshot_rows: Callable[[str], Iterable[Any]],
    load_names: Callable[[str], Mapping[str, str]],
    bar_dates: Sequence[str],
    load_bars: Callable[[list[str], str, str], Mapping],
    today: str | None = None,
    horizons: Sequence[int] = ACCURACY_HORIZONS,
    cost_bps: float | None = None,
) -> dict:
    """auction 全历史准确率（可成交开盘 → 第 N 交易日开盘，默认扣 13bp）。"""

    dates = sorted({str(day) for day in snapshot_dates if day})
    cutoff = _cutoff(bar_dates, max(horizons))
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
    axes: Counter[str] = Counter()
    versions_seen: Counter[str] = Counter()
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
        for key, count in (selection.get("axes") or {}).items():
            axes[key] += count
        for key, count in (selection.get("versions_seen") or {}).items():
            versions_seen[key] += count
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
        samples, rows_by_code, bar_dates, ACCURACY_WINDOW_AUCTION, cutoff,
        scope="auction", horizons=horizons, cost_bps=cost_bps)
    any_valid = any(
        payload.get("valid", 0) > 0
        for payload in evaluated["horizons"].values())
    if not bar_dates:
        status, reason = "insufficient_data", "local_bars_missing"
    elif not any_valid:
        status, reason = "insufficient_data", "no_mature_samples"
    else:
        status, reason = "ok", None
    return _response(
        status=status, reason=reason, bar_dates=bar_dates, today=today,
        cutoff=evaluated["as_of"], window=ACCURACY_WINDOW_AUCTION,
        snapshot_count=len(dates), day_kind_counts={},
        summary=evaluated["summary"], stocks=evaluated["stocks"],
        excluded_snapshot_dates=excluded_dates, warnings=warnings,
        groups=evaluated["groups"], spread=evaluated["spread"],
        independent_dates=evaluated["independent_dates"],
        independent_codes=evaluated["independent_codes"],
        versions_seen=versions_seen, axes=axes,
        horizons=evaluated["horizons"], contract=evaluated["contract"],
        stratification=evaluated["stratification"])
