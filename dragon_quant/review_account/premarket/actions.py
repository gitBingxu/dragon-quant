"""premarket.actions — 行动上限过滤链（Specification / 轻量谓词链）。

只做既有枚举的**交集**：不新造动作、不预测收益、不输出买卖指令。判定顺序：

1. prep 不可用 / 版本不支持 / review 不可用 → ``observe_only``；
2. ``eligibility != "eligible"`` → ``no_new_position``；
3. 冻结预期缺失（含 ``role=null`` 诊断标的）→ ``observe_only``；
4. ``position_pct is None`` → ``observe_only``；``0`` → ``no_new_position``
   （明确不新建，**不是清仓**）；
5. 无可用 auction 行 → ``observe_only``；
6. ``is_weak`` / ``status="vetoed"`` → ``no_new_position``；
7. auction ``data_quality != "ok"`` → ``observe_only``；
8. 全部通过 → ``wait``（09:30 前不宣布 confirmed）。

第 3 步先于第 4 步：产品方案明确「``expectations`` 缺失（含 role=null 诊断标的）
→ observe_only」，避免把「无法给出计划」误写成「明确不参与」。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

STATUS_WAIT = "wait"
STATUS_OBSERVE = "observe_only"
STATUS_NO_NEW = "no_new_position"

STATES = ("confirmed", "weakened", "invalidated", "unknown")

#: 既有动作枚举全集（与 ``prep.review.feedback.STATE_ACTIONS`` 一致）。
ACTION_VALUES = frozenset({
    "execute_plan", "execute_partial", "downgrade_half", "wait_pullback",
    "cancel_entry", "exit_via_sell_rules", "observe_only", "wait",
})

POSITION_EXIT = "existing_sell_rules_only"
WAIT_REQUIRES = ["open_verification", "external_evidence_check", "account_gate"]


def _observe_states() -> dict[str, list[str]]:
    return {state: [STATUS_OBSERVE] for state in STATES}


def normalize_after_verification(value: Any) -> dict[str, list[str]]:
    """冻结四态映射：缺失 / 空集合的 state 统一为 ``observe_only``。"""

    source = value if isinstance(value, Mapping) else {}
    result: dict[str, list[str]] = {}
    for state in STATES:
        actions = source.get(state)
        cleaned = [action for action in actions
                   if isinstance(action, str) and action in ACTION_VALUES] \
            if isinstance(actions, list) else []
        result[state] = cleaned or [STATUS_OBSERVE]
    return result


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _ceiling(status: str, blocked_by: list[str], *,
             after_verification: Any) -> dict:
    wait = status == STATUS_WAIT
    return {
        "status": status,
        "allowed_now": ["wait"] if wait else [STATUS_OBSERVE],
        "blocked_by": list(blocked_by),
        "after_verification": (normalize_after_verification(after_verification)
                               if wait else _observe_states()),
        "requires": list(WAIT_REQUIRES) if wait else [],
        "position_exit": POSITION_EXIT,
    }


def build_ceiling(
    *,
    prep_usable: bool,
    prep_reason: Optional[str],
    eligibility: Any,
    position_pct: Any,
    expectations_present: bool,
    auction_present: bool,
    auction_usable: bool,
    auction_status: Optional[str],
    is_weak: bool,
    reject_reason: Optional[str],
    auction_quality: Optional[str],
    after_verification: Any,
) -> dict:
    """按固定顺序计算单只候选的行动上限（纯函数）。"""

    if not prep_usable:
        return _ceiling(STATUS_OBSERVE, [prep_reason or "prep_unavailable"],
                        after_verification=after_verification)

    if eligibility != "eligible":
        value = eligibility if isinstance(eligibility, str) and eligibility \
            else "unknown"
        return _ceiling(STATUS_NO_NEW, [f"eligibility_{value}"],
                        after_verification=after_verification)

    if not expectations_present:
        return _ceiling(STATUS_OBSERVE, ["expectations_unavailable"],
                        after_verification=after_verification)

    position = _number(position_pct)
    if position is None:
        return _ceiling(STATUS_OBSERVE, ["position_pct_null"],
                        after_verification=after_verification)
    if position <= 0:
        return _ceiling(STATUS_NO_NEW, ["position_pct_zero"],
                        after_verification=after_verification)

    if (not auction_present or not auction_usable
            or auction_status in (None, "unavailable", "empty", "failed",
                                  "unknown")):
        return _ceiling(STATUS_OBSERVE, ["auction_unavailable"],
                        after_verification=after_verification)

    if is_weak or auction_status == "vetoed":
        reason = reject_reason if isinstance(reject_reason, str) and reject_reason \
            else "weak"
        return _ceiling(STATUS_NO_NEW, [f"auction_veto:{reason}"],
                        after_verification=after_verification)

    if auction_quality != "ok":
        return _ceiling(STATUS_OBSERVE, ["auction_data_quality_degraded"],
                        after_verification=after_verification)

    return _ceiling(STATUS_WAIT, [], after_verification=after_verification)


__all__ = [
    "ACTION_VALUES",
    "POSITION_EXIT",
    "STATES",
    "STATUS_NO_NEW",
    "STATUS_OBSERVE",
    "STATUS_WAIT",
    "build_ceiling",
    "normalize_after_verification",
]
