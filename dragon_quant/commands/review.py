"""review 命令：龙头回测验证，或启动回测结果 Web UI。"""

from __future__ import annotations


def handle(args, services):
    """龙头回测命令。"""
    from dragon_quant.review import run_review

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
    run_review(
        trade_date=date_str,
        top_n=args.top,
        force=args.force,
        verbose=True,
        source=args.source,
        services=services,
    )

    # --ui: 回测后启动 UI
    if args.ui:
        _start_ui(args)


def _start_ui(args):
    """启动 Web UI 服务器。"""
    from web_ui.server import start_server
    start_server(port=args.port, open_browser=not args.no_browser,
                 default_source=getattr(args, "source", "v2"))
