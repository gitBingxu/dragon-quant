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
from ..presenters import bs_research_view
from ..presenters import scan_quality as quality_view

router = APIRouter(tags=["research"])

class ExperimentRequest(BaseModel):
    run_id: int
    sell_rules: list[str] = []
    confirm: bool = False


class BsAdviceRequest(BaseModel):
    account: dict
    policy: dict | None = None
    cutoff: str


class BsRunRequest(BaseModel):
    account: dict
    policy: dict
    date_from: str
    date_to: str
    cohort_mode: str = "rolling"
    persist: bool = True


class BsExperimentRequest(BaseModel):
    account: dict
    policy: dict
    date_from: str
    date_to: str
    cohort_mode: str = "rolling"
    confirm: bool = False
    new_attempt: bool = False
    study: dict | None = None
    enhanced_experiment_id: int | None = None


def _iso(raw: str | None, field: str) -> str | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return params.normalize_date(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", f"{field}: {exc}") from exc


def _bs_http_status(exc) -> int:
    code = str(getattr(exc, "code", ""))
    if code in ("local_assets_missing", "local_intraday_events_missing",
                "insufficient_data", "no_universe"):
        return 422
    if code in ("experiment_busy",):
        return 409
    if code in ("ledger_missing", "experiment_not_found"):
        return 404
    if code in ("account_file_invalid", "policy_config_invalid",
                "policy_config_missing", "account_cash_unknown",
                "invalid_request", "cohort_invalid"):
        return 400
    return 422


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


@router.post("/api/research/bs-advice", response_model=None)
def bs_advice(payload: BsAdviceRequest):
    """只读 BS 建议：结构化账户/策略 JSON，服务端不读任意路径。"""
    from dragon_quant.research import bs_advice as service
    from dragon_quant.research.envelope import InvalidRequest

    try:
        result = service.build_advice(
            account_payload=payload.account, policy_payload=payload.policy,
            cutoff=payload.cutoff)
    except (service.BsAdviceError, service.PolicyFileError,
            service.AccountFileError) as exc:
        raise ApiError(400, getattr(exc, "code", "bs_advice_error"),
                       str(exc)) from exc
    except InvalidRequest as exc:
        raise ApiError(400, exc.code, exc.message) from exc
    return {"data": result, "view": bs_research_view.present_advice(result)}


@router.post("/api/research/bs-review", response_model=None)
def bs_review(payload: BsRunRequest, request: Request = None):
    """显式模拟回放（写入 AccountLedger）；结构化 payload，不接受路径/脚本。"""
    from dragon_quant.infra.storage import paths
    from dragon_quant.research import bs_advice as service
    from dragon_quant.research.experiments import process_lock

    origin = request.headers.get("origin") if request is not None else None
    if origin and not origin_allowed(origin):
        return JSONResponse(status_code=400,
                            content={"error": "跨站来源不允许运行 BS 回放"})
    try:
        date_from = params.normalize_date(payload.date_from)
        date_to = params.normalize_date(payload.date_to)
        if date_from > date_to:
            raise ApiError(400, "invalid_request", "date_from 不能晚于 date_to")
        if payload.cohort_mode not in ("rolling", "fixed"):
            raise ApiError(400, "invalid_request", "cohort_mode 非法")
        policy = service.load_policy_payload(payload.policy)
        account = service.parse_account_file(
            payload.account, cutoff=None, policy_hash=policy.content_hash)
        with process_lock(paths.DATA_DIR / "locks" / "bs_review.lock"):
            result = service.run_bs_review(
                account=account, policy=policy, date_from=date_from,
                date_to=date_to, cohort_mode=payload.cohort_mode,
                persist=payload.persist)
    except ApiError:
        raise
    except (service.BsReviewError, service.BsRunError,
            service.PolicyFileError, service.AccountFileError) as exc:
        raise ApiError(_bs_http_status(exc),
                       getattr(exc, "code", "bs_review_error"), str(exc)) from exc
    except ValueError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc
    except Exception as exc:
        from dragon_quant.research.experiments import ExperimentBusy

        if isinstance(exc, ExperimentBusy):
            raise ApiError(409, "experiment_busy",
                           "已有 BS 回放在运行，请稍后再试") from exc
        raise
    return JSONResponse(
        status_code=201,
        content={"data": result,
                 "view": bs_research_view.present_review(result)})


@router.get("/api/research/bs-ledger", response_model=None)
def bs_ledger(ledger_id: str, limit: int = 100, cursor: str | None = None):
    from dragon_quant.research import bs_advice as service

    if limit < 1 or limit > 500:
        raise ApiError(400, "invalid_request", "limit 必须在 1..500")
    try:
        data = service.read_run(ledger_id, limit=limit, cursor=cursor)
    except service.BsRunError as exc:
        raise ApiError(400, getattr(exc, "code", "bs_run_error"), str(exc)) from exc
    if data is None:
        raise ApiError(404, "ledger_missing", "未找到 BS 账本")
    return {"data": data,
            "view": bs_research_view.present_ledger(data)}


@router.get("/api/research/bs-experiments", response_model=None)
def list_bs_experiments(limit: int = 20, cursor: int | None = None):
    from dragon_quant.research import bs_advice as service

    if limit < 1 or limit > 200:
        raise ApiError(400, "invalid_request", "limit 必须在 1..200")
    if cursor is not None and cursor < 0:
        raise ApiError(400, "invalid_request", "cursor 必须是非负整数")
    page = service.list_bs_experiments(limit=limit, cursor=cursor)
    return {"data": page["items"],
            "view": bs_research_view.present_experiment_list(
                page, page["items"]),
            "meta": {"total": page["total"], "truncated": page["has_more"],
                     "next_cursor": page["next_cursor"]}}


@router.get("/api/research/bs-experiments/{experiment_id}", response_model=None)
def show_bs_experiment(experiment_id: int, include_frozen: bool = False,
                       include_details: bool = False):
    from dragon_quant.research import bs_advice as service

    row = service.show_bs_experiment(
        experiment_id, include_frozen=bool(include_frozen),
        include_details=bool(include_details))
    if row is None:
        raise ApiError(404, "experiment_not_found", "未找到 BS 实验")
    return {"data": row,
            "view": bs_research_view.present_experiment(row)}


@router.post("/api/research/bs-experiments/{experiment_id}/replay",
             response_model=None)
def replay_bs_experiment(experiment_id: int):
    from dragon_quant.research import bs_advice as service

    try:
        result = service.replay_bs_experiment(int(experiment_id))
    except service.BsExperimentError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from exc
    return {"data": result,
            "view": bs_research_view.present_experiment(result)}


@router.post("/api/research/bs-experiments/{experiment_id}/resume",
             response_model=None)
def resume_bs_experiment(experiment_id: int, request: Request = None):
    from dragon_quant.infra.storage import paths
    from dragon_quant.research import bs_advice as service
    from dragon_quant.research.bs_advice import service as flow
    from dragon_quant.research.experiments import ExperimentBusy

    try:
        data = flow.resume_experiment_flow(
            experiment_id=int(experiment_id),
            lock_path=paths.DATA_DIR / "locks" / "bs_experiment.lock")
    except ExperimentBusy as exc:
        raise ApiError(409, "experiment_busy",
                       "已有对照实验在运行，请稍后再试") from exc
    except service.BsExperimentError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from exc
    return {"data": data,
            "view": bs_research_view.present_experiment(data)}


@router.post("/api/research/bs-experiments", response_model=None)
def create_bs_experiment(payload: BsExperimentRequest, request: Request = None):
    """显式冻结对照（confirm=true）；共享 service 负责锁/冻结/生命周期。"""
    from dragon_quant.infra.storage import paths
    from dragon_quant.research import bs_advice as service
    from dragon_quant.research.bs_advice import service as flow
    from dragon_quant.research.experiments import (
        ConfirmationRequired, ExperimentBusy,
    )

    origin = request.headers.get("origin") if request is not None else None
    if origin and not origin_allowed(origin):
        return JSONResponse(status_code=400,
                            content={"error": "跨站来源不允许运行 BS 实验"})
    if not payload.confirm:
        raise ApiError(400, "confirmation_required",
                       "对照实验会消耗本地算力，需显式 confirm=true")
    try:
        date_from = params.normalize_date(payload.date_from)
        date_to = params.normalize_date(payload.date_to)
        if date_from > date_to:
            raise ApiError(400, "invalid_request", "date_from 不能晚于 date_to")
        if payload.cohort_mode not in ("rolling", "fixed"):
            raise ApiError(400, "invalid_request", "cohort_mode 非法")
        policy = service.load_policy_payload(payload.policy)
        account = service.parse_account_file(
            payload.account, cutoff=None, policy_hash=policy.content_hash)
        enhanced = (flow.enhanced_freeze_for_experiment(
                        experiment_id=int(payload.enhanced_experiment_id))
                    if payload.enhanced_experiment_id is not None else None)
        data = flow.run_experiment_flow(
            account=account, policy=policy, date_from=date_from,
            date_to=date_to, confirm=True,
            lock_path=paths.DATA_DIR / "locks" / "bs_experiment.lock",
            cohort_mode=payload.cohort_mode,
            new_attempt=bool(payload.new_attempt),
            study=payload.study, enhanced=enhanced)
    except ApiError:
        raise
    except ConfirmationRequired as exc:
        raise ApiError(400, "confirmation_required", str(exc)) from exc
    except ExperimentBusy as exc:
        raise ApiError(409, "experiment_busy",
                       "已有对照实验在运行，请稍后再试") from exc
    except service.BsExperimentError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from exc
    except (service.PolicyFileError, service.AccountFileError) as exc:
        raise ApiError(400, getattr(exc, "code", "invalid_request"),
                       str(exc)) from exc
    except ValueError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc
    return JSONResponse(
        status_code=201,
        content={"data": data,
                 "view": bs_research_view.present_experiment(data)})


__all__: list[Any] = ["router"]
