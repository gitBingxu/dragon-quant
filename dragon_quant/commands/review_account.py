"""review-account 命令：账户级模拟交易回测，或账户 Web UI。"""

from __future__ import annotations

import sys


def handle(args, services):
    """账户级模拟交易 review 命令（退出码/错误通道与原 handler 一致）。"""
    if args.ui_only:
        _start_ui(args)
        return

    if not args.date_from or not args.date_to:
        print("错误: review-account 需要 --from 和 --to，或使用 --ui-only", file=sys.stderr)
        return

    from dragon_quant.commands import _shared
    from dragon_quant.review_account import run_review_account

    options: dict = (
        {"strategy_params": _shared.load_strategy_params(args.config)}
        if args.config else {}
    )
    run_review_account(
        date_from=_normalize_cli_date(args.date_from),
        date_to=_normalize_cli_date(args.date_to),
        initial_cash=args.capital,
        source=args.source,
        strategy_name=args.strategy,
        verbose=True,
        services=services,
        **(options or {}),
    )

    if args.ui:
        _start_ui(args)


def _start_ui(args):
    """启动账户级 review Web UI。"""
    from web_ui.server import start_server
    start_server(
        port=args.port,
        open_browser=not args.no_browser,
        default_source=getattr(args, "source", "v2"),
        default_page="account",
    )


def _normalize_cli_date(d: str) -> str:
    from dragon_quant.commands._shared import normalize_cli_date
    return normalize_cli_date(d)
