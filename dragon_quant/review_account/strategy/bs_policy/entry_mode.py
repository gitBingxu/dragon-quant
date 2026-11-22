"""显式 ``entry_mode=probe_then_confirm`` 的计划资格过滤（纯函数）。

不改变排序/评分/纯核门禁：只把“不满足 probe-first 约束的完整新增风险计划”
标为不可执行并保留审计原因；保护/减仓/退出/等待/加仓仍由既有逻辑决定。
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Iterable, Mapping

from .base import (
    PLAN_AUCTION_CONFIRM, PLAN_BREAKOUT, PLAN_CONFIRM, PLAN_PROBE,
    PLAN_PULLBACK, PLAN_SWITCH,
)
from dragon_quant.review_account.strategy.event_inputs import canonical_input

from .decision import PolicyContext

FULL_ENTRY_KINDS = (PLAN_BREAKOUT, PLAN_PULLBACK, PLAN_CONFIRM,
                    PLAN_AUCTION_CONFIRM)
REASON_PROBE_FIRST = "entry_mode_probe_first"
REASON_PROBE_EXISTS = "entry_mode_probe_already_exists"
REASON_USE_ADD = "entry_mode_use_add_after_probe"


def _owner_key(value: Mapping | None) -> tuple:
    return tuple(sorted((str(key), json.dumps(canonical_input(item), sort_keys=True,
                                             ensure_ascii=False))
                        for key, item in dict(value or {}).items()))


def _state_for(context: PolicyContext, code: str):
    owner = _owner_key(context.active_owner)
    for state in context.policy_states or ():
        if state.code != code:
            continue
        if owner and _owner_key(state.owner) and owner != _owner_key(state.owner):
            continue
        return state
    return None


def apply_entry_mode(plans: Iterable, context: PolicyContext,
                     config) -> tuple[list, list[dict]]:
    if getattr(config, "entry_mode", "compete") != "probe_then_confirm":
        return list(plans), []
    result, gates = [], []
    for plan in plans:
        kind = str(getattr(plan, "plan_kind", ""))
        code = str(getattr(plan, "code", "") or "")
        state = _state_for(context, code) if code else None
        reason = None
        if kind in FULL_ENTRY_KINDS:
            reason = (REASON_USE_ADD if state is not None
                      and int(getattr(state, "probe_filled_qty", 0) or 0) > 0
                      else REASON_PROBE_FIRST)
        elif kind == PLAN_PROBE and state is not None and (
                getattr(state, "probe_order_id", "")
                or int(getattr(state, "probe_filled_qty", 0) or 0) > 0):
            reason = REASON_PROBE_EXISTS
        elif kind == PLAN_SWITCH and code and state is None:
            reason = REASON_PROBE_FIRST
        if reason is not None:
            plan = replace(plan, executable=False,
                           blocked_reasons=tuple(dict.fromkeys(
                               tuple(getattr(plan, "blocked_reasons", ()) or ())
                               + (reason,))))
            gates.append({"plan_id": getattr(plan, "plan_id", ""),
                          "plan_kind": kind, "code": code,
                          "reason": reason, "blocked": True,
                          "detail": "显式 probe_then_confirm 政策：先真实 "
                                    "probe 成交，再由独立确认加仓"})
        result.append(plan)
    return result, gates


__all__ = ["apply_entry_mode", "FULL_ENTRY_KINDS", "REASON_PROBE_FIRST"]
