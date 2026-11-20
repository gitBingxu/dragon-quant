"""BS 显式运行的持久化与有界只读查询（A4）。

复用 ``AccountLedger`` / ``AccountEngine`` / 共享事件处理器：初始账户以一条
``seed`` 事件原子落账（batches/orders/reservations/恢复/标记事实），随后逐事件
CAS 提交。``ledger_id`` 由**完整初始账户 + 策略 hash + 快照 + 区间 + 事件摘要**
决定：同输入幂等、不同初始资金/账户/事件独立；不会互相覆盖。advice 仍只读，
写只发生在显式 review 运行。
"""

from __future__ import annotations

import dataclasses
from typing import Mapping, Optional

from dragon_quant.review_account.execution_core.matching import ExecutionCostModel
from dragon_quant.review_account.ledger.store import AccountLedger
from dragon_quant.review_account.strategy.account_engine import AccountEngine
from dragon_quant.review_account.strategy.account_runtime import (
    AccountPolicyRuntime, build_account_runtime,
)
from dragon_quant.review_account.strategy.bs_policy.scenario_execution import (
    seed_ledger_state,
)
from dragon_quant.review_account.strategy.event_inputs import (
    canonical_input, hash_payload,
)
from dragon_quant.review_account.strategy.policy_replay import state_snapshot

BS_RUN_SCHEMA_VERSION = "bs_run/1"


class BsRunError(ValueError):
    def __init__(self, message: str, *, code: str = "bs_run_error") -> None:
        super().__init__(message)
        self.code = code


