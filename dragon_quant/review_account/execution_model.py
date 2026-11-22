"""账户执行成本口径：研究与运行时**共用**的纯函数（版本化）。

W2 场景模拟与 W3 账本撮合必须使用同一套费用/滑点/整手口径，避免两个同名公式
随着演进渐行渐远。本模块只依赖标准库，可被两处共同 import 而不引入循环依赖；
口径变化必须显式提升 :data:`EXECUTION_MODEL_VERSION`，不得静默改数。
"""

from __future__ import annotations

EXECUTION_MODEL_VERSION = "exec_cost/1"


def fee_amount(amount: float, side: str, *, commission_rate: float,
               min_commission: float, stamp_tax_rate: float) -> float:
    """双边费用：佣金有最低值，卖出另加印花税。``amount`` 为成交额。"""
    commission = max(float(min_commission), float(amount) * float(commission_rate))
    if side == "sell":
        commission += float(amount) * float(stamp_tax_rate)
    return commission


def lot_floor(qty: float, lot: int) -> int:
    """按整手向下取整（``lot<=0`` 时只取整数部分）。"""
    return int(qty // lot) * lot if lot > 0 else int(qty)


def apply_slippage(price: float, side: str, *, buy_slippage: float,
                   sell_slippage: float) -> float:
    """把参考价换算成含滑点的成交价。"""
    if side == "buy":
        return float(price) * (1 + float(buy_slippage))
    return float(price) * (1 - float(sell_slippage))
