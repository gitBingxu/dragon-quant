"""只读准确率 / 单日效果端点（方案 §7.2–§7.5）。

路由只做参数与响应；信号、窗口、聚合与板块匹配全部在
``web_ui.backend.presenters``（纯投影）。行情只读本地 ``stock_daily``，
不触网、不写库。本 router 必须**先于** ``assets.router`` 注册，避免
``/api/prep/accuracy`` 被 ``/api/prep/{trade_date}`` 吞掉。
"""

from __future__ import annotations

from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import params
from ..deps import (
    assets_repository,
    bar_calendar_loader,
    clock_module,
    local_bars_loader,
)
from ..errors import ApiError
from ..presenters import accuracy as accuracy_view
from ..presenters import day_effect as day_effect_view
from ..presenters.bars_loader import BarsTruncated, load_bars_resilient
from ..presenters.local_market import shift_day

router = APIRouter(tags=["accuracy"])

SHANGHAI = ZoneInfo("Asia/Shanghai")
EFFECT_LOOKBACK_DAYS = 10
EFFECT_LOOKAHEAD_DAYS = 30


def _today_iso(clock) -> str:
    return clock.now(SHANGHAI).strftime("%Y-%m-%d")


def _normalize_date_or_400(raw: str) -> str:
    try:
        return params.normalize_date(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc


# ─── 响应模型 ────────────────────────────────────────────────────────────


class AccuracyWindowModel(BaseModel):
    entry_offset: int
    exit_offset: int
    entry_price: str
    exit_price: str
    cost_bps: float


class AccuracySummary(BaseModel):
    correct: int
    valid: int
    excluded: int
    hit_rate: float | None = None
    excluded_by_reason: dict[str, int] = {}


class AccuracyStock(BaseModel):
    code: str
    name: str | None = None
    correct: int = 0
    valid: int = 0
    excluded: int = 0
    hit_rate: float | None = None


class AccuracyResponse(BaseModel):
    status: Literal["ok", "insufficient_data"]
    reason: str | None = None
    as_of: str | None = None
    latest_bar_date: str | None = None
    snapshot_count: int
    data_source: Literal["local_stock_daily"] = "local_stock_daily"
    calendar_basis: str
    sample_unit: Literal["stock_day"] = "stock_day"
    window: AccuracyWindowModel
    summary: AccuracySummary
    day_kind_counts: dict[str, int] = {}
    stocks: list[AccuracyStock] = []
    excluded_snapshot_dates: dict[str, int] = {}
    warnings: list[str] = []
    caveats: list[str] = []


class EffectBoardRow(BaseModel):
    rank: int | None = None
    board_name: str | None = None
    category: str | None = None
    is_mainline: bool = True
    board_code: str | None = None
    t_change_pct: float | None = None
    next_change_pct: float | None = None
    match: str | None = None
    exclusion_reason: str | None = None
    warnings: list[str] = []


class EffectBoardSummary(BaseModel):
    evaluated: int = 0
    missing: int = 0
    mean_change_pct: float | None = None


class EffectBoards(BaseModel):
    available: bool
    reason: str | None = None
    source: str | None = None
    rows: list[EffectBoardRow] = []
    summary: EffectBoardSummary


class EffectStockRow(BaseModel):
    code: str
    name: str | None = None
    group: Literal["recommended", "not_recommended", "data_insufficient"]
    role: str | None = None
    review_score: float | None = None
    eligibility: str | None = None
    position_pct: float | None = None
    status: Literal["completed", "cannot_evaluate", "pending"]
    buy_date: str | None = None
    buy_price: float | None = None
    max_return_5d: float | None = None
    max_drawdown_5d: float | None = None
    max_return_hold_days: int | None = None
    partial_window: bool = False
    reason: str | None = None


class EffectGroupSummary(BaseModel):
    group: str
    label: str
    valid: int
    cannot_evaluate: int
    pending: int
    mean_max_return_5d: float | None = None
    median_max_return_5d: float | None = None
    mean_max_drawdown_5d: float | None = None
    median_max_drawdown_5d: float | None = None


class EffectStocks(BaseModel):
    available: bool
    reason: str | None = None
    rows: list[EffectStockRow] = []
    summary: list[EffectGroupSummary] = []


class EffectCoverage(BaseModel):
    boards_total: int
    stocks_total: int
    bar_dates: int


class PrepEffectResponse(BaseModel):
    status: Literal["ok", "insufficient_data"]
    reason: str | None = None
    trade_date: str
    next_trade_date: str | None = None
    snapshot_version: str | None = None
    data_source: Literal["local_prep_assets+local_stock_daily"] = (
        "local_prep_assets+local_stock_daily")
    calendar_basis: str
    boards: EffectBoards
    stocks: EffectStocks
    coverage: EffectCoverage
    warnings: list[str] = []
    caveats: list[str] = []


# ─── 端点 ────────────────────────────────────────────────────────────────


@router.get("/api/prep/accuracy", response_model=AccuracyResponse)
def prep_accuracy(assets=Depends(assets_repository),
                  load_bars=Depends(local_bars_loader),
                  load_calendar=Depends(bar_calendar_loader),
                  clock=Depends(clock_module)) -> AccuracyResponse:
    """prep 全历史逐股方向准确率（固定 v2、无日期参数）。"""

    try:
        dates = assets.list_prep_snapshot_dates()
    except Exception:
        dates = []
    payload = accuracy_view.build_prep_accuracy(
        snapshot_dates=dates,
        load_snapshot=lambda day: assets.get_prep_snapshot(day, "v2"),
        bar_dates=load_calendar() or [],
        load_bars=load_bars,
        today=_today_iso(clock),
    )
    return AccuracyResponse(**payload)


@router.get("/api/auction/accuracy", response_model=AccuracyResponse)
def auction_accuracy(assets=Depends(assets_repository),
                     load_bars=Depends(local_bars_loader),
                     load_calendar=Depends(bar_calendar_loader),
                     clock=Depends(clock_module)) -> AccuracyResponse:
    """竞价全历史逐股方向准确率（固定 v2、无日期参数）。"""

    try:
        dates = assets.list_auction_snapshot_dates()
    except Exception:
        dates = []
    payload = accuracy_view.build_auction_accuracy(
        snapshot_dates=dates,
        load_snapshot_rows=lambda day: assets.list_auction_snapshots(day, "v2"),
        load_names=lambda day: assets.list_auction_asset_names(day),
        bar_dates=load_calendar() or [],
        load_bars=load_bars,
        today=_today_iso(clock),
    )
    return AccuracyResponse(**payload)


def _candidate_codes(snapshot) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    codes: list[str] = []
    for item in snapshot.get("review_candidates") or []:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if isinstance(code, str) and code and code not in codes:
            codes.append(code)
    return codes


@router.get("/api/prep/{trade_date}/effect", response_model=PrepEffectResponse)
def prep_day_effect(trade_date: str,
                    assets=Depends(assets_repository),
                    load_bars=Depends(local_bars_loader),
                    load_calendar=Depends(bar_calendar_loader)) -> PrepEffectResponse:
    """当前选中日效果：主线次日涨跌 + 严格逐股收益/回撤（只读本地）。"""

    day = _normalize_date_or_400(trade_date)
    try:
        snapshot = assets.get_prep_snapshot(day, "v2")
    except Exception:
        snapshot = None
    try:
        blocks = assets.get_prep_asset(day, "ths_block_top")
    except Exception:
        blocks = None

    calendar = load_calendar() or []
    next_trade_date = next((item for item in sorted(set(calendar))
                            if item > day), None)
    next_blocks = None
    if next_trade_date is not None:
        try:
            next_blocks = assets.get_prep_asset(next_trade_date, "ths_block_top")
        except Exception:
            next_blocks = None

    codes = _candidate_codes(snapshot)
    bars: dict = {}
    fatal_reason: str | None = None
    extra_warnings: list[str] = []
    if codes:
        try:
            loaded = load_bars_resilient(
                load_bars, codes, shift_day(day, -EFFECT_LOOKBACK_DAYS),
                shift_day(day, EFFECT_LOOKAHEAD_DAYS))
            bars = dict(loaded.get("rows") or {})
        except BarsTruncated:
            fatal_reason = "local_bars_truncated"
        except Exception as exc:
            bars = {}
            extra_warnings.append(
                f"local_bars_error: {type(exc).__name__}: {exc}")

    payload = day_effect_view.build_day_effect(
        trade_date=day, snapshot=snapshot, calendar=calendar, blocks=blocks,
        next_blocks=next_blocks, bars=bars,
        next_trade_date=next_trade_date, fatal_reason=fatal_reason,
        extra_warnings=extra_warnings)
    return PrepEffectResponse(**payload)
