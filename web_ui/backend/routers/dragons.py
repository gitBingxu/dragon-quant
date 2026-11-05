"""龙头端点：冻结的 ``/api/dragons`` / ``/api/summary`` + 新增只读端点。"""

from __future__ import annotations

from datetime import datetime
from functools import wraps
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import compat, params
from ..deps import (
    clock_module,
    dragon_repository,
    kline_provider,
    scan_repository,
)
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
    items: list[DragonPreview]


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


_SCAN_RANK_MISSING = 9999


def _scan_contributions(day: str, src: str, scans) -> list[dict]:
    """当日可见原始真龙贡献。

    过滤口径与 ``dragons.rebuild_dragons_for_date`` 的入选集合保持一致：
    ``is_true_dragon is not False``（None 视为未知但保留）、``rank is not None``、
    ``rank <= scan_top_n``。展示层只复用该口径的结果，不复制 5 日 gate 规则。
    """
    contribs = scans.list_scan_stock_contributions_by_date(day, source=src)
    return [
        c for c in contribs
        if c.get("is_true_dragon") is not False
        and c.get("rank") is not None
        and c.get("scan_top_n") is not None
        and c["rank"] <= c["scan_top_n"]
    ]


def _best_scan_contributions(contribs: list[dict]) -> dict[str, dict]:
    """按 ``code`` 选最佳贡献：``rank`` 最小 → ``composite_score`` 最高 → 最新扫描。

    规则逐项对齐 ``dragons.rebuild_dragons_for_date``（仅 3 个键）；出现第三个
    消费方时再提取为公共只读函数。
    """
    best: dict[str, dict] = {}
    for c in contribs:
        code = c.get("code")
        if not code:
            continue
        cur = best.get(code)
        if cur is None:
            best[code] = c
            continue
        rank_new = c["rank"] if c.get("rank") is not None else _SCAN_RANK_MISSING
        rank_cur = cur["rank"] if cur.get("rank") is not None else _SCAN_RANK_MISSING
        if rank_new != rank_cur:
            if rank_new < rank_cur:
                best[code] = c
            continue
        score_new = c.get("composite_score") or 0
        score_cur = cur.get("composite_score") or 0
        if score_new != score_cur:
            if score_new > score_cur:
                best[code] = c
            continue
        if (c.get("scan_created_at") or "") > (cur.get("scan_created_at") or ""):
            best[code] = c
    return best


def _find_scan_contribution(day: str, code: str, src: str, scans) -> dict | None:
    """当日扫描贡献中该 ``code`` 的最佳行；无则 ``None``。"""
    return _best_scan_contributions(_scan_contributions(day, src, scans)).get(code)


