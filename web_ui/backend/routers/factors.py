"""因子端点：注册表、当日值（PIT）与 build 审计。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import params
from ..deps import factor_service, factor_storage
from ..errors import ApiError

router = APIRouter(tags=["factors"])


class FactorSpecView(BaseModel):
    name: str
    label: str | None = None
    direction: str | None = None
    unit: str | None = None
    frequency: str | None = None
    source: str | None = None
    available_phase: str | None = None
    pit_rule: str | None = None
    lookback: int | None = None
    enabled: bool = False
    depends_on: list[str] = []
    valid_from: str | None = None
    deprecated_at: str | None = None
    description: str | None = None
    code_version: str | None = None
    code_version_source: str | None = None
    db_version: str | None = None
    db_registered: bool = False
    params: dict | None = None
    spec_hash: str | None = None


class FactorValueRow(BaseModel):
    code: str
    factor_name: str
    value: float
    available_at: str = ""
    source_scan_id: str | None = None


class FactorCoverage(BaseModel):
    coverage: float | None = None
    rows: int | None = None
    status: str | None = None
    warnings: list[str] = []


class FactorDayView(BaseModel):
    trade_date: str
    resolved_versions: dict[str, str]
    missing_versions: list[str]
    rows: list[FactorValueRow]
    coverage: dict[str, FactorCoverage] | None = None
    coverage_source: Literal["build_run", "build_run_missing"]
    build_id: int | None = None


class FactorRunFactor(BaseModel):
    name: str
    status: str | None = None
    rows: int | None = None
    coverage: float | None = None
    reason: str | None = None


class FactorRun(BaseModel):
    id: int
    trade_date: str
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str = ""
    ok: bool | None = None
    scan: dict | None = None
    warnings: list[str] = []
    factors: list[FactorRunFactor] | None = None
    report_error: str | None = None
    report: dict | None = None


def _registry_map(registry: list[dict]) -> dict[str, dict]:
    """``factor_registry`` 已按 created_at desc 排序，每因子取首行。"""
    mapping: dict[str, dict] = {}
    for row in registry:
        name = row.get("factor_name")
        if name and name not in mapping:
            mapping[name] = row
    return mapping


@router.get("/api/factors")
def list_factors(source: str | None = None, enabled_only: bool = False,
                 service=Depends(factor_service),
                 storage=Depends(factor_storage)) -> dict:
    specs = service.list_factors()
    registry = _registry_map(storage.list_registry())
    data: list[FactorSpecView] = []
    for spec in specs:
        if source and spec.get("source") != source:
            continue
        if enabled_only and not spec.get("default_enabled"):
            continue
        db_row = registry.get(spec["name"]) or {}
        data.append(FactorSpecView(
            name=spec["name"],
            label=spec.get("label"),
            direction=spec.get("direction"),
            unit=spec.get("unit"),
            frequency=spec.get("frequency"),
            source=spec.get("source"),
            available_phase=spec.get("available_phase"),
            pit_rule=spec.get("pit_rule"),
            lookback=spec.get("lookback"),
            enabled=bool(spec.get("default_enabled")),
            depends_on=list(spec.get("depends_on") or []),
            valid_from=spec.get("valid_from"),
            deprecated_at=spec.get("deprecated_at"),
            description=spec.get("description"),
            code_version=spec.get("version"),
            code_version_source=spec.get("version_source"),
            db_version=db_row.get("factor_version"),
            db_registered=bool(db_row),
            params=db_row.get("params"),
            spec_hash=db_row.get("spec_hash"),
        ))
    return {"data": [item.model_dump() for item in data], "count": len(data)}


def _coverage_entry(entry: dict) -> FactorCoverage:
    warnings = entry.get("warnings")
    return FactorCoverage(
        coverage=entry.get("coverage"),
        rows=entry.get("rows"),
        status=entry.get("status"),
        warnings=list(warnings or []),
    )


@router.get("/api/factors/values", response_model=FactorDayView)
def factor_values(trade_date: str | None = None, name: str | None = None,
                  code: str | None = None, version: str | None = None,
                  service=Depends(factor_service),
                  storage=Depends(factor_storage)) -> FactorDayView:
    if not trade_date:
        raise ApiError(400, "invalid_request", "trade_date 必填")
    try:
        day = params.normalize_date(trade_date)
        payload = service.query(date=day, name=name, code=code, version=version)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc

    values = payload.get("values") or {}
    available_at = payload.get("available_at") or {}
    source_scan_id = payload.get("source_scan_id") or {}
    rows: list[FactorValueRow] = []
    for stock_code, factors in values.items():
        for factor_name, value in factors.items():
            if value is None:
                continue
            rows.append(FactorValueRow(
                code=stock_code,
                factor_name=factor_name,
                value=float(value),
                available_at=str(
                    (available_at.get(stock_code) or {}).get(factor_name) or ""
                ),
                source_scan_id=(source_scan_id.get(stock_code) or {}).get(factor_name),
            ))

    runs = storage.list_build_runs(trade_date=day, limit=1)
    coverage: dict[str, FactorCoverage] | None = None
    coverage_source: Literal["build_run", "build_run_missing"] = "build_run_missing"
    build_id: int | None = None
    if runs:
        run = runs[0]
        report = run.get("report") if isinstance(run.get("report"), dict) else {}
        factors = report.get("factors") if isinstance(report, dict) else None
        if isinstance(factors, dict) and factors:
            coverage = {}
            for factor_name, entry in factors.items():
                if name and factor_name != name:
                    continue
                if isinstance(entry, dict):
                    coverage[factor_name] = _coverage_entry(entry)
            coverage_source = "build_run"
            build_id = run.get("id")
        else:
            build_id = run.get("id")

    return FactorDayView(
        trade_date=day,
        resolved_versions=dict(payload.get("resolved_versions") or {}),
        missing_versions=list(payload.get("missing_versions") or []),
        rows=rows,
        coverage=coverage,
        coverage_source=coverage_source,
        build_id=build_id,
    )


@router.get("/api/factors/runs", response_model=None)
def factor_runs(trade_date: str | None = None, limit: int = 20,
                include_report: bool = False,
                storage=Depends(factor_storage)) -> dict:
    limits = params.clamp_limit(limit, default=20, maximum=200)
    if trade_date:
        try:
            day = params.normalize_date(trade_date)
        except ValueError as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
    else:
        day = None
    runs = storage.list_build_runs(trade_date=day, limit=limits)
    data: list[dict] = []
    for run in runs:
        report = run.get("report") if isinstance(run.get("report"), dict) else {}
        raw_factors = report.get("factors") if isinstance(report, dict) else None
        factors = None
        if isinstance(raw_factors, dict):
            factors = [
                FactorRunFactor(
                    name=name,
                    status=(entry or {}).get("status"),
                    rows=(entry or {}).get("rows"),
                    coverage=(entry or {}).get("coverage"),
                    reason=(entry or {}).get("reason"),
                ).model_dump()
                for name, entry in raw_factors.items()
            ]
        item = FactorRun(
            id=int(run.get("id") or 0),
            trade_date=run.get("trade_date") or "",
            started_at=run.get("started_at"),
            finished_at=run.get("finished_at"),
            created_at=run.get("created_at") or "",
            ok=report.get("ok") if isinstance(report, dict) else None,
            scan=report.get("scan") if isinstance(report, dict) else None,
            warnings=list(report.get("warnings") or [])
            if isinstance(report, dict) else [],
            factors=factors,
            report_error=run.get("report_error"),
            report=report if include_report else None,
        )
        data.append(item.model_dump())
    return {"data": data, "count": len(data)}
