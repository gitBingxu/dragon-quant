"""BS 政策风险/额度/失效/恢复配置值（schema_version=1）。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .base import PolicyConfigError

@dataclass(frozen=True)
class Preference:
    """风险偏好：rho ∈ [0,1] 是保守/悲观系数（越大越看重场景下限）。"""

    rho: float

    def validate(self) -> None:
        if not math.isfinite(self.rho) or not 0.0 <= self.rho <= 1.0:
            raise PolicyConfigError("preference.rho 必须在 [0,1] 内")

    def to_dict(self) -> dict:
        return {"rho": self.rho}


@dataclass(frozen=True)
class RiskBudgets:
    """账户风险预算（单位：人民币元，全部是**计划损失**而非投入金额）。

    - ``per_trade``：单笔新增风险上限；
    - ``account``：全账户新增风险上限（含存量共同压力）；
    - ``per_theme``：单一已知主题上限；
    - ``unknown_group``：未能排除重叠的未知归属共同压力上限；
    - ``giveback``：浮盈回吐预算（与本金风险分开）；
    - ``held_principal``：存量本金风险上限（独立于新增风险）。
    """

    per_trade: float
    account: float
    per_theme: float
    unknown_group: float
    giveback: float
    held_principal: float

    def validate(self) -> None:
        values = (self.per_trade, self.account, self.per_theme,
                  self.unknown_group, self.giveback, self.held_principal)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise PolicyConfigError("风险预算必须是非负有限数值")
        if self.per_trade > self.account:
            raise PolicyConfigError("per_trade 不能大于 account")
        if self.per_theme > self.account:
            raise PolicyConfigError("per_theme 不能大于 account")
        if self.unknown_group > self.account:
            raise PolicyConfigError("unknown_group 不能大于 account")
        if self.held_principal > self.account:
            raise PolicyConfigError("held_principal 不能大于 account")

    def to_dict(self) -> dict:
        return {
            "per_trade": self.per_trade,
            "account": self.account,
            "per_theme": self.per_theme,
            "unknown_group": self.unknown_group,
            "giveback": self.giveback,
            "held_principal": self.held_principal,
        }


@dataclass(frozen=True)
class SizingLimits:
    """试探/确认额度（人民币元计划损失）与增配步长。"""

    probe_risk: float
    confirmed_risk: float
    add_step_risk: float
    max_adds: int

    def validate(self, budgets: RiskBudgets) -> None:
        values = (self.probe_risk, self.confirmed_risk, self.add_step_risk)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise PolicyConfigError("试探/确认额度必须是非负有限数值")
        if self.probe_risk > self.confirmed_risk:
            raise PolicyConfigError("probe_risk 不能大于 confirmed_risk")
        if self.confirmed_risk > budgets.per_trade:
            raise PolicyConfigError("confirmed_risk 不能大于 per_trade 预算")
        if self.add_step_risk > self.confirmed_risk:
            raise PolicyConfigError("add_step_risk 不能大于 confirmed_risk")
        if not isinstance(self.max_adds, int) or self.max_adds < 0:
            raise PolicyConfigError("max_adds 必须是非负整数")

    def to_dict(self) -> dict:
        return {
            "probe_risk": self.probe_risk,
            "confirmed_risk": self.confirmed_risk,
            "add_step_risk": self.add_step_risk,
            "max_adds": self.max_adds,
        }


@dataclass(frozen=True)
class Invalidation:
    """价格/时间失效（百分比为负）与灾难保护（更宽的灾难线）。"""

    price_stop_pct: float
    time_limit_days: int
    confirm_deadline_days: int
    disaster_stop_pct: float

    def validate(self) -> None:
        if not math.isfinite(self.price_stop_pct) or not -100 < self.price_stop_pct < 0:
            raise PolicyConfigError("price_stop_pct 必须在 (-100, 0) 内")
        if not math.isfinite(self.disaster_stop_pct) or not -100 < self.disaster_stop_pct <= 0:
            raise PolicyConfigError("disaster_stop_pct 必须在 (-100, 0] 内")
        if self.disaster_stop_pct and self.disaster_stop_pct > self.price_stop_pct:
            raise PolicyConfigError("灾难线必须比结构失效线更宽（更小）")
        if not isinstance(self.time_limit_days, int) or self.time_limit_days <= 0:
            raise PolicyConfigError("time_limit_days 必须是正整数")
        if not isinstance(self.confirm_deadline_days, int) or self.confirm_deadline_days <= 0:
            raise PolicyConfigError("confirm_deadline_days 必须是正整数")
        if self.confirm_deadline_days > self.time_limit_days:
            raise PolicyConfigError("确认期限不能晚于评价期限")

    def to_dict(self) -> dict:
        return {
            "price_stop_pct": self.price_stop_pct,
            "time_limit_days": self.time_limit_days,
            "confirm_deadline_days": self.confirm_deadline_days,
            "disaster_stop_pct": self.disaster_stop_pct,
        }


@dataclass(frozen=True)
class RecoveryPolicy:
    """收缩触发与分阶段恢复（不自动最大恢复）。"""

    drawdown_trigger_pct: float
    loss_streak_trigger: int
    cooldown_days: int
    stages: tuple[float, ...]
    conditions: tuple[str, ...]

    def validate(self) -> None:
        if not math.isfinite(self.drawdown_trigger_pct) or self.drawdown_trigger_pct <= 0:
            raise PolicyConfigError("drawdown_trigger_pct 必须是正数")
        if not isinstance(self.loss_streak_trigger, int) or self.loss_streak_trigger < 1:
            raise PolicyConfigError("loss_streak_trigger 必须 >= 1")
        if not isinstance(self.cooldown_days, int) or self.cooldown_days < 0:
            raise PolicyConfigError("cooldown_days 必须是非负整数")
        if not self.stages:
            raise PolicyConfigError("recovery.stages 不能为空")
        previous = 0.0
        for stage in self.stages:
            if not math.isfinite(stage) or not 0 < stage <= 1:
                raise PolicyConfigError("recovery.stages 每项必须在 (0,1] 内")
            if stage <= previous:
                raise PolicyConfigError("recovery.stages 必须严格递增")
            previous = stage
        if not self.conditions:
            raise PolicyConfigError("分阶段恢复必须声明恢复条件")

    def to_dict(self) -> dict:
        return {
            "drawdown_trigger_pct": self.drawdown_trigger_pct,
            "loss_streak_trigger": self.loss_streak_trigger,
            "cooldown_days": self.cooldown_days,
            "stages": list(self.stages),
            "conditions": list(self.conditions),
        }
