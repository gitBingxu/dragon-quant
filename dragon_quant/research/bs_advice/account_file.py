"""账户文件 schema（v1）：只读账户/批次/预留/运行事实的显式输入。

``research bs-advice`` 与 review-account BS 分支消费同一份账户文件，避免 W4b 再
拼第二套解析。纪律：

- 货币固定 CNY、股数单位固定「股/share」，错单位/NaN/未来 marks/异配置哈希显式
  拒绝，不静默换算或补齐；
- 批次保留用户原始 ``batch_id``、完整 ``strategy_ref``/owner/plan/family 与
  ``initial_risk``；缺成本/入场日等事实不伪造，只把该批次标为受限（不猜空仓全卖）；
- 未知现金保留为 ``None``（受限，不伪造 0 余额），由上层阻止依赖现金的动作。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Optional

from dragon_quant.review_account.evidence import thaw
from dragon_quant.review_account.execution_core.models import (
    LedgerError, Order, Reservation as CoreReservation,
)
from dragon_quant.review_account.execution_core.runtime_facts import (
    BatchFacts, MarkFact,
)
from dragon_quant.review_account.strategy.bs_policy import (
    AccountView, BatchView, PositionView, Reservation,
)
from dragon_quant.review_account.strategy.bs_policy.scenario_contracts import (
    PositionPolicyState, RecoveryState,
)

ACCOUNT_FILE_SCHEMA_VERSION = 1
CURRENCY = "CNY"
SHARE_UNITS = frozenset({"share", "股", "shares"})

_ALLOWED_TOP = frozenset({
    "schema_version", "currency", "shares_unit", "as_of", "cash",
    "peak_equity", "drawdown_pct", "consecutive_losses", "positions",
    "reservations", "pending_orders", "recovery", "policy_states",
    "runtime_facts", "evidence", "watch_codes", "policy_ref", "notes",
})
_ALLOWED_POSITION = frozenset({
    "code", "name", "theme_ids", "pressure_pct", "trend", "held_days",
    "batches", "mark", "mark_source",
})
_ALLOWED_BATCH = frozenset({
    "batch_id", "qty", "sellable_qty", "entry_date", "cost_price",
    "sellable_date", "original_qty", "family_id", "initial_risk",
    "strategy_ref", "plan_id", "owner",
})
_ALLOWED_RESERVATION = frozenset({
    "order_id", "code", "side", "qty", "amount", "status", "purpose",
    "price", "theme_ids", "pressure_pct", "risk_amount", "valid_until_event",
})
_ALLOWED_ORDER = frozenset({
    "order_id", "code", "side", "qty", "status", "created_event",
    "filled_qty", "limit_price", "reserved_amount", "reserved_risk",
    "locked_qty", "valid_until_event", "trigger_kind", "trigger_price",
    "evidence_rule", "evidence_ids", "depends_on", "sequence", "risk_amount",
    "risk_themes", "strategy_ref", "plan_id", "obligation_id", "batch_ids",
    "family_id", "invalidate_below", "reason",
})
_ALLOWED_RUNTIME_FACTS = frozenset({
    "mark_facts", "batch_facts", "fund_credits", "mark_sources",
})


class AccountFileError(ValueError):
    def __init__(self, message: str, *, code: str = "account_file_invalid") -> None:
        super().__init__(message)
        self.code = code


def _reject_constant(token: str):
    raise AccountFileError(f"账户文件含非有限数值 {token}（不允许 NaN/Infinity）")


def _mapping(value, what: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise AccountFileError(f"{what} 必须是对象")
    return value


def _strict(payload: Mapping, allowed: frozenset, what: str) -> None:
    unknown = sorted(set(map(str, payload)) - allowed)
    if unknown:
        raise AccountFileError(f"{what} 含未知字段: {unknown}")


def _finite(value, what: str, *, optional: bool = False,
            low: Optional[float] = None) -> Optional[float]:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AccountFileError(f"{what} 必须是有限数值")
    number = float(value)
    if not math.isfinite(number):
        raise AccountFileError(f"{what} 不允许 NaN/Infinity")
    if low is not None and number < low:
        raise AccountFileError(f"{what} 不能小于 {low}")
    return number


def _int(value, what: str, *, optional: bool = False) -> Optional[int]:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AccountFileError(f"{what} 必须是非负整数")
    return value


def _str_list(value, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) for item in value):
        raise AccountFileError(f"{what} 必须是字符串数组")
    return tuple(value)


def _moment(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed


def _not_future(value: Optional[str], cutoff: Optional[datetime],
                what: str) -> None:
    if not value:
        return
    moment = _moment(value)
    if moment is None or moment.tzinfo is None:
        raise AccountFileError(f"{what} 必须是带时区的合法时点: {value!r}")
    if cutoff is not None and moment > cutoff:
        raise AccountFileError(f"{what} 晚于决策 cutoff（拒绝未来数据）: {value}")


def _core_order(row: Mapping) -> Order:
    payload = _mapping(row, "pending_orders[]")
    _strict(payload, _ALLOWED_ORDER, "pending_order")
    try:
        return Order(
            order_id=str(payload["order_id"]), code=str(payload["code"]),
            side=str(payload["side"]), qty=int(payload["qty"]),
            status=str(payload["status"]), created_event=int(payload["created_event"]),
            filled_qty=int(payload.get("filled_qty") or 0),
            limit_price=_finite(payload.get("limit_price"), "order.limit_price",
                                optional=True),
            reserved_amount=float(_finite(payload.get("reserved_amount", 0.0),
                                          "order.reserved_amount", low=0.0) or 0.0),
            reserved_risk=float(_finite(payload.get("reserved_risk", 0.0),
                                        "order.reserved_risk", low=0.0) or 0.0),
            locked_qty=int(payload.get("locked_qty") or 0),
            valid_until_event=(None if payload.get("valid_until_event") is None
                               else int(payload["valid_until_event"])),
            trigger_kind=str(payload.get("trigger_kind") or ""),
            trigger_price=_finite(payload.get("trigger_price"),
                                  "order.trigger_price", optional=True),
            evidence_rule=str(payload.get("evidence_rule") or ""),
            evidence_ids=tuple(payload.get("evidence_ids") or ()),
            depends_on=tuple(payload.get("depends_on") or ()),
            sequence=int(payload.get("sequence") or 0),
            risk_amount=float(_finite(payload.get("risk_amount", 0.0),
                                      "order.risk_amount", low=0.0) or 0.0),
            risk_themes=tuple(payload.get("risk_themes") or ()),
            strategy_ref=dict(_mapping(payload.get("strategy_ref") or {},
                                       "order.strategy_ref")),
            plan_id=str(payload.get("plan_id") or ""),
            obligation_id=str(payload.get("obligation_id") or ""),
            batch_ids=tuple(payload.get("batch_ids") or ()),
            family_id=str(payload.get("family_id") or ""),
            invalidate_below=_finite(payload.get("invalidate_below"),
                                     "order.invalidate_below", optional=True),
            reason=str(payload.get("reason") or ""))
    except (KeyError, TypeError, ValueError, LedgerError) as exc:
        raise AccountFileError(f"pending_order 非法：{exc}",
                               code="account_file_invalid") from exc


def _core_reservation(row: Mapping) -> tuple[CoreReservation, Reservation]:
    payload = _mapping(row, "reservations[]")
    _strict(payload, _ALLOWED_RESERVATION, "reservation")
    code = str(payload.get("code") or "")
    side = str(payload.get("side") or "")
    qty = int(_int(payload.get("qty"), "reservation.qty") or 0)
    price = _finite(payload.get("price"), "reservation.price", optional=True)
    amount = _finite(payload.get("amount"), "reservation.amount", optional=True)
    if amount is None:
        if price is None:
            raise AccountFileError("reservation 需要 amount 或 price")
        amount = price * qty
    try:
        core = CoreReservation(
            order_id=str(payload.get("order_id") or f"resv:{code}:{side}:{qty}"),
            code=code, side=side, qty=qty, amount=float(amount),
            status=str(payload.get("status") or "reserved"),
            purpose=str(payload.get("purpose") or ""), price=price,
            theme_ids=tuple(payload.get("theme_ids") or ()),
            pressure_pct=_finite(payload.get("pressure_pct"),
                                 "reservation.pressure_pct", optional=True),
            risk_amount=float(_finite(payload.get("risk_amount", 0.0),
                                      "reservation.risk_amount", low=0.0) or 0.0),
            valid_until_event=(None if payload.get("valid_until_event") is None
                               else int(payload["valid_until_event"])))
    except (TypeError, ValueError, LedgerError) as exc:
        raise AccountFileError(f"reservation 非法：{exc}") from exc
    view = Reservation(code=code, side=side, qty=qty, price=price,
                       theme_ids=core.theme_ids,
                       pressure_pct=core.pressure_pct, purpose=core.purpose)
    return core, view


@dataclass(frozen=True)
class AccountSnapshot:
    """解析后的只读账户快照 + 显式受限项（不伪造缺失事实）。"""

    account: AccountView
    as_of: str = ""
    recovery: Optional[RecoveryState] = None
    policy_states: tuple = ()
    runtime_facts: Mapping = None  # type: ignore[assignment]
    evidence: Mapping = None  # type: ignore[assignment]
    watch_codes: tuple[str, ...] = ()
    policy_hash: Optional[str] = None
    restrictions: tuple[dict, ...] = ()
    notes: str = ""
    pending_orders: tuple = ()
    core_reservations: tuple = ()
    mark_facts: Mapping = None  # type: ignore[assignment]
    batch_facts: Mapping = None  # type: ignore[assignment]
    fund_credits: Mapping = None  # type: ignore[assignment]

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "account": self.account.to_dict(),
            "recovery": self.recovery.to_dict() if self.recovery else None,
            "policy_states": [item.to_dict() for item in self.policy_states],
            "runtime_facts": thaw(dict(self.runtime_facts or {})),
            "evidence": thaw(dict(self.evidence or {})),
            "watch_codes": list(self.watch_codes),
            "policy_hash": self.policy_hash,
            "restrictions": [dict(item) for item in self.restrictions],
            "notes": self.notes,
            "pending_orders": [order.to_row() for order in self.pending_orders],
            "reservations": [item.to_row() for item in self.core_reservations],
            "mark_facts": [item.to_row()
                           for item in (self.mark_facts or {}).values()],
            "batch_facts": [item.to_row()
                            for item in (self.batch_facts or {}).values()],
            "fund_credits": thaw(dict(self.fund_credits or {})),
        }

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(sorted({position.code for position in self.account.positions}))


def _batch(batch: Mapping, *, cutoff: Optional[datetime],
           restrictions: list[dict]) -> Optional[BatchView]:
    _strict(batch, _ALLOWED_BATCH, "batch")
    batch_id = batch.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise AccountFileError("batch.batch_id 必须是非空字符串")
    qty = _int(batch.get("qty"), f"batch[{batch_id}].qty")
    if not qty:
        raise AccountFileError(f"batch[{batch_id}].qty 必须为正整数")
    entry_date = str(batch.get("entry_date") or "")
    cost = _finite(batch.get("cost_price", 0.0), f"batch[{batch_id}].cost_price",
                   low=0.0) or 0.0
    # 事实缺失：不伪造可卖/成本；把批次标为受限并从可执行批次中排除。
    missing = []
    if cost <= 0:
        missing.append("cost_price")
    if not entry_date:
        missing.append("entry_date")
    if missing:
        restrictions.append({
            "reason": "account_batch_incomplete",
            "code": str(batch.get("owner") or ""),
            "batch_id": batch_id,
            "missing": missing,
            "detail": "批次成本/入场日未知，不伪造保护或成交",
        })
        return None
    sellable = _int(batch.get("sellable_qty", 0), f"batch[{batch_id}].sellable_qty")
    sellable = min(int(sellable or 0), int(qty))
    original = _int(batch.get("original_qty", qty),
                    f"batch[{batch_id}].original_qty")
    ref = batch.get("strategy_ref") or {}
    if not isinstance(ref, Mapping):
        raise AccountFileError(f"batch[{batch_id}].strategy_ref 必须是对象")
    return BatchView(
        batch_id=batch_id, qty=int(qty), sellable_qty=sellable,
        entry_date=entry_date, cost_price=float(cost),
        sellable_date=batch.get("sellable_date"),
        strategy_ref=dict(ref), plan_id=str(batch.get("plan_id") or ""),
        owner=str(batch.get("owner") or ""), original_qty=int(original or qty),
        family_id=str(batch.get("family_id") or ""),
        initial_risk=float(_finite(batch.get("initial_risk", 0.0),
                                   f"batch[{batch_id}].initial_risk",
                                   low=0.0) or 0.0))


def _position(payload: Mapping, *, cutoff: Optional[datetime],
              restrictions: list[dict]) -> Optional[PositionView]:
    _strict(payload, _ALLOWED_POSITION, "position")
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        raise AccountFileError("position.code 必须是非空字符串")
    raw_batches = payload.get("batches")
    if not isinstance(raw_batches, (list, tuple)) or not raw_batches:
        restrictions.append({"reason": "account_batch_missing", "code": code,
                             "detail": "缺批次，不猜可卖量/成本"})
        return None
    batches: list[BatchView] = []
    for raw in raw_batches:
        parsed = _batch(_mapping(raw, f"position[{code}].batches[]"),
                        cutoff=cutoff, restrictions=restrictions)
        if parsed is not None:
            batches.append(parsed)
    if not batches:
        return None
    qty = sum(item.qty for item in batches)
    sellable = sum(item.sellable_qty for item in batches)
    weighted_cost = (sum(batch.cost_price * batch.qty for batch in batches) / qty
                     if qty else 0.0)
    entry_date = min((batch.entry_date for batch in batches if batch.entry_date),
                     default="")
    mark_source = payload.get("mark_source")
    mark = payload.get("mark")
    if mark_source is not None:
        mark_source = _mapping(mark_source, f"position[{code}].mark_source")
        _not_future(mark_source.get("available_at"), cutoff,
                    f"position[{code}].mark_source.available_at")
        _not_future(mark_source.get("execution_time"), cutoff,
                    f"position[{code}].mark_source.execution_time")
        if mark is None:
            mark = mark_source.get("price")
    mark_value = _finite(mark, f"position[{code}].mark", optional=True)
    if mark_value is None:
        restrictions.append({"reason": "account_mark_missing", "code": code,
                             "detail": "缺当前盯市价，估值依赖的动作受限"})
    pressures = payload.get("pressure_pct")
    pressure = _finite(pressures, f"position[{code}].pressure_pct", optional=True)
    if pressure is not None and not 0 <= pressure <= 1:
        raise AccountFileError(f"position[{code}].pressure_pct 必须在 [0,1]")
    return PositionView(
        code=code, name=str(payload.get("name") or ""), qty=qty,
        sellable_qty=sellable, cost_price=float(weighted_cost),
        entry_date=entry_date,
        mark=mark_value, theme_ids=_str_list(payload.get("theme_ids"),
                                             f"position[{code}].theme_ids"),
        pressure_pct=pressure, trend=str(payload.get("trend") or "unknown"),
        held_days=_int(payload.get("held_days"),
                       f"position[{code}].held_days", optional=True),
        batches=tuple(batches))


def parse_account_file(payload: Mapping, *, cutoff: Optional[str] = None,
                       policy_hash: Optional[str] = None) -> AccountSnapshot:
    payload = _mapping(payload, "账户文件")
    _strict(payload, _ALLOWED_TOP, "账户文件")
    if payload.get("schema_version") != ACCOUNT_FILE_SCHEMA_VERSION:
        raise AccountFileError(
            f"账户文件 schema_version 必须为 {ACCOUNT_FILE_SCHEMA_VERSION}")
    currency = payload.get("currency", CURRENCY)
    if currency != CURRENCY:
        raise AccountFileError(f"账户货币必须是 {CURRENCY}，得到 {currency!r}")
    unit = payload.get("shares_unit", "share")
    if unit not in SHARE_UNITS:
        raise AccountFileError(f"股数单位必须为 {sorted(SHARE_UNITS)}，得到 {unit!r}")
    cutoff_moment = None
    if cutoff:
        cutoff_moment = _moment(cutoff)
        if cutoff_moment is None:
            raise AccountFileError(f"cutoff 不是合法时点: {cutoff!r}")
    declared = payload.get("policy_ref")
    if declared is not None:
        declared = _mapping(declared, "policy_ref")
        if policy_hash is not None and declared.get("content_hash") not in (
                None, policy_hash):
            raise AccountFileError(
                "账户文件 policy_ref 与 --bs-policy 内容哈希不一致（异配置冲突）",
                code="policy_ref_conflict")
    restrictions: list[dict] = []
    positions: list[PositionView] = []
    for raw in payload.get("positions") or []:
        parsed = _position(_mapping(raw, "positions[]"), cutoff=cutoff_moment,
                           restrictions=restrictions)
        if parsed is not None:
            positions.append(parsed)
    reservations = []
    core_reservations = []
    for raw in payload.get("reservations") or []:
        core, view = _core_reservation(raw)
        core_reservations.append(core)
        reservations.append(view)
    pending_orders = tuple(_core_order(raw)
                           for raw in payload.get("pending_orders") or [])
    runtime_facts = dict(payload.get("runtime_facts") or {})
    _strict(runtime_facts, _ALLOWED_RUNTIME_FACTS, "runtime_facts")
    mark_facts = {}
    for raw in runtime_facts.get("mark_facts") or []:
        try:
            fact = MarkFact.from_row(_mapping(raw, "runtime_facts.mark_facts[]"))
        except Exception as exc:  # noqa: BLE001 - 坏事实显式拒绝，不静默丢弃
            raise AccountFileError(f"mark_fact 非法：{exc}") from exc
        mark_facts[fact.code] = fact
    batch_facts = {}
    for raw in runtime_facts.get("batch_facts") or []:
        row = _mapping(raw, "runtime_facts.batch_facts[]")
        batch_id = row.get("batch_id")
        if not isinstance(batch_id, str) or not batch_id:
            raise AccountFileError("batch_facts 行缺少 batch_id")
        try:
            batch_facts[batch_id] = BatchFacts.from_row(row)
        except Exception as exc:  # noqa: BLE001
            raise AccountFileError(f"batch_fact 非法：{exc}") from exc
    fund_credits = dict(runtime_facts.get("fund_credits") or {})
    as_of = payload.get("as_of")
    if as_of is not None:
        _not_future(as_of, cutoff_moment, "as_of")
    cash = payload.get("cash")
    cash_value = _finite(cash, "cash", optional=True, low=0.0)
    if cash_value is None:
        restrictions.append({"reason": "account_cash_unknown",
                             "detail": "现金未知：不伪造余额，依赖现金的动作受限"})
    account = AccountView(
        cash=cash_value, positions=tuple(positions),
        reservations=tuple(reservations),
        peak_equity=_finite(payload.get("peak_equity"), "peak_equity",
                            optional=True, low=0.0),
        drawdown_pct=_finite(payload.get("drawdown_pct"), "drawdown_pct",
                             optional=True, low=0.0),
        consecutive_losses=_int(payload.get("consecutive_losses", 0),
                                "consecutive_losses") or 0)
    recovery = None
    if payload.get("recovery") is not None:
        recovery = RecoveryState.from_row(_mapping(payload["recovery"], "recovery"))
    states = tuple(
        PositionPolicyState.from_row(_mapping(item, "policy_states[]"))
        for item in payload.get("policy_states") or [])
    mark_sources: dict[str, dict] = {}
    for raw in payload.get("positions") or []:
        raw = _mapping(raw, "positions[]")
        code = raw.get("code")
        source = raw.get("mark_source")
        if isinstance(code, str) and isinstance(source, Mapping):
            mark_sources[code] = dict(source)
    runtime_facts = dict(payload.get("runtime_facts") or {})
    if mark_sources:
        runtime_facts["mark_sources"] = mark_sources
    return AccountSnapshot(
        account=account, as_of=str(payload.get("as_of") or cutoff or ""),
        recovery=recovery, policy_states=states,
        runtime_facts=runtime_facts,
        evidence=dict(payload.get("evidence") or {}),
        watch_codes=_str_list(payload.get("watch_codes"), "watch_codes"),
        policy_hash=(declared or {}).get("content_hash") if declared else None,
        restrictions=tuple(restrictions),
        notes=str(payload.get("notes") or ""),
        pending_orders=pending_orders,
        core_reservations=tuple(core_reservations),
        mark_facts=mark_facts, batch_facts=batch_facts,
        fund_credits=fund_credits)


def load_account_file(path: Optional[str], *, cutoff: Optional[str] = None,
                      policy_hash: Optional[str] = None) -> AccountSnapshot:
    if not path:
        raise AccountFileError("缺少 --account-file 文件路径",
                               code="account_file_missing")
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise AccountFileError(f"无法读取账户文件 {path}: {exc}",
                               code="account_file_unreadable") from exc
    try:
        payload = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise AccountFileError(f"账户文件不是合法 JSON：{exc}",
                               code="account_file_invalid") from exc
    return parse_account_file(payload, cutoff=cutoff, policy_hash=policy_hash)


__all__ = [
    "ACCOUNT_FILE_SCHEMA_VERSION", "CURRENCY", "SHARE_UNITS",
    "AccountFileError", "AccountSnapshot", "parse_account_file",
    "load_account_file",
]
