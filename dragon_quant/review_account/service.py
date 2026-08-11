"""账户级 review 的 CLI/service 入口。"""

import json
from typing import Optional

from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.simulator import AccountSimulator
from dragon_quant.storage import db


def run_review_account(date_from: str,
                       date_to: str,
                       *,
                       initial_cash: float = 100_000.0,
                       source: str = "v2",
                       strategy_name: str = "dragon_pullback_daily",
                       verbose: bool = True) -> dict:
    """运行账户级交易模拟并持久化结果。"""
    cfg = StrategyConfig(
        source=source,
        strategy_name=strategy_name,
        initial_cash=initial_cash,
    )
    sim = AccountSimulator(cfg)
    result = sim.run(date_from, date_to)

    run_id = db.create_review_account_run(
        source=source,
        strategy_name=strategy_name,
        strategy_params_json=json.dumps(cfg.to_json_dict(), ensure_ascii=False),
        date_from=date_from,
        date_to=date_to,
        initial_cash=result["initial_cash"],
        final_equity=result["final_equity"],
        total_return=result["total_return"],
        max_drawdown=result["max_drawdown"],
        trade_count=result["trade_count"],
        win_rate=result["win_rate"],
    )
    db.save_review_account_results(
        run_id,
        snapshots=[s.__dict__ for s in result["snapshots"]],
        trades=[
            {**t.__dict__, "signal_json": json.dumps(t.signal, ensure_ascii=False)}
            for t in result["trades"]
        ],
        positions=[
            {
                **p.__dict__,
                "entry_signal_json": json.dumps(p.entry_signal, ensure_ascii=False),
                "exit_signal_json": json.dumps(p.exit_signal, ensure_ascii=False),
            }
            for p in result["positions"]
        ],
        events=[
            {
                **e.__dict__,
                "signal_json": json.dumps(e.signal, ensure_ascii=False),
            }
            for e in result.get("events", [])
        ],
    )
    result["run_id"] = run_id

    if verbose:
        _print_summary(result, source, strategy_name)
    return result


def _print_summary(result: dict, source: str, strategy_name: str):
    print(f"账户级回测 [{source}] {strategy_name}")
    print(f"区间: {result['date_from']} ~ {result['date_to']}")
    print(f"初始资金: {result['initial_cash']:.2f}")
    print(f"最终权益: {result['final_equity']:.2f}")
    print(f"累计收益: {result['total_return']:+.2f}%")
    print(f"最大回撤: {result['max_drawdown']:+.2f}%")
    if result["win_rate"] is None:
        print("胜率: —")
    else:
        print(f"胜率: {result['win_rate']:.1f}%")
    print(f"交割单: {result['trade_count']} 笔")
    print(f"run_id: {result['run_id']}")
