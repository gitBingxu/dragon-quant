"""review-account 命令：账户级模拟交易回测，或账户 Web UI。"""

from __future__ import annotations

import json
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
    from dragon_quant.review_account.main import run_review_account

    options: dict = (
        {"strategy_params": _shared.load_strategy_params(args.config)}
        if args.config else {}
    )
    want_json = bool(getattr(args, "json", False))
    result = run_review_account(
        date_from=_normalize_cli_date(args.date_from),
        date_to=_normalize_cli_date(args.date_to),
        initial_cash=args.capital,
        source=args.source,
        strategy_name=args.strategy,
        verbose=not want_json,
        services=services,
        **(options or {}),
    )
    if want_json:
        # 兼容回执：只新增 JSON 输出，人读默认格式不变；供 Agent 取得真实 run_id
        # 后再用 `research account --run-id` 读回。
        print(json.dumps({
            "ok": True,
            "run_id": result.get("run_id"),
            "source": result.get("source") or args.source,
            "strategy_name": result.get("strategy_name") or args.strategy,
            "date_from": result.get("date_from"),
            "date_to": result.get("date_to"),
            "date_to_clamped": bool(result.get("date_to_clamped")),
            "initial_cash": result.get("initial_cash"),
            "final_equity": result.get("final_equity"),
            "total_return": result.get("total_return"),
            "max_drawdown": result.get("max_drawdown"),
            "trade_count": result.get("trade_count"),
            "win_rate": result.get("win_rate"),
        }, ensure_ascii=False, indent=2))

    if args.ui:
        _start_ui(args)


def _start_ui(args):
    """启动账户级 review Web UI（deprecated 别名，转发 dashboard 语义）。"""
    from dragon_quant.commands.dashboard import ensure_web_extra
    from web_ui.server import start_server

    ensure_web_extra(auto=False)
    print(
        "review-account: --ui/--ui-only 已弃用，"
        "请改用 dragon-quant dashboard（后续版本将移除）",
        file=sys.stderr,
    )
    start_server(
        port=args.port,
        open_browser=not args.no_browser,
        default_source=getattr(args, "source", "v2"),
        default_page="account",
    )


def _normalize_cli_date(d: str) -> str:
    from dragon_quant.commands._shared import normalize_cli_date
    return normalize_cli_date(d)
