"""数量/价格/费用纯函数（风险距离、整手、压力上限）。"""

from __future__ import annotations

import math
from typing import Mapping, Optional

from .config import BSPolicyConfig, ScenarioSpec

def _fee(amount: float, side: str, spec: ScenarioSpec) -> float:
    commission = max(spec.min_commission, amount * spec.commission_rate)
    if side == "sell":
        commission += amount * spec.stamp_tax_rate
    return commission


def _lot(value: float, lot: int) -> int:
    return int(value // lot) * lot


def _half_qty(qty: int, lot: int) -> int:
    if qty <= 0:
        return 0
    return _lot(qty // 2, lot) or qty


def _round_price(value: Optional[float]) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return round(value, 4)


def _structure_price(structure: Mapping, key: str) -> Optional[float]:
    value = structure.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return round(number, 4)


def _risk_quantity(*, risk: float, price: Optional[float], stop_pct: float,
                   spec: ScenarioSpec, cash_limit: Optional[float]) -> int:
    if price is None or price <= 0 or risk <= 0:
        return 0
    per_share = price * (abs(stop_pct) / 100 + spec.buy_slippage
                         + spec.sell_slippage + 2 * spec.commission_rate
                         + spec.stamp_tax_rate)
    if per_share <= 0:
        return 0
    qty = _lot(risk / per_share, spec.lot_size)
    if cash_limit is not None:
        while qty > 0 and price * qty * (1 + spec.buy_slippage) + _fee(
                price * qty, "buy", spec) > cash_limit:
            qty -= spec.lot_size
    return max(0, qty)


def _cap_by_pressure(qty: int, price: Optional[float],
                     pressure_pct: Optional[float], limit: float,
                     lot: int) -> int:
    if qty <= 0 or price is None or pressure_pct is None or limit <= 0:
        return qty
    per_share_loss = price * float(pressure_pct)
    if per_share_loss <= 0:
        return qty
    return min(qty, _lot(limit / per_share_loss, lot))



def _stop_price(cost: Optional[float], config: BSPolicyConfig) -> Optional[float]:
    if cost is None or cost <= 0:
        return None
    return _round_price(cost * (1 + config.invalidation.price_stop_pct / 100))


def _disaster_price(cost: Optional[float],
                    config: BSPolicyConfig) -> Optional[float]:
    if not config.invalidation.disaster_stop_pct or cost is None or cost <= 0:
        return None
    return _round_price(cost * (1 + config.invalidation.disaster_stop_pct / 100))
