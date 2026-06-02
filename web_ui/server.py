"""
Web UI 服务器 — 龙头回测数据可视化

基于 Python 3 stdlib http.server，零外部依赖。
启动后浏览器打开可筛选、排序、查询 dragons 表回测数据。

用法：
    python -m web_ui.server [--port 8765]
    或通过 CLI: dragon-quant review --ui
"""

import json
import os
import sys
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

# 项目根目录（web_ui 的父级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_HTML_PATH = Path(__file__).resolve().parent / "index.html"


def _get_db():
    """延迟导入 db 模块，避免启动时初始化数据库"""
    sys.path.insert(0, str(_PROJECT_ROOT))
    from dragon_quant.storage import db
    db.init_db()
    return db


def _load_html() -> str:
    """加载 HTML 模板"""
    if _HTML_PATH.exists():
        return _HTML_PATH.read_text(encoding="utf-8")
    # 兜底：内联最小 HTML
    return "<html><body><h1>Web UI — index.html 未找到</h1></body></html>"


class ReviewHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器 — 路由 / 和 /api/*"""

    # ---------- 路由分发 ----------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        try:
            if path == "/":
                self._serve_html()
            elif path == "/api/dragons":
                self._serve_api_dragons(parse_qs(parsed.query))
            elif path == "/api/summary":
                self._serve_api_summary()
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ---------- 响应工具 ----------

    def _serve_html(self):
        html = _load_html()
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
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
        rows = db.query_dragons(filters, sort_by=sort_by, sort_dir=sort_dir)
        self._send_json({"data": rows, "count": len(rows)})

    def _serve_api_summary(self):
        """GET /api/summary — 汇总统计"""
        db = _get_db()
        summary = db.get_review_summary()
        self._send_json(summary)

    # ---------- 日志静默 ----------

    def log_message(self, format, *args):
        """抑制默认 stderr 日志，仅输出到终端"""
        pass


def _first(params: dict, key: str) -> Optional[str]:
    """取 query 参数的第一个值"""
    vals = params.get(key, [])
    return vals[0] if vals else None


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

    return f


def start_server(port: int = 8765, open_browser: bool = True):
    """启动 HTTP 服务器。

    Args:
        port: 监听端口
        open_browser: 是否自动打开浏览器
    """
    server = HTTPServer(("127.0.0.1", port), ReviewHandler)
    url = f"http://localhost:{port}"

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
    args = p.parse_args()
    start_server(args.port, open_browser=not args.no_browser)
