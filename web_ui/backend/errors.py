"""错误形状：冻结端点手工单键，新端点 ``{error, detail}``。

本模块**不**注册全局 ``ValueError → 400``：冻结 GET 端点的 ``ValueError``
在本项目里一直是 500（``{"error": "missing run_id"}``），新端点自行显式转
400（``ApiError`` 或 pydantic 校验）。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """新端点的显式错误：``{"error": code, "detail": detail}``。"""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def _legacy_body_error(exc: RequestValidationError) -> str | None:
    """兼容 POST /api/account/runs 的旧 body 解析文案（单键 ``{"error"}``）。

    旧 stdlib 实现区分「非法 JSON」与「JSON 不是对象」；FastAPI 的
    ``RequestValidationError`` 把两者都变成 422/400 新形状。这里只在该端点
    恢复旧文案，其它端点保持新形状。
    """
    for error in exc.errors():
        location = error.get("loc") or ()
        if "body" not in location:
            continue
        kind = error.get("type")
        if kind == "json_invalid":
            return "invalid json body"
        if kind in {"dict_type", "model_attributes_type"}:
            return "json body must be an object"
        context = error.get("ctx") or {}
        message = str(context.get("error") or "")
        if "json" in message.lower():
            return "invalid json body"
    return None


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "detail": exc.detail},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request,
                                exc: RequestValidationError) -> JSONResponse:
        path = (request.url.path or "").rstrip("/")
        if request.method == "POST" and path == "/api/account/runs":
            legacy = _legacy_body_error(exc)
            if legacy is not None:
                return JSONResponse(status_code=400, content={"error": legacy})
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request", "detail": str(exc)},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": str(exc)},
        )
