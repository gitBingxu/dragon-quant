"""扫描端点：只读 ``scans_v2`` / ``scan_stocks_v2`` / ``scan_logs_v2``。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import params
from ..deps import scan_logs_repository, scan_repository
from ..errors import ApiError
from ..presenters import scan_view
from ..schemas import Dims

router = APIRouter(tags=["scans"])

ScanStatus = Literal["completed", "completed_with_errors", "logs_missing"]
LOG_LEVELS = ("info", "warn", "error")


class ScanSummary(BaseModel):
    id: str
    scan_date: str
    elapsed_s: float | None = None
    top_n: int | None = None
    candidates_n: int | None = None
    workers: int | None = None
    created_at: str = ""
    source: str = "v2"
    true_dragon_n: int | None = None
    stock_n: int | None = None
    log_entries: int = 0
    error_n: int | None = None
    status: ScanStatus = "completed"


class ScanDetail(ScanSummary):
    has_raw_output: bool = False
    raw_output_bytes: int | None = None


class ScanLogEntry(BaseModel):
    ts: float | None = None
    time: str | None = None
    category: str = ""
    level: str = ""
    message: str = ""
    code: str = ""


class ScanLogSummary(BaseModel):
    total_entries: int = 0
    phases: list[dict] = []
    api_total: int = 0
    api_ok: int = 0
    api_error: int = 0
    api_by_provider: dict[str, dict] = {}
    scorer_count: int = 0
    error_count: int = 0
    truncated: bool = False


class ScanLogsPage(BaseModel):
    scan_id: str
    available: bool
    total: int
    entries: list[ScanLogEntry]
    summary: ScanLogSummary | None = None


class ScanStock(BaseModel):
    code: str
    name: str
    rank: int | None = None
    composite_score: float | None = None
    board_count: int | None = None
    concepts: list[str] = []
    dims: Dims
    is_true_dragon: bool | None = None
    reject_reason: str | None = None
    report_text: str = ""


class ScanStocksPage(BaseModel):
    data: list[ScanStock]
    count: int
    total: int
    truncated: bool


def _summary(row: dict, stock_stats=None, log_stats=None) -> dict:
    return scan_view.build_scan_row(row, stock_stats, log_stats)


@router.get("/api/scans", response_model=None)
def list_scans(limit: int = 50, scans=Depends(scan_repository),
               logs=Depends(scan_logs_repository)):
    limits = params.clamp_limit(limit, default=50, maximum=200)
    rows = scans.list_scans(limit=limits)
    ids = [row.get("id") for row in rows if row.get("id")]
    stock_stats = scans.summarize_scan_stocks(ids, source="v2")
    log_stats = logs.summarize_scan_logs(ids, source="v2")
    data = scan_view.build_scan_rows(rows, stock_stats, log_stats)
    return {"data": data, "count": len(data)}


@router.get("/api/scans/{scan_id}", response_model=ScanDetail)
def get_scan(scan_id: str, scans=Depends(scan_repository),
             logs=Depends(scan_logs_repository)) -> ScanDetail:
    row = scans.get_scan(scan_id)
    if row is None:
        raise ApiError(404, "not_found", f"scan {scan_id} 不存在")
    stock_stats = scans.summarize_scan_stocks([scan_id], source="v2")
    log_stats = logs.summarize_scan_logs([scan_id], source="v2")
    raw = row.get("raw_output")
    return ScanDetail(
        **_summary(row, stock_stats, log_stats),
        has_raw_output=raw is not None,
        raw_output_bytes=(len(raw) if isinstance(raw, str) else None),
    )


@router.get("/api/scans/{scan_id}/logs", response_model=ScanLogsPage)
def scan_logs(scan_id: str, level: str | None = None, offset: int = 0,
              limit: int = 200, scans=Depends(scan_repository),
              logs=Depends(scan_logs_repository)) -> ScanLogsPage:
    if scans.get_scan(scan_id) is None:
        raise ApiError(404, "not_found", f"scan {scan_id} 不存在")
    normalized_level = (level or "").strip().lower() or None
    if normalized_level not in (None, *LOG_LEVELS):
        raise ApiError(400, "invalid_request",
                       f"level 只接受 {'/'.join(LOG_LEVELS)}")
    window = params.clamp_limit(limit, default=200, maximum=500)
    page = logs.get_scan_logs_page(
        scan_id, level=normalized_level,
        offset=max(0, int(offset)), limit=window)
    payload = scan_view.build_scan_logs(
        scan_id, page=page, summary_raw=logs.log_summary(scan_id))
    return ScanLogsPage(**payload)


def _stock(row: dict) -> ScanStock:
    return ScanStock(
        code=row.get("code") or "",
        name=row.get("name") or "",
        rank=row.get("rank"),
        composite_score=row.get("composite_score"),
        board_count=row.get("board_count"),
        concepts=row.get("concepts") or [],
        dims=Dims(
            drive=row.get("dim_drive"),
            leadership=row.get("dim_leadership"),
            anti_drop=row.get("dim_anti_drop"),
            liquidity=row.get("dim_liquidity"),
            absorption=row.get("dim_absorption"),
        ),
        is_true_dragon=row.get("is_true_dragon"),
        reject_reason=row.get("reject_reason"),
        report_text=row.get("report_text") or "",
    )


def _sort_stocks(rows: list[dict]) -> list[dict]:
    """rank null 最后 → composite desc → code（稳定排序，避免 SQLite NULL 差异）。"""
    ordered = sorted(rows, key=lambda row: row.get("code") or "")
    ordered.sort(key=lambda row: (
        row.get("composite_score") is None,
        -(row.get("composite_score") or 0.0),
    ))
    ordered.sort(key=lambda row: (row.get("rank") is None, row.get("rank") or 0))
    return ordered


@router.get("/api/scans/{scan_id}/stocks", response_model=ScanStocksPage)
def scan_stocks(scan_id: str, true_only: bool = False, limit: int = 200,
                offset: int = 0, scans=Depends(scan_repository)) -> ScanStocksPage:
    rows = scans.get_scan_stocks(scan_id)
    if not rows and scans.get_scan(scan_id) is None:
        raise ApiError(404, "not_found", f"scan {scan_id} 不存在")
    ordered = _sort_stocks(rows)
    if true_only:
        ordered = [row for row in ordered if row.get("is_true_dragon")]
    total = len(ordered)
    start = max(0, int(offset))
    limits = params.clamp_limit(limit, default=200, maximum=1000)
    window = ordered[start:start + limits]
    return ScanStocksPage(
        data=[_stock(row) for row in window],
        count=len(window),
        total=total,
        truncated=start + limits < total,
    )
