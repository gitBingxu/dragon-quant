"""复盘计划后续收益投影（方案 §3.2 / §4.1 / §5，只读 Facade）。

语义：评估日 ``T`` 上「上次计划（计划日 ``D``）」的逐股后续净收益。
样本锚 **计划日 D**（``plan_review`` 属于计划日快照），行情读**评估日之后**写入
的本地 ``prep_assets.stock_daily``，窗口锚 ``T``。

边界：
  - 零新增收益口径：复用 ``run_decision_review``（13bp 成本 / 可成交剔除 / 分组）；
  - 零阈值改动、零写库、零 provider；快照与行情全部经注入；
  - 缺数据一律「待评估」并给稳定原因码，不补算、不写 0。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from dragon_quant.infra.factor.decision_review import (
    DEFAULT_COST_BPS,
    DEFAULT_HORIZONS,
    SOURCE_GROUP_SPECS,
    build_adapter,
    run_decision_review,
)

from .. import params
from .local_market import LOCAL_CAVEATS, LocalStockDailyMarket, shift_day

MAX_PLAN_CODES = 120
BAR_LOOKBACK_DAYS = 120
BAR_LOOKAHEAD_DAYS = 30
ACCEPTED_VERSIONS = ("0.7", "0.8")
GROUP_ORDER = {"recommended": 0, "not_recommended": 1, "data_insufficient": 2}

PREP_FOLLOWUP_CAVEATS = (
    "净收益 = 评估日开盘 → 第 N 个交易日后开盘，已扣 13bp 往返成本。",
    "T+3/T+5 依赖评估日之后仍写入本地日 K 的 prep 运行；尚未发生的交易日"
    "显示「待评估」，不做预测。",
    "计划日快照缺失、版本过旧或无 review_candidates 时，逐股收益一律待评估。",
)


# ─── 计划日解析（唯一实现，方案 §5.1） ────────────────────────────────────


def _normalize_optional(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return params.normalize_date(raw)
    except ValueError:
        return None


def resolve_plan_date(*, plan_review: Any, prev_meta: Any) -> dict:
    """返回 ``{plan_date, plan_date_source, warnings}``；``None`` 表示无计划。"""

    review = plan_review if isinstance(plan_review, Mapping) else None
    meta = prev_meta if isinstance(prev_meta, Mapping) else {}
    prev_date = _normalize_optional(meta.get("trade_date"))
    if review is None:
        # 当前快照无 plan_review：无 T-1 计划，不做「上一快照」兜底。
        return {"plan_date": None, "plan_date_source": None, "warnings": []}

    resolved = _normalize_optional(review.get("plan_date"))
    if resolved is not None:
        warnings: list[str] = []
        if prev_date is not None and resolved != prev_date:
            warnings.append(
                f"plan_date_mismatch: 计划日 {resolved} 与上一快照 {prev_date} "
                "不一致，按计划日口径统计")
        return {"plan_date": resolved, "plan_date_source": "plan_review",
                "warnings": warnings}
    if prev_date is not None:
        return {
            "plan_date": prev_date,
            "plan_date_source": "previous_snapshot",
            "warnings": [
                "plan_date_fallback: 快照缺 plan_review.plan_date，"
                f"按上一快照日期 {prev_date} 统计"],
        }
    return {"plan_date": None, "plan_date_source": None, "warnings": []}


# ─── 单日预读缓存（只有一天，供 adapter 复用） ────────────────────────────


class _PreloadedStore:
    """``PrepSnapshotAdapter`` 的鸭子类型 store：只允许读计划日那一天。"""

    def __init__(self, store, day: str) -> None:
        self._store = store
        self._day = day
        self._loaded = None

    def snapshot_dates(self, date_from: str, date_to: str) -> list[str]:
        return [self._day] if date_from <= self._day <= date_to else []

    def load(self, day: str):
        if day != self._day:
            raise KeyError(f"_PreloadedStore 只预载 {self._day}，收到 {day}")
        if self._loaded is None:
            self._loaded = self._store.load(day)
        return self._loaded


def _split_load(loaded: Any) -> tuple[dict, dict]:
    """``store.load`` 正常返回 ``(payload, meta)``；兼容直接返回 payload。"""

    if isinstance(loaded, tuple) and len(loaded) == 2:
        payload, meta = loaded
        return (payload if isinstance(payload, Mapping) else {}), (
            meta if isinstance(meta, Mapping) else {})
    return (loaded if isinstance(loaded, Mapping) else {}), {}


# ─── 投影 helper ──────────────────────────────────────────────────────────


def _empty_groups(horizons: Sequence[int]) -> list[dict]:
    return [
        {
            "horizon": horizon,
            "groups": [
                {
                    "group": spec.key,
                    "label": spec.label,
                    "valid": 0,
                    "excluded": 0,
                    "mean_net_return_pct": None,
                    "median_net_return_pct": None,
                }
                for spec in SOURCE_GROUP_SPECS["prep"]
            ],
            "spread": None,
        }
        for horizon in horizons
    ]


def _empty_payload(
    *,
    trade_date: str,
    horizons: list[int],
    reason: str,
    plan_date: str | None = None,
    plan_date_source: str | None = None,
    warnings: Sequence[str] = (),
    caveats: Sequence[str] = (),
) -> dict:
    return {
        "status": "insufficient_data",
        "reason": reason,
        "trade_date": trade_date,
        "plan_date": plan_date,
        "plan_date_source": plan_date_source,
        "data_source": "local_stock_daily",
        "cost_bps": float(DEFAULT_COST_BPS),
        "horizons": list(horizons),
        "rows": [],
        "summary": _empty_groups(horizons),
        "local_coverage": {
            "plan_codes": 0,
            "loaded_codes": 0,
            "codes_with_bars": 0,
            "bar_dates": 0,
            "truncated": False,
        },
        "warnings": list(warnings),
        "caveats": _caveats(caveats),
    }


def _caveats(extra: Sequence[str]) -> list[str]:
    merged: list[str] = []
    for item in [*LOCAL_CAVEATS, *PREP_FOLLOWUP_CAVEATS, *extra]:
        text = str(item)
        if text and text not in merged:
            merged.append(text)
    return merged


def _project_summary(payload: Mapping[str, Any],
                     horizons: Sequence[int]) -> list[dict]:
    """只暴露 ``valid / excluded / mean / median``；不暴露命中率与质量标签。"""

    summary: list[dict] = []
    for horizon in horizons:
        groups: list[dict] = []
        for group in payload.get("groups") or []:
            cell = ((group.get("horizons") or {}).get(str(horizon)) or {})
            groups.append({
                "group": group.get("group"),
                "label": group.get("label"),
                "valid": int(cell.get("valid") or 0),
                "excluded": int(cell.get("excluded") or 0),
                "mean_net_return_pct": cell.get("mean_net_return_pct"),
                "median_net_return_pct": cell.get("median_net_return_pct"),
            })
        spread = ((payload.get("spreads") or {}).get(str(horizon)) or {}).get("value")
        summary.append({"horizon": horizon, "groups": groups, "spread": spread})
    return summary


def _summary_from_rows(rows: Sequence[Mapping[str, Any]],
                       horizons: Sequence[int]) -> list[dict]:
    """无本地日 K 的退化路径：全部单元格都是 ``missing_bar``，均值不可算。"""

    summary: list[dict] = []
    for horizon in horizons:
        groups: list[dict] = []
        for spec in SOURCE_GROUP_SPECS["prep"]:
            cells = [
                row["horizons"].get(str(horizon))
                for row in rows if row.get("group") == spec.key
            ]
            groups.append({
                "group": spec.key,
                "label": spec.label,
                "valid": sum(1 for cell in cells
                             if cell and cell.get("net_return_pct") is not None),
                "excluded": sum(1 for cell in cells
                                if cell and cell.get("net_return_pct") is None),
                "mean_net_return_pct": None,
                "median_net_return_pct": None,
            })
        summary.append({"horizon": horizon, "groups": groups, "spread": None})
    return summary


def _cell(sample: Mapping[str, Any] | None, reason_when_missing: str) -> dict:
    if sample is None:
        return {
            "entry_date": None,
            "exit_date": None,
            "net_return_pct": None,
            "outcome": None,
            "exclusion_reason": reason_when_missing,
        }
    return {
        "entry_date": sample.get("entry_date"),
        "exit_date": sample.get("exit_date"),
        "net_return_pct": sample.get("net_return_pct"),
        "outcome": sample.get("outcome"),
        "exclusion_reason": sample.get("exclusion_reason"),
    }


def _build_rows(observations: Sequence[Any], samples: Sequence[Mapping[str, Any]],
                horizons: Sequence[int], trade_date: str) -> list[dict]:
    by_key = {
        (str(sample.get("code")), int(sample.get("horizon") or 0)): sample
        for sample in samples
        if sample.get("snapshot_date") == trade_date
    }
    rows: list[dict] = []
    for observation in observations:
        rows.append({
            "code": observation.code,
            "name": observation.name,
            "group": observation.group,
            "score": observation.score,
            "level": observation.level,
            "horizons": {
                str(horizon): _cell(
                    by_key.get((observation.code, horizon)), "missing_bar")
                for horizon in horizons
            },
        })
    rows.sort(key=lambda row: (GROUP_ORDER.get(row["group"], 9), row["code"]))
    return rows


def _ordered_codes(observations: Sequence[Any]) -> list[str]:
    seen: list[str] = []
    for observation in observations:
        if observation.code not in seen:
            seen.append(observation.code)
    return seen


def _reason_from_payload(reason: Any) -> str | None:
    return {
        "no_snapshot_in_range": "plan_snapshot_missing",
        "calendar_unavailable": "local_bars_missing",
    }.get(str(reason) if reason is not None else "")


# ─── 入口 ─────────────────────────────────────────────────────────────────


def run_plan_followup(
    *,
    trade_date: str,
    current: Mapping[str, Any] | None,
    prev_meta: Mapping[str, Any] | None,
    store,
    load_bars: Callable[[list[str], str, str], Mapping[str, Any]],
    horizons: Sequence[int] | None = None,
    current_error: str | None = None,
) -> dict:
    """构建 ``PlanFollowup``：逐股 T+1/3/5 净收益 + 分组概览 + 覆盖说明。"""

    requested = sorted({int(h) for h in (horizons or DEFAULT_HORIZONS)}
                       ) or list(DEFAULT_HORIZONS)
    warnings: list[str] = []
    if current_error:
        warnings.append(f"current_snapshot_corrupt: {current_error}")

    if not isinstance(current, Mapping):
        return _empty_payload(trade_date=trade_date, horizons=requested,
                              reason="snapshot_missing", warnings=warnings)

    plan_review = current.get("plan_review")
    if not isinstance(plan_review, Mapping):
        return _empty_payload(trade_date=trade_date, horizons=requested,
                              reason="no_plan", warnings=warnings)

    resolved = resolve_plan_date(plan_review=plan_review, prev_meta=prev_meta)
    warnings.extend(resolved["warnings"])
    plan_date = resolved["plan_date"]
    plan_source = resolved["plan_date_source"]
    if plan_date is None:
        return _empty_payload(trade_date=trade_date, horizons=requested,
                              reason="no_plan", warnings=warnings)

    try:
        dates = store.snapshot_dates(plan_date, plan_date)
    except Exception as exc:      # 损坏快照只降级，不 500
        warnings.append(f"plan_snapshot_corrupt: {type(exc).__name__}: {exc}")
        return _empty_payload(trade_date=trade_date, horizons=requested,
                              reason="plan_snapshot_corrupt",
                              plan_date=plan_date, plan_date_source=plan_source,
                              warnings=warnings)
    if not dates:
        return _empty_payload(trade_date=trade_date, horizons=requested,
                              reason="plan_snapshot_missing",
                              plan_date=plan_date, plan_date_source=plan_source,
                              warnings=warnings)

    plan_store = _PreloadedStore(store, plan_date)
    try:
        plan_payload, _plan_meta = _split_load(plan_store.load(plan_date))
    except Exception as exc:
        warnings.append(f"plan_snapshot_corrupt: {type(exc).__name__}: {exc}")
        return _empty_payload(trade_date=trade_date, horizons=requested,
                              reason="plan_snapshot_corrupt",
                              plan_date=plan_date, plan_date_source=plan_source,
                              warnings=warnings)

    version = plan_payload.get("version")
    if not plan_payload:
        # 快照日期存在但 payload 为空：按损坏处理，不冒充「版本过旧」。
        return _empty_payload(trade_date=trade_date, horizons=requested,
                              reason="plan_snapshot_corrupt",
                              plan_date=plan_date, plan_date_source=plan_source,
                              warnings=warnings)
    if str(version) not in ACCEPTED_VERSIONS:
        return _empty_payload(
            trade_date=trade_date, horizons=requested,
            reason="plan_snapshot_unsupported",
            plan_date=plan_date, plan_date_source=plan_source,
            warnings=warnings,
            caveats=("计划日快照版本不在 0.7/0.8，请先 prep --recompute 重算",))

    candidates = plan_payload.get("review_candidates")
    if not isinstance(candidates, list) or not candidates:
        return _empty_payload(trade_date=trade_date, horizons=requested,
                              reason="plan_samples_missing",
                              plan_date=plan_date, plan_date_source=plan_source,
                              warnings=warnings)

    adapter = build_adapter("prep", plan_store)
    try:
        plan_day = adapter.load_day(plan_date)
    except Exception as exc:
        warnings.append(f"plan_snapshot_corrupt: {type(exc).__name__}: {exc}")
        return _empty_payload(trade_date=trade_date, horizons=requested,
                              reason="plan_snapshot_corrupt",
                              plan_date=plan_date, plan_date_source=plan_source,
                              warnings=warnings)
    observations = list(plan_day.observations)
    if not observations:
        return _empty_payload(trade_date=trade_date, horizons=requested,
                              reason="plan_samples_missing",
                              plan_date=plan_date, plan_date_source=plan_source,
                              warnings=warnings)

    codes = _ordered_codes(observations)
    codes_truncated = len(codes) > MAX_PLAN_CODES
    load_codes = codes[:MAX_PLAN_CODES]
    try:
        loaded = load_bars(load_codes,
                           shift_day(trade_date, -BAR_LOOKBACK_DAYS),
                           shift_day(trade_date, BAR_LOOKAHEAD_DAYS)) or {}
    except Exception as exc:      # 行情故障不当成 0 收益
        warnings.append(f"local_bars_error: {type(exc).__name__}: {exc}")
        loaded = {}
    bars = dict(loaded.get("rows") or {})
    bar_dates = list(loaded.get("bar_dates") or [])
    bars_truncated = bool(loaded.get("truncated"))
    if codes_truncated:
        warnings.append(
            f"codes_truncated: 计划股票 {len(codes)} 超过上限 {MAX_PLAN_CODES}，"
            "超出部分按「待评估」处理")
    if bars_truncated:
        warnings.append("bars_truncated: 本地日 K 超过 max_bars 上限，已整组截断")

    coverage = {
        "plan_codes": len(codes),
        "loaded_codes": len(bars),
        "codes_with_bars": sum(1 for code in load_codes if bars.get(code)),
        "bar_dates": len(bar_dates),
        "truncated": codes_truncated or bars_truncated,
    }

    if not bars:
        rows = _build_rows(observations, [], requested, plan_date)
        return {
            "status": "insufficient_data",
            "reason": "local_bars_missing",
            "trade_date": trade_date,
            "plan_date": plan_date,
            "plan_date_source": plan_source,
            "data_source": "local_stock_daily",
            "cost_bps": float(DEFAULT_COST_BPS),
            "horizons": requested,
            "rows": rows,
            "summary": _summary_from_rows(rows, requested),
            "local_coverage": coverage,
            "warnings": warnings,
            "caveats": _caveats(()),
        }

    payload = run_decision_review(
        adapter,
        market=LocalStockDailyMarket(bars),
        date_from=plan_date,
        date_to=plan_date,
        horizons=requested,
        include_samples=True,
    )
    samples = list(payload.get("samples") or [])
    rows = _build_rows(observations, samples, requested, plan_date)
    any_return = any(
        cell["net_return_pct"] is not None
        for row in rows for cell in row["horizons"].values()
    )
    reason = None if any_return else (
        _reason_from_payload(payload.get("reason")) or "insufficient_samples")

    warnings.extend(str(item) for item in (payload.get("warnings") or []))
    config = payload.get("config") or {}
    return {
        "status": "ok" if any_return else "insufficient_data",
        "reason": reason,
        "trade_date": trade_date,
        "plan_date": plan_date,
        "plan_date_source": plan_source,
        "data_source": "local_stock_daily",
        "cost_bps": float(config.get("cost_bps") or DEFAULT_COST_BPS),
        "horizons": requested,
        "rows": rows,
        "summary": _project_summary(payload, requested),
        "local_coverage": coverage,
        "warnings": warnings,
        "caveats": _caveats([str(item) for item in payload.get("caveats") or []]),
    }


__all__ = [
    "MAX_PLAN_CODES",
    "PREP_FOLLOWUP_CAVEATS",
    "resolve_plan_date",
    "run_plan_followup",
]
