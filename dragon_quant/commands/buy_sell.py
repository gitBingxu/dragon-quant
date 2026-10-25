"""buy / sell 命令：实盘辅助买卖信号（纯信号记账）。"""

from __future__ import annotations

import json
import sys


def handle_buy(args, services):
    """执行当前时点的买入信号（纯信号记账，非模拟账户）。"""
    from dragon_quant.commands import _shared
    from dragon_quant.live_trade import run_buy

    as_json = getattr(args, "json", False)
    raw_date = getattr(args, "date", None)
    trade_date = raw_date
    try:
        trade_date = _resolve_trade_date(raw_date)
        result = run_buy(trade_date, source=args.source, verbose=not as_json,
                         as_of=args.at,
                         strategy_params=_shared.load_strategy_params(args.config),
                         force=getattr(args, "force", False), services=services)
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
    from dragon_quant.live_trade import run_sell

    trade_date = _resolve_trade_date(args.date)
    try:
        run_sell(trade_date, source=args.source, verbose=True,
                 as_of=args.at,
                 strategy_params=_shared.load_strategy_params(args.config),
                 force=getattr(args, "force", False), services=services)
    except ValueError as e:
        print(f"⏸ 暂不执行卖出：{e}", file=sys.stderr)
        sys.exit(1)


def _resolve_trade_date(raw):
    from dragon_quant.commands._shared import resolve_trade_date
    return resolve_trade_date(raw)
