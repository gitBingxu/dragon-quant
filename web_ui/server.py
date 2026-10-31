"""Deprecated 兼容壳：``web_ui.server.start_server``。

新实现位于 :mod:`web_ui.backend`；本模块只保留旧入口的名字与参数顺序，
供 ``python -m web_ui.server``、第三方嵌入和既有调用方过渡使用。
"""

from __future__ import annotations

from .backend.settings import DashboardSettings

DEFAULT_UI_PORT = 8765


def start_server(port: int = DEFAULT_UI_PORT, open_browser: bool = True,
                 default_source: str = "v2", default_page: str = "review",
                 *, block: bool = True):
    """启动仪表盘（deprecated）；内部委托 ``start_dashboard``。"""
    from .backend.serve import start_dashboard

    settings = DashboardSettings(
        default_source=default_source if default_source in {"v1", "v2"} else "v2",
        default_page="account" if default_page == "account" else "review",
    )
    return start_dashboard(
        port=port,
        open_browser=open_browser,
        settings=settings,
        block=block,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Review Web UI（deprecated，请用 dragon-quant dashboard）")
    parser.add_argument("--port", type=int, default=DEFAULT_UI_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--source", default="v2", choices=["v1", "v2"])
    args = parser.parse_args(argv)
    start_server(
        port=args.port,
        open_browser=not args.no_browser,
        default_source=args.source,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
