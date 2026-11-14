"""账户回测端点：9 个账户端点（含增量 201）与单飞锁。

错误语义与旧 ``ReviewHandler`` 逐字一致：
- GET 端点的 ``ValueError`` 落到 500 单键（基线冻结）；
- POST/DELETE 的 ``ValueError`` 是 400 单键；
- POST/DELETE 的其它异常 500 单键。
"""

from __future__ import annotations

from functools import wraps

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from .. import compat
from ..locks import ACCOUNT_HEAVY_LOCK
from ..deps import (
    account_repository,
    benchmark_provider,
    dashboard_settings,
    evaluation_module,
    run_account_backtest,
    run_incremental_account_backtest,
    workspace_module,
)
from ..http_guards import origin_allowed

router = APIRouter(tags=["account"])

# 兼容既有测试/调用方的名字；底层是账户生成与实验共享的单飞锁。
_account_run_lock = ACCOUNT_HEAVY_LOCK


def frozen_500(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 基线冻结为单键 500
            return JSONResponse(status_code=500, content={"error": str(exc)})

    return wrapper


def mutation_errors(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 单键 500
            return JSONResponse(status_code=500, content={"error": str(exc)})

    return wrapper


@router.get("/api/account/runs", response_model=None)
@frozen_500
def account_runs(request: Request, account=Depends(account_repository),
                 settings=Depends(dashboard_settings)):
    return compat.account_runs(
        account, request.query_params, settings.default_source,
    )


@router.post("/api/account/runs", response_model=None)
@mutation_errors
def create_account_run(payload: dict | None = Body(default=None),
                       request: Request = None,
                       account=Depends(account_repository),
                       run_account=Depends(run_account_backtest),
                       settings=Depends(dashboard_settings),
                       workspace=Depends(workspace_module)):
    origin = request.headers.get("origin") if request is not None else None
    if origin and not origin_allowed(origin):
        raise ValueError("跨站来源不允许启动账户回测")
    payload = payload if isinstance(payload, dict) else {}
    selection = _resolve_selection(payload, settings, workspace)
    if not _account_run_lock.acquire(blocking=False):
        return JSONResponse(
            status_code=409,
            content={"error": "account run already in progress"},
        )
    try:
        result = compat.create_account_run(
            payload, default_source=settings.default_source,
            run_account=run_account, account=account, selection=selection,
        )
    finally:
        _account_run_lock.release()
    return JSONResponse(status_code=201, content=result)


def _resolve_selection(payload: dict, settings, workspace):
    """账户回测的策略选择：显式 strategy_id > 已保存选择；无 workspace 时 None。

    用户策略（会执行本地 Python）必须显式本机启用；默认 dashboard 只读。
    """
    strategy_id = payload.get("strategy_id")
    if strategy_id is not None and (not isinstance(strategy_id, str) or not strategy_id.strip()):
        raise ValueError("strategy_id 必须是非空字符串")
    parameters = payload.get("strategy_parameters")
    if parameters is not None and not isinstance(parameters, dict):
        raise ValueError("strategy_parameters 必须是对象")
    path = getattr(settings, "workspace_path", None)
    try:
        current = workspace.current_selection(path)
    except Exception:
        current = {}
    if not (strategy_id or current.get("workspace")):
        return None
    try:
        selection = workspace.resolve_selection(path, strategy_id, parameters)
    except workspace.WorkspaceError as exc:  # type: ignore[attr-defined]
        raise ValueError(str(exc)) from exc
    if selection.descriptor.source == "user" and not getattr(
            settings, "workspace_writes_enabled", False):
        raise ValueError("本机未启用 Web 用户策略执行（dashboard --workspace-writes）")
    return selection


@router.post("/api/account/runs/incremental", response_model=None)
@mutation_errors
def create_incremental_account_run(
        payload: dict | None = Body(default=None),
        account=Depends(account_repository),
        run_incremental=Depends(run_incremental_account_backtest)):
    """基于既有 run 的最后收盘状态续算；与生成回测共用单飞锁。"""
    if not _account_run_lock.acquire(blocking=False):
        return JSONResponse(
            status_code=409,
            content={"error": "account run already in progress"},
        )
    try:
        result = compat.create_incremental_account_run(
            payload or {}, account=account, run_incremental=run_incremental,
        )
    finally:
        _account_run_lock.release()
    if "error" in result:
        return JSONResponse(status_code=404, content=result)
    return JSONResponse(status_code=201, content=result)


@router.delete("/api/account/runs", response_model=None)
@mutation_errors
def delete_account_run(request: Request, account=Depends(account_repository)):
    result = compat.delete_account_run(account, request.query_params)
    if "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


@router.get("/api/account/snapshots", response_model=None)
@frozen_500
def account_snapshots(request: Request, account=Depends(account_repository)):
    return compat.account_snapshots(account, request.query_params)


@router.get("/api/account/benchmark", response_model=None)
@frozen_500
def account_benchmark(request: Request, account=Depends(account_repository),
                      provider=Depends(benchmark_provider)):
    result = compat.account_benchmark(account, provider, request.query_params)
    if "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


@router.get("/api/account/trades", response_model=None)
@frozen_500
def account_trades(request: Request, account=Depends(account_repository)):
    return compat.account_trades(account, request.query_params)


@router.get("/api/account/positions", response_model=None)
@frozen_500
def account_positions(request: Request, account=Depends(account_repository),
                      evaluation=Depends(evaluation_module)):
    return compat.account_positions(account, evaluation, request.query_params)


@router.get("/api/account/summary", response_model=None)
@frozen_500
def account_summary(request: Request, account=Depends(account_repository),
                    evaluation=Depends(evaluation_module)):
    """账户收益/风险与运行质量摘要（只读，不重算）。"""
    result = compat.account_summary(account, evaluation, request.query_params)
    if "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


@router.get("/api/account/events", response_model=None)
@frozen_500
def account_events(request: Request, account=Depends(account_repository)):
    return compat.account_events(account, request.query_params)
