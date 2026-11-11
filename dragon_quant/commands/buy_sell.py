"""buy / sell 命令：实盘辅助买卖信号（纯信号记账）。"""

from __future__ import annotations

import json
import sys


def handle_buy(args, services):
    """执行当前时点的买入信号（纯信号记账，非模拟账户）。"""
    from dragon_quant.commands import _shared
    from dragon_quant.review_account.live_trade.main import run_buy

    as_json = getattr(args, "json", False)
    raw_date = getattr(args, "date", None)
    trade_date = raw_date
    try:
        trade_date = _resolve_trade_date(raw_date)
        selection = _resolve_selection(getattr(args, "workspace", None),
                                       getattr(args, "strategy", None))
        call_kwargs = dict(
            source=args.source, verbose=not as_json, as_of=args.at,
            strategy_params=_shared.load_strategy_params(args.config),
            force=getattr(args, "force", False), services=services)
        if selection is not None:
            call_kwargs["selection"] = selection
        result = run_buy(trade_date, **call_kwargs)
        if as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except ValueError as e:
        if as_json:
            print(json.dumps({"ok": False, "mode": "buy",
                              "trade_date": trade_date or _resolve_trade_date(None),
                              "error": str(e)}, ensure_ascii=False, indent=2))
        else:
            print(f"⏸ 暂不执行买入：{e}", file=sys.stderr)
        sys.exit(1)


def handle_sell(args, services):
    """执行当前时点的卖出信号（纯信号记账，非模拟账户）。"""
    from dragon_quant.commands import _shared
    from dragon_quant.review_account.live_trade.main import run_sell

    trade_date = _resolve_trade_date(args.date)
    as_json = getattr(args, "json", False)
    try:
        # sell 默认评估全部归属；--strategy 只筛选该归属持仓，不替换退出规则。
        selection = _resolve_active_selection(getattr(args, "workspace", None))
        call_kwargs = dict(
            source=args.source, verbose=not as_json, as_of=args.at,
            strategy_params=_shared.load_strategy_params(args.config),
            force=getattr(args, "force", False), services=services)
        if selection is not None:
            call_kwargs["selection"] = selection
        if getattr(args, "strategy", None) is not None:
            call_kwargs["only_strategy_id"] = args.strategy
        result = run_sell(trade_date, **call_kwargs)
        if as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except ValueError as e:
        print(f"⏸ 暂不执行卖出：{e}", file=sys.stderr)
        sys.exit(1)


def _resolve_selection(workspace, strategy_name):
    from dragon_quant.workspace.config import resolve_workspace_path

    looks_like_id = bool(strategy_name and ":" in strategy_name)
    if not (workspace or looks_like_id or resolve_workspace_path(None) is not None):
        return None
    from dragon_quant.workspace.main import resolve_selection

    return resolve_selection(explicit_workspace=workspace,
                             explicit_strategy_id=strategy_name)


def _resolve_active_selection(workspace):
    from dragon_quant.workspace.config import resolve_workspace_path

    if not (workspace or resolve_workspace_path(None) is not None):
        return None
    from dragon_quant.workspace.main import resolve_selection

    return resolve_selection(explicit_workspace=workspace)


def _resolve_trade_date(raw):
    from dragon_quant.commands._shared import resolve_trade_date
    return resolve_trade_date(raw)
