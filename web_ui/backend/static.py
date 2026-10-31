"""静态托管 / SPA 回退 / Cache-Control（纯 ASGI 中间件）。"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Iterable

BUILD_HINT = (
    "<html><body style='font-family:sans-serif;padding:40px'>"
    "<h1>Web UI 尚未构建</h1>"
    "<p>请先构建前端产物：</p>"
    "<pre>cd web_ui/frontend\nnpm install\nnpm run build</pre>"
    "</body></html>"
)

_IMMUTABLE = "public, max-age=31536000, immutable"
_NO_STORE = "no-cache, no-store, must-revalidate"
_NO_CACHE = "no-cache"


def _header(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    for key, value in headers:
        if key.lower() == name:
            return value
    return None


class CacheControlMiddleware:
    """等价旧 ``server.py`` 的缓存策略：

    - ``/assets/*`` 且 200 → 一年 immutable；
    - ``text/html``（index / fallback）→ no-store；
    - 其余（含 ``/api/*`` JSON）→ no-cache。
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                status = message.get("status", 200)
                content_type = (_header(headers, b"content-type") or b"").decode(
                    "latin-1"
                )
                if path.startswith("/assets/") and status == 200:
                    cache = _IMMUTABLE
                elif content_type.startswith("text/html"):
                    cache = _NO_STORE
                else:
                    cache = _NO_CACHE
                headers = [
                    (key, value)
                    for key, value in headers
                    if key.lower() != b"cache-control"
                ]
                headers.append((b"cache-control", cache.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _guess_media_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def mount_frontend(app, dist_dir: Path) -> None:
    """dist 存在则用 ``app.frontend``；缺失则返回 build hint（200）。"""
    index = Path(dist_dir) / "index.html"
    if not index.is_file():
        from fastapi.responses import HTMLResponse

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        @app.get("/{path:path}", response_class=HTMLResponse, include_in_schema=False)
        def _build_hint(path: str = "") -> HTMLResponse:
            return HTMLResponse(BUILD_HINT, status_code=200)

        return

    app.frontend("/", directory=str(dist_dir), fallback="index.html")
