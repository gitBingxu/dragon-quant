"""``create_app(settings)``：FastAPI 应用工厂（fastapi 延迟导入）。"""

from __future__ import annotations

from .settings import DashboardSettings


def create_app(settings: DashboardSettings | None = None):
    """装配 FastAPI 应用：中间件 → 路由 → /api 404 兜底 → SPA 前端。"""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    from .errors import install_error_handlers
    from .routers import ROUTERS
    from .static import CacheControlMiddleware, mount_frontend

    settings = settings or DashboardSettings()

    app = FastAPI(
        title="dragon-quant dashboard",
        description="龙头战法量化筛选系统本地只读仪表盘",
    )
    app.state.settings = settings

    # 纯 ASGI 中间件：在响应流上叠加 Cache-Control（不改路由实现）。
    app.add_middleware(CacheControlMiddleware)
    # 抵御 DNS rebinding：只接受本机 Host。
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]"],
    )

    install_error_handlers(app)

    for router in ROUTERS:
        app.include_router(router)

    # 必须在 app.frontend 之前注册：未知 /api 路径返回 404 JSON，
    # 绝不被 SPA fallback 吃成 index.html。
    @app.api_route(
        "/api/{rest:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        include_in_schema=False,
    )
    def api_not_found(rest: str):
        return JSONResponse(status_code=404, content={"error": "not found"})

    mount_frontend(app, settings.dist_dir)
    return app
