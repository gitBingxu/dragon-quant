"""账户回测端点：7 个兼容端点（含 POST/DELETE）+ 单飞锁。

错误语义与旧 ``ReviewHandler`` 逐字一致：
- GET 端点的 ``ValueError`` 落到 500 单键（基线冻结）；
- POST/DELETE 的 ``ValueError`` 是 400 单键；
- POST/DELETE 的其它异常 500 单键。
"""

from __future__ import annotations

import threading
from functools import wraps

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from .. import compat
from ..deps import (
    account_repository,
    benchmark_provider,
    dashboard_settings,
    evaluation_module,
    run_account_backtest,
)

router = APIRouter(tags=["account"])

_account_run_lock = threading.Lock()


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
                       account=Depends(account_repository),
                       run_account=Depends(run_account_backtest),
                       settings=Depends(dashboard_settings)):
    if not _account_run_lock.acquire(blocking=False):
        return JSONResponse(
            status_code=409,
            content={"error": "account run already in progress"},
        )
    try:
        result = compat.create_account_run(
            payload or {}, default_source=settings.default_source,
            run_account=run_account, account=account,
        )
    finally:
        _account_run_lock.release()
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


@router.get("/api/account/events", response_model=None)
@frozen_500
def account_events(request: Request, account=Depends(account_repository)):
    return compat.account_events(account, request.query_params)
