"""实盘辅助 buy/sell 命令的 service 层：策略装配 + 调用 LiveTrader + 中文输出。"""

from typing import Optional

from dragon_quant.live_trade.trader import LiveTrader
from dragon_quant.review_account.models import StrategyConfig


def _make_config(source: str, strategy_params: Optional[dict]) -> StrategyConfig:
    cfg = StrategyConfig(source=source)
    if strategy_params is not None:
        cfg = StrategyConfig.from_dict({**cfg.to_json_dict(), **strategy_params, "source": source})
    return cfg


def run_buy(trade_date: str, source: str = "v2", verbose: bool = True,
            as_of: Optional[str] = None, strategy_params: Optional[dict] = None) -> dict:
    cfg = _make_config(source, strategy_params)
    result = LiveTrader(cfg, source=source).buy(trade_date, as_of)
    if verbose:
        _print_buy(trade_date, result)
    return result


def run_sell(trade_date: str, source: str = "v2", verbose: bool = True,
             as_of: Optional[str] = None, strategy_params: Optional[dict] = None) -> dict:
    cfg = _make_config(source, strategy_params)
    result = LiveTrader(cfg, source=source).sell(trade_date, as_of)
    if verbose:
        _print_sell(trade_date, result)
    return result


# ─── 输出格式化 ───

def _print_buy(trade_date: str, result: dict):
    print(f"【买入建议 · {trade_date}】")
    for b in result.get("buys", []):
        print(f"  🟢 买入 {b['name'] or b['code']}（{b['code']}）@{b['entry_price']:.2f}")
        print(f"     {b['reason_text']}")
    if not result.get("buys"):
        print(f"  {result.get('reason_text', '')}")
    rejected = [d for d in result.get("details", []) if not d.get("passed")]
    if rejected:
        print(f"  候选未触发买点（{len(rejected)} 只）：")
        for d in rejected:
            label = d.get("name") or d.get("code") or "?"
            print(f"     - {label}（{d.get('code', '')}）：{d.get('reason_text', '')}")


def _print_sell(trade_date: str, result: dict):
    print(f"【卖出建议 · {trade_date}】")
    for s in result.get("sells", []):
        print(f"  🔴 卖出 {s['name'] or s['code']}（{s['code']}）@{s['exit_price']:.2f}"
              f"，持有 {s['hold_days']} 天")
        print(f"     理由：{s['reason_text']}")
    if not result.get("sells"):
        print(f"  {result.get('reason_text', '')}")
    for h in result.get("held", []):
        print(f"  🟢 继续持有 {h['name'] or h['code']}（{h['code']}）"
              f"，当前最高 {h['highest_return']:+.1f}%")
