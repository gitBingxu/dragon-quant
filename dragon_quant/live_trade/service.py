"""实盘辅助 buy/sell/account 命令的 service 层：账户管理 + 调用 LiveTrader + 中文输出。"""

import json
from typing import Optional

from dragon_quant.live_trade.trader import LiveTrader
from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.storage import db

DEFAULT_ACCOUNT = "default"


def _load_account(name: str, cfg: StrategyConfig,
                  create_capital: Optional[float] = None) -> Optional[dict]:
    """取账户；不存在时按 create_capital 隐式创建（None 则不创建）。"""
    account = db.get_live_account(name)
    if account:
        return account
    if create_capital is None:
        return None
    return db.ensure_live_account(
        name, initial_cash=create_capital,
        strategy_name=cfg.strategy_name,
        strategy_params_json=json.dumps(cfg.to_json_dict(), ensure_ascii=False),
    )


def init_account(name: str = DEFAULT_ACCOUNT, capital: float = 100_000.0,
                 source: str = "v2") -> dict:
    """新建或重置纸上账户。"""
    cfg = StrategyConfig(source=source, initial_cash=capital)
    account = db.ensure_live_account(
        name, initial_cash=capital, strategy_name=cfg.strategy_name,
        strategy_params_json=json.dumps(cfg.to_json_dict(), ensure_ascii=False),
        reset=True,
    )
    print(f"已初始化实盘辅助账户「{name}」，初始资金 {capital:,.0f} 元")
    return account


def _account_config(account: Optional[dict], capital: float, source: str,
                    strategy_params: Optional[dict]) -> StrategyConfig:
    saved = json.loads(account.get("strategy_params_json") or "{}") if account else {}
    cfg = StrategyConfig.from_dict(saved or {"source": source, "initial_cash": capital})
    if strategy_params is not None:
        StrategyConfig.from_dict(strategy_params)
        requested = StrategyConfig.from_dict({**cfg.to_json_dict(), **strategy_params,
                                             "initial_cash": cfg.initial_cash, "source": source})
        if account and requested != cfg:
            raise ValueError("账户已存在，不能静默切换策略；请使用新的 --account 创建独立实验账户")
        cfg = requested
    if cfg.source != source:
        raise ValueError("--source 与账户持久化策略不一致")
    return cfg


def run_buy(trade_date: str, capital: float = 100_000.0,
            account_name: str = DEFAULT_ACCOUNT, source: str = "v2",
            verbose: bool = True, as_of: Optional[str] = None,
            strategy_params: Optional[dict] = None) -> dict:
    account = db.get_live_account(account_name)
    cfg = _account_config(account, capital, source, strategy_params)
    account = account or _load_account(account_name, cfg, create_capital=capital)
    trader = LiveTrader(account, cfg)
    result = trader.buy(trade_date, as_of)
    if verbose:
        _print_buy(trade_date, account_name, result)
    return result


def run_sell(trade_date: str, account_name: str = DEFAULT_ACCOUNT,
             source: str = "v2", verbose: bool = True, as_of: Optional[str] = None,
             strategy_params: Optional[dict] = None) -> dict:
    account = db.get_live_account(account_name)
    cfg = _account_config(account, account["initial_cash"] if account else 100_000, source, strategy_params)
    if not account:
        if verbose:
            print(f"账户「{account_name}」不存在，请先执行 buy 或 account init")
        return {"results": [], "error": "no_account"}
    trader = LiveTrader(account, cfg)
    result = trader.sell(trade_date, as_of)
    if verbose:
        _print_sell(trade_date, account_name, result)
    return result


def run_account_status(account_name: str = DEFAULT_ACCOUNT) -> dict:
    account = db.get_live_account(account_name)
    if not account:
        print(f"账户「{account_name}」不存在，请先执行 buy 或 account init")
        return {"error": "no_account"}
    positions = db.list_live_positions(account["id"], status="open")
    trades = db.list_live_trades(account["id"])
    _print_status(account, positions, trades)
    return {"account": account, "positions": positions, "trades": trades}


# ─── 输出格式化 ───

def _print_buy(trade_date: str, account_name: str, result: dict):
    print(f"【买入建议 · {trade_date} · 账户 {account_name}】")
    for t in result.get("trades", []):
        print(f"  {t['side']} {t['name'] or t['code']} {t['qty']}股 @ {t['price']:.4f}，费用{t['fee']:.2f}")
        print(f"     {t['reason_text']}，剩余现金{t['cash_after']:.2f}")
    if not result.get("trades"):
        print(f"  {result.get('reason_text', '')}")
    for order in result.get("pending", []):
        print(f"  待执行 {order['side']} {order['stock_code']}：{order['reason_text']}")
    rejected = [d for d in result.get("details", []) if not d.get("passed")]
    if rejected:
        print(f"  候选未触发买点（{len(rejected)} 只）：")
        for d in rejected:
            label = d.get("name") or d.get("code") or "?"
            print(f"     - {label}（{d.get('code', '')}）：{d.get('reason_text', '')}")


def _print_sell(trade_date: str, account_name: str, result: dict):
    print(f"【卖出建议 · {trade_date} · 账户 {account_name}】")
    results = result.get("results", [])
    if not results:
        print(f"  {result.get('reason_text', '本次没有成交')}")
        for order in result.get("pending", []):
            print(f"  待执行 {order['side']} {order['stock_code']}：{order['reason_text']}")
        return
    for r in results:
        if r.get("action") == "sell":
            t = r["trade"]
            print(f"  🔴 卖出 {t['name'] or t['code']}（{t['code']}）"
                  f" {t['qty']} 股 @ {t['price']:.2f}，实现盈亏 {t['realized_pnl']:+,.0f} 元")
            print(f"     理由：{t['reason_text']}")
        else:
            print(f"  🟢 继续持有 {r.get('name') or r.get('code')}：{r.get('reason_text', '')}")


def _print_status(account: dict, positions: list[dict], trades: list[dict]):
    print(f"【账户 {account['name']}】")
    print(f"  初始资金 {account['initial_cash']:,.0f}｜可用现金 {account['cash']:,.0f}")
    print(f"  当前持仓 {len(positions)} 只：")
    for p in positions:
        print(f"    - {p['name'] or p['code']}（{p['code']}）{p['qty']} 股"
              f"，成本 {p['entry_price']:.2f}，买入日 {p['entry_date']}")
    print(f"  历史交割单 {len(trades)} 笔"
          + ("（最近5笔）" if len(trades) > 5 else ""))
    for t in trades[-5:]:
        print(f"    {t['trade_date']} {t['side']} {t['name'] or t['code']}"
              f" {t['qty']} @ {t['price']:.2f}  [{t['reason_code']}]")
