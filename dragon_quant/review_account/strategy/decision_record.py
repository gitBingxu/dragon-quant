"""DecisionRecord / 执行审计的序列化（A4，窄门面）。

从 ``account_engine`` 拆出：只把一次决定的完整 ``DecisionBundle`` 与执行结果
规范化成可持久化 dict，不做 I/O、不撮合、不决策。``event`` 行在
``ledger.store`` 的同一事务里落账（幂等/冲突由事件表主键与哈希负责）。
"""

from __future__ import annotations

from typing import Mapping, Optional


def build_decision_record(decision, *, source: str, event_id: str,
                          event_time: str, day: str, timestamp: int,
                          decision_kwargs: Optional[Mapping] = None,
                          frozen_ref: Optional[Mapping] = None,
                          runtime_audit: Optional[Mapping] = None) -> dict:
    """完整决定审计：全部方案/拒绝/受限原因 + cutoff/冻结引用 + 意图。

    无决定返回 ``{}``（不伪造决定）；``bundle`` 为 ``None`` 的测试适配器仍记录
    实际意图，避免把未建模当无审计。``runtime_audit`` 保存**本事件实际消费**
    的趋势/持有期审计（适配器的原始输入 → 决策证据可追溯）。
    """
    if decision is None:
        return {}
    kwargs = dict(decision_kwargs or {})
    snapshot = kwargs.get("snapshot")
    bundle = decision.bundle
    from dragon_quant.review_account.evidence import thaw

    return {
        "record_version": 1, "event_id": event_id,
        "runtime_audit": dict(runtime_audit or {}),
        "decision_time": (getattr(bundle, "decision_time", None)
                          or kwargs.get("decision_time") or event_time),
        "trade_date": day, "event_ts": int(timestamp),
        "source_hash": source,
        "cutoff": kwargs.get("cutoff") or kwargs.get("decision_time") or event_time,
        "snapshot_id": (getattr(bundle, "snapshot_id", None)
                        or getattr(snapshot, "snapshot_id", None)),
        "config_hash": getattr(bundle, "config_hash", None),
        "policy_version": getattr(bundle, "policy_version", ""),
        "frozen_ref": thaw(dict(frozen_ref or {})),
        "bundle": bundle.to_dict() if bundle is not None else None,
        "intents": [
            {"action": intent.action, "code": intent.code,
             "qty": intent.qty, "plan_id": intent.plan_id,
             "obligation_id": intent.obligation_id,
             "owner": thaw(dict(intent.strategy_ref or {})),
             "batch_ids": list(intent.batch_ids or ()),
             "trigger_kind": intent.trigger_kind,
             "trigger_price": intent.trigger_price,
             "evidence_rule": getattr(intent, "evidence_rule", ""),
             "evidence_ids": list(getattr(intent, "evidence_ids", ()) or ()),
             "depends_on": list(getattr(intent, "depends_on", ()) or ()),
             "sequence": int(getattr(intent, "sequence", 0) or 0),
             "invalidate_below": getattr(intent, "invalidate_below", None),
             "family_id": getattr(intent, "family_id", "")}
            for intent in tuple(getattr(decision, "intents", ()) or ())],
    }


def build_execution_record(*, result, orders, mark_facts, batch_facts,
                           canceled, revocations=(), day: str,
                           timestamp: int) -> dict:
    """事件执行审计：订单/成交关联、撤单、未成交/拒绝原因与事实快照。"""
    touched = set(result.created) | set(canceled)
    touched.update(fill.order_id for fill in result.fills)
    associated = []
    for order in sorted(orders, key=lambda item: item.order_id):
        if order.order_id not in touched:
            continue
        associated.append({
            "order_id": order.order_id, "code": order.code,
            "side": order.side, "qty": order.qty,
            "filled_qty": order.filled_qty, "status": order.status,
            "plan_id": order.plan_id, "obligation_id": order.obligation_id,
            "batch_ids": list(order.batch_ids or ()),
            "reserved_amount": order.reserved_amount,
            "reserved_risk": order.reserved_risk})
    return {
        "record_version": 1, "event_ts": int(timestamp),
        "trade_date": day,
        "fills": [fill.to_row() for fill in result.fills],
        "orders": associated, "canceled": list(canceled),
        "revocations": [dict(item) for item in (revocations or ())],
        "matches": [dict(item) for item in result.matches],
        "rejected": [dict(item) for item in result.rejected],
        "mark_facts": {code: fact.to_row()
                       for code, fact in (mark_facts or {}).items()},
        "batch_facts": {batch_id: fact.to_row()
                        for batch_id, fact in (batch_facts or {}).items()},
    }


__all__ = ["build_decision_record", "build_execution_record"]
