"""单因子评估（方案 §4.6）：RankIC / ICIR / Newey-West / 分组 / 覆盖率 / PIT 口径。

设计约束（逐条对应方案）：
  - **IC 主指标 Spearman（RankIC）**，Pearson 只作附加输出；
  - ``nw_lag = max(horizon - 1, 0)`` 由 ``horizon`` 派生，不设独立 ``label_horizon``；
  - 分组只允许 2 / 3 组（``5`` 直接 ``ValueError``）；某日某组 < 3 只 → 该日不进分组统计；
  - 标签从 ``available_at`` 之后**第一个可成交日开盘**起算（开盘→开盘），停牌样本剔除；
  - 样本不足返回 ``status="insufficient_data"``（不返回裸数字、不返回空表）；
  - 清洗/统计只在当日截面内做；本模块零 numpy / scipy / pandas；
  - **不 import review_account**：行情以参数 ``data`` 注入（鸭子类型，需 ``daily`` 与
    可选的 ``calendar``）。
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Mapping, Optional
from zoneinfo import ZoneInfo

from . import engine
from . import stats as fstats
from . import storage as fstore
from .registry import FactorRegistryError, get_spec
# Task 9 前的过渡依赖：扫描产物经函数内 lazy import `_scan_store()`（定义在文件末尾）。

SHANGHAI = ZoneInfo("Asia/Shanghai")
PIT_POLICIES = ("strict", "research")
BASIS_BUCKETS = ("observed", "statutory_backfilled", "revised")
GROUP_CHOICES = (2, 3)
LABEL_RULE = "next_open_to_next_open"
OPEN_TIME = time(9, 30)


def _normalize_date(raw: str) -> str:
    from .service import normalize_date
    return normalize_date(raw)


def _parse_meta(row: Mapping) -> dict:
    raw = row.get("source_meta_json")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {"meta_error": True}
    return payload if isinstance(payload, dict) else {"meta_error": True}


def _bar_attr(bar, field, default=None):
    if isinstance(bar, Mapping):
        return bar.get(field, default)
    return getattr(bar, field, default)


def _bar_date(bar) -> Optional[str]:
    stamp = _bar_attr(bar, "timestamp")
    if stamp is None:
        stamp = _bar_attr(bar, "date") or _bar_attr(bar, "trade_date")
    if stamp is None:
        return None
    if isinstance(stamp, datetime):
        return stamp.astimezone(SHANGHAI).date().isoformat()
    if isinstance(stamp, (int, float)):
        return datetime.fromtimestamp(float(stamp) / 1000.0, SHANGHAI).date().isoformat()
    try:
        return engine.parse_local(str(stamp)).date().isoformat()
    except ValueError:
        return None


def _tradable(bar) -> bool:
    if bar is None:
        return False
    open_price = _bar_attr(bar, "open")
    try:
        if open_price is None or float(open_price) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    volume = _bar_attr(bar, "volume")
    amount = _bar_attr(bar, "amount")
    try:
        if volume is not None and amount is not None:
            if float(volume) == 0 and float(amount) == 0:
                return False       # 停牌（量额双零）
    except (TypeError, ValueError):
        pass
    return True


def _universe_size(trade_date: str) -> Optional[int]:
    try:
        scan = engine.canonical_scan(trade_date)
        if not scan:
            return None
        rows = _scan_store().get_scan_stocks(scan["id"]) or []
        return len(rows) or None
    except Exception:
        return None


class _Market:
    """行情访问缓存（鸭子类型注入；失败只降级，不抛给调用方）。"""

    def __init__(self, data, start: str):
        self.data = data
        self.start = start
        self.bars: dict[str, dict[str, object]] = {}
        self.errors: dict[str, str] = {}

    def load(self, code: str) -> dict[str, object]:
        if code in self.bars:
            return self.bars[code]
        try:
            raw = self.data.daily(code, self.start) or []
        except Exception as exc:
            self.errors[code] = f"{type(exc).__name__}: {exc}"
            raw = []
        mapping: dict[str, object] = {}
        for bar in raw:
            day = _bar_date(bar)
            if day:
                mapping[day] = bar
        self.bars[code] = mapping
        return mapping

    def calendar(self, start: str, end: str) -> tuple[list[str], list[str]]:
        degraded: list[str] = []
        dates: list[str] = []
        getter = getattr(self.data, "calendar", None)
        if callable(getter):
            try:
                dates = sorted({_normalize_date(item)
                                for item in getter(start, end) if item})
            except Exception as exc:
                degraded.append(f"calendar_unavailable: {type(exc).__name__}: {exc}")
        if not dates:
            dates = sorted({day for mapping in self.bars.values() for day in mapping
                            if start <= day <= end})
            if dates:
                degraded.append("calendar_fallback: 用个股日K并集代替指数交易日历")
        return dates, degraded


def _forward_return(available_at: str, horizon: int, calendar: list[str],
                    bars: Mapping[str, object]) -> Optional[float]:
    """``available_at`` 之后第一个可成交日开盘 → 第 ``horizon`` 个交易日开盘。"""
    try:
        moment = engine.parse_local(available_at)
    except (TypeError, ValueError):
        return None
    target = moment.date().isoformat()
    start_index: Optional[int] = None
    for index, day in enumerate(calendar):
        if day > target:
            start_index = index
            break
        if day == target and moment.time() < OPEN_TIME:
            start_index = index
            break
    if start_index is None:
        return None
    end_index = start_index + horizon
    if end_index >= len(calendar):
        return None
    start_bar = bars.get(calendar[start_index])
    end_bar = bars.get(calendar[end_index])
    if not _tradable(start_bar) or not _tradable(end_bar):
        return None
    start_open = float(_bar_attr(start_bar, "open"))
    end_open = float(_bar_attr(end_bar, "open"))
    if start_open <= 0:
        return None
    return (end_open / start_open - 1.0) * 100.0


def _basis_and_period(meta: Mapping, code: str, *, fin_source: bool = False) -> tuple[str, Optional[str]]:
    basis_map = meta.get("basis_by_code") or {}
    basis = basis_map.get(code) if isinstance(basis_map, Mapping) else None
    if not basis:
        basis = meta.get("pit_basis")
    if basis == "mixed" or not basis:
        # 旧数据无逐 code 口径：strict 下按最保守的「回填」处理并告警。
        # 非财务源（scan/vpa）没有 PIT 三桶语义，保持 observed。
        basis = "statutory_backfilled" if fin_source else "observed"
    if basis not in BASIS_BUCKETS:
        basis = "statutory_backfilled" if fin_source else "observed"
    period_map = meta.get("period_by_code") or {}
    period = period_map.get(code) if isinstance(period_map, Mapping) else None
    if not period:
        period = meta.get("period")
    return basis, period


def _daily_ic(observations: list[dict], min_cross_section: int) -> tuple[list[dict], int]:
    """逐日 IC；返回 ``(有效日记录, 跳过日数)``。"""
    by_date: dict[str, list[dict]] = defaultdict(list)
    for obs in observations:
        by_date[obs["date"]].append(obs)
    days: list[dict] = []
    skipped = 0
    for trade_date in sorted(by_date):
        rows = [row for row in by_date[trade_date] if row.get("label") is not None]
        if len(rows) < min_cross_section:
            skipped += 1
            continue
        values = [row["value"] for row in rows]
        labels = [row["label"] for row in rows]
        rho = fstats.spearman(values, labels)
        if rho is None:
            skipped += 1
            continue
        distinct = len(set(values))
        tie_ratio = 1.0 - distinct / len(values)
        day = {
            "date": trade_date,
            "n": len(rows),
            "codes": [row["code"] for row in rows],
            "rank_ic": rho,
            "pearson_ic": fstats.pearson(values, labels),
            "tie_ratio": tie_ratio,
            "kendall_tau_b": (fstats.kendall_tau_b(values, labels)
                              if tie_ratio > 0.5 else None),
            "values": values,
            "labels": labels,
            "periods": [row.get("period") for row in rows],
            "membership_key": [(row["code"], row["value"]) for row in rows],
        }
        days.append(day)
    return days, skipped


def _aggregate_ic(days: list[dict], horizon: int) -> dict:
    ics = [day["rank_ic"] for day in days]
    n = len(ics)
    mean_ic = fstats.mean(ics)
    std_ic = fstats.stdev(ics, ddof=1)
    icir = (mean_ic / std_ic) if mean_ic is not None and std_ic else None
    naive_t = (icir * math.sqrt(n)) if icir is not None else None
    lag = max(horizon - 1, 0)
    nw_t = fstats.newey_west_t(ics, lag=lag) if n >= 2 else None
    se = (mean_ic / nw_t) if (mean_ic is not None and nw_t) else None
    ci95 = ([mean_ic - 1.96 * se, mean_ic + 1.96 * se]
            if se is not None and mean_ic is not None else None)
    taus = [day["kendall_tau_b"] for day in days if day.get("kendall_tau_b") is not None]
    return {
        "mean": mean_ic,
        "std": std_ic,
        "icir": icir,
        "t_stat": naive_t,
        "nw_t_stat": nw_t,
        "ci95": ci95,
        "positive_ratio": (sum(1 for value in ics if value > 0) / n) if n else None,
        "n_days": n,
        "kendall_tau_b": fstats.mean(taus),
        "autocorr1": fstats.autocorr1(ics),
    }


def _group_stats(days: list[dict], groups: int) -> tuple[list[dict], Optional[float], Optional[float]]:
    totals = [{"n": 0, "sum": 0.0, "hits": 0} for _ in range(groups)]
    memberships: list[dict[str, int]] = []
    pool_sum = 0.0
    pool_n = 0
    for day in days:
        pairs = sorted(zip(day["values"], day["labels"], day["membership_key"]))
        n = len(pairs)
        buckets: list[list[tuple]] = [[] for _ in range(groups)]
        for index, item in enumerate(pairs):
            buckets[index * groups // n].append(item)
        if any(len(bucket) < 3 for bucket in buckets):
            continue
        membership: dict[str, int] = {}
        for group_index, bucket in enumerate(buckets):
            for value, label, key in bucket:
                code = key[0]
                membership[code] = group_index
                totals[group_index]["n"] += 1
                totals[group_index]["sum"] += label
                totals[group_index]["hits"] += 1 if label > 0 else 0
        memberships.append(membership)
        pool_sum += sum(day["labels"])
        pool_n += len(day["labels"])
    output = [{
        "group": index + 1,
        "n": entry["n"],
        "mean_return": (entry["sum"] / entry["n"]) if entry["n"] else None,
        "hit_rate": (entry["hits"] / entry["n"]) if entry["n"] else None,
    } for index, entry in enumerate(totals)]
    pool_mean = (pool_sum / pool_n) if pool_n else None
    return output, pool_mean, memberships


def _turnover(memberships: list[dict[str, int]]) -> Optional[float]:
    changes: list[float] = []
    for previous, current in zip(memberships, memberships[1:]):
        common = set(previous) & set(current)
        if not common:
            continue
        changed = sum(1 for code in common if previous[code] != current[code])
        changes.append(changed / len(common))
    return fstats.mean(changes)


def _aggregate_periods(days: list[dict]) -> dict:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for day in days:
        periods = {period for period in day.get("periods") or () if period}
        label = next(iter(periods)) if len(periods) == 1 else ("mixed" if periods else "unknown")
        buckets[label].append(day)
    result = {}
    for label in sorted(buckets):
        members = buckets[label]
        result[label] = {
            "days": len(members),
            "observations": sum(day["n"] for day in members),
            "ic_mean": fstats.mean([day["rank_ic"] for day in members]),
        }
    return result


def _effective_observations(rows: list[dict]) -> dict:
    """1q 因子：因子值变化次数 / 报告期切换次数 / 独立 block 数。"""
    changes = 0
    switches = 0
    periods: set = set()
    by_code: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        meta = _parse_meta(row)
        period = (meta.get("period_by_code") or {}).get(row["code"]) or meta.get("period")
        by_code[row["code"]].append({**row, "_period": period})
        if period:
            periods.add(period)
    for code_rows in by_code.values():
        code_rows.sort(key=lambda item: item["trade_date"])
        for previous, current in zip(code_rows, code_rows[1:]):
            if previous["value"] != current["value"]:
                changes += 1
            if previous["_period"] != current["_period"]:
                switches += 1
    return {
        "value_changes": changes,
        "period_switches": switches,
        "blocks": len(periods),
    }


def _insufficient(reason: str, **extra) -> dict:
    payload = {
        "ok": True,
        "status": "insufficient_data",
        "reason": reason,
        "ic": {"n_days": 0},
        "days": 0,
        "observations": 0,
        "effective_independent_days": 0,
        "coverage": {"mean": None, "min": None, "missing_rate": None},
        "groups": [],
        "long_short": {"mean": None, "tradable": False,
                       "pool_mean_return": None},
        "turnover": {"mean": None, "definition": "group_membership_change"},
        "pit_basis_mix": {bucket: 0 for bucket in BASIS_BUCKETS},
        "retracted_count": 0,
        "nw_lag": max(int(extra.get("horizon") or 1) - 1, 0),
        "label_rule": LABEL_RULE,
        "universe": "limit_up_candidates",
        "trial_count": 1,
        "warnings": [],
        "degraded": [],
    }
    payload.update(extra)
    return payload


def evaluate_factor(name: str, date_from: str, date_to: str, *,
                    horizon: int = 1, groups: int = 2,
                    version: Optional[str] = None,
                    data=None,
                    min_days: int = 20, min_cross_section: int = 10,
                    pit_policy: str = "strict", db_path=None) -> dict:
    """评估单因子；返回结构见方案 §4.6（永不返回裸数字）。"""
    start = _normalize_date(date_from)
    end = _normalize_date(date_to)
    if start > end:
        raise ValueError(f"--from({start}) 不能晚于 --to({end})")
    if not isinstance(horizon, int) or horizon < 1:
        raise ValueError("horizon 必须是 ≥ 1 的整数")
    if groups not in GROUP_CHOICES:
        raise ValueError(f"groups 只允许 {GROUP_CHOICES}（5 组样本过少，直接拒绝）")
    if pit_policy not in PIT_POLICIES:
        raise ValueError(f"pit_policy 非法: {pit_policy!r}（合法值 {PIT_POLICIES}）")
    if min_days < 1 or min_cross_section < 3:
        raise ValueError("min_days ≥ 1 且 min_cross_section ≥ 3（2 只截面的 RankIC 纯噪声）")
    if data is None:
        raise ValueError("evaluate_factor 需要注入 data（MarketData 鸭子类型）")
    try:
        spec = get_spec(name)
    except FactorRegistryError as exc:
        raise ValueError(str(exc)) from exc

    payload = fstore.get_series(name, None, start, end, version=version, db_path=db_path)
    resolved_version = payload["resolved_versions"].get(name)
    rows = payload.get("rows") or []
    if not resolved_version:
        return _insufficient(
            f"version_not_registered: 因子 {name} 尚未 build（无注册版本）",
            factor=name, resolved_version=None, horizon=horizon,
            label_rule=LABEL_RULE, universe="limit_up_candidates",
            trial_count=1, build_ids=[])
    if not rows:
        return _insufficient(
            f"no_factor_values: 区间 {start}~{end} 无 {name}@{resolved_version} 的值",
            factor=name, resolved_version=resolved_version, horizon=horizon,
            label_rule=LABEL_RULE, universe="limit_up_candidates",
            trial_count=1, build_ids=fstore.list_build_ids(start, end, db_path=db_path))

    warnings: list[str] = []
    degraded: list[str] = []
    window_start = (datetime.strptime(start, "%Y-%m-%d")
                    - timedelta(days=40)).date().isoformat()
    window_end = (datetime.strptime(end, "%Y-%m-%d")
                  + timedelta(days=max(30, horizon * 5 + 10))).date().isoformat()
    market = _Market(data, window_start)
    codes = sorted({row["code"] for row in rows})
    for code in codes:
        market.load(code)
    if market.errors:
        degraded.append(f"market_data_missing: {len(market.errors)}/{len(codes)} 只无日K")
    calendar, calendar_degraded = market.calendar(window_start, window_end)
    degraded.extend(calendar_degraded)
    if not calendar:
        return _insufficient(
            "calendar_unavailable: 无法获得交易日历，前向收益不可计算",
            factor=name, resolved_version=resolved_version, horizon=horizon,
            label_rule=LABEL_RULE, universe="limit_up_candidates",
            trial_count=1, warnings=warnings, degraded=degraded,
            build_ids=fstore.list_build_ids(start, end, db_path=db_path))

    # ── 观测样本（因子值 + 前向收益 + PIT 口径）────────────────────────────
    observations: list[dict] = []
    label_missing = 0
    pit_ambiguous = 0
    meta_errors = 0
    retracted_by_date: dict[str, int] = {}
    finance_versions: dict[tuple, dict] = {}
    annualizations: set[str] = set()
    for row in rows:
        meta = _parse_meta(row)
        if meta.get("meta_error"):
            meta_errors += 1
        basis, period = _basis_and_period(
            meta, row["code"], fin_source=spec.source == "fin")
        if (spec.source == "fin" and meta.get("pit_basis") == "mixed"
                and not (meta.get("basis_by_code") or {})):
            pit_ambiguous += 1
        # retracted_count 是「当日因子级常量」（同一日所有 code 的 meta 相同），
        # 必须按日期去重后再求和，否则会被 code 数放大。
        retracted_by_date[row["trade_date"]] = max(
            retracted_by_date.get(row["trade_date"], 0),
            int(meta.get("retracted_count") or 0))
        for entry in meta.get("finance_data_versions") or []:
            if not isinstance(entry, Mapping) or not entry.get("file_md5"):
                continue
            key = (entry.get("period"), entry.get("file_md5"))
            existing = finance_versions.get(key)
            candidate = {
                "period": entry.get("period"),
                "file_md5": entry.get("file_md5"),
                "first_seen_at": entry.get("first_seen_at"),
                "available_at": entry.get("available_at"),
            }
            if existing is None:
                finance_versions[key] = candidate
            else:
                for field in ("first_seen_at", "available_at"):
                    if candidate.get(field) and (
                            not existing.get(field) or candidate[field] < existing[field]):
                        existing[field] = candidate[field]
        if meta.get("annualization"):
            annualizations.add(str(meta["annualization"]))
        label = _forward_return(
            row["available_at"], horizon, calendar, market.load(row["code"]))
        if label is None:
            label_missing += 1
            continue
        observations.append({
            "date": row["trade_date"],
            "code": row["code"],
            "value": float(row["value"]),
            "label": label,
            "basis": basis,
            "period": period,
        })

    retracted_count = sum(retracted_by_date.values())
    if meta_errors:
        degraded.append(f"source_meta_invalid: {meta_errors} 行 source_meta_json 无法解析")
    if pit_ambiguous:
        warnings.append(
            f"pit_basis_mixed_fallback: {pit_ambiguous} 条样本缺逐 code 口径，"
            "按 statutory_backfilled 处理（strict 下剔除）")

    research_obs = list(observations)
    strict_obs = [obs for obs in observations if obs["basis"] != "statutory_backfilled"]
    selected = strict_obs if pit_policy == "strict" else research_obs

    # ── 覆盖率（因子存在率；分母 = 当日扫描候选数）─────────────────────────
    present_codes_by_date: dict[str, set] = defaultdict(set)
    for row in rows:
        present_codes_by_date[row["trade_date"]].add(row["code"])
    coverage_values: list[float] = []
    coverage_unavailable_days = 0
    for trade_date in sorted(present_codes_by_date):
        universe = _universe_size(trade_date)
        if universe is None:
            # 分母不可用（无扫描池/DB 异常）→ 不伪造 100% 覆盖率
            coverage_unavailable_days += 1
            continue
        coverage_values.append(
            min(1.0, len(present_codes_by_date[trade_date]) / max(1, universe)))
    if coverage_unavailable_days:
        degraded.append(
            f"coverage_universe_unavailable: {coverage_unavailable_days} 天无扫描池，"
            "覆盖率分母不可用（已从均值剔除）")
    coverage_mean = fstats.mean(coverage_values)
    coverage = {
        "mean": coverage_mean,
        "min": min(coverage_values) if coverage_values else None,
        "missing_rate": (1.0 - coverage_mean) if coverage_mean is not None else None,
    }

    days, skipped_days = _daily_ic(selected, min_cross_section)
    n_days = len(days)
    cross_sections = [day["n"] for day in days]
    median_cross = fstats.median(cross_sections)
    observations_count = sum(day["n"] for day in days)
    ic = _aggregate_ic(days, horizon)
    effective_days = max(1, int(round(n_days / horizon))) if n_days else 0

    group_rows, pool_mean, memberships = _group_stats(days, groups)
    long_short = {
        "mean": (group_rows[-1]["mean_return"] - group_rows[0]["mean_return"]
                 if (group_rows[-1]["mean_return"] is not None
                     and group_rows[0]["mean_return"] is not None) else None),
        "tradable": False,
        "pool_mean_return": pool_mean,
    }
    turnover = {
        "mean": _turnover(memberships),
        "definition": "group_membership_change",
        "basis": "consecutive_included_days",
    }

    high_tie_days = [day["date"] for day in days if day["tie_ratio"] > 0.5]
    if high_tie_days:
        warnings.append(
            f"high_tie_ratio: {len(high_tie_days)} 天并列率 > 50%"
            f"（如 {', '.join(high_tie_days[:5])}），已额外输出 Kendall tau-b")
    if skipped_days:
        warnings.append(
            f"skipped_days: {skipped_days} 天有效截面 < {min_cross_section} 只，"
            "已跳过且不计入 IC 序列")
    if label_missing:
        warnings.append(f"label_missing: {label_missing} 条样本因停牌/数据不足无前向收益")
    if median_cross:
        threshold = 1.96 / math.sqrt(max(1.0, median_cross - 1))
        warnings.append(
            f"单日截面中位数 {median_cross:g} 只，5% 双侧显著约需 |IC| {threshold:.2f}")

    # ── PIT 口径 ──────────────────────────────────────────────────────────
    included_pairs = {(day["date"], code) for day in days
                      for code in day.get("codes") or ()}
    included_obs = [obs for obs in selected
                    if (obs["date"], obs["code"]) in included_pairs]
    basis_counts = {bucket: 0 for bucket in BASIS_BUCKETS}
    for obs in included_obs:
        basis_counts[obs["basis"]] = basis_counts.get(obs["basis"], 0) + 1
    research_basis = {bucket: 0 for bucket in BASIS_BUCKETS}
    for obs in research_obs:
        research_basis[obs["basis"]] = research_basis.get(obs["basis"], 0) + 1
    restatement = research_basis.get("statutory_backfilled", 0) > 0
    policy_samples = {"strict": len(strict_obs), "research": len(research_obs)}
    if pit_policy == "strict":
        if restatement:
            warnings.append(
                f"strict: 已剔除 {research_basis['statutory_backfilled']} 条 "
                "statutory_backfilled 样本（回填期不构成可证伪 PIT 证据）")
    elif restatement:
        ratio = research_basis["statutory_backfilled"] / max(1, len(research_obs))
        warnings.append(
            f"research: statutory_backfilled 占 {ratio:.1%}，回填期 IC 可能偏乐观"
            "（restatement_unverifiable）")
        warnings.append("research 结论禁止写入 factor_model_snapshots")

    # ── 财务 / 1q 额外输出 ────────────────────────────────────────────────
    by_report_period = _aggregate_periods(days) if spec.source == "fin" else {}
    report_period_mixed = False
    if spec.source == "fin":
        periods_in_ic = {period for day in days
                         for period in (day.get("periods") or ()) if period}
        report_period_mixed = len(periods_in_ic) > 1
        if report_period_mixed:
            warnings.append(
                f"mixed_report_period: IC 跨 {len(periods_in_ic)} 个报告期拼接"
                f"（{sorted(periods_in_ic)}），代际跳变见 by_report_period；"
                "禁止直接把切换日两侧 IC 当作同一条序列")
    finance_data_versions = sorted(
        finance_versions.values(),
        key=lambda item: (str(item.get("period") or ""), str(item.get("file_md5") or ""))
    ) if spec.source == "fin" else []
    finance_extra: dict = {}
    if spec.source == "fin":
        finance_extra = {
            "by_report_period": by_report_period,
            "report_period_mixed": report_period_mixed,
            "finance_data_versions": finance_data_versions,
            "annualization": (sorted(annualizations)[0]
                              if len(annualizations) == 1 else None),
            "restatement_unverifiable": restatement,
            "pit_policy_samples": policy_samples,
            "model_snapshot_allowed": pit_policy == "strict",
        }
    if spec.frequency == "1q":
        effective_obs = _effective_observations(rows)
        blocks: list[Optional[str]] = []
        for day in days:
            periods = {period for period in day.get("periods") or () if period}
            blocks.append(next(iter(periods)) if len(periods) == 1 else None)
        block_t = fstats.block_bootstrap_t(
            [day["rank_ic"] for day in days], blocks)
        ic["report_period_block_t"] = block_t
        ic["t_stat_label"] = "pseudo_independent"
        warnings.append(
            "1q 因子：日频 naive t 为 pseudo_independent（同一股票约 60 个交易日同值）")
        finance_extra["effective_independent_observations"] = effective_obs

    insufficient_reasons: list[str] = []
    if n_days < min_days:
        insufficient_reasons.append(f"有效交易日 {n_days} < min_days {min_days}")
    if median_cross is None or median_cross < min_cross_section:
        insufficient_reasons.append(
            f"单日有效截面中位数 {median_cross or 0:g} < min_cross_section {min_cross_section}")
    if pit_policy == "strict" and not strict_obs:
        insufficient_reasons.append("strict 口径下样本为 0（历史回填期不可证伪）")
    status = "insufficient_data" if insufficient_reasons else "evaluated"

    result = {
        "ok": True,
        "status": status,
        "reason": "; ".join(insufficient_reasons) if insufficient_reasons else None,
        "factor": name,
        "resolved_version": resolved_version,
        "frequency": spec.frequency,
        "universe": "limit_up_candidates",
        "horizon": horizon,
        "nw_lag": max(horizon - 1, 0),
        "group_count": groups,
        "label_rule": LABEL_RULE,
        "days": n_days,
        "observations": observations_count,
        "effective_independent_days": effective_days,
        "coverage": coverage,
        "ic": ic,
        "groups": group_rows,
        "long_short": long_short,
        "turnover": turnover,
        "pit_policy": pit_policy,
        "pit_basis_mix": basis_counts,
        "retracted_count": retracted_count,
        "warnings": warnings,
        "trial_count": 1,
        "build_ids": fstore.list_build_ids(start, end, db_path=db_path),
        "degraded": degraded,
        **finance_extra,
    }
    return result


# ─── 因子层只读扫描仓储（函数内 lazy import canonical 路径）──────────────


def _scan_store():
    """只读扫描产物经 ``scan.storage``；必须函数内 lazy import。"""
    from dragon_quant.scan.storage import main as scan_store

    return scan_store
