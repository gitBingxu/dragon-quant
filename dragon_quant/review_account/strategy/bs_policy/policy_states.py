"""G3 真实试探/加仓政策状态的推进与投影（纯叶子，W2/W3 共用）。

状态只由**真实成交**创建/推进：发出意图、拒单、零容量、未成交都不产生或消耗
次数。``probe`` 计划的首笔真实买成交建立状态；``add`` 计划的**每张订单首次**
真实成交才把 ``adds_done`` 加一并消费该订单的独立确认，之后的同单部分成交只
累计真实量、不重复计数（same-entry 部分成交只计一次）。

查找/归并身份是完整 ``owner + code + family``（family 默认为试探计划 ID），
不是只按 code 取第一条；同 code 不同 owner/独立 family 互不串。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Optional

from .base import PLAN_ADD, PLAN_PROBE
from .scenario_contracts import PositionPolicyState

_CST = timezone(timedelta(hours=8))


def _iso(timestamp_ms: Optional[int]) -> str:
    if timestamp_ms is None:
        return ""
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=_CST).isoformat()


def plan_kind(plan_id: str) -> str:
    """计划 ID 的类别前缀（``probe:...`` → ``probe``）。"""
    if not isinstance(plan_id, str) or not plan_id:
        return ""
    return plan_id.split(":", 1)[0]


def owner_identity(ref) -> tuple:
    from dragon_quant.review_account.execution_core.identity import ref_identity

    return ref_identity(dict(ref or {}))


def family_key(state: PositionPolicyState) -> tuple:
    """完整归并身份：冻结 owner + code + family（默认为试探计划 ID）。"""
    return (owner_identity(state.owner), state.code,
            state.family_id or state.probe_plan_id)


def states_from_rows(rows: Iterable[Mapping]) -> tuple[PositionPolicyState, ...]:
    """从已提交的行读回（坏行显式失败，不静默丢弃）。"""
    return tuple(PositionPolicyState.from_row(row) for row in rows or ())


def states_to_rows(states: Iterable[PositionPolicyState]) -> list[dict]:
    return [state.to_dict() for state in states or ()]


def _index(states: Iterable[PositionPolicyState]
           ) -> dict[tuple, PositionPolicyState]:
    index: dict[tuple, PositionPolicyState] = {}
    for state in states or ():
        index.setdefault(family_key(state), state)
    return index


def _probe_evidence(order) -> tuple[str, ...]:
    ids = tuple(getattr(order, "evidence_ids", ()) or ())
    if ids:
        return ids
    rule = str(getattr(order, "evidence_rule", "") or "")
    return (rule,) if rule else ()


def _new_probe(order, *, event_time_iso: str, event_ts: int,
               version: str) -> PositionPolicyState:
    return PositionPolicyState(
        code=order.code, probe_plan_id=order.plan_id,
        probe_filled_at=event_time_iso,
        probe_evidence_ids=_probe_evidence(order), adds_done=0,
        version=version, owner=dict(order.strategy_ref or {}),
        family_id=order.plan_id, probe_order_id=order.order_id,
        probe_filled_qty=0, cumulative_filled_qty=0, add_filled_qty=0,
        consumed_orders=(), pending_target_qty=int(order.qty or 0),
        expires_at=_iso(getattr(order, "valid_until_event", None)))


def advance_policy_states(states: Iterable[PositionPolicyState], *,
                          fills: Iterable, orders: Iterable,
                          event_time_iso: str, event_ts: int,
                          version: str = ""
                          ) -> tuple[PositionPolicyState, ...]:
    """用**真实成交回报**推进 policy state；只由 probe/add 买单驱动。

    - probe 首笔成交创建状态（保留首次成交时点、原证据身份、累计真实量）；
    - add 订单首次成交加一 ``adds_done`` 并消费该订单独立确认证据，同单后续
      部分成交不重复计数；``partial probe`` 只按真实成交量记录，不虚增计划量；
    - 未成交/拒单/零容量不会出现在 ``fills``，因此不消耗次数。
    """
    index = _index(states)
    by_order = {str(getattr(order, "order_id", "")): order
                for order in orders or ()}
    for fill in fills or ():
        if str(getattr(fill, "side", "")) != "buy":
            continue
        order = by_order.get(str(getattr(fill, "order_id", "")))
        if order is None:
            continue
        kind = plan_kind(getattr(order, "plan_id", ""))
        qty = int(getattr(fill, "qty", 0) or 0)
        if qty <= 0:
            continue
        if kind == PLAN_PROBE:
            key = (owner_identity(order.strategy_ref), order.code, order.plan_id)
            state = index.get(key) or _new_probe(
                order, event_time_iso=event_time_iso, event_ts=event_ts,
                version=version)
            state = replace(
                state,
                probe_filled_qty=state.probe_filled_qty + qty,
                cumulative_filled_qty=state.cumulative_filled_qty + qty)
            index[key] = state
            continue
        if kind != PLAN_ADD:
            continue
        target_key = _bound_probe_key(index, order)
        if target_key is None:
            continue                       # 未绑定真实试探父腿的 add 不推进次数
        state = index[target_key]
        consumed = tuple(state.consumed_orders)
        adds = int(state.adds_done)
        confirmations = tuple(state.confirmations_used)
        if order.order_id not in consumed:
            consumed = consumed + (order.order_id,)
            adds += 1
            confirmations = confirmations + _probe_evidence(order)
        index[target_key] = replace(
            state,
            add_filled_qty=state.add_filled_qty + qty,
            cumulative_filled_qty=state.cumulative_filled_qty + qty,
            adds_done=adds, consumed_orders=consumed,
            confirmations_used=confirmations)
    return tuple(index[key] for key in sorted(index, key=repr))


def _bound_probe_key(index: Mapping[tuple, PositionPolicyState],
                     order) -> Optional[tuple]:
    """用 add 订单的显式父腿身份（``depends_on`` = 真实 probe order id）绑定。"""
    deps = set(getattr(order, "depends_on", ()) or ())
    if not deps:
        return None
    for key, state in index.items():
        if state.probe_order_id and state.probe_order_id in deps:
            return key
    return None


__all__ = [
    "advance_policy_states", "family_key", "plan_kind", "owner_identity",
    "states_from_rows", "states_to_rows",
]
