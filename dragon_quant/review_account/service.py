"""账户级 review 的 CLI/service 入口。"""

import json
from typing import Optional

from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.priority import RANKING_MODE_LABELS
from dragon_quant.review_account.simulator import AccountSimulator, annotate_post_exit
from dragon_quant.storage import db


def _xueqiu_from_services(services):
    """可选 M2 装配注入：取出 bundle 的雪球 provider；无注入返回 None。

    None 表示沿用 ``AccountSimulator`` → ``MarketData`` 的既有默认构造，
    旧调用与 ``MarketData(provider=...)`` / provider patch 行为不变。
    """
    if services is None:
        return None
    bundle = getattr(services, "providers", None)
    if bundle is None:
        return None
    return bundle.xueqiu


def run_review_account(date_from: str,
                       date_to: str,
                       *,
                       initial_cash: float = 100_000.0,
                       source: str = "v2",
                       strategy_name: str = "dragon_pullback_daily",
                       verbose: bool = True,
                       display_name: Optional[str] = None,
                       strategy_params: Optional[dict] = None,
                       services=None) -> dict:
    """运行账户级交易模拟并持久化结果。

    ``services`` 为可选 M2 装配注入口：传入且其 ``providers`` 非空时，K 线
    加载使用 ``services.providers.xueqiu``；不传时保持原有 ``XueqiuProvider()``
    构造路径。策略、成交、候选池、信号与输出不变。
    """
    if strategy_params is not None and not isinstance(strategy_params, dict):
        raise ValueError("strategy_params 必须是 JSON 对象")
    cfg = StrategyConfig.from_dict({**(strategy_params or {}), "source": source,
                                    "strategy_name": strategy_name, "initial_cash": initial_cash})
    sim = AccountSimulator(cfg, provider=_xueqiu_from_services(services))
    result = sim.run(date_from, date_to)
    # 事后评估：清仓后窗口指标。放在 run() 之外，回测核心保持纯 as-of；失败逐条降级。
    annotate_post_exit(result["positions"], sim.data)

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
        display_name=display_name,
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
    result["display_name"] = display_name

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
    mode = result.get("ranking_mode")
    if mode:
        print(f"排序模式: {mode}（{RANKING_MODE_LABELS.get(mode, mode)}）")
    fallbacks = {day: reason for day, reason in (result.get("fallback_reasons") or {}).items()
                 if reason}
    if fallbacks:
        reasons = "、".join(sorted(set(fallbacks.values())))
        print(f"候选池回退: {len(fallbacks)} 天（{reasons}）")
    for note in result.get("priority_degraded") or []:
        print(f"⚠️ {note}")
    suspended = result.get("suspended_codes") or []
    if suspended:
        print(f"停牌跳过: {len(suspended)} 只 / {result.get('suspended_days_count', 0)} 天"
              f"（{'、'.join(suspended)}）")
    for note in result.get("data_degraded") or []:
        print(f"⚠️ {note}")
    print(f"run_id: {result['run_id']}")
