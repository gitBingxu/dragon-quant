from __future__ import annotations

import math

from dragon_quant.review_account.models import Position, StrategyConfig


def fee(amount: float, side: str, cfg: StrategyConfig) -> float:
    return max(cfg.min_commission, amount * cfg.commission_rate) + (
        amount * cfg.stamp_tax_rate if side == "SELL" else 0.0
    )


def fill_price(row: dict, side: str, cfg: StrategyConfig, raw: float | None = None) -> float | None:
    price = raw if raw is not None else row.get("execution_price")
    if not price or not math.isfinite(price) or price <= 0:
        return None
    upper, lower = row.get("limit_up"), row.get("limit_down")
    if (upper and price > upper + .005) or (lower and price < lower - .005):
        return None
    if side == "BUY" and upper and price >= upper - .005:
        return None
    if side == "SELL" and lower and price <= lower + .005:
        return None
    result = price * (1 + cfg.buy_slippage if side == "BUY" else 1 - cfg.sell_slippage)
    if (upper and result > upper) or (lower and result < lower):
        return None
    return round(result, 4)


def daily_fallback_sell_price(position: Position, row: dict, reason: str, cfg: StrategyConfig) -> float:
    """日K兜底日的卖出成交价（无分时，按原保守约定按原因近似）。

    止损：跌破止损线，若当日开盘已低于止损线则按开盘价（跳空），否则按止损线；
    保本：按完整成本线；其余（移动止盈/弱势/放量等）：按当日收盘价。
    """
    entry = position.entry_price
    if reason in {"hard_stop_loss", "first_day_stop_loss"}:
        pct = cfg.first_day_stop_loss_pct if reason == "first_day_stop_loss" else cfg.stop_loss_pct
        stop = entry * (1 + pct / 100)
        return row["open"] if row["open"] <= stop else stop
    if reason == "profit_back_to_cost_take_profit":
        return break_even_price(position, cfg)
    return row["bar_close"]


def buy_quantity(cash: float, equity: float, price: float, cfg: StrategyConfig) -> int:
    budget = min(cash, equity * cfg.max_position_pct / 100)
    qty = int(budget / price / cfg.lot_size) * cfg.lot_size
    if cfg.risk_per_trade_pct:
        risk = equity * cfg.risk_per_trade_pct / 100
        per_share = price * (-cfg.first_day_stop_loss_pct / 100 + cfg.sell_slippage
                             + 2 * cfg.commission_rate + cfg.stamp_tax_rate)
        qty = min(qty, int(max(0, risk - 2 * cfg.min_commission) / per_share / cfg.lot_size) * cfg.lot_size)
    while qty > 0 and price * qty + fee(price * qty, "BUY", cfg) > budget:
        qty -= cfg.lot_size
    return qty


def sell_quantity(position: Position, reason: str, cfg: StrategyConfig) -> int:
    if reason != "next_day_limit_up_half":
        return position.qty
    return (position.qty // 2 // cfg.lot_size) * cfg.lot_size or position.qty


def break_even_price(position: Position, cfg: StrategyConfig) -> float:
    qty = position.qty
    percentage = position.cost / (1 - cfg.commission_rate - cfg.stamp_tax_rate)
    minimum = (position.cost + cfg.min_commission) / (1 - cfg.stamp_tax_rate)
    return max(percentage, minimum) / qty / (1 - cfg.sell_slippage)
