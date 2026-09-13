from __future__ import annotations

import math

from dragon_quant.review_account.models import Position, StrategyConfig


def fee(amount: float, side: str, cfg: StrategyConfig) -> float:
    return max(cfg.min_commission, amount * cfg.commission_rate) + (
        amount * cfg.stamp_tax_rate if side == "SELL" else 0.0
    )


def fill_price(row: dict, side: str, cfg: StrategyConfig) -> float | None:
    price = row.get("execution_price")
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
