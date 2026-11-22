"""BS 政策内核的基础值：错误、数值/日期校验与稳定常量（纯计算）。"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Optional

BS_DECISION_SCHEMA_VERSION = "2"
ENTRY_RULE_PREFIX = "entry:"
CONFIRM_RULE_PREFIX = "confirm:"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PolicyConfigError(ValueError):
    """BS 政策配置缺失/非法/跨字段冲突（不退化成默认风险参数）。"""

    reason = "policy_config_invalid"

    def __init__(self, message: str, *, reason: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        if reason:
            self.reason = reason


class PolicyInputError(ValueError):
    """调用方提供的账户/批次/证据/场景输入不自洽（不静默修正）。"""

    reason = "policy_input_invalid"

    def __init__(self, message: str, *, reason: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        if reason:
            self.reason = reason


def _valid_date(value: str) -> bool:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _finite_or_none(value: Any, *, positive: bool = False) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    return number

ACTION_WAIT = "wait"
ACTION_NO_ACTION = "no_action"
ACTION_ENTER = "enter"
ACTION_PROBE = "probe"
ACTION_ADD = "add"
ACTION_HOLD = "hold"
ACTION_REDUCE = "reduce"
ACTION_EXIT = "exit"
ACTION_SWITCH = "switch"

PLAN_WAIT = "wait"
PLAN_NO_ACTION = "no_action"
PLAN_BREAKOUT = "breakout"
PLAN_PULLBACK = "pullback"
PLAN_CONFIRM = "confirm"
PLAN_AUCTION_CONFIRM = "auction_confirm"
PLAN_PROBE = "probe"
PLAN_ADD = "add"
PLAN_HOLD = "hold"
PLAN_PARTIAL_REDUCE = "partial_reduce"
PLAN_FULL_EXIT = "full_exit"
PLAN_SWITCH = "switch"
PLAN_RECOVERY = "recovery"

SCENARIO_HISTORICAL = "historical_joint"
SCENARIO_PRESSURE = "pressure"
SCENARIO_TEACHING = "teaching_fixture"


def _finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None



_NEW_RISK_KINDS = frozenset({
    PLAN_BREAKOUT, PLAN_PULLBACK, PLAN_CONFIRM, PLAN_AUCTION_CONFIRM,
    PLAN_PROBE, PLAN_ADD, PLAN_SWITCH,
})
