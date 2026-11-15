"""研究入口：scan 候选质量（只读）与账户对照实验（用户显式触发）。

边界：
  - scan-quality 只读 ``scans`` / ``scan_stocks`` 与本地 ``stock_daily``，
    不改 dragons 去重与生产候选池；
  - experiments 只在用户点击并 ``confirm=true`` 后运行，冻结指定 run 的
    期间/资金/策略/上游母集；行情只用本地快照，失败返回证据不足；
  - GET 只看已保存报告，任何查看都不隐式重算。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import params
from ..deps import (
    account_repository,
    assets_repository,
    bar_calendar_loader,
    candidate_loader_factory,
    clock_module,
    dashboard_settings,
    factor_storage,
    local_bars_loader,
    prep_snapshot_reader,
    scan_repository,
)
from ..errors import ApiError
from ..http_guards import origin_allowed
from ..locks import ACCOUNT_HEAVY_LOCK
from ..presenters import scan_quality as quality_view

router = APIRouter(tags=["research"])

class ExperimentRequest(BaseModel):
    run_id: int
    sell_rules: list[str] = []
    confirm: bool = False


def _iso(raw: str | None, field: str) -> str | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return params.normalize_date(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", f"{field}: {exc}") from exc


def _default_range(clock) -> tuple[str, str]:
    today = clock.now().strftime("%Y-%m-%d")
    start = (datetime.strptime(today, "%Y-%m-%d")
             - timedelta(days=60)).strftime("%Y-%m-%d")
    return start, today


@router.get("/api/research/scan-quality", response_model=None)
def scan_quality(date_from: str | None = None, date_to: str | None = None,
                 scans=Depends(scan_repository),
                 load_bars=Depends(local_bars_loader),
                 load_calendar=Depends(bar_calendar_loader),
                 clock=Depends(clock_module)):
    default_from, default_to = _default_range(clock)
    date_from = _iso(date_from, "date_from") or default_from
    date_to = _iso(date_to, "date_to") or default_to
    if date_from > date_to:
        raise ApiError(400, "invalid_request", "date_from 不能晚于 date_to")

    return quality_view.run_scan_quality(
        date_from=date_from,
        date_to=date_to,
        list_dates=lambda start, end: scans.list_scan_dates(start, end),
        contributions=lambda day: scans.list_scan_stock_contributions_by_date(day),
        load_bars=load_bars,
        load_calendar=load_calendar,
    )


@router.post("/api/account/experiments", response_model=None)
def create_account_experiments(payload: dict | None = Body(default=None),
                               request: Request = None,
                               settings=Depends(dashboard_settings),
                               account=Depends(account_repository),
                               assets=Depends(assets_repository),
                               factor_store=Depends(factor_storage),
                               load_bars=Depends(local_bars_loader),
                               load_calendar=Depends(bar_calendar_loader),
                               loader_factory=Depends(candidate_loader_factory),
                               snapshot_reader=Depends(prep_snapshot_reader)):
    from dragon_quant.infra.storage import paths
    from dragon_quant.research.envelope import InvalidRequest
    from dragon_quant.research.experiments import (
        ExperimentBusy,
        ExperimentInputError,
        run_account_experiment,
    )

    body = ExperimentRequest(**(payload or {}))
    origin = request.headers.get("origin") if request is not None else None
    if origin and not origin_allowed(origin):
        return JSONResponse(status_code=400,
                            content={"error": "跨站来源不允许运行对照实验"})
    run = account.get_review_account_run(body.run_id)
    ref = (run or {}).get("run_meta", {}).get("strategy_ref") if run else None
    if isinstance(ref, dict) and str(ref.get("strategy_id", "")).startswith("user:"):
        if not getattr(settings, "workspace_writes_enabled", False):
            return JSONResponse(
                status_code=403,
                content={"error": "本机未启用 Web 用户策略执行（dashboard --workspace-writes）"})
    try:
        report = run_account_experiment(
            run_id=body.run_id,
            sell_rules=body.sell_rules,
            confirm=body.confirm,
            account=account,
            factor_store=factor_store,
            load_bars=load_bars,
            load_calendar=load_calendar,
            loader_factory=loader_factory,
            snapshot_reader=snapshot_reader,
            assets=assets,
            save_experiment=lambda *args, **kwargs: account.save_review_account_experiment(
                *args, **kwargs),
            heavy_lock=ACCOUNT_HEAVY_LOCK,
            lock_path=paths.DATA_DIR / "locks" / "account_experiment.lock",
            use_worker=getattr(settings, "workspace_writes_enabled", False),
        )
    except InvalidRequest as exc:
        raise ApiError(400, exc.code, exc.message) from exc
    except ExperimentBusy:
        return JSONResponse(status_code=409, content={
            "error": "experiment_running",
            "detail": "已有对照实验在运行，请稍后再试",
        })
    except ExperimentInputError as exc:
        if exc.code == "run_not_found":
            return JSONResponse(status_code=404, content={"error": "run not found"})
        # 本地行情截断/冻结失败：明确证据不足，不返回部分统计。
        raise ApiError(exc.status, exc.code, str(exc)) from exc
    return JSONResponse(status_code=201, content={"data": report})


@router.get("/api/account/experiments", response_model=None)
def list_account_experiments(run_id: str | None = None,
                             account=Depends(account_repository)):
    if not run_id:
        raise ApiError(400, "invalid_request", "missing run_id")
    try:
        value = int(run_id)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", "invalid run_id") from exc
    return {"data": account.query_review_account_experiments(value)}


__all__: list[Any] = ["router"]
