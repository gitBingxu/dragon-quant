from __future__ import annotations

import math

from dragon_quant.review_account.models import Position, StrategyConfig

# 半仓腿原因码：`sell_quantity` 减半、`apply_sell_fill` 置 took_profit_half（同一信号只一条腿）。
_HALF_REASONS = frozenset({"next_day_limit_up_half", "first_day_stop_half"})


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


def _stop_fallback_price(entry: float, row: dict, pct: float) -> float:
    """兜底日止损近似价：开盘已破线按开盘价（跳空），否则按止损线。"""
    stop = entry * (1 + pct / 100)
    return row["open"] if row["open"] <= stop else stop


def daily_fallback_sell_price(position: Position, row: dict, reason: str, cfg: StrategyConfig) -> float:
    """日K兜底日的卖出成交价（无分时，按原保守约定按原因近似）。

    止损类：跌破止损线，若当日开盘已低于止损线则按开盘价（跳空），否则按止损线；
    保本：按完整成本线；其余（移动止盈/弱势/放量等）：按当日收盘价。
    首日两种新原因码同属止损类：`first_day_stop_half` 用减半线，`first_day_disaster_stop`
    用灾难线（未配置时退化为减半线，保证映射表对每个已登记 reason 都有确定语义）——
    兜底日只有 open/late 两帧，无法像真盘中那样在击穿瞬间退出（方案 §4.2）。
    本表按 reason 定价，不看 toggle。
    """
    entry = position.entry_price
    if reason in {"hard_stop_loss", "first_day_stop_loss"}:
        pct = cfg.first_day_stop_loss_pct if reason == "first_day_stop_loss" else cfg.stop_loss_pct
        return _stop_fallback_price(entry, row, pct)
    if reason == "first_day_stop_half":
        return _stop_fallback_price(entry, row, cfg.first_day_stop_loss_pct)
    if reason == "first_day_disaster_stop":
        return _stop_fallback_price(entry, row,
                                    cfg.first_day_disaster_stop_pct or cfg.first_day_stop_loss_pct)
    if reason == "profit_back_to_cost_take_profit":
        return break_even_price(position, cfg)
    return row["bar_close"]


def buy_quantity(cash: float, equity: float, price: float, cfg: StrategyConfig,
                 budget_scale: float = 1.0) -> int:
    budget = min(cash, equity * cfg.max_position_pct / 100) * budget_scale
    qty = int(budget / price / cfg.lot_size) * cfg.lot_size
    if cfg.risk_per_trade_pct:
        risk = equity * cfg.risk_per_trade_pct / 100
        # 风险距离读 StrategyConfig 单一真值（首日规则开启后是 (half+tail)/2 的保守上界）。
        per_share = price * (-cfg.first_day_risk_distance_pct() / 100 + cfg.sell_slippage
                             + 2 * cfg.commission_rate + cfg.stamp_tax_rate)
        qty = min(qty, int(max(0, risk - 2 * cfg.min_commission) / per_share / cfg.lot_size) * cfg.lot_size)
    while qty > 0 and price * qty + fee(price * qty, "BUY", cfg) > budget:
        qty -= cfg.lot_size
    return qty


def _half_qty(qty: int, lot_size: int) -> int:
    """半仓腿数量：向下取整到整手；不足一手时退化为全卖（不产生 0 股成交）。"""
    return (qty // 2 // lot_size) * lot_size or qty


def sell_quantity(position: Position, reason: str, cfg: StrategyConfig) -> int:
    if reason not in _HALF_REASONS:
        return position.qty
    return _half_qty(position.qty, cfg.lot_size)


def notional_quantity(cfg: StrategyConfig, price: float) -> int:
    """实盘辅助的虚拟数量口径：满仓单票（initial_cash × max_position_pct），与回测首仓同口径。

    可能返回 0（股价超预算），调用方按回测 `_fill` 的同款语义不建仓。
    """
    return buy_quantity(cfg.initial_cash, cfg.initial_cash, price, cfg)


def apply_sell_fill(position: Position, price: float, reason_code: str, cfg: StrategyConfig) -> dict:
    """卖出成交的仓位记账原语（TradingEngine._fill 与 live_trade.SignalEngine._fill_sell 共用）。

    副作用：扣减 position.qty/cost、累加 realized_pnl、半仓时置 took_profit_half。
    返回 {qty, amount, fee, cost, pnl, partial}；账户/信号簿记（现金、realized_today、
    ClosedPosition、DB 路由）由调用方负责。
    """
    qty = sell_quantity(position, reason_code, cfg)
    amount, cost_fee = price * qty, fee(price * qty, "SELL", cfg)
    cost = position.cost * qty / position.qty
    pnl = amount - cost_fee - cost
    position.realized_pnl += pnl
    position.qty -= qty
    position.cost -= cost
    partial = bool(position.qty)
    if partial and reason_code in _HALF_REASONS:
        position.took_profit_half = True
    return {"qty": qty, "amount": amount, "fee": cost_fee, "cost": cost,
            "pnl": pnl, "partial": partial}


def track_peak(position: Position, row: dict, entry_ts: int) -> None:
    """峰值跟踪原语：同一根 K 的高点不能反向激活该根 K 内的保护（两引擎共用）。"""
    if row["bar_timestamp"] > entry_ts:
        observed_high = (row["bar_high"] if row["bar_timestamp"] - 300_000 >= entry_ts
                         else row["bar_close"])
        position.highest_price = max(position.highest_price, observed_high)
        position.highest_return = max(position.highest_return,
                                      (position.highest_price / position.entry_price - 1) * 100)


def break_even_price(position: Position, cfg: StrategyConfig) -> float:
    qty = position.qty
    percentage = position.cost / (1 - cfg.commission_rate - cfg.stamp_tax_rate)
    minimum = (position.cost + cfg.min_commission) / (1 - cfg.stamp_tax_rate)
    return max(percentage, minimum) / qty / (1 - cfg.sell_slippage)
