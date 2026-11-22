"""场景口径与评价方法参数（费用/延迟/敏感性）。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .base import PolicyConfigError

@dataclass(frozen=True)
class ScenarioSpec:
    """共同场景口径：期限、执行延迟、费用、滑点、整手与现金再投资。"""

    horizon_days: int
    latency_steps: int
    commission_rate: float
    min_commission: float
    stamp_tax_rate: float
    buy_slippage: float
    sell_slippage: float
    lot_size: int
    reinvest_cash: bool
    step_unit: str = "trading_day"

    def validate(self) -> None:
        if not isinstance(self.horizon_days, int) or self.horizon_days < 1:
            raise PolicyConfigError("scenario.horizon_days 必须是正整数")
        if not isinstance(self.latency_steps, int) or self.latency_steps < 1:
            raise PolicyConfigError("scenario.latency_steps 必须 >= 1")
        for name in ("commission_rate", "stamp_tax_rate", "buy_slippage",
                     "sell_slippage"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value < 1:
                raise PolicyConfigError(f"scenario.{name} 必须在 [0,1) 内")
        if not math.isfinite(self.min_commission) or self.min_commission < 0:
            raise PolicyConfigError("scenario.min_commission 必须非负")
        if not isinstance(self.lot_size, int) or self.lot_size <= 0:
            raise PolicyConfigError("scenario.lot_size 必须是正整数")
        if not isinstance(self.reinvest_cash, bool):
            raise PolicyConfigError("scenario.reinvest_cash 必须是布尔值")
        if not isinstance(self.step_unit, str) or not self.step_unit.strip():
            raise PolicyConfigError("scenario.step_unit 必须是非空字符串")
        if self.step_unit not in ("trading_day", "calendar_day"):
            raise PolicyConfigError(
                "scenario.step_unit 必须是 trading_day 或 calendar_day")

    def to_dict(self) -> dict:
        return {
            "horizon_days": self.horizon_days,
            "latency_steps": self.latency_steps,
            "commission_rate": self.commission_rate,
            "min_commission": self.min_commission,
            "stamp_tax_rate": self.stamp_tax_rate,
            "buy_slippage": self.buy_slippage,
            "sell_slippage": self.sell_slippage,
            "lot_size": self.lot_size,
            "reinvest_cash": self.reinvest_cash,
            "step_unit": self.step_unit,
        }


@dataclass(frozen=True)
class EvaluationPolicy:
    """概率资格与敏感性参数（方法参数，不是用户风险偏好）。"""

    min_probability_samples: int = 30
    rho_sensitivity: float = 0.10
    cost_sensitivity: float = 0.50
    latency_sensitivity_steps: int = 1

    def validate(self) -> None:
        if self.min_probability_samples < 1:
            raise PolicyConfigError("min_probability_samples 必须 >= 1")
        if not math.isfinite(self.rho_sensitivity) or not 0 < self.rho_sensitivity < 0.5:
            raise PolicyConfigError("rho_sensitivity 必须在 (0,0.5) 内")
        if not math.isfinite(self.cost_sensitivity) or not 0 <= self.cost_sensitivity < 1:
            raise PolicyConfigError("cost_sensitivity 必须在 [0,1) 内")
        if not isinstance(self.latency_sensitivity_steps, int) \
                or self.latency_sensitivity_steps < 0:
            raise PolicyConfigError("latency_sensitivity_steps 必须是非负整数")

    def to_dict(self) -> dict:
        return {
            "min_probability_samples": self.min_probability_samples,
            "rho_sensitivity": self.rho_sensitivity,
            "cost_sensitivity": self.cost_sensitivity,
            "latency_sensitivity_steps": self.latency_sensitivity_steps,
        }
