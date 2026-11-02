"""prep / auction 端点：只读 ``assets.db``（``create=False``，绝不建库）。

路由只做参数解析与响应模型；三态/豁免/跨日/成效规则全部在
``web_ui.backend.presenters``（纯投影，可单测）。行情只读本地 ``stock_daily``，
竞价页不触网。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import params
from ..deps import (
    assets_repository,
    bar_calendar_loader,
    clock_module,
    decision_review_store,
    local_bars_loader,
    review_assets_enabled,
)
from ..errors import ApiError
from ..presenters import auction_effect as auction_effect_view
from ..presenters import auction_view, prep_effect, prep_view
from ..presenters.calendar_defaults import resolve_default_date

router = APIRouter(tags=["assets"])

SHANGHAI = ZoneInfo("Asia/Shanghai")
PREP_ASSET_TYPES = prep_view.ASSET_TYPES
_BAD_STATUS = prep_view.BAD_STATUS


# ─── 公共 helper ───────────────────────────────────────────────────────────


def _normalize_date_or_400(raw: str) -> str:
    try:
        return params.normalize_date(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc


def _today_iso(clock) -> str:
    return clock.now(SHANGHAI).strftime("%Y-%m-%d")


def _window_start(end_day: str, days: int) -> str:
    start = datetime.strptime(end_day, "%Y-%m-%d") - timedelta(days=max(0, days - 1))
    return start.strftime("%Y-%m-%d")


def _clamp_days(raw: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "invalid_request", "days 必须是整数") from exc
    if value < 1 or value > 366:
        raise ApiError(400, "invalid_request", "days 必须在 1..366 之间")
    return value


def _as_dict(value) -> dict | None:
    return value if isinstance(value, dict) else None


def _as_list(value) -> list[dict] | None:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return None


# ─── prep 响应模型 ─────────────────────────────────────────────────────────


class ExemptAsset(BaseModel):
    asset_type: str
    reason: str


class PrepCalendarDay(BaseModel):
    trade_date: str
    status: Literal["complete", "incomplete", "missing"]
    has_snapshot: bool
    has_assets: bool
    asset_count: int
    expected_ok: int
    expected_total: int
    expected_missing: list[str] = []
    expected_degraded: list[str] = []
    core_ok: int = 0
    core_total: int = 0
    exempt: list[ExemptAsset] = []
    report_error: str | None = None


class PrepCalendar(BaseModel):
    available: bool
    start_date: str
    end_date: str
    latest_record_date: str | None = None
    days: list[PrepCalendarDay]
    default_date: str
    default_date_source: Literal[
        "local_bar_previous", "local_bar_latest",
        "record_date_fallback", "today_fallback"]
    calendar_basis: Literal["local_stock_daily_bars_union", "record_dates"]
    calendar_latest_date: str | None = None
    degraded: bool = False
    degraded_reason: str | None = None


class PrepAssetStatus(BaseModel):
    asset_type: str
    present: bool
    status: str | None = None
    note: str | None = None
    error: str | None = None
    as_of: str | None = None
    usable_display: bool = False
    category: str = "auxiliary"
    expected: bool = False
    legal_absence: str | None = None
    asset_complete: bool = False


class PrepCollectionReport(BaseModel):
    asset_type: str
    status: str | None = None
    note: str | None = None
    error: str | None = None


class PrepWatermark(BaseModel):
    trade_date: str
    available: bool
    assets: list[PrepAssetStatus]
    collection_report: list[PrepCollectionReport]
    missing: list[str]
    degraded: list[str]
    has_snapshot: bool
    has_prior_snapshot: bool = False
    first_record_day: bool = False
    expected_ok: int = 0
    expected_total: int = 0


class PrepDates(BaseModel):
    asset_dates: list[str]
    snapshot_dates: list[str]
    count: int


class MainlineDerived(BaseModel):
    source: Literal["review", "legacy"]
    index: int
    board_name: str | None = None
    stored_high_num: float | None = None
    board_height: int | None = None
    needs_recompute: bool = False
    evidence: list[str] = []


class MarketDerived(BaseModel):
    source: Literal["review", "sentiment"]
    stored_max_board_height: float | None = None
    max_board_height: int | None = None
    needs_recompute: bool = False
    evidence: list[str] = []


class PrepDerived(BaseModel):
    mainlines: list[MainlineDerived] = []
    market: MarketDerived
    needs_recompute: bool = False


class PrepSnapshotView(BaseModel):
    trade_date: str
    version: str | None = None
    created_at: str | None = None
    blocks_present: list[str]
    sentiment: dict | None = None
    main_boards: list[dict] | None = None
    macro: dict | None = None
    dragon_source: dict | None = None
    candidates: list[dict] | None = None
    review_candidates: list[dict] | None = None
    review: dict | None = None
    feedback: dict | None = None
    plan_review: dict | None = None
    slim: bool = True
    raw_size_bytes: int
    derived: PrepDerived


class MarketSide(BaseModel):
    available: bool
    trade_date: str | None = None
    state: str | None = None
    score: float | None = None
    coeff: float | None = None
    position_advice: str | None = None
    position_pct: float | None = None


class VerificationEvidence(BaseModel):
    prev_state: str | None = None
    current_state: str | None = None
    prev_score: float | None = None
    current_score: float | None = None
    score_change: float | None = None
    limit_up_cnt: float | None = None
    limit_down_cnt: float | None = None
    broken_rate: float | None = None
    yesterday_premium_pct: float | None = None


class MarketVerification(BaseModel):
    conclusion: Literal["correct", "wrong", "neutral", "not_evaluable"]
    reason: str | None = None
    advice: Literal["empty", "hold"] | None = None
    prev_trade_date: str | None = None
    current_trade_date: str | None = None
    evidence: VerificationEvidence


class MarketComparison(BaseModel):
    prev: MarketSide
    current: MarketSide
    score_change: float | None = None
    verification: MarketVerification


class MainlineDelta(BaseModel):
    index: int
    board_name: str | None = None
    rank: int | None = None
    prev_rank: int | None = None
    prev_score: float | None = None
    rank_delta: int | None = None
    score_delta: float | None = None
    state: str | None = None
    unknown_reason: str | None = None


class PlanName(BaseModel):
    code: str
    name: str | None = None
    source: Literal["prev_plan_asset", "prev_snapshot_context",
                    "current_snapshot", "missing"]


class PlanEffectItem(BaseModel):
    code: str
    name: str | None = None
    planned_action: str | None = None
    planned_position: float | None = None
    feedback_state: str = "unknown"
    outcome_pct: float | None = None
    actual_position: float | None = None
    execution_known: bool = False
    unknown_reason: str | None = None


class NoTradeItem(BaseModel):
    code: str
    name: str | None = None
    judgement: Literal["correct_avoidance", "missed_opportunity", "data_gap"]
    outcome_pct: float | None = None


class PlanGroupOutcome(BaseModel):
    key: Literal["recommended", "not_recommended", "unknown"]
    count: int
    with_outcome: int
    mean_outcome_pct: float | None = None


class PlanEffect(BaseModel):
    available: bool
    reason: str | None = None
    plan_date: str | None = None
    eval_date: str | None = None
    checkpoint: str | None = None
    items: list[PlanEffectItem] = []
    no_trade: list[NoTradeItem] = []
    groups: list[PlanGroupOutcome] = []
    market: MarketComparison
    degraded: list[str] = []


class ReviewContext(BaseModel):
    trade_date: str
    available: bool
    reason: str | None = None
    prev_trade_date: str | None = None
    prev_meta_error: str | None = None
    mainlines: list[MainlineDelta] = []
    plan_names: list[PlanName] = []
    plan_effect: PlanEffect
    degraded: list[str] = []


# ─── plan-followup 响应模型（样本锚计划日，行情只读本地日 K） ─────────────


class PlanFollowupCell(BaseModel):
    entry_date: str | None = None
    exit_date: str | None = None
    net_return_pct: float | None = None
    outcome: Literal["up", "down", "flat"] | None = None
    exclusion_reason: str | None = None


class PlanFollowupRow(BaseModel):
    code: str
    name: str | None = None
    group: Literal["recommended", "not_recommended", "data_insufficient"]
    score: float | None = None
    level: str | None = None
    horizons: dict[str, PlanFollowupCell] = {}


class PlanFollowupGroup(BaseModel):
    group: str
    label: str
    valid: int
    excluded: int
    mean_net_return_pct: float | None = None
    median_net_return_pct: float | None = None


class PlanFollowupHorizon(BaseModel):
    horizon: int
    groups: list[PlanFollowupGroup] = []
    spread: float | None = None


class PlanFollowupCoverage(BaseModel):
    plan_codes: int
    loaded_codes: int
    codes_with_bars: int
    bar_dates: int
    truncated: bool = False


class PlanFollowup(BaseModel):
    status: Literal["ok", "insufficient_data"]
    reason: str | None = None
    trade_date: str
    plan_date: str | None = None
    plan_date_source: Literal["plan_review", "previous_snapshot"] | None = None
    data_source: Literal["local_stock_daily"]
    cost_bps: float
    horizons: list[int]
    rows: list[PlanFollowupRow] = []
    summary: list[PlanFollowupHorizon] = []
    local_coverage: PlanFollowupCoverage
    warnings: list[str] = []
    caveats: list[str] = []


# ─── auction 响应模型 ──────────────────────────────────────────────────────


class AuctionCalendarDay(BaseModel):
    trade_date: str
    status: Literal["complete", "incomplete", "missing"]
    has_snapshot: bool
    has_assets: bool
    snapshot_count: int
    asset_count: int
    snapshot_only_codes: list[str] = []
    asset_only_codes: list[str] = []


class AuctionCalendar(BaseModel):
    available: bool
    start_date: str
    end_date: str
    latest_record_date: str | None = None
    days: list[AuctionCalendarDay]
    default_date: str
    default_date_source: Literal[
        "local_bar_previous", "local_bar_latest",
        "record_date_fallback", "today_fallback"]
    calendar_basis: Literal["local_stock_daily_bars_union", "record_dates"]
    calendar_latest_date: str | None = None
    degraded: bool = False
    degraded_reason: str | None = None


class AuctionSnapshotRow(BaseModel):
    code: str
    version: str | None = None
    is_weak: bool | None = None
    strength: float | None = None
    level: str | None = None
    direction: str | None = None
    rank: int | None = None
    validation_status: str | None = None
    data_quality: dict | None = None
    risk_events: list | None = None
    reject_reason: str | None = None
    error: str | None = None
    name: str | None = None
    collected: bool = False
    evaluated: bool = True
    collection_status: Literal["ok", "asset_only", "snapshot_only", "missing"] = "ok"
    key_evidence: dict | None = None


class AuctionRunView(BaseModel):
    snapshot_count: int
    asset_count: int
    snapshot_only_codes: list[str] = []
    asset_only_codes: list[str] = []
    quality_counts: dict[str, int] = {}
    error_rows: int = 0
    config_versions: list[str] = []
    validation_statuses: dict[str, int] = {}
    created_at_min: str | None = None
    created_at_max: str | None = None
    evaluation_available: bool = False


class AuctionDayView(BaseModel):
    trade_date: str
    available: bool
    count: int
    snapshots: list[AuctionSnapshotRow]
    summary: dict[str, int]
    assets_count: int | None = None
    run: AuctionRunView
    names: dict[str, str] = {}
    asset_codes: list[str] = []


class AuctionDates(BaseModel):
    snapshot_dates: list[str]
    asset_dates: list[str]
    count: int


class HorizonGroup(BaseModel):
    group: str
    label: str
    expected_direction: str | None = None
    observations: int
    valid: int
    excluded: int
    coverage: float | None = None
    direction_evaluable: bool = False
    direction_hit_rate: float | None = None
    mean_net_return_pct: float | None = None
    median_net_return_pct: float | None = None
    quality: Literal["ok", "low_confidence", "not_evaluable"]
    above_reference: bool | None = None


class HorizonEffect(BaseModel):
    horizon: int
    groups: list[HorizonGroup]
    spread: float | None = None


class SelectedDayOutcome(BaseModel):
    code: str
    horizon: int
    entry_date: str | None = None
    exit_date: str | None = None
    net_return_pct: float | None = None
    outcome: Literal["up", "down", "flat"] | None = None
    exclusion_reason: str | None = None


class LocalCoverage(BaseModel):
    snapshot_dates: int
    window_codes: int
    loaded_codes: int
    selected_codes: int
    codes_with_bars: int
    bar_dates: int
    truncated: bool


class AuctionEffect(BaseModel):
    status: Literal["ok", "insufficient_data"]
    reason: str | None = None
    date_from: str
    date_to: str
    data_source: Literal["local_stock_daily"] = "local_stock_daily"
    calendar_basis: str
    cost_bps: float
    horizons: list[HorizonEffect]
    selected_day: list[SelectedDayOutcome]
    local_coverage: LocalCoverage
    warnings: list[str] = []
    caveats: list[str] = []


# ─── prep 端点 ─────────────────────────────────────────────────────────────


@router.get("/api/prep/dates", response_model=PrepDates)
def prep_dates(limit: int = 30, assets=Depends(assets_repository)) -> PrepDates:
    limits = params.clamp_limit(limit, default=30, maximum=200)
    asset_dates = assets.list_prep_asset_dates()
    snapshot_dates = assets.list_prep_snapshot_dates()
    union = sorted(set(asset_dates) | set(snapshot_dates))[-limits:]
    keep = set(union)
    return PrepDates(
        asset_dates=[day for day in asset_dates if day in keep],
        snapshot_dates=[day for day in snapshot_dates if day in keep],
        count=len(union),
    )


@router.get("/api/prep/calendar", response_model=PrepCalendar)
def prep_calendar(days: int = 365, end: str | None = None,
                  assets=Depends(assets_repository),
                  clock=Depends(clock_module),
                  review_enabled: bool = Depends(review_assets_enabled),
                  load_calendar=Depends(bar_calendar_loader),
                  ) -> PrepCalendar:
    window = _clamp_days(days)
    end_day = _normalize_date_or_400(end) if end else _today_iso(clock)
    start_day = _window_start(end_day, window)
    rows = assets.prep_calendar_rows(start_day, end_day)
    built = prep_view.build_calendar(
        rows=rows, start_date=start_day, end_date=end_day, today=end_day,
        review_assets_enabled=review_enabled,
        first_snapshot_date=assets.first_prep_snapshot_date(),
    )
    defaults = resolve_default_date(
        page="prep", bar_dates=load_calendar(start_day, end_day) or [],
        reference_date=end_day,
        latest_record_date=built.get("latest_record_date"))
    return PrepCalendar(available=bool(rows), **built, **defaults)


@router.get("/api/prep/{trade_date}", response_model=PrepWatermark)
def prep_watermark(trade_date: str, assets=Depends(assets_repository),
                   clock=Depends(clock_module),
                   review_enabled: bool = Depends(review_assets_enabled),
                   ) -> PrepWatermark:
    day = _normalize_date_or_400(trade_date)
    today = _today_iso(clock)
    payloads = assets.get_prep_assets(day)
    report = _as_dict(payloads.get("collection_report")) or {}
    _prev, prev_meta = assets.get_previous_prep_snapshot(day)
    has_prior_snapshot = prev_meta.get("trade_date") is not None

    rows = prep_view.build_asset_rows(
        payloads=payloads, trade_date=day, today=today,
        review_assets_enabled=review_enabled,
        has_prior_snapshot=has_prior_snapshot)
    summary = prep_view.summarize_expected(rows)

    missing = [row["asset_type"] for row in rows if not row["present"]]
    degraded = [
        row["asset_type"] for row in rows
        if row["status"] in _BAD_STATUS
        or (row["as_of"] and row["as_of"] != day)
    ]

    collection: list[PrepCollectionReport] = []
    for asset_type in PREP_ASSET_TYPES:
        entry = _as_dict(report.get(asset_type)) or {}
        collection.append(PrepCollectionReport(
            asset_type=asset_type,
            status=entry.get("status"),
            note=entry.get("note"),
            error=entry.get("error"),
        ))

    first_record_day = day == assets.first_prep_record_date()

    return PrepWatermark(
        trade_date=day,
        available=any(asset_type in payloads for asset_type in PREP_ASSET_TYPES),
        assets=[PrepAssetStatus(**row) for row in rows],
        collection_report=collection,
        missing=missing,
        degraded=degraded,
        has_snapshot=assets.has_prep_snapshot(day),
        has_prior_snapshot=has_prior_snapshot,
        first_record_day=first_record_day,
        expected_ok=summary["expected_ok"],
        expected_total=summary["expected_total"],
    )


@router.get("/api/prep/{trade_date}/snapshot", response_model=PrepSnapshotView)
def prep_snapshot(trade_date: str, source: str = "v2", slim: bool = True,
                  assets=Depends(assets_repository)) -> PrepSnapshotView:
    day = _normalize_date_or_400(trade_date)
    src = params.parse_source(source)
    payload, meta = assets.get_prep_snapshot(day, src, with_meta=True)
    if payload is None:
        raise ApiError(404, "not_found", f"{day} 无 {src} 复盘快照")

    raw_size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    blocks_present = list(payload.keys())
    working = dict(payload)
    if slim:
        working.pop("raw_candidates", None)
        review_candidates = working.get("review_candidates")
        if isinstance(review_candidates, list):
            working["review_candidates"] = [
                {key: value for key, value in item.items() if key != "legacy"}
                if isinstance(item, dict) else item
                for item in review_candidates
            ]

    return PrepSnapshotView(
        trade_date=day,
        version=working.get("version"),
        created_at=(meta or {}).get("created_at"),
        blocks_present=blocks_present,
        sentiment=_as_dict(working.get("sentiment")),
        main_boards=_as_list(working.get("main_boards")),
        macro=_as_dict(working.get("macro")),
        dragon_source=_as_dict(working.get("dragon_source")),
        candidates=_as_list(working.get("candidates")),
        review_candidates=_as_list(working.get("review_candidates")),
        review=_as_dict(working.get("review")),
        feedback=_as_dict(working.get("feedback")),
        plan_review=_as_dict(working.get("plan_review")),
        slim=slim,
        raw_size_bytes=raw_size,
        derived=prep_view.build_snapshot_derived(payload),
    )


@router.get("/api/prep/{trade_date}/review-context", response_model=ReviewContext)
def prep_review_context(trade_date: str, source: str = "v2",
                        assets=Depends(assets_repository)) -> ReviewContext:
    day = _normalize_date_or_400(trade_date)
    src = params.parse_source(source)
    current = assets.get_prep_snapshot(day, src)
    prev, prev_meta = assets.get_previous_prep_snapshot(day, src)
    prev_plan = assets.get_prep_asset(day, "prev_plan")
    return ReviewContext(**prep_view.build_review_context(
        trade_date=day, current=current, prev=prev,
        prev_meta=prev_meta, prev_plan=prev_plan))


@router.get("/api/prep/{trade_date}/plan-followup", response_model=PlanFollowup)
def prep_plan_followup(trade_date: str,
                       horizons: str | None = "1,3,5",
                       assets=Depends(assets_repository),
                       store=Depends(decision_review_store),
                       load_bars=Depends(local_bars_loader)) -> PlanFollowup:
    """计划日被否 / 推荐股的 T+1/3/5 净收益（固定 v2，无 source 参数）。"""

    day = _normalize_date_or_400(trade_date)
    requested = _parse_horizons(horizons)
    current_error: str | None = None
    try:
        current = assets.get_prep_snapshot(day, "v2")
    except Exception as exc:      # 损坏快照 → 空态，不 500
        current = None
        current_error = f"{type(exc).__name__}: {exc}"
    try:
        _prev, prev_meta = assets.get_previous_prep_snapshot(day, "v2")
    except Exception:
        prev_meta = {}
    payload = prep_effect.run_plan_followup(
        trade_date=day,
        current=current,
        prev_meta=prev_meta,
        store=store.build_store("prep"),
        load_bars=load_bars,
        horizons=requested,
        current_error=current_error,
    )
    return PlanFollowup(**payload)


# ─── auction 端点 ──────────────────────────────────────────────────────────


@router.get("/api/auction/dates", response_model=AuctionDates)
def auction_dates(limit: int = 30, assets=Depends(assets_repository)) -> AuctionDates:
    limits = params.clamp_limit(limit, default=30, maximum=200)
    snapshot_dates = assets.list_auction_snapshot_dates()
    asset_dates = assets.list_auction_asset_dates()
    union = sorted(set(snapshot_dates) | set(asset_dates))[-limits:]
    keep = set(union)
    return AuctionDates(
        snapshot_dates=[day for day in snapshot_dates if day in keep],
        asset_dates=[day for day in asset_dates if day in keep],
        count=len(union),
    )


@router.get("/api/auction/calendar", response_model=AuctionCalendar)
def auction_calendar(days: int = 365, end: str | None = None,
                     assets=Depends(assets_repository),
                     clock=Depends(clock_module),
                     load_calendar=Depends(bar_calendar_loader),
                     ) -> AuctionCalendar:
    window = _clamp_days(days)
    end_day = _normalize_date_or_400(end) if end else _today_iso(clock)
    start_day = _window_start(end_day, window)
    rows = assets.auction_calendar_rows(start_day, end_day)
    built = auction_view.build_auction_calendar(
        rows=rows, start_date=start_day, end_date=end_day)
    defaults = resolve_default_date(
        page="auction", bar_dates=load_calendar(start_day, end_day) or [],
        reference_date=end_day,
        latest_record_date=built.get("latest_record_date"))
    return AuctionCalendar(available=bool(rows), **built, **defaults)


@router.get("/api/auction/{trade_date}", response_model=AuctionDayView)
def auction_day(trade_date: str, source: str = "v2", with_assets: bool = False,
                assets=Depends(assets_repository)) -> AuctionDayView:
    day = _normalize_date_or_400(trade_date)
    src = params.parse_source(source)
    rows = assets.list_auction_snapshots(day, src, with_meta=True)
    names = assets.list_auction_asset_names(day)
    asset_codes = sorted(names)
    payload = auction_view.build_auction_day(
        trade_date=day, rows=rows, names=names, asset_codes=asset_codes)
    # 既有键语义保留：只有显式 with_assets 才回填原始资产数（不读 items）。
    payload["assets_count"] = len(asset_codes) if with_assets else None
    return AuctionDayView(**payload)


def _parse_horizons(raw: str | None) -> list[int]:
    allowed = set(auction_effect_view.ALLOWED_HORIZONS)
    if raw is None:
        return sorted(allowed)
    text = raw.strip()
    if not text:
        raise ApiError(400, "invalid_request", "horizons 不能为空")
    values: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError as exc:
            raise ApiError(400, "invalid_request",
                           "horizons 只接受 {1,3,5} 的非空子集") from exc
        if value not in allowed:
            raise ApiError(400, "invalid_request",
                           "horizons 只接受 {1,3,5} 的非空子集")
        values.append(value)
    if not values:
        raise ApiError(400, "invalid_request", "horizons 不能为空")
    return sorted(set(values))


@router.get("/api/auction/{trade_date}/effect", response_model=AuctionEffect)
def auction_effect(trade_date: str, window: int = 20,
                   horizons: str | None = "1,3,5",
                   store=Depends(decision_review_store),
                   load_bars=Depends(local_bars_loader)) -> AuctionEffect:
    day = _normalize_date_or_400(trade_date)
    if window < 1 or window > auction_effect_view.WINDOW_MAX:
        raise ApiError(
            400, "invalid_request",
            f"window 必须在 1..{auction_effect_view.WINDOW_MAX} 之间")
    payload = auction_effect_view.run_auction_effect(
        trade_date=day,
        store=store.build_store("auction"),
        load_bars=load_bars,
        window=window,
        horizons=_parse_horizons(horizons),
    )
    return AuctionEffect(**payload)