def _jsonable(value):
    """把引擎返回值规范化为 JSON 可序列化结构（bundle/intent 等值对象）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    return str(value)


def _events_digest(events) -> str:
    payload = []
    for event in events:
        payload.append({
            "timestamp": int(event.timestamp),
            "trade_date": event.trade_date,
            "rows": canonical_input(dict(event.rows or {})),
            "candidates": [candidate.code for candidate in
                           tuple(event.candidates or ())],
            "decide": bool(event.decide),
            "decision_inputs": canonical_input(dict(event.decision_kwargs or {})),
            "capacity": canonical_input(dict(event.capacity_override or {})),
        })
    return hash_payload(payload)


def run_ledger_id(*, cash: float, strategy_ref: Mapping, config_hash: str,
                  snapshot_id: str, date_from: str, date_to: str,
                  seed_state, events, calendar=(), cost_model=None,
                  max_quote_age_ms=None, order_ttl_ms=None,
                  allow_unknown_risk: bool = False) -> str:
    batches = [batch.to_row() for batch in sorted(seed_state.batches,
                                                  key=lambda item: item.batch_id)]
    orders = [order.to_row() for order in sorted(seed_state.orders,
                                                 key=lambda item: item.order_id)]
    reservations = [item.to_row() for item in sorted(
        seed_state.reservations, key=lambda item: item.order_id)]
    digest = hash_payload({
        "schema": BS_RUN_SCHEMA_VERSION,
        "cash": float(cash), "strategy_ref": canonical_input(dict(strategy_ref)),
        "config_hash": config_hash, "snapshot_id": snapshot_id,
        "date_from": date_from, "date_to": date_to,
        "batches": canonical_input(batches), "orders": canonical_input(orders),
        "reservations": canonical_input(reservations),
        "recovery": canonical_input(dict(seed_state.recovery or {})),
        "marks": canonical_input(dict(seed_state.marks or {})),
        "policy_states": canonical_input(list(seed_state.policy_states or ())),
        "batch_facts": canonical_input({
            key: (value.to_row() if hasattr(value, "to_row") else value)
            for key, value in (seed_state.batch_facts or {}).items()}),
        "fund_credits": canonical_input(dict(seed_state.fund_credits or {})),
        "calendar": list(calendar or ()),
        "cost_model": canonical_input({
            "commission_rate": getattr(cost_model, "commission_rate", None),
            "min_commission": getattr(cost_model, "min_commission", None),
            "stamp_tax_rate": getattr(cost_model, "stamp_tax_rate", None),
            "buy_slippage": getattr(cost_model, "buy_slippage", None),
            "sell_slippage": getattr(cost_model, "sell_slippage", None),
            "lot_size": getattr(cost_model, "lot_size", None)}),
        "max_quote_age_ms": max_quote_age_ms,
        "order_ttl_ms": order_ttl_ms,
        "allow_unknown_risk": bool(allow_unknown_risk),
        "source_hash_version": "c3b3/1",
        "execution_impl": "bs_execution/1",
        "events": _events_digest(events),
    })
    return f"bsrun_{digest[:24]}"


def _seed_payload(seed_state) -> str:
    return hash_payload({
        "batches": [batch.to_row() for batch in seed_state.batches],
        "orders": [order.to_row() for order in seed_state.orders],
        "reservations": [item.to_row() for item in seed_state.reservations],
        "recovery": dict(seed_state.recovery or {}),
        "marks": dict(seed_state.marks or {}),
        "fund_credits": dict(seed_state.fund_credits or {}),
    })


def persist_run(*, account, policy, ref: Mapping, snapshot_id: str,
                events, date_from: str, date_to: str,
                cost_model: ExecutionCostModel, calendar,
                max_quote_age_ms: Optional[int] = None,
                order_ttl_ms: Optional[int] = 86_400_000,
                risk_caps=None, allow_unknown_risk: bool = False,
                controls=None) -> dict:
    """显式 review 运行：seed + 逐事件 CAS 落账；返回稳定 run/ledger 视图。"""
    from dragon_quant.review_account.models import StrategyConfig

    if account.account.cash is None:
        raise BsRunError("账户现金未知，不能持久化运行", code="account_cash_unknown")
    seed = seed_ledger_state(
        account.account, asof=date_from, recovery=account.recovery,
        policy_states=account.policy_states,
        pending_orders=account.pending_orders,
        reservations=account.core_reservations,
        mark_facts=account.mark_facts, batch_facts=account.batch_facts,
        fund_credits=account.fund_credits)
    seed.trade_date = date_from
    seed.config_hash = policy.content_hash
    seed.snapshot_id = snapshot_id
    seed.strategy_ref = ref
    ledger_id = run_ledger_id(
        cash=float(account.account.cash), strategy_ref=ref,
        config_hash=policy.content_hash, snapshot_id=snapshot_id,
        date_from=date_from, date_to=date_to, seed_state=seed, events=events,
        calendar=calendar, cost_model=cost_model,
        max_quote_age_ms=max_quote_age_ms, order_ttl_ms=order_ttl_ms,
        allow_unknown_risk=allow_unknown_risk)
    ledger = AccountLedger(ledger_id)
    ledger.create(initial_cash=float(account.account.cash),
                  strategy_ref=ref, config_hash=policy.content_hash,
                  snapshot_id=snapshot_id, status="running",
                  expected_events=len(events))
    state = ledger.load_state()
    if state is None:
        raise BsRunError(f"账本 {ledger_id} 创建后不可读", code="ledger_unreadable")
    fresh_run = state.revision == 0
    try:
        if fresh_run:
            seed_hash = _seed_payload(seed)
            ledger.commit_event(
                expected_revision=0, event_id=f"seed:{ledger_id}",
                source_hash=seed_hash, content_hash=seed_hash, state=seed)
    except Exception as exc:
        ledger.update_run_status(status="failed", completed_events=0,
                                 watermark=-1, failure_reason=str(exc),
                                 failed_cursor=-1)
        raise
    try:
        runtime: AccountPolicyRuntime = build_account_runtime(
            ref, controls=controls or StrategyConfig())
        engine = AccountEngine(
            ledger, runtime, cost_model, calendar=tuple(calendar),
            max_quote_age_ms=max_quote_age_ms, order_ttl_ms=order_ttl_ms,
            risk_caps=risk_caps, allow_unknown_risk=allow_unknown_risk)
        outcomes = []
        prior = ledger.run_status() or {}
        completed = int(prior.get("completed_events") or 0)
        last_cursor = int(prior.get("watermark") or -1)
        for index, event in enumerate(events):
            outcome = engine.process_event(
                event, decide=event.decide, candidates=event.candidates,
                decision_kwargs=event.decision_kwargs,
                capacity_override=event.capacity_override)
            outcomes.append(outcome)
            last_cursor = int(event.timestamp)
            if outcome.get("status") != "already_processed":
                completed += 1
            # 提交成功但状态更新崩溃：按已处理事件序号推进，续跑不丢进度。
            completed = max(completed, index + 1)
            ledger.update_run_status(completed_events=completed,
                                     watermark=last_cursor)
        final = ledger.load_state()
        ledger.update_run_status(status="completed",
                                 completed_events=completed,
                                 watermark=last_cursor,
                                 failure_reason="", failed_cursor=-1)
        run_status = ledger.run_status()
    except Exception as exc:
        # 失败保留可审计 partial（逐事件 CAS 已提交），生命周期明确 failed。
        ledger.update_run_status(
            status="failed", failure_reason=f"{type(exc).__name__}: {exc}",
            failed_cursor=last_cursor if 'last_cursor' in locals() else -1)
        raise
    finally:
        if 'runtime' in locals():
            runtime.close()
    if final is None:
        raise BsRunError(f"账本 {ledger_id} 提交后不可读", code="ledger_unreadable")
    view = state_snapshot(final, final.trade_date)
    fills = ledger.query_fills()
    from .advice import origin_batch_id

    for row in fills:
        row["orig_batch_id"] = origin_batch_id(str(row.get("batch_id") or ""))
    return {
        "schema_version": BS_RUN_SCHEMA_VERSION,
        "ledger_id": ledger_id, "mode": "persisted",
        "persisted": True, "orders_placed": False,
        "revision": int(final.revision), "run_status": run_status,
        "date_from": date_from, "date_to": date_to,
        "policy": policy.to_dict(), "snapshot_id": snapshot_id,
        "events": [_jsonable(item) for item in outcomes],
        "final_state": view,
        "orders": ledger.query_orders(),
        "fills": fills,
        "batches": ledger.query_batches(),
    }


def read_run(ledger_id: str, *, limit: int = 100,
             cursor: Optional[str] = None) -> Optional[dict]:
    """有界只读读回（跨重启可查）；不存在返回 None，不建库/不迁移。"""
    if not ledger_id:
        raise BsRunError("需要 ledger_id", code="invalid_request")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise BsRunError("limit 必须是 1..500 的整数", code="invalid_request")
    ledger = AccountLedger(ledger_id)
    state = ledger.load_state()
    if state is None:
        return None
    try:
        page = ledger.read_history(limit=limit, cursor=cursor)
    except Exception as exc:  # noqa: BLE001 - 坏 cursor 显式拒绝
        raise BsRunError(f"cursor 无效：{exc}", code="invalid_cursor") from exc
    return {
        "schema_version": BS_RUN_SCHEMA_VERSION,
        "ledger_id": ledger_id, "persisted": True,
        "run_status": ledger.run_status(),
        "revision": int(state.revision),
        "final_state": state_snapshot(state, state.trade_date),
        "orders": ledger.query_orders(),
        "fills": ledger.query_fills(),
        "batches": ledger.query_batches(),
        "history": {
            "records": [record.to_dict() for record in page.records],
            "next_cursor": page.next_cursor, "has_more": page.has_more,
        },
    }


__all__ = [
    "BS_RUN_SCHEMA_VERSION", "BsRunError", "run_ledger_id", "persist_run",
    "read_run",
]
