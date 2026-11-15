"""策略页端点：清单 / 当前选择 / 持久切换（Web 与 CLI 共读同一保存状态）。

约束（方案 §13.5）：

- GET 只读 descriptor，**不导入用户 Python**；
- 不从请求接受任意宿主绝对路径或 Python 源码；workspace 由服务端设置或 CLI
  解析优先级决定；
- 选择变更必须显式启用（``workspace_writes_enabled``）且同源；
- revision 冲突返回 409，非法输入返回 400。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from ..deps import dashboard_settings, workspace_module, workspace_writes_enabled
from ..http_guards import origin_allowed

router = APIRouter(tags=["strategies"])


@router.get("/api/strategies", response_model=None)
def list_strategies(request: Request, module=Depends(workspace_module),
                    settings=Depends(dashboard_settings)):
    try:
        return module.list_strategies(settings.workspace_path)
    except Exception as exc:  # noqa: BLE001 - 只读端点基线为单键 500
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.get("/api/strategies/current", response_model=None)
def current_strategy(request: Request, module=Depends(workspace_module),
                     settings=Depends(dashboard_settings)):
    try:
        return module.current_selection(settings.workspace_path)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.post("/api/strategies/selection", response_model=None)
def select_strategy(payload: dict | None = Body(default=None),
                    request: Request = None,
                    module=Depends(workspace_module),
                    settings=Depends(dashboard_settings)):
    origin = request.headers.get("origin") if request is not None else None
    if origin and not origin_allowed(origin):
        return JSONResponse(status_code=400,
                            content={"error": "跨站来源不允许变更策略选择"})
    if not workspace_writes_enabled(settings):
        return JSONResponse(status_code=403,
                            content={"error": "本机未启用 Web 策略变更"})
    payload = payload or {}
    strategy_id = payload.get("strategy_id")
    if not isinstance(strategy_id, str) or not strategy_id.strip():
        return JSONResponse(status_code=400, content={"error": "strategy_id 必须是非空字符串"})
    parameters = payload.get("parameters")
    if parameters is not None and not isinstance(parameters, dict):
        return JSONResponse(status_code=400, content={"error": "parameters 必须是对象"})
    expected_revision = payload.get("expected_revision")
    if expected_revision is not None and (
            not isinstance(expected_revision, int) or isinstance(expected_revision, bool)):
        return JSONResponse(status_code=400, content={"error": "expected_revision 必须是整数"})
    try:
        result = module.select_strategy(settings.workspace_path, strategy_id,
                                        parameters, expected_revision)
    except module.WorkspaceConflict as exc:  # type: ignore[attr-defined]
        return JSONResponse(status_code=409, content={"error": str(exc)})
    except module.WorkspaceError as exc:  # type: ignore[attr-defined]
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": str(exc)})
    return JSONResponse(status_code=200, content=result)
