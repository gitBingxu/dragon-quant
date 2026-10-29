"""prep / auction 决策结果回测（``factor decision-review``）纯核心。

设计约束（技术方案 ``archived_md/factor_framework/2026-10-29-decision-outcome-review-technical-design.md``）：
  - **纯函数、零 I/O**：不 import ``review_account`` / provider / ``infra.assets`` /
    sqlite3 / requests / urllib / pandas；快照经 ``SnapshotStore`` 注入、行情经
    ``market``（``daily`` 必需、``calendar`` 可选）注入。
  - **Adapter + Strategy + Template Method**：``PrepSnapshotAdapter`` /
    ``AuctionSnapshotAdapter`` 把异构快照映射为同构 ``DecisionObservation``；
    版本白名单、分组语义、组序随适配器（策略）走，骨架固定。
  - **方向判定用净收益（G2）**：``up = net > 0`` / ``down = net < 0`` /
    ``flat = round(net, 6) == 0``（保本）；``gross`` 只进逐样本账本供审计。
  - **排除样本不删**：``build_tradable_labels`` 返回 ``insufficient_data`` 时
    账本仍按观测 × horizon 全量产出（全部带剔除原因），分母与覆盖率不消失。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

from . import stats as fstats
from .review_shadow import DEFAULT_COST_BPS, _bar_date, build_tradable_labels

DEFAULT_HORIZONS: tuple[int, ...] = (1, 3, 5)
DEFAULT_MIN_DAYS = 20
DEFAULT_MIN_GROUP_SAMPLES = 30
SCHEMA_VERSION = "dr_v1"
REFERENCE_LINE = 0.65
WILSON_CAVEAT = "date_correlated_descriptive_only"
FLAT_EPS = 1e-6          # 净收益归零精度（个百分点）

UNIVERSE = "persisted_decision_snapshots"
COVERAGE_BASIS = "persisted_snapshots"
OUTCOME_BASIS = "net_open_to_open_after_cost"
NET_RETURN_BASIS = "gross_minus_cost"
ENTRY_RULE = "available_at_first_tradable_open"
EXIT_RULE = "nth_trading_day_open"


# ─── 组定义（策略随来源）──────────────────────────────────────────────────


@dataclass(frozen=True)
class GroupSpec:
    key: str
    label: str
    expected_direction: Optional[str]       # "up" | "down" | None


SOURCE_GROUP_SPECS: dict[str, tuple[GroupSpec, ...]] = {
    "prep": (
        GroupSpec("recommended", "推荐", "up"),
        GroupSpec("not_recommended", "明确不推荐", "down"),
        GroupSpec("data_insufficient", "数据不足", None),
    ),
    "auction": (
        GroupSpec("strong", "强", "up"),
        GroupSpec("medium", "中", None),
        GroupSpec("weak", "弱", "down"),
        GroupSpec("vetoed", "否决", "down"),
        GroupSpec("unavailable", "数据不足", None),
    ),
}

ACCEPTED_VERSIONS: dict[str, tuple[str, ...]] = {
    "prep": ("0.7",),
    "auction": ("2",),
}

SPREAD_PAIRS: dict[str, tuple[str, str]] = {
    "prep": ("recommended", "not_recommended"),
    "auction": ("strong", "weak"),
}

PREP_CAVEATS = (
    "方向命中率仅描述历史样本，不构成策略有效性结论",
    "Wilson 95% 区间假设样本近似独立；同日多只与相邻 horizon 存在相关性，区间偏窄",
    "prep 分组为决策语义：position_pct=0 表示不建议新建仓，不等于看空",
)
AUCTION_CAVEATS = (
    "方向命中率仅描述历史样本，不构成策略有效性结论",
    "Wilson 95% 区间假设样本近似独立；同日多只与相邻 horizon 存在相关性，区间偏窄",
    "auction 分母仅为已持久化快照，不含未持久化的空盘口/失败候选",
)


# ─── 数据模型 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionObservation:
    source: str
    snapshot_version: str
    snapshot_date: str
    snapshot_created_at: Optional[str]
    code: str
    name: Optional[str]
    group: str
    available_at: str
    score: Optional[float]
    level: Optional[str]
    veto: bool
    data_quality_status: Optional[str]


@dataclass(frozen=True)
class SnapshotDay:
    snapshot_date: str
    version: Optional[str]
    created_at: Optional[str]
    observations: tuple[DecisionObservation, ...]
    counters: Mapping[str, Any]


# ─── 通用数值 helper ──────────────────────────────────────────────────────


def _finite(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text_or_none(value) -> Optional[str]:
    return None if value is None else str(value)


def _calendar_day(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().replace("T", " ")
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return None


def _to_day(value) -> Optional[str]:
    """把 ``datetime`` / ``date`` / 字符串 / epoch(ms|s) 归一为 ``YYYY-MM-DD``。"""
    if isinstance(value, str):
        return _calendar_day(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # provider 的 ``timestamp`` 是 epoch 毫秒；秒级输入也兼容。
        scale = 1000.0 if abs(float(value)) > 1e11 else 1.0
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        return _dt.fromtimestamp(float(value) / scale,
                                 _tz(_td(hours=8))).date().isoformat()
    date_method = getattr(value, "date", None)
    if callable(date_method):
        return date_method().isoformat()
    return None


def _bar_field(bar, name: str, default=None):
    if isinstance(bar, Mapping):
        return bar.get(name, default)
    return getattr(bar, name, default)


def _bar_day(bar) -> Optional[str]:
    if isinstance(bar, Mapping):
        candidates = (bar.get("trade_date"), bar.get("date"), bar.get("timestamp"))
    else:
        candidates = (getattr(bar, "trade_date", None),
                      getattr(bar, "date", None),
                      getattr(bar, "timestamp", None))
    for value in candidates:
        if value is None:
            continue
        day = _to_day(value)
        if day:
            return day
    return None


def _normalize_bar(bar) -> dict:
    """保留可成交口径所需的字段，并补一个 ISO ``trade_date``。

    provider 的原始日 K 以 epoch 毫秒 ``timestamp`` 标识日期，而
    ``review_shadow._bar_date`` 只认 ``trade_date`` 字符串 / ``datetime``；
    在注入边界统一归一，避免把有效行情误判为 ``missing_bar``。
    """
    return {
        "trade_date": _bar_day(bar),
        "open": _bar_field(bar, "open"),
        "volume": _bar_field(bar, "volume"),
        "amount": _bar_field(bar, "amount"),
        "limit_up_price": _bar_field(bar, "limit_up_price"),
        "limit_up": _bar_field(bar, "limit_up"),
    }


# ─── Adapters（策略：分组 / 版本 / 组序）──────────────────────────────────


class PrepSnapshotAdapter:
    source = "prep"
    accepted_versions = ACCEPTED_VERSIONS["prep"]
    group_specs = SOURCE_GROUP_SPECS["prep"]

    def __init__(self, store):
        self.store = store

    def snapshot_dates(self, date_from: str, date_to: str) -> list[str]:
        return self.store.snapshot_dates(date_from, date_to)

    def load_day(self, snapshot_date: str) -> SnapshotDay:
        payload, meta = self.store.load(snapshot_date)
        created = (meta or {}).get("created_at") if isinstance(meta, Mapping) else None
        return self.map_day(snapshot_date, payload, created)

    def map_day(self, snapshot_date: str, payload, created_at=None) -> SnapshotDay:
        counters = {
            "malformed": 0, "empty_decision": 0, "duplicate_code": 0,
            "unparsed_position_pct": 0, "frozen_at_mismatch": 0,
            "unsupported_version": 0, "versions": {},
        }
        payload = payload if isinstance(payload, Mapping) else {}
        version = payload.get("version")
        version_key = "null" if version is None else str(version)
        counters["versions"][version_key] = \
            counters["versions"].get(version_key, 0) + 1
        if version not in self.accepted_versions:
            counters["unsupported_version"] = 1
            return SnapshotDay(snapshot_date, version, created_at, (), counters)

        candidates = payload.get("review_candidates")
        if not isinstance(candidates, list):
            counters["malformed"] = 1
            return SnapshotDay(snapshot_date, version, created_at, (), counters)

        strategy = payload.get("strategy_context")
        frozen_at = strategy.get("frozen_at") if isinstance(strategy, Mapping) else None
        if frozen_at and _calendar_day(frozen_at) not in (None, snapshot_date):
            counters["frozen_at_mismatch"] = 1

        observations: list[DecisionObservation] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            raw_code = candidate.get("code")
            if not raw_code:
                continue
            code = str(raw_code)
            if code in seen:
                counters["duplicate_code"] += 1
                continue
            seen.add(code)

            position = candidate.get("position_pct")
            numeric: Optional[float] = None
            if isinstance(position, (int, float)) and not isinstance(position, bool):
                numeric = _finite(position)
            if numeric is not None:
                if numeric == 0:
                    group = "not_recommended"
                elif numeric > 0 and candidate.get("eligibility") == "eligible":
                    group = "recommended"
                else:
                    group = "data_insufficient"
            else:
                group = "data_insufficient"
                if position is not None:
                    counters["unparsed_position_pct"] += 1

            observations.append(DecisionObservation(
                source=self.source,
                snapshot_version=str(version),
                snapshot_date=snapshot_date,
                snapshot_created_at=created_at,
                code=code,
                name=_text_or_none(candidate.get("name")),
                group=group,
                available_at=f"{snapshot_date}T16:40:00+08:00",
                score=_finite(candidate.get("review_score")),
                level=_text_or_none(candidate.get("review_grade")),
                veto=False,
                data_quality_status=None,
            ))

        if not observations:
            counters["empty_decision"] = 1
        return SnapshotDay(snapshot_date, version, created_at,
                           tuple(observations), counters)


_LEVEL_GROUPS = {"强": "strong", "中": "medium", "弱": "weak"}


def _auction_group(row: Mapping) -> tuple[str, bool]:
    """返回 ``(group, unmapped_level)``；优先级 unavailable → vetoed → level。"""
    quality = row.get("data_quality")
    status = quality.get("status") if isinstance(quality, Mapping) else None
    if "unverified" not in row:
        return "unavailable", False          # 缺键 fail-closed
    unverified = row.get("unverified")
    strength = _finite(row.get("strength"))
    level = row.get("level")
    level_valid = level in _LEVEL_GROUPS
    if (status == "unavailable" or unverified is True
            or strength is None or level is None or not level_valid):
        unmapped = level is not None and not level_valid
        return "unavailable", unmapped
    if row.get("is_weak") is True:
        return "vetoed", False
    return _LEVEL_GROUPS[level], False


class AuctionSnapshotAdapter:
    source = "auction"
    accepted_versions = ACCEPTED_VERSIONS["auction"]
    group_specs = SOURCE_GROUP_SPECS["auction"]

    def __init__(self, store):
        self.store = store

    def snapshot_dates(self, date_from: str, date_to: str) -> list[str]:
        return self.store.snapshot_dates(date_from, date_to)

    def load_day(self, snapshot_date: str) -> SnapshotDay:
        return self.map_day(snapshot_date, self.store.load(snapshot_date))

    def map_day(self, snapshot_date: str, rows) -> SnapshotDay:
        counters = {
            "corrupt_rows": 0, "unsupported_version": 0, "duplicate_code": 0,
            "unmapped_level": 0, "usable_rows": 0, "versions": {},
        }
        observations: list[DecisionObservation] = []
        seen: set[str] = set()
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            if row.get("error"):
                counters["corrupt_rows"] += 1
                continue
            version = row.get("version")
            version_key = "null" if version is None else str(version)
            counters["versions"][version_key] = \
                counters["versions"].get(version_key, 0) + 1
            if version not in self.accepted_versions:
                counters["unsupported_version"] += 1
                continue
            counters["usable_rows"] += 1
            raw_code = row.get("code")
            if not raw_code:
                continue
            code = str(raw_code)
            if code in seen:
                counters["duplicate_code"] += 1
                continue
            seen.add(code)
            group, unmapped = _auction_group(row)
            if unmapped:
                counters["unmapped_level"] += 1
            quality = row.get("data_quality")
            status = quality.get("status") if isinstance(quality, Mapping) else None
            observations.append(DecisionObservation(
                source=self.source,
                snapshot_version=str(version),
                snapshot_date=snapshot_date,
                snapshot_created_at=_text_or_none(row.get("created_at")),
                code=code,
                name=None,
                group=group,
                available_at=f"{snapshot_date}T09:25:00+08:00",
                score=_finite(row.get("strength")),
                level=_text_or_none(row.get("level")),
                veto=row.get("is_weak") is True,
                data_quality_status=_text_or_none(status),
            ))
        return SnapshotDay(snapshot_date, None, None, tuple(observations), counters)


def build_adapter(source: str, store):
    if source == "prep":
        return PrepSnapshotAdapter(store)
    if source == "auction":
        return AuctionSnapshotAdapter(store)
    raise ValueError(f"未知决策回测来源: {source!r}（合法值 prep / auction）")


# ─── 行情注入缓存 ─────────────────────────────────────────────────────────


class _BarView:
    """按 code 只取一次日 K；``build_tradable_labels`` 的多次 ``daily`` 命中缓存。"""

    def __init__(self, market, start: str):
        self._market = market
        self._start = start
        self._bars: dict[str, list] = {}
        self.errors: dict[str, str] = {}

    def load(self, code: str) -> list:
        if code in self._bars:
            return self._bars[code]
        try:
            raw = self._market.daily(code, self._start) or []
        except Exception as exc:      # 行情缺失只降级，不抛给调用方
            self.errors[code] = f"{type(exc).__name__}: {exc}"
            raw = []
        self._bars[code] = [_normalize_bar(bar) for bar in raw]
        return self._bars[code]

    def daily(self, code, start):
        return self.load(code)

    def bars_by_code(self) -> dict[str, list]:
        return self._bars


def _resolve_calendar(market, view: _BarView, window_start: str,
                      window_end: str) -> tuple[list[str], str]:
    getter = getattr(market, "calendar", None)
    dates: list[str] = []
    if callable(getter):
        try:
            raw = getter(window_start, window_end)
            if isinstance(raw, (list, tuple, set)):
                dates = sorted({day for day in (_calendar_day(item) for item in raw)
                                if day and window_start <= day <= window_end})
        except Exception:
            dates = []
    if dates:
        return dates, "index_calendar"
    union = sorted({
        day for bars in view.bars_by_code().values()
        for bar in bars
        for day in [_bar_date(bar)]
        if day and window_start <= day <= window_end
    })
    return union, "bars_union"


# ─── 校验 helper ──────────────────────────────────────────────────────────


def _normalize_horizons(horizons) -> list[int]:
    if horizons is None:
        return list(DEFAULT_HORIZONS)
    values: list[int] = []
    for item in horizons:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"horizon 必须是 ≥ 1 的整数: {item!r}")
        if item < 1:
            raise ValueError(f"horizon 必须是 ≥ 1 的整数: {item!r}")
        values.append(int(item))
    if not values:
        return list(DEFAULT_HORIZONS)
    return sorted(set(values))


def _validate_cost(cost_bps) -> float:
    number = _finite(cost_bps)
    if number is None or number < 0:
        raise ValueError(f"cost_bps 必须是有限非负数: {cost_bps!r}")
    return number


def _classify(net_return_pct: float) -> str:
    rounded = round(net_return_pct, 6)
    if rounded > 0:
        return "up"
    if rounded < 0:
        return "down"
    return "flat"


# ─── 指标聚合 ─────────────────────────────────────────────────────────────


def _aggregate_horizon(rows: list[dict], expected_direction: Optional[str],
                       horizon: int, min_days: int,
                       min_group_samples: int) -> dict:
    observations = len(rows)
    valid_rows = [row for row in rows if row["outcome"] is not None]
    valid = len(valid_rows)
    excluded = observations - valid
    coverage = (valid / observations) if observations else None

    up = sum(1 for row in valid_rows if row["outcome"] == "up")
    down = sum(1 for row in valid_rows if row["outcome"] == "down")
    flat = sum(1 for row in valid_rows if row["outcome"] == "flat")

    direction_evaluable = expected_direction in ("up", "down") and valid > 0
    if direction_evaluable:
        hits = sum(1 for row in valid_rows if row["outcome"] == expected_direction)
        direction_hit_rate = hits / valid
        wilson = fstats.wilson_interval(hits, valid)
    else:
        direction_hit_rate = None
        wilson = None

    by_day: dict[str, list[dict]] = {}
    for row in valid_rows:
        by_day.setdefault(row["snapshot_date"], []).append(row)
    daily_rates: list[float] = []
    if direction_evaluable:
        for day in sorted(by_day):
            day_rows = by_day[day]
            daily_rates.append(
                sum(1 for row in day_rows if row["outcome"] == expected_direction)
                / len(day_rows))
    daily_hit_rate_mean = fstats.mean(daily_rates) if daily_rates else None

    days = len(by_day)
    days_with_observations = len({row["snapshot_date"] for row in rows})
    effective_independent_days = (0 if days == 0
                                  else max(1, int(round(days / horizon))))

    nets = [row["net_return_pct"] for row in valid_rows]
    ordered = sorted(nets)
    mean_net = fstats.mean(nets)
    median_net = fstats.median(nets)
    p25 = fstats.quantile(ordered, 0.25) if ordered else None
    p75 = fstats.quantile(ordered, 0.75) if ordered else None

    if observations == 0:
        quality = "not_evaluable"
    elif days < min_days or valid < min_group_samples:
        quality = "low_confidence"
    else:
        quality = "ok"

    above_reference = None
    if quality == "ok" and direction_evaluable and direction_hit_rate is not None:
        above_reference = direction_hit_rate >= REFERENCE_LINE

    return {
        "observations": observations,
        "valid": valid,
        "excluded": excluded,
        "coverage": coverage,
        "up": up,
        "down": down,
        "flat": flat,
        "direction_evaluable": direction_evaluable,
        "direction_hit_rate": direction_hit_rate,
        "direction_hit_denominator": "valid_including_flat",
        "daily_hit_rate_mean": daily_hit_rate_mean,
        "days": days,
        "days_with_observations": days_with_observations,
        "effective_independent_days": effective_independent_days,
        "wilson95": wilson,
        "wilson_n": valid if direction_evaluable else None,
        "wilson_caveat": WILSON_CAVEAT,
        "mean_net_return_pct": mean_net,
        "median_net_return_pct": median_net,
        "p25_net_return_pct": p25,
        "p75_net_return_pct": p75,
        "reference_line": REFERENCE_LINE,
        "above_reference": above_reference,
        "quality": quality,
        "expected_direction": expected_direction,
    }


# ─── 账本 + 装配 ──────────────────────────────────────────────────────────


def _ledger_row(observation: DecisionObservation, horizon: int,
                expected_direction: Optional[str], label,
                exclusion_reason: Optional[str], cost_bps: float) -> dict:
    row = {
        "sample_id": f"{observation.source}:{observation.snapshot_date}:"
                     f"{observation.code}:{horizon}",
        "source": observation.source,
        "snapshot_date": observation.snapshot_date,
        "snapshot_version": observation.snapshot_version,
        "snapshot_created_at": observation.snapshot_created_at,
        "code": observation.code,
        "name": observation.name,
        "group": observation.group,
        "expected_direction": expected_direction,
        "available_at": observation.available_at,
        "horizon": horizon,
        "entry_date": None,
        "exit_date": None,
        "entry_price": None,
        "exit_price": None,
        "cost_bps": cost_bps,
        "gross_return_pct": None,
        "net_return_pct": None,
        "outcome": None,
        "exclusion_reason": exclusion_reason,
    }
    if label is not None:
        entry_price = label["entry_price"]
        exit_price = label["exit_price"]
        gross = (exit_price / entry_price - 1.0) * 100.0
        net = gross - cost_bps / 100.0
        row.update({
            "entry_date": label["entry_date"],
            "exit_date": label["exit_date"],
            "entry_price": entry_price,
            "exit_price": exit_price,
            "gross_return_pct": gross,
            "net_return_pct": net,
            "outcome": _classify(net),
            "exclusion_reason": None,
        })
    return row


def _build_warnings(source: str, snapshots: Mapping, calendar_basis: str,
                    frozen_mismatch: int, market_errors: int,
                    market_codes: int) -> list[str]:
    warnings: list[str] = []
    unsupported = snapshots["unsupported_version"]
    if unsupported:
        unit = "日" if source == "prep" else "行"
        seen = snapshots["_seen"]
        warnings.append(
            f"unsupported_snapshot_version: {source} {unsupported} {unit}"
            f"（seen {seen}）；请先按当前实现重算"
            "（prep --recompute / auction --recompute DATE）")
    missing = snapshots["missing"]
    if missing:
        warnings.append(
            f"snapshot_missing: {missing}/{snapshots['requested_days']} 交易日无快照"
            "（仅统计库内持久化快照）")
    empty_decision = snapshots["empty_decision"]
    if empty_decision:
        warnings.append(f"empty_decision: {empty_decision} 日快照可解析但无决策观测")
    corrupt = snapshots["corrupt_rows"]
    if corrupt:
        warnings.append(f"corrupt_snapshot_rows: {corrupt} 行 JSON 损坏已跳过")
    if source == "auction":
        warnings.append(
            "coverage_basis=persisted_snapshots: 未持久化的空盘口/失败候选不在分母内"
            "（无 run manifest）")
    if calendar_basis == "bars_union":
        warnings.append(
            "calendar_fallback: 用个股日 K 并集代替指数交易日历，requested_days 可能缺失")
    if frozen_mismatch:
        warnings.append(
            f"prep_frozen_at_mismatch: {frozen_mismatch} 日 frozen_at 日期与 trade_date"
            " 不一致（入场仍按 16:40 契约）")
    if market_errors:
        warnings.append(f"market_data_missing: {market_errors}/{market_codes} 只无日 K")
    return warnings


def _assemble(*, adapter, date_from: str, date_to: str, horizons: list[int],
              cost_bps: float, min_days: int, min_group_samples: int,
              include_samples: bool, days: list[SnapshotDay],
              calendar: Optional[list[str]], ledger: list[dict],
              calendar_basis: str, reason: Optional[str],
              market_errors: int, market_codes: int) -> dict:
    version_units = list(adapter.accepted_versions)
    unsupported = sum(day.counters.get("unsupported_version", 0) for day in days)
    malformed = sum(day.counters.get("malformed", 0) for day in days)
    empty_decision = sum(day.counters.get("empty_decision", 0) for day in days)
    corrupt_rows = sum(day.counters.get("corrupt_rows", 0) for day in days)
    duplicate_code = sum(day.counters.get("duplicate_code", 0) for day in days)
    unparsed_position = sum(day.counters.get("unparsed_position_pct", 0)
                            for day in days)
    unmapped_level = sum(day.counters.get("unmapped_level", 0) for day in days)
    frozen_mismatch = sum(day.counters.get("frozen_at_mismatch", 0) for day in days)

    seen_versions: dict[str, int] = {}
    for day in days:
        for version, count in (day.counters.get("versions") or {}).items():
            seen_versions[version] = seen_versions.get(version, 0) + count
    seen_versions = dict(sorted(seen_versions.items()))

    snapshot_dates = [day.snapshot_date for day in days]
    with_snapshot = len(snapshot_dates)
    if calendar is None:
        requested_days = None
        missing = None
        non_trading = 0
    else:
        calendar_set = set(calendar)
        requested_days = sum(1 for day in calendar if date_from <= day <= date_to)
        intersections = sum(1 for day in snapshot_dates if day in calendar_set)
        missing = requested_days - intersections
        non_trading = sum(1 for day in snapshot_dates if day not in calendar_set)
    if adapter.source == "prep":
        usable = with_snapshot - unsupported - malformed
    else:
        usable = sum(1 for day in days if day.counters.get("usable_rows", 0) > 0)

    snapshots = {
        "requested_days": requested_days,
        "with_snapshot": with_snapshot,
        "missing": missing,
        "usable": usable,
        "unsupported_version": unsupported,
        "malformed": malformed,
        "empty_decision": empty_decision,
        "corrupt_rows": corrupt_rows,
        "non_trading_snapshot_dates": non_trading,
        "_seen": seen_versions,
    }

    groups: list[dict] = []
    group_lookup: dict[str, dict] = {}
    any_ok = False
    for spec in adapter.group_specs:
        horizon_map: dict[str, dict] = {}
        for horizon in horizons:
            rows = [row for row in ledger
                    if row["group"] == spec.key and row["horizon"] == horizon]
            summary = _aggregate_horizon(rows, spec.expected_direction, horizon,
                                         min_days, min_group_samples)
            if summary["quality"] == "ok":
                any_ok = True
            horizon_map[str(horizon)] = summary
        groups.append({
            "group": spec.key,
            "label": spec.label,
            "expected_direction": spec.expected_direction,
            "horizons": horizon_map,
        })
        group_lookup[spec.key] = horizon_map

    pair = SPREAD_PAIRS[adapter.source]
    spreads: dict[str, dict] = {}
    for horizon in horizons:
        left = group_lookup[pair[0]][str(horizon)]["mean_net_return_pct"]
        right = group_lookup[pair[1]][str(horizon)]["mean_net_return_pct"]
        spreads[str(horizon)] = {
            "pair": [pair[0], pair[1]],
            "basis": "mean_net_return_pct",
            "value": (left - right) if (left is not None and right is not None) else None,
        }

    sample_exclusions: dict[str, int] = {}
    for row in ledger:
        reason_value = row["exclusion_reason"]
        if reason_value:
            sample_exclusions[reason_value] = sample_exclusions.get(reason_value, 0) + 1
    sample_exclusions = dict(sorted(sample_exclusions.items()))
    snapshot_exclusions = {
        "unsupported_snapshot_version": unsupported,
        "missing_snapshot": missing,
        "duplicate_code": duplicate_code,
    }
    if adapter.source == "prep":
        snapshot_exclusions["unparsed_position_pct"] = unparsed_position
    else:
        snapshot_exclusions["unmapped_level"] = unmapped_level
    snapshot_exclusions = dict(sorted(snapshot_exclusions.items()))

    warnings = _build_warnings(adapter.source, snapshots, calendar_basis,
                               frozen_mismatch, market_errors, market_codes)
    public_snapshots = {key: value for key, value in snapshots.items()
                        if not key.startswith("_")}
    caveats = list(PREP_CAVEATS if adapter.source == "prep" else AUCTION_CAVEATS)
    status = "ok" if any_ok else "insufficient_data"

    config = {
        "horizons": list(horizons),
        "cost_bps": cost_bps,
        "min_days": min_days,
        "min_group_samples": min_group_samples,
        "include_samples": bool(include_samples),
        "outcome_basis": OUTCOME_BASIS,
        "net_return_basis": NET_RETURN_BASIS,
        "entry_rule": ENTRY_RULE,
        "exit_rule": EXIT_RULE,
        "calendar_basis": calendar_basis,
    }

    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "status": status,
    }
    if reason is not None:
        payload["reason"] = reason
    payload.update({
        "source": adapter.source,
        "date_from": date_from,
        "date_to": date_to,
        "universe": UNIVERSE,
        "coverage_basis": COVERAGE_BASIS,
        "config": config,
        "snapshots": public_snapshots,
        "versions": {
            "accepted": version_units,
            "seen": seen_versions,
            "unsupported_units": unsupported,
        },
        "groups": groups,
        "spreads": spreads,
        "exclusions": {
            "sample": sample_exclusions,
            "snapshot": snapshot_exclusions,
        },
        "caveats": caveats,
        "warnings": warnings,
    })
    if include_samples:
        payload["samples"] = list(ledger)
    return payload


# ─── 入口（Template Method）───────────────────────────────────────────────


def run_decision_review(
    adapter,
    *,
    market,
    date_from: str,
    date_to: str,
    horizons: Optional[Sequence[int]] = None,
    cost_bps: float = DEFAULT_COST_BPS,
    min_days: int = DEFAULT_MIN_DAYS,
    min_group_samples: int = DEFAULT_MIN_GROUP_SAMPLES,
    include_samples: bool = False,
) -> dict:
    if market is None:
        raise ValueError("decision_review 需要注入 market（daily/calendar 鸭子类型）")
    date_from = str(date_from)
    date_to = str(date_to)
    if date_from > date_to:
        raise ValueError(f"--from({date_from}) 不能晚于 --to({date_to})")
    normalized_horizons = _normalize_horizons(horizons)
    cost_bps = _validate_cost(cost_bps)
    if int(min_days) < 1:
        raise ValueError("min_days 必须 ≥ 1")
    if int(min_group_samples) < 1:
        raise ValueError("min_group_samples 必须 ≥ 1")
    min_days = int(min_days)
    min_group_samples = int(min_group_samples)

    max_horizon = max(normalized_horizons)
    try:
        window_end = (datetime.strptime(date_to, "%Y-%m-%d")
                      + timedelta(days=max(30, max_horizon * 5 + 10))
                      ).strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"无效日期: {date_to!r}") from exc

    snapshot_dates = sorted({str(day) for day in
                             adapter.snapshot_dates(date_from, date_to)})
    days = [adapter.load_day(day) for day in snapshot_dates]
    observations = [obs for day in days for obs in day.observations]
    codes = sorted({obs.code for obs in observations})

    view = _BarView(market, date_from)
    for code in codes:
        view.load(code)
    calendar, calendar_basis = _resolve_calendar(market, view, date_from, window_end)
    market_errors = len(view.errors)

    def degenerate(reason: str, active_calendar: Optional[list[str]]) -> dict:
        return _assemble(
            adapter=adapter, date_from=date_from, date_to=date_to,
            horizons=normalized_horizons, cost_bps=cost_bps,
            min_days=min_days, min_group_samples=min_group_samples,
            include_samples=include_samples, days=days,
            calendar=active_calendar, ledger=[], calendar_basis=calendar_basis,
            reason=reason, market_errors=market_errors,
            market_codes=len(codes))

    if not calendar:
        return degenerate("calendar_unavailable", None)
    if not snapshot_dates:
        return degenerate("no_snapshot_in_range", calendar)

    payloads = [{"code": obs.code, "available_at": obs.available_at}
                for obs in observations]
    label_result = build_tradable_labels(
        payloads, view, calendar, horizons=normalized_horizons, cost_bps=cost_bps)
    label_index = {
        (label["trade_date"], label["code"], int(label["horizon"])): label
        for label in label_result["labels"]
    }
    exclusion_index = {
        (item["trade_date"], item["code"], int(item["horizon"])): item["reason"]
        for item in label_result["exclusion_items"]
    }

    direction_by_group = {spec.key: spec.expected_direction
                          for spec in adapter.group_specs}
    ledger: list[dict] = []
    for observation in observations:
        expected = direction_by_group.get(observation.group)
        for horizon in normalized_horizons:
            key = (observation.snapshot_date, observation.code, horizon)
            label = label_index.get(key)
            reason_value = None if label is not None else \
                exclusion_index.get(key, "unmapped_label")
            ledger.append(_ledger_row(observation, horizon, expected, label,
                                      reason_value, cost_bps))
    ledger.sort(key=lambda row: (row["snapshot_date"], row["code"], row["horizon"]))

    expected_rows = len(observations) * len(normalized_horizons)
    payload = _assemble(
        adapter=adapter, date_from=date_from, date_to=date_to,
        horizons=normalized_horizons, cost_bps=cost_bps,
        min_days=min_days, min_group_samples=min_group_samples,
        include_samples=include_samples, days=days,
        calendar=calendar, ledger=ledger, calendar_basis=calendar_basis,
        reason=None, market_errors=market_errors, market_codes=len(codes))
    if len(ledger) != expected_rows:
        payload["warnings"].append("ledger_mismatch")
    return payload


__all__ = [
    "ACCEPTED_VERSIONS",
    "AuctionSnapshotAdapter",
    "DEFAULT_COST_BPS",
    "DEFAULT_HORIZONS",
    "DEFAULT_MIN_DAYS",
    "DEFAULT_MIN_GROUP_SAMPLES",
    "DecisionObservation",
    "GroupSpec",
    "PrepSnapshotAdapter",
    "SOURCE_GROUP_SPECS",
    "SnapshotDay",
    "build_adapter",
    "run_decision_review",
]
