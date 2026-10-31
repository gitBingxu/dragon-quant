"""扫描端点：只读 ``scans_v2`` / ``scan_stocks_v2``。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import params
from ..deps import scan_repository
from ..errors import ApiError
from ..schemas import Dims

router = APIRouter(tags=["scans"])


class ScanSummary(BaseModel):
    id: str
    scan_date: str
    elapsed_s: float | None = None
    top_n: int | None = None
    candidates_n: int | None = None
    workers: int | None = None
    created_at: str = ""
    source: str = "v2"


class ScanDetail(ScanSummary):
    has_raw_output: bool = False
    raw_output_bytes: int | None = None


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


def _summary(row: dict) -> ScanSummary:
    return ScanSummary(
        id=row.get("id") or "",
        scan_date=row.get("scan_date") or "",
        elapsed_s=row.get("elapsed_s"),
        top_n=row.get("top_n"),
        candidates_n=row.get("candidates_n"),
        workers=row.get("workers"),
        created_at=row.get("created_at") or "",
        source=row.get("source") or "v2",
    )


@router.get("/api/scans", response_model=None)
def list_scans(limit: int = 50, scans=Depends(scan_repository)):
    limits = params.clamp_limit(limit, default=50, maximum=200)
    rows = scans.list_scans(limit=limits)
    return {"data": [_summary(row).model_dump() for row in rows], "count": len(rows)}


@router.get("/api/scans/{scan_id}", response_model=ScanDetail)
def get_scan(scan_id: str, scans=Depends(scan_repository)) -> ScanDetail:
    row = scans.get_scan(scan_id)
    if row is None:
        raise ApiError(404, "not_found", f"scan {scan_id} 不存在")
    raw = row.get("raw_output")
    return ScanDetail(
        **_summary(row).model_dump(),
        has_raw_output=raw is not None,
        raw_output_bytes=(len(raw) if isinstance(raw, str) else None),
    )


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
