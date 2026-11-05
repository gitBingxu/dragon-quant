"""review 命令：龙头回测验证（双口径），或启动回测结果 Web UI。"""

from __future__ import annotations

import sys

from dragon_quant.commands.dashboard import ensure_web_extra, is_addr_in_use

DEFAULT_UI_PORT = 8765
# 兼容旧测试：``review._is_addr_in_use`` 仍是同一判定函数的别名。
_is_addr_in_use = is_addr_in_use

_DEPRECATION = "review: --ui/--ui-only 已弃用，请改用 dragon-quant dashboard（后续版本将移除）"


def handle(args, services):
    """龙头回测命令。"""
    from dragon_quant.review.main import run_review

    date_str = None
    if args.date:
        d = args.date
        if len(d) == 8:
            date_str = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        else:
            date_str = d

    # --ui-only: 只启动 UI
    if args.ui_only:
        _start_ui(args)
        return

    # 正常回测
    try:
        run_review(
            trade_date=date_str,
            top_n=args.top,
            force=args.force,
            verbose=True,
            source=args.source,
            services=services,
            recompute_all=getattr(args, "recompute_all", False),
            dry_run=getattr(args, "dry_run", False),
            limit=getattr(args, "limit", None),
        )
    except ValueError as exc:
        print(f"review: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    # --ui: 回测后启动 UI
    if args.ui:
        _start_ui(args)


def _start_ui(args):
    """启动 Web UI（deprecated 别名，转发 dashboard 语义）。

    未显式给 ``--port`` 时默认 :data:`DEFAULT_UI_PORT`；该端口被占用时警告
    一次并仅重试一次 ``port=0``（由 OS 分配）。显式端口被占用时报错退出，
    不静默换端口。非端口占用错误（如 ``EACCES``）原样抛出。
    """
    ensure_web_extra(auto=False)
    print(_DEPRECATION, file=sys.stderr)

    from web_ui.server import start_server

    requested = getattr(args, "port", None)
    explicit = requested is not None
    port = requested if explicit else DEFAULT_UI_PORT
    options = {
        "open_browser": not args.no_browser,
        "default_source": getattr(args, "source", "v2"),
    }

    try:
        start_server(port=port, **options)
        return
    except OSError as exc:
        if not _is_addr_in_use(exc):
            raise
        if explicit:
            print(
                f"review: Web UI 端口 {port} 已被占用。"
                f"请关闭占用该端口的进程，或用 --port <其他端口> 指定端口。",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc

    print(
        f"review: Web UI 端口 {port} 已被占用，改用系统分配端口重试…",
        file=sys.stderr,
    )
    start_server(port=0, **options)
