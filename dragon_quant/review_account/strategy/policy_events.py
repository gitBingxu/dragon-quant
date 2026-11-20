"""账户政策事件的共享适配（W3 引擎 / W2 场景政策链共用一份）。

只做**适配与共享 Reducer**，不复制政策判断或撮合：

- 已提交政策/恢复事实 → 运行时输入（显式只读输入不得静默覆盖）；
- 意图 → 共享 ``TransitionCommand``（保留 trigger/evidence/depends/有效期/家族）；
- 本事件不再需要的未成交买单显式撤单（释放预留）；
- 真实成交回报 → 推进 ``PositionPolicyState``（probe→add 家族/次数/确认消费）。

两个适配器因此消费同一政策输入、同一命令结构与同一回报 Reducer。
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from dragon_quant.review_account.execution_core.identity import ref_identity
from dragon_quant.review_account.execution_core.models import LedgerError
from dragon_quant.review_account.execution_core.transition import TransitionCommand

from dragon_quant.review_account.strategy.bs_policy.policy_states import (
    advance_policy_states, family_key, states_from_rows, states_to_rows,
)
from dragon_quant.review_account.strategy.bs_policy.scenario_contracts import (
    RecoveryState,
)


def merge_policy_states(committed, explicit):
    """已提交状态 + 显式只读输入；同身份冲突显式失败（不静默覆盖）。"""
    if not explicit:
        return tuple(committed)
    index = {family_key(item): item for item in committed}
    for item in explicit:
        key = family_key(item)
        prior = index.get(key)
        if prior is not None and prior.to_dict() != item.to_dict():
            raise LedgerError("显式只读政策状态与已提交真实状态冲突，拒绝静默覆盖")
        index[key] = item
    return tuple(index[key] for key in sorted(index, key=repr))


def _recovery_identity(state) -> dict:
    """恢复状态的**权威**字段（legacy ``conditions_met`` 仅展示，不参与冲突）。

    触发时点派生的收缩快照（``contraction_targets``/``target_snapshot_trade_date``）
    由内核生成，不要求调用方复刻，也不作为显式输入冲突判据。
    """
    data = state.to_dict()
    data.pop("conditions_met", None)
    data.pop("contraction_targets", None)
    data.pop("target_snapshot_trade_date", None)
    return data


def seed_committed_policy_inputs(state, kwargs: dict, *, recovery=None) -> None:
    """把已提交政策/恢复事实喂给运行时；显式输入不得静默覆盖真实状态。"""
    committed = states_from_rows(getattr(state, "policy_states", None) or ())
    explicit = kwargs.pop("policy_states", None)
    kwargs["policy_states"] = merge_policy_states(committed, explicit)
    committed_recovery = recovery
    if committed_recovery is None and getattr(state, "recovery", None):
        committed_recovery = RecoveryState.from_row(state.recovery)
    if committed_recovery is None:
        return
    provided = kwargs.get("recovery")
    if provided is not None:
        if isinstance(provided, Mapping):
            provided = RecoveryState.from_row(provided)
        if _recovery_identity(provided) != _recovery_identity(committed_recovery):
            raise LedgerError("显式只读恢复状态与已提交真实状态冲突，拒绝静默覆盖")
    kwargs["recovery"] = committed_recovery


def advance_state_policy_states(state, *, fills, event_time_iso: str,
                                event_ts: int, version: str = "") -> None:
    committed = states_from_rows(getattr(state, "policy_states", None) or ())
    advanced = advance_policy_states(
        committed, fills=fills, orders=state.orders,
        event_time_iso=event_time_iso, event_ts=event_ts, version=version)
    state.policy_states = states_to_rows(advanced)


def command_from_intent(intent) -> TransitionCommand:
    return TransitionCommand(
        action=intent.action, code=intent.code, qty=intent.qty,
        owner=intent.strategy_ref, plan_id=intent.plan_id,
        obligation_id=intent.obligation_id, limit_price=intent.limit_price,
        trigger_kind=intent.trigger_kind, trigger_price=intent.trigger_price,
        evidence_rule=intent.evidence_rule,
        evidence_ids=tuple(intent.evidence_ids or ()),
        depends_on=tuple(intent.depends_on or ()),
        sequence=int(intent.sequence or 0),
        valid_until_event=intent.valid_until_event,
        risk_amount=float(intent.risk_amount or 0.0),
        risk_themes=tuple(intent.risk_themes or ()),
        family_id=str(getattr(intent, "family_id", "") or ""),
        pressure_pct=getattr(intent, "pressure_pct", None),
        invalidate_below=getattr(intent, "invalidate_below", None),
        batch_ids=tuple(intent.batch_ids or ()))


def _desired_key(code: str, action: str, plan_id: str, owner: Mapping,
                 batch_ids: Sequence[str]) -> tuple:
    return (code, action, plan_id, ref_identity(owner),
            tuple(batch_ids or ()))


def cancel_stale_buys(state, intents: Iterable) -> list[str]:
    """本事件政策不再声明、且非终结的买单显式撤单并释放预留。"""
    from dragon_quant.review_account.ledger.matching import _released

    desired = {_desired_key(intent.code, intent.action, intent.plan_id,
                            intent.strategy_ref, intent.batch_ids or ())
               for intent in intents}
    canceled: list[str] = []
    for order in list(state.orders):
        if order.side != "buy" or order.is_terminal:
            continue
        # 已授权的 pending 目标继续执行，不在下一次真实决策时因「本事件未再声明」
        # 被误撤：已部分成交的原单剩余目标保留到**失效/到期/显式撤销**（到期由
        # transition._expire 按 valid_until_event 取消）。这避免已消费的确认证据
        # 反向使同一单剩余失效；也不会新建第二张单（_has_open_order 去重）。
        if order.filled_qty > 0:
            continue
        key = _desired_key(order.code, "buy", order.plan_id,
                           order.strategy_ref, order.batch_ids or ())
        if key in desired:
            continue
        state.orders = [
            (order.with_status(order.status if order.is_terminal
                               else "cancelled", locked_qty=0)
             if item.order_id == order.order_id else item)
            for item in state.orders]
        state.reservations = [
            _released(item) if item.order_id == order.order_id else item
            for item in state.reservations]
        canceled.append(order.order_id)
    return canceled


def authorization_revocations(state, *, prices: Mapping, market_allowed,
                              envelope_halted) -> list[dict]:
    """按本事件 PIT 事实判定**明确授权撤销**（与「合法余量继续」分开）。

    只撤销**未成交新风险余量**，返回带 order_id/scope/原因 的撤销集合：

    - 明确 ``envelope_halted is True`` 或 ``market_allowed is False`` → 撤所有
      开着的买单余量（明确禁止新增风险，不依赖价格）；
    - 否则若订单冻结了价格失效线且本事件已知价格低于该线 → ``price_invalidation``。
    - 价格/授权**未知**（``None``）不推断失效（保守），不编造撤销。

    仅针对买腿；卖出/减仓保护与已持仓不受影响，也不倒销真实 fill。
    """
    revocations: list[dict] = []
    for order in getattr(state, "orders", ()) or ():
        if getattr(order, "side", "") != "buy" or order.is_terminal:
            continue
        remaining = int(order.quantity_remaining)
        if remaining <= 0:
            continue
        reason = ""
        if envelope_halted is True:
            reason = "risk_envelope_halted"
        elif market_allowed is False:
            reason = "market_new_risk_blocked"
        else:
            line = getattr(order, "invalidate_below", None)
            price = (prices or {}).get(order.code)
            if line is not None and price is not None \
                    and float(price) < float(line):
                reason = "price_invalidation"
        if not reason:
            continue
        revocations.append({
            "order_id": order.order_id, "code": order.code,
            "plan_id": order.plan_id, "family_id": order.family_id,
            "batch_ids": list(order.batch_ids or ()),
            "remaining_qty": remaining, "reason": reason})
    return revocations


def contraction_revocations(state, *, recovery: Mapping,
                            event_ts: int) -> list[dict]:
    """收缩期内撤销**触发前已存在**的买单未成余量（剩余同属新增风险）。

    - 只有本次决策仍在收缩且授权 scale < 1.0 时才撤；
    - 触发事件当次由本决策新建的买单（created_event == 触发事件）按新授权
      合法，保留；触发前已存在的 partial/未成买单余量撤单、释放预留；
    - 旧 episode 缺触发锚时 fail closed：按本事件为界撤掉先存买单余量。
    """
    if not recovery.get("active"):
        return []
    scale = recovery.get("scale")
    if scale is None or float(scale) >= 1.0 - 1e-9:
        return []
    anchor = recovery.get("trigger_event")
    cutoff = int(anchor) if anchor is not None else int(event_ts)
    revocations: list[dict] = []
    for order in getattr(state, "orders", ()) or ():
        if getattr(order, "side", "") != "buy" or order.is_terminal:
            continue
        remaining = int(order.quantity_remaining)
        if remaining <= 0:
            continue
        if int(order.created_event) >= cutoff:
            continue
        revocations.append({
            "order_id": order.order_id, "code": order.code,
            "plan_id": order.plan_id, "family_id": order.family_id,
            "batch_ids": list(order.batch_ids or ()),
            "remaining_qty": remaining,
            "reason": "contraction_new_risk_revoked"})
    return revocations


def apply_revocations(state, revocations: Iterable[Mapping]) -> list[str]:
    """执行明确撤销：撤未成余量、释放预留、保留已成交；返回被撤 order_id。"""
    from dragon_quant.review_account.ledger.matching import _released

    reasons = {str(item["order_id"]): str(item.get("reason", ""))
               for item in revocations or ()}
    canceled: list[str] = []
    for order in list(getattr(state, "orders", ()) or ()):
        if order.order_id not in reasons or order.is_terminal:
            continue
        state.orders = [
            (order.with_status("cancelled", locked_qty=0,
                               reserved_amount=0.0, reserved_risk=0.0)
             if item.order_id == order.order_id else item)
            for item in state.orders]
        state.reservations = [
            _released(item) if item.order_id == order.order_id else item
            for item in state.reservations]
        canceled.append(order.order_id)
    return canceled


__all__ = [
    "advance_state_policy_states", "apply_revocations",
    "authorization_revocations", "cancel_stale_buys", "command_from_intent",
    "contraction_revocations", "merge_policy_states",
    "seed_committed_policy_inputs",
]
