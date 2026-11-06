"""``create_app(settings)``：FastAPI 应用工厂（fastapi 延迟导入）。"""

from __future__ import annotations

from .settings import DashboardSettings


def create_app(settings: DashboardSettings | None = None):
    """装配 FastAPI 应用：中间件 → 路由 → /api 404 兜底 → SPA 前端。"""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    from .bs_runner import BsRunner
    from .errors import install_error_handlers
    from .review_runner import ReviewJobRunner
    from .routers import ROUTERS
    from .run_runner import RunJobRunner
    from .static import CacheControlMiddleware, mount_frontend
    from .trade_calendar import TradeCalendarProbe

    settings = settings or DashboardSettings()

    app = FastAPI(
        title="dragon-quant dashboard",
        description="本地面板（查询为主；BS / 回测增量经子进程生成）",
    )
    app.state.settings = settings
    # 只构造（无 I/O、不 spawn）；启动由 ``serve._serve`` 按开关触发。
    app.state.bs_runner = BsRunner()
    # 龙头回测增量作业：只有显式 POST 才启动，查看页面绝不触发。
    app.state.review_runner = ReviewJobRunner()
    # scan / prep / auction 共享单飞作业：同样只在显式 POST 时启动。
    app.state.run_runner = RunJobRunner()
    # 交易日历探测：构造无 I/O，首次判定才联网并按自然日缓存。
    app.state.trade_calendar = TradeCalendarProbe()

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
