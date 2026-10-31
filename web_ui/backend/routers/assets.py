"""prep / auction 端点：只读 ``assets.db``（``create=False``，绝不建库）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import params
from ..deps import assets_repository
from ..errors import ApiError

router = APIRouter(tags=["assets"])

PREP_ASSET_TYPES = [
    "dragons",
    "ths_limit_up_pool",
    "ths_block_top",
    "market_breadth",
    "fg_boards",
    "holdings",
    "fund_flow",
    "cninfo_announcements",
    "macro_global",
    "stock_daily",
    "prev_plan",
    "auction_feedback",
    "t1_intraday",
]
_BAD_STATUS = {"error", "empty_suspicious", "unavailable"}
_REQUIRED_NONEMPTY = {"dragons", "ths_limit_up_pool"}


# ─── prep ──────────────────────────────────────────────────────────────────


class PrepAssetStatus(BaseModel):
    asset_type: str
    present: bool
    status: str | None = None
    note: str | None = None
    error: str | None = None
    as_of: str | None = None
    usable_display: bool = False


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


class PrepDates(BaseModel):
    asset_dates: list[str]
    snapshot_dates: list[str]
    count: int


def _payload_empty(payload) -> bool:
    if not isinstance(payload, dict):
        return True
    for key in ("rows", "items"):
        value = payload.get(key)
        if isinstance(value, (list, dict)) and value:
            return False
    for key in ("count", "total"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value:
            return False
    return True


def _report_entry(report, asset_type: str) -> dict:
    if not isinstance(report, dict):
        return {}
    entry = report.get(asset_type)
    return entry if isinstance(entry, dict) else {}


def _normalize_date_or_400(raw: str) -> str:
    try:
        return params.normalize_date(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc


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


@router.get("/api/prep/{trade_date}", response_model=PrepWatermark)
def prep_watermark(trade_date: str, assets=Depends(assets_repository)) -> PrepWatermark:
    day = _normalize_date_or_400(trade_date)
    payloads = assets.get_prep_assets(day)
    report = payloads.get("collection_report")

    statuses: list[PrepAssetStatus] = []
    missing: list[str] = []
    degraded: list[str] = []
    for asset_type in PREP_ASSET_TYPES:
        present = asset_type in payloads
        payload = payloads.get(asset_type)
        entry = _report_entry(report, asset_type)
        status = entry.get("status")
        as_of = payload.get("as_of") if isinstance(payload, dict) else None
        if not present:
            missing.append(asset_type)
        if status in _BAD_STATUS or (as_of and as_of != day):
            degraded.append(asset_type)
        usable = present and status not in _BAD_STATUS
        if usable and asset_type in _REQUIRED_NONEMPTY and _payload_empty(payload):
            usable = False
        if usable and as_of and as_of != day:
            usable = False
        statuses.append(PrepAssetStatus(
            asset_type=asset_type,
            present=present,
            status=status,
            note=entry.get("note"),
            error=entry.get("error"),
            as_of=as_of,
            usable_display=usable,
        ))

    collection: list[PrepCollectionReport] = []
    if isinstance(report, dict):
        for asset_type in PREP_ASSET_TYPES:
            entry = _report_entry(report, asset_type)
            collection.append(PrepCollectionReport(
                asset_type=asset_type,
                status=entry.get("status"),
                note=entry.get("note"),
                error=entry.get("error"),
            ))

    return PrepWatermark(
        trade_date=day,
        available=any(asset_type in payloads for asset_type in PREP_ASSET_TYPES),
        assets=statuses,
        collection_report=collection,
        missing=missing,
        degraded=degraded,
        has_snapshot=assets.has_prep_snapshot(day),
    )


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


def _as_dict(value) -> dict | None:
    return value if isinstance(value, dict) else None


def _as_list(value) -> list[dict] | None:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return None


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
    )


# ─── auction ───────────────────────────────────────────────────────────────


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


class AuctionDayView(BaseModel):
    trade_date: str
    available: bool
    count: int
    snapshots: list[AuctionSnapshotRow]
    summary: dict[str, int]
    assets_count: int | None = None


class AuctionDates(BaseModel):
    snapshot_dates: list[str]
    asset_dates: list[str]
    count: int


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


def _auction_summary(rows: list[dict]) -> dict[str, int]:
    summary = {
        "strong": 0,
        "medium": 0,
        "weak": 0,
        "vetoed": 0,
        "unavailable": 0,
        "unknown": 0,
    }
    for row in rows:
        quality = row.get("data_quality") if isinstance(row.get("data_quality"), dict) else {}
        if row.get("is_weak"):
            summary["vetoed"] += 1
        elif quality.get("status") == "unavailable":
            summary["unavailable"] += 1
        elif row.get("unverified"):
            summary["unknown"] += 1
        elif row.get("level") == "强":
            summary["strong"] += 1
        elif row.get("level") == "中":
            summary["medium"] += 1
        elif row.get("level") == "弱":
            summary["weak"] += 1
        else:
            summary["unknown"] += 1
    return summary


def _auction_row(row: dict) -> AuctionSnapshotRow:
    return AuctionSnapshotRow(
        code=row.get("code") or "",
        version=row.get("version") or row.get("config_version"),
        is_weak=row.get("is_weak"),
        strength=row.get("strength"),
        level=row.get("level"),
        direction=row.get("direction"),
        rank=row.get("rank"),
        validation_status=row.get("validation_status"),
        data_quality=row.get("data_quality"),
        risk_events=row.get("risk_events"),
        reject_reason=row.get("reject_reason"),
        error=row.get("error"),
    )


@router.get("/api/auction/{trade_date}", response_model=AuctionDayView)
def auction_day(trade_date: str, source: str = "v2", with_assets: bool = False,
                assets=Depends(assets_repository)) -> AuctionDayView:
    day = _normalize_date_or_400(trade_date)
    src = params.parse_source(source)
    rows = assets.list_auction_snapshots(day, src)
    assets_count = len(assets.list_auction_assets(day)) if with_assets else None
    return AuctionDayView(
        trade_date=day,
        available=bool(rows),
        count=len(rows),
        snapshots=[_auction_row(row) for row in rows],
        summary=_auction_summary(rows),
        assets_count=assets_count,
    )
