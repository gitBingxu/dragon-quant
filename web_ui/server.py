"""
Web UI 服务器 — 龙头回测数据可视化

基于 Python 3 stdlib http.server，零外部依赖。
启动后浏览器打开可筛选、排序、查询 dragons 表回测数据。

用法：
    python -m web_ui.server [--port 8765]
    或通过 CLI: dragon-quant review --ui
"""

import json
import sys
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

# 项目根目录（web_ui 的父级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Vite 构建产物目录（前端源码在 web_ui/frontend，构建到此）
_DIST_DIR = (Path(__file__).resolve().parent / "dist").resolve()

# 静态资源扩展名 → Content-Type
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".map": "application/json; charset=utf-8",
}

_BUILD_HINT = (
    "<html><body style='font-family:sans-serif;padding:40px'>"
    "<h1>Web UI 尚未构建</h1>"
    "<p>请先构建前端产物：</p>"
    "<pre>cd web_ui/frontend\nnpm install\nnpm run build</pre>"
    "</body></html>"
)


def _get_db():
    """延迟导入 db 模块，避免启动时初始化数据库"""
    sys.path.insert(0, str(_PROJECT_ROOT))
    from dragon_quant.storage import db
    db.init_db()
    return db


def _resolve_static(path: str) -> Optional[Path]:
    """将 URL 路径解析为 dist 内的安全文件路径。

    做目录边界校验，禁止 `..` 路径穿越；越界或不存在返回 None。
    """
    rel = path.lstrip("/")
    if not rel:
        return None
    candidate = (_DIST_DIR / rel).resolve()
    # 边界校验：必须仍在 dist 目录内
    if candidate != _DIST_DIR and _DIST_DIR not in candidate.parents:
        return None
    if candidate.is_file():
        return candidate
    return None


class ReviewHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器 — 路由 / 和 /api/*"""

    # ---------- 路由分发 ----------

    def do_GET(self):
        parsed = urlparse(self.path)
        # 保留原始 path 用于静态资源匹配；api 路由用去尾斜杠版本
        raw_path = parsed.path
        path = raw_path.rstrip("/") or "/"

        try:
            if path == "/api/dragons":
                self._serve_api_dragons(parse_qs(parsed.query))
            elif path == "/api/summary":
                self._serve_api_summary(parse_qs(parsed.query))
            elif path.startswith("/api/"):
                self._send_json({"error": "not found"}, 404)
            else:
                self._serve_static(raw_path)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ---------- 响应工具 ----------

    def _serve_static(self, raw_path: str):
        """托管 dist 静态资源；未命中文件则回退 SPA 入口 index.html。"""
        index = _DIST_DIR / "index.html"
        if not index.is_file():
            body = _BUILD_HINT.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        target = _resolve_static(raw_path)
        # 命中具体静态文件 → 直接返回；否则回退 index.html（SPA 入口）
        if target is None:
            target = index
            cache = "no-cache, no-store, must-revalidate"
        else:
            # 带哈希的 assets 可长缓存，其余不缓存
            cache = (
                "public, max-age=31536000, immutable"
                if "/assets/" in raw_path
                else "no-cache"
            )

        body = target.read_bytes()
        ctype = _CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- API ----------

    def _serve_api_dragons(self, params: dict):
        """GET /api/dragons?code=...&name=...&status=...&sort_by=...&sort_dir=...

        支持的查询参数：
        - code: 股票代码（模糊匹配）
        - name: 名称（模糊匹配）
        - date_from / date_to: 入选日期范围
        - score_min / score_max: 综合分范围
        - return_min / return_max: 最大收益率范围
        - status: review_status 过滤（pending/completed/no_entry/error，逗号分隔）
        - sort_by: 排序字段（默认 composite_score）
        - sort_dir: asc / desc（默认 desc）
        """
        db = _get_db()
        filters = _parse_filters(params)
        sort_by = _first(params, "sort_by") or "composite_score"
        sort_dir = _first(params, "sort_dir") or "desc"
        source = _parse_source(params, getattr(self.server, "default_source", "v1"))
        rows = db.query_dragons(filters, sort_by=sort_by, sort_dir=sort_dir, source=source)
        self._send_json({"data": rows, "count": len(rows)})

    def _serve_api_summary(self, params: dict):
        """GET /api/summary — 汇总统计"""
        db = _get_db()
        source = _parse_source(params, getattr(self.server, "default_source", "v1"))
        summary = db.get_review_summary(source=source)
        self._send_json(summary)

    # ---------- 日志静默 ----------

    def log_message(self, format, *args):
        """抑制默认 stderr 日志，仅输出到终端"""
        pass


def _first(params: dict, key: str) -> Optional[str]:
    """取 query 参数的第一个值"""
    vals = params.get(key, [])
    return vals[0] if vals else None


def _parse_source(params: dict, default: str = "v1") -> str:
    """解析 dragon 体系来源，非法值回退到默认值。"""
    src = (_first(params, "source") or default or "v1").lower().strip()
    return src if src in {"v1", "v2"} else "v1"


def _parse_filters(params: dict) -> dict:
    """将 URL query params 转为 query_dragons 的 filters dict"""
    f: dict = {}

    code = _first(params, "code")
    if code:
        f["code_like"] = code.strip()

    name = _first(params, "name")
    if name:
        f["name_like"] = name.strip()

    df = _first(params, "date_from")
    if df:
        f["date_from"] = df.strip()

    dt = _first(params, "date_to")
    if dt:
        f["date_to"] = dt.strip()

    smin = _first(params, "score_min")
    if smin:
        try:
            f["score_min"] = float(smin)
        except ValueError:
            pass

    smax = _first(params, "score_max")
    if smax:
        try:
            f["score_max"] = float(smax)
        except ValueError:
            pass

    rmin = _first(params, "return_min")
    if rmin:
        try:
            f["return_min"] = float(rmin)
        except ValueError:
            pass

    rmax = _first(params, "return_max")
    if rmax:
        try:
            f["return_max"] = float(rmax)
        except ValueError:
            pass

    dmin = _first(params, "drawdown_min")
    if dmin:
        try:
            f["drawdown_min"] = float(dmin)
        except ValueError:
            pass

    dmax = _first(params, "drawdown_max")
    if dmax:
        try:
            f["drawdown_max"] = float(dmax)
        except ValueError:
            pass

    status = _first(params, "status")
    if status:
        f["status"] = [s.strip() for s in status.split(",") if s.strip()]

    vmin = _first(params, "version_min")
    if vmin:
        f["version_min"] = vmin.strip()

    vmax = _first(params, "version_max")
    if vmax:
        f["version_max"] = vmax.strip()

    return f


def start_server(port: int = 8765, open_browser: bool = True, default_source: str = "v1"):
    """启动 HTTP 服务器。

    Args:
        port: 监听端口
        open_browser: 是否自动打开浏览器
    """
    default_source = default_source if default_source in {"v1", "v2"} else "v1"
    server = HTTPServer(("127.0.0.1", port), ReviewHandler)
    server.default_source = default_source
    url = f"http://localhost:{port}?source={default_source}"

    print(f"🐉 Review Web UI 已启动 → {url}")
    print("   按 Ctrl+C 停止服务")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 服务器已停止")
        server.server_close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Review Web UI")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--source", default="v1", choices=["v1", "v2"])
    args = p.parse_args()
    start_server(args.port, open_browser=not args.no_browser, default_source=args.source)
