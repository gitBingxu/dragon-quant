"""组合状态原语（纯值）：触发判定与整手换算。

执行/持仓真值已迁到共享执行核心（:mod:`review_account.execution_core`）：场景
模拟与账本共用同一批次/订单/成交转移，本模块不再保留第二套 ``Lot``/现金簿记。
"""

from __future__ import annotations

from typing import Optional

from dragon_quant.review_account.execution_model import (
    lot_floor as _lot_floor,
)

from .plans import PlanLeg


def lot_floor(qty: float, lot: int) -> int:
    return _lot_floor(qty, lot)


def trigger_met(leg: PlanLeg, price: Optional[float]) -> bool:
    if leg.trigger_price is None:
        return True
    if price is None:
        return False
    if leg.trigger_mode == "below":
        return price <= leg.trigger_price + 1e-9
    return price >= leg.trigger_price - 1e-9
