"""龙头端点：冻结的 ``/api/dragons`` / ``/api/summary`` + 新增只读端点。"""

from __future__ import annotations

from functools import wraps
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import compat, params
from ..deps import dragon_repository, scan_repository
from ..errors import ApiError
from ..schemas import Dims, QuoteFields, ReviewFields

router = APIRouter(tags=["dragons"])


def frozen_500(fn):
    """冻结端点的错误形状：任何异常 → 500 ``{"error": <message>}``。"""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 基线冻结为单键 500
            return JSONResponse(status_code=500, content={"error": str(exc)})

    return wrapper


@router.get("/api/dragons", response_model=None, include_in_schema=True)
@frozen_500
def list_dragons(request: Request, dragons=Depends(dragon_repository)):
    """冻结契约：``{data, count, summary}`` 键集不变（不设 response_model）。"""
    return compat.dragons_payload(
        dragons, request.query_params, request.app.state.settings.default_source,
    )


@router.get("/api/summary", response_model=None, include_in_schema=True)
@frozen_500
def legacy_summary(request: Request, dragons=Depends(dragon_repository)):
    return compat.legacy_summary(
        dragons, request.query_params, request.app.state.settings.default_source,
    )


# ─── 新增：近几日榜单 ──────────────────────────────────────────────────────


class DragonPreview(BaseModel):
    code: str
    name: str
    rank: int | None = None
    composite_score: float | None = None
    is_true_dragon: bool | None = None


class DragonDateEntry(BaseModel):
    trade_date: str
    count: int
    preview: list[DragonPreview]


class TradeDatesPage(BaseModel):
    data: list[DragonDateEntry]
    count: int


def _preview_sort(rows: list[dict]) -> list[dict]:
    ordered = sorted(rows, key=lambda row: row.get("code") or "")
    ordered.sort(
        key=lambda row: (
            row.get("composite_score") is None,
            -(row.get("composite_score") or 0.0),
        )
    )
    ordered.sort(key=lambda row: (row.get("rank") is None, row.get("rank") or 0))
    return ordered


def _preview(row: dict) -> DragonPreview:
    return DragonPreview(
        code=row.get("code") or "",
        name=row.get("name") or "",
        rank=row.get("rank"),
        composite_score=row.get("composite_score"),
        is_true_dragon=row.get("is_true_dragon"),
    )


@router.get("/api/dragons/trade-dates", response_model=TradeDatesPage)
def list_trade_dates(source: str = "v2", limit: int = 10,
                     dragons=Depends(dragon_repository)) -> TradeDatesPage:
    limits = params.clamp_limit(limit, default=10, maximum=50)
    src = params.parse_source(source)
    dates = dragons.list_dragon_trade_dates(source=src)
    recent = list(reversed(dates[-limits:]))
    data: list[DragonDateEntry] = []
    for trade_date in recent:
        rows = dragons.get_dragons(trade_date, src)
        data.append(DragonDateEntry(
            trade_date=trade_date,
            count=len(rows),
            preview=[_preview(row) for row in _preview_sort(rows)[:3]],
        ))
    return TradeDatesPage(data=data, count=len(data))


# ─── 新增：单只龙头明细 ────────────────────────────────────────────────────


class DragonDetail(BaseModel):
    trade_date: str
    code: str
    name: str
    source: str
    scan_id: str | None = None
    rank: int | None = None
    composite_score: float | None = None
    board_count: int | None = None
    concepts: list[str] = []
    report_text: str = ""
    is_true_dragon: bool | None = None
    quote: QuoteFields
    review_loose: ReviewFields
    review_strict: ReviewFields | None = None
    derived_status: str | None = None
    caliber: str
    dims: Dims | None = None
    dims_source: Literal["scan_stocks", "scan_stocks_missing", "scan_missing"]
    reject_reason: str | None = None


def _review_fields(row: dict, prefix: str = "") -> ReviewFields:
    return ReviewFields(
        buy_date=row.get(f"{prefix}buy_date"),
        buy_price=row.get(f"{prefix}buy_price"),
        max_return_5d=row.get(f"{prefix}max_return_5d"),
        max_drawdown_5d=row.get(f"{prefix}max_drawdown_5d"),
        max_return_hold_days=row.get(f"{prefix}max_return_hold_days"),
        review_status=row.get(f"{prefix}review_status"),
    )


def _dims(stock: dict) -> Dims:
    return Dims(
        drive=stock.get("dim_drive"),
        leadership=stock.get("dim_leadership"),
        anti_drop=stock.get("dim_anti_drop"),
        liquidity=stock.get("dim_liquidity"),
        absorption=stock.get("dim_absorption"),
    )


@router.get("/api/dragons/{trade_date}/{code}", response_model=DragonDetail)
def dragon_detail(trade_date: str, code: str, source: str = "v2",
                  caliber: str | None = None,
                  dragons=Depends(dragon_repository),
                  scans=Depends(scan_repository)) -> DragonDetail:
    try:
        day = params.normalize_date(trade_date)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc
    src = params.parse_source(source)
    resolved = dragons.resolve_caliber(caliber, src)
    rows = dragons.query_dragons(
        {"date_from": day, "date_to": day}, source=src, caliber=resolved,
    )
    row = next((item for item in rows if item.get("code") == code), None)
    if row is None:
        raise ApiError(404, "not_found", f"{day} 无代码 {code} 的龙头记录")

    scan_id = row.get("scan_id")
    dims = None
    reject_reason = None
    if not scan_id:
        dims_source = "scan_missing"
    else:
        stocks = [item for item in scans.get_scan_stocks(scan_id, src)
                  if item.get("code") == code]
        if stocks:
            dims = _dims(stocks[0])
            reject_reason = stocks[0].get("reject_reason")
            dims_source = "scan_stocks"
        else:
            dims_source = "scan_stocks_missing"

    return DragonDetail(
        trade_date=row.get("trade_date") or day,
        code=row.get("code") or code,
        name=row.get("name") or "",
        source=src,
        scan_id=scan_id,
        rank=row.get("rank"),
        composite_score=row.get("composite_score"),
        board_count=row.get("board_count"),
        concepts=row.get("concepts") or [],
        report_text=row.get("report_text") or "",
        is_true_dragon=row.get("is_true_dragon"),
        quote=QuoteFields(
            open=row.get("open_px"),
            close=row.get("close_px"),
            high=row.get("high_px"),
            low=row.get("low_px"),
            pct=row.get("pct"),
            turnover_rate=row.get("turnover_rate"),
            amount=row.get("amount"),
            market_cap=row.get("market_cap"),
        ),
        review_loose=_review_fields(row),
        review_strict=(
            _review_fields(row, prefix="strict_")
            if row.get("strict_reviewed") else None
        ),
        derived_status=row.get("derived_status"),
        caliber=resolved,
        dims=dims,
        dims_source=dims_source,
        reject_reason=reject_reason,
    )