@router.get("/api/dragons/trade-dates", response_model=TradeDatesPage)
def list_trade_dates(source: str = "v2", limit: int = 10,
                     dragons=Depends(dragon_repository),
                     scans=Depends(scan_repository)) -> TradeDatesPage:
    limits = params.clamp_limit(limit, default=10, maximum=50)
    src = params.parse_source(source)
    # 日期并集：扫描表覆盖「当日全部被 5 日去重」的日期，dragons 表兜底遗留数据。
    dates = sorted(
        set(scans.list_scan_dates(source=src))
        | set(dragons.list_dragon_trade_dates(source=src)),
        reverse=True,
    )
    data: list[DragonDateEntry] = []
    # 逐日构建非空卡片直到收满 limit；不得先截断日期再丢弃空条目。
    for trade_date in dates:
        if len(data) >= limits:
            break
        rows = list(_best_scan_contributions(
            _scan_contributions(trade_date, src, scans)).values())
        if not rows:
            # 无可用扫描贡献（v1 历史 / 扫描表被清理）→ 保持旧行为。
            rows = dragons.get_dragons(trade_date, src)
        ordered = _preview_sort(rows)
        if not ordered:
            continue
        items = [_preview(row) for row in ordered]
        data.append(DragonDateEntry(
            trade_date=trade_date,
            count=len(items),
            preview=items[:3],
            items=items,
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
    strict_tradable_open: dict | None = None


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


def _find_dragon_row(day: str, code: str, src: str, caliber: str,
                     dragons) -> dict | None:
    """按可见口径查 ``dragons_v2`` 行；无则 ``None``（明细与绩效共用）。"""
    rows = dragons.query_dragons(
        {"date_from": day, "date_to": day}, source=src, caliber=caliber,
    )
    return next((item for item in rows if item.get("code") == code), None)


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
    row = _find_dragon_row(day, code, src, resolved, dragons)
    if row is None:
        # 被 5 日去重排除的当日真龙：回退原始扫描贡献（字段结构不变）。
        row = _find_scan_contribution(day, code, src, scans)
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
        strict_tradable_open=row.get("strict_tradable_open"),
        caliber=resolved,
        dims=dims,
        dims_source=dims_source,
        reject_reason=reject_reason,
    )


# ─── 新增：入选后表现（宽松口径，至最新有效日 K） ─────────────────────────


class DragonPerformance(BaseModel):
    trade_date: str
    code: str
    caliber: Literal["loose"] = "loose"
    status: Literal["ok", "no_entry", "empty_window", "error"]
    as_of: str
    buy_date: str | None = None
    buy_price: float | None = None
    max_return_since_entry: float | None = None
    max_drawdown_since_entry: float | None = None
    peak_date: str | None = None
    latest_bar_date: str | None = None
    bars_in_window: int | None = None
    bars_after_entry: int | None = None
    error: str | None = None


def _normalize_date_or_400(raw: str | None, field: str) -> str:
    try:
        return params.normalize_date(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", f"{field}: {exc}") from exc


def _kline_days(trade_date: str, as_of: str) -> int:
    span = (datetime.strptime(as_of, "%Y-%m-%d")
            - datetime.strptime(trade_date, "%Y-%m-%d")).days
    return min(1500, max(90, span + 30))


@router.get(
    "/api/dragons/{trade_date}/{code}/performance",
    response_model=DragonPerformance,
)
def dragon_performance(
    trade_date: str,
    code: str,
    as_of: str | None = None,
    dragons=Depends(dragon_repository),
    scans=Depends(scan_repository),
    provider=Depends(kline_provider),
    clock=Depends(clock_module),
) -> DragonPerformance:
    """入选后表现：宽松入场语义，窗口到 ``as_of`` 的最新有效日 K。

    该端点是抽屉内的局部增强：记录存在时一律 200，传输/数据问题用
    ``status="error"`` 表达；400 只给参数错误，404 只给记录不存在。
    """
    day = _normalize_date_or_400(trade_date, "trade_date")
    if as_of:
        as_of_day = _normalize_date_or_400(as_of, "as_of")
    else:
        as_of_day = clock.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    if as_of_day < day:
        raise ApiError(400, "invalid_request", "as_of 不能早于 trade_date")

    # 记录存在性：dragons_v2 行或当日原始扫描贡献任一命中即可（绩效口径固定 v2）。
    row = _find_dragon_row(day, code, "v2", dragons.resolve_caliber(None, "v2"),
                           dragons)
    if row is None and _find_scan_contribution(day, code, "v2", scans) is None:
        raise ApiError(404, "not_found", f"{day} 无代码 {code} 的龙头记录")

    from dragon_quant.review.performance import compute_performance

    try:
        klines = provider.get_kline(
            code, days=_kline_days(day, as_of_day), fq_type="normal")
    except Exception as exc:  # noqa: BLE001 - 传输失败降级为 200 error
        return DragonPerformance(
            trade_date=day, code=code, status="error", as_of=as_of_day,
            error=f"K线拉取失败: {exc}",
        )

    result = compute_performance(klines, day, as_of_day)
    return DragonPerformance(trade_date=day, code=code, **result)
