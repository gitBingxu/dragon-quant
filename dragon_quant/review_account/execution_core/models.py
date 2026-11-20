"""批次账本真值（W2/W3 共用纯值）：批次 / 订单 / 成交腿 / 预留 / 状态。

批次是账务真值，聚合持仓由批次导出。数值/日期/状态在入口即校验（拒绝 NaN/inf/
布尔伪数字/未知方向/非法迁移）；不可变身份字段与可迁移状态字段分离，避免
「同 ID 异内容」被静默覆盖。未知可卖日不再等价「全部可卖」（L7）。

风险与资金单位分开（L2）：``risk_amount``/``initial_risk``/``reserved_risk`` 是
**风险金额（元）**，``reserved_amount`` 是**现金预留（元）**；主题用显式有限
元组保存（``risk_themes``/``theme_ids``），不只保留第一个 theme。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional

from dragon_quant.review_account.evidence import freeze, thaw

from .runtime_facts import BatchFacts, MarkFact

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SELLABLE_UNKNOWN = "unknown"   # 未知可卖：受限，不可卖
SELLABLE_ON = "on"             # trade_date >= sellable_date 可卖
SELLABLE_AFTER = "after"       # trade_date > sellable_date 可卖（T+1）


class LedgerError(ValueError):
    """账本操作的显式错误基类。"""


class LedgerConflictError(LedgerError):
    """相同不可变 ID 但内容冲突。"""


class LedgerRevisionError(LedgerError):
    """提交的基准 revision 已过期（并发/乱序），调用方须 reload 后重算。"""


class LedgerIntegrityError(LedgerError):
    """读到损坏的持久化数据。"""


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def finite_number(value: Any, *, field_name: str, nonneg: bool = False,
                  positive: bool = False) -> float:
    """严格数值校验：拒绝布尔/非数值/NaN/Inf；可选非负/正。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LedgerError(f"{field_name} 必须是有限数值，得到 {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise LedgerError(f"{field_name} 不允许 NaN/Infinity")
    if nonneg and number < 0:
        raise LedgerError(f"{field_name} 不能为负")
    if positive and number <= 0:
        raise LedgerError(f"{field_name} 必须为正")
    return number


def positive_int(value: Any, *, field_name: str,
                 allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LedgerError(f"{field_name} 必须是整数")
    if value < 0 or (value == 0 and not allow_zero):
        bound = "非负" if allow_zero else "正"
        raise LedgerError(f"{field_name} 必须是{bound}整数")
    return value


def _theme_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        raise LedgerError("主题必须是字符串元组，不能传单个字符串")
    return tuple(str(item) for item in value if str(item))


def _hash(payload) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class OrderStatus(str, Enum):
    PROPOSED = "proposed"
    RESERVED = "reserved"
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    OrderStatus.PROPOSED.value: frozenset({
        OrderStatus.RESERVED.value, OrderStatus.REJECTED.value,
        OrderStatus.CANCELLED.value}),
    OrderStatus.RESERVED.value: frozenset({
        OrderStatus.PENDING.value, OrderStatus.PARTIALLY_FILLED.value,
        OrderStatus.FILLED.value, OrderStatus.CANCELLED.value,
        OrderStatus.REJECTED.value}),
    OrderStatus.PENDING.value: frozenset({
        OrderStatus.PARTIALLY_FILLED.value, OrderStatus.FILLED.value,
        OrderStatus.CANCELLED.value, OrderStatus.REJECTED.value}),
    OrderStatus.PARTIALLY_FILLED.value: frozenset({
        OrderStatus.FILLED.value, OrderStatus.CANCELLED.value}),
    OrderStatus.FILLED.value: frozenset(),
    OrderStatus.CANCELLED.value: frozenset(),
    OrderStatus.REJECTED.value: frozenset(),
}


def check_status_transition(old: str, new: str) -> None:
    if old == new:
        return
    allowed = LEGAL_TRANSITIONS.get(old)
    if allowed is None or new not in allowed:
        raise LedgerError(f"非法订单状态迁移: {old} -> {new}")


@dataclass(frozen=True)
class Batch:
    """一笔真实买入批次。不可变身份字段参与 content_hash，`remaining` 可迁移。"""

    batch_id: str
    code: str
    qty: int
    remaining: int
    entry_date: str
    cost_price: float
    fee: float = 0.0
    initial_risk: float = 0.0
    risk_themes: tuple[str, ...] = ()
    strategy_ref: Mapping = field(default_factory=dict)
    plan_id: str = ""
    obligation_id: str = ""
    fill_id: str = ""
    source: str = "runtime"
    sellable_mode: str = SELLABLE_UNKNOWN
    sellable_date: Optional[str] = None
    #: 交易家族（G3 probe + 其 add 归并同一闭合组）；空 = 无家族（旧口径）。
    family_id: str = ""

    def __post_init__(self) -> None:
        if not self.batch_id or not self.code:
            raise LedgerError("batch_id/code 不能为空")
        positive_int(self.qty, field_name="batch.qty")
        positive_int(self.remaining, field_name="batch.remaining", allow_zero=True)
        if self.remaining > self.qty:
            raise LedgerError("batch.remaining 不能大于 qty")
        if not _valid_date(self.entry_date):
            raise LedgerError(f"非法 batch.entry_date: {self.entry_date!r}")
        finite_number(self.cost_price, field_name="batch.cost_price",
                      nonneg=True, positive=True)
        finite_number(self.fee, field_name="batch.fee", nonneg=True)
        finite_number(self.initial_risk, field_name="batch.initial_risk",
                      nonneg=True)
        if self.sellable_mode not in (SELLABLE_UNKNOWN, SELLABLE_ON,
                                      SELLABLE_AFTER):
            raise LedgerError(f"非法 sellable_mode: {self.sellable_mode!r}")
        if self.sellable_mode != SELLABLE_UNKNOWN:
            if not _valid_date(self.sellable_date or ""):
                raise LedgerError(
                    "已声明可卖模式的批次必须有合法 sellable_date")
        object.__setattr__(self, "risk_themes", _theme_tuple(self.risk_themes))
        object.__setattr__(self, "strategy_ref",
                           freeze(dict(self.strategy_ref or {})))

    def identity(self) -> dict:
        payload = {
            "batch_id": self.batch_id, "code": self.code, "qty": self.qty,
            "entry_date": self.entry_date, "cost_price": self.cost_price,
            "fee": self.fee, "initial_risk": self.initial_risk,
            "risk_themes": list(self.risk_themes),
            "strategy_ref": thaw(self.strategy_ref), "plan_id": self.plan_id,
            "obligation_id": self.obligation_id, "fill_id": self.fill_id,
            "source": self.source, "sellable_mode": self.sellable_mode,
            "sellable_date": self.sellable_date,
        }
        # 空家族不写入身份键：旧库既有 content_hash 保持可复核；真实家族参与
        # 不可变身份（同一批次的家族归属不可被后续事件改写）。
        if self.family_id:
            payload["family_id"] = self.family_id
        return payload

    @property
    def content_hash(self) -> str:
        return _hash(self.identity())

    def sellable(self, trade_date: str) -> int:
        """可卖数量；未知/未到期一律 0（不得把未知当全部可卖）。"""
        if self.remaining <= 0 or self.sellable_mode == SELLABLE_UNKNOWN:
            return 0
        date = self.sellable_date or ""
        if not _valid_date(date):
            return 0
        if self.sellable_mode == SELLABLE_ON:
            return self.remaining if trade_date >= date else 0
        return self.remaining if trade_date > date else 0

    def with_remaining(self, remaining: int) -> "Batch":
        return Batch(
            batch_id=self.batch_id, code=self.code, qty=self.qty,
            remaining=remaining, entry_date=self.entry_date,
            cost_price=self.cost_price, fee=self.fee,
            initial_risk=self.initial_risk, risk_themes=self.risk_themes,
            strategy_ref=self.strategy_ref, plan_id=self.plan_id,
            obligation_id=self.obligation_id, fill_id=self.fill_id,
            source=self.source, sellable_mode=self.sellable_mode,
            sellable_date=self.sellable_date, family_id=self.family_id)

    def to_row(self) -> dict:
        return {**self.identity(), "remaining": self.remaining,
                "content_hash": self.content_hash}


@dataclass(frozen=True)
class Order:
    order_id: str
    code: str
    side: str
    qty: int
    status: str
    created_event: int
    filled_qty: int = 0
    limit_price: Optional[float] = None
    reserved_amount: float = 0.0
    reserved_risk: float = 0.0
    locked_qty: int = 0
    valid_until_event: Optional[int] = None
    trigger_kind: str = ""       # price_above / price_below / evidence / none
    trigger_price: Optional[float] = None
    evidence_rule: str = ""
    evidence_ids: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    sequence: int = 0
    risk_amount: float = 0.0
    risk_themes: tuple[str, ...] = ()
    strategy_ref: Mapping = field(default_factory=dict)
    plan_id: str = ""
    obligation_id: str = ""
    #: 明确批次范围：空 = 该归属全部批次（旧口径，不是"未知被当成明确 scope"）。
    batch_ids: tuple[str, ...] = ()
    #: 交易家族（G3 probe + 其 add 归并同一闭合组）；空 = 无家族（旧口径）。
    family_id: str = ""
    #: 冻结的价格失效线（低于即撤销未成新风险余量）；None=未知（不推断失效）。
    invalidate_below: Optional[float] = None
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.order_id:
            raise LedgerError("order_id 不能为空")
        if self.side not in ("buy", "sell"):
            raise LedgerError(f"非法订单方向: {self.side!r}")
        positive_int(self.qty, field_name="order.qty")
        positive_int(self.filled_qty, field_name="order.filled_qty", allow_zero=True)
        if self.filled_qty > self.qty:
            raise LedgerError("order.filled_qty 不能大于 qty")
        positive_int(self.locked_qty, field_name="order.locked_qty", allow_zero=True)
        if self.status not in LEGAL_TRANSITIONS:
            raise LedgerError(f"非法订单状态: {self.status!r}")
        if isinstance(self.created_event, bool) or not isinstance(self.created_event, int):
            raise LedgerError("order.created_event 必须是整数")
        finite_number(self.reserved_amount, field_name="order.reserved_amount",
                      nonneg=True)
        finite_number(self.reserved_risk, field_name="order.reserved_risk",
                      nonneg=True)
        finite_number(self.risk_amount, field_name="order.risk_amount",
                      nonneg=True)
        if self.limit_price is not None:
            finite_number(self.limit_price, field_name="order.limit_price",
                          positive=True)
        if self.trigger_price is not None:
            finite_number(self.trigger_price, field_name="order.trigger_price",
                          positive=True)
        if self.invalidate_below is not None:
            finite_number(self.invalidate_below,
                          field_name="order.invalidate_below", positive=True)
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids or ()))
        object.__setattr__(self, "depends_on", tuple(self.depends_on or ()))
        object.__setattr__(self, "risk_themes", _theme_tuple(self.risk_themes))
        object.__setattr__(self, "batch_ids", tuple(self.batch_ids or ()))
        object.__setattr__(self, "strategy_ref",
                           freeze(dict(self.strategy_ref or {})))

    @property
    def quantity_remaining(self) -> int:
        return self.qty - self.filled_qty

    @property
    def is_terminal(self) -> bool:
        return not LEGAL_TRANSITIONS[self.status]

    def identity(self) -> dict:
        payload = {
            "order_id": self.order_id, "code": self.code, "side": self.side,
            "qty": self.qty, "created_event": self.created_event,
            "limit_price": self.limit_price, "trigger_kind": self.trigger_kind,
            "trigger_price": self.trigger_price,
            "evidence_rule": self.evidence_rule,
            "evidence_ids": list(self.evidence_ids),
            "depends_on": list(self.depends_on), "sequence": self.sequence,
            "risk_amount": self.risk_amount,
            "risk_themes": list(self.risk_themes),
            "strategy_ref": thaw(self.strategy_ref), "plan_id": self.plan_id,
            "obligation_id": self.obligation_id, "reason": self.reason,
        }
        # 空批次范围是旧口径（该归属全部批次），不写入身份键：旧库既有
        # content_hash 保持可复核；非空范围参与不可变身份（scope 不可被改写）。
        if self.batch_ids:
            payload["batch_ids"] = list(self.batch_ids)
        if self.family_id:
            payload["family_id"] = self.family_id
        if self.invalidate_below is not None:
            payload["invalidate_below"] = self.invalidate_below
        return payload

    @property
    def content_hash(self) -> str:
        return _hash(self.identity())

    def with_status(self, status: str, *, filled_qty: Optional[int] = None,
                    reserved_amount: Optional[float] = None,
                    reserved_risk: Optional[float] = None,
                    locked_qty: Optional[int] = None) -> "Order":
        check_status_transition(self.status, status)
        return Order(
            order_id=self.order_id, code=self.code, side=self.side, qty=self.qty,
            status=status, created_event=self.created_event,
            filled_qty=(self.filled_qty if filled_qty is None else filled_qty),
            limit_price=self.limit_price,
            reserved_amount=(self.reserved_amount
                             if reserved_amount is None else reserved_amount),
            reserved_risk=(self.reserved_risk
                           if reserved_risk is None else reserved_risk),
            locked_qty=(self.locked_qty if locked_qty is None else locked_qty),
            valid_until_event=self.valid_until_event,
            trigger_kind=self.trigger_kind, trigger_price=self.trigger_price,
            evidence_rule=self.evidence_rule, evidence_ids=self.evidence_ids,
            depends_on=self.depends_on, sequence=self.sequence,
            risk_amount=self.risk_amount, risk_themes=self.risk_themes,
            strategy_ref=self.strategy_ref, plan_id=self.plan_id,
            obligation_id=self.obligation_id, batch_ids=self.batch_ids,
            family_id=self.family_id,
            invalidate_below=self.invalidate_below, reason=self.reason)

    def to_row(self) -> dict:
        return {
            "order_id": self.order_id, "code": self.code, "side": self.side,
            "qty": self.qty, "filled_qty": self.filled_qty,
            "status": self.status, "limit_price": self.limit_price,
            "reserved_amount": self.reserved_amount,
            "reserved_risk": self.reserved_risk,
            "locked_qty": self.locked_qty, "created_event": self.created_event,
            "valid_until_event": self.valid_until_event,
            "trigger_kind": self.trigger_kind, "trigger_price": self.trigger_price,
            "evidence_rule": self.evidence_rule,
            "evidence_ids": list(self.evidence_ids),
            "depends_on": list(self.depends_on), "sequence": self.sequence,
            "risk_amount": self.risk_amount,
            "risk_themes": list(self.risk_themes),
            "strategy_ref": thaw(self.strategy_ref), "plan_id": self.plan_id,
            "obligation_id": self.obligation_id,
            "batch_ids": list(self.batch_ids), "family_id": self.family_id,
            "invalidate_below": self.invalidate_below,
            "reason": self.reason,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    code: str
    side: str
    price: float
    qty: int
    fee: float
    event_id: str
    event_hash: str = ""
    batch_id: str = ""
    realized_pnl: float = 0.0

    def __post_init__(self) -> None:
        if not self.fill_id or not self.order_id:
            raise LedgerError("fill_id/order_id 不能为空")
        if self.side not in ("buy", "sell"):
            raise LedgerError("非法成交方向")
        finite_number(self.price, field_name="fill.price", positive=True)
        positive_int(self.qty, field_name="fill.qty")
        finite_number(self.fee, field_name="fill.fee", nonneg=True)
        finite_number(self.realized_pnl, field_name="fill.realized_pnl")

    @property
    def content_hash(self) -> str:
        return _hash(self.to_row(include_hash=False))

    def to_row(self, *, include_hash: bool = True) -> dict:
        row = {
            "fill_id": self.fill_id, "order_id": self.order_id,
            "code": self.code, "side": self.side, "price": self.price,
            "qty": self.qty, "fee": self.fee, "event_id": self.event_id,
            "event_hash": self.event_hash, "batch_id": self.batch_id,
            "realized_pnl": self.realized_pnl,
        }
        if include_hash:
            row["content_hash"] = self.content_hash
        return row


@dataclass(frozen=True)
class Reservation:
    order_id: str
    code: str
    side: str
    qty: int
    amount: float
    status: str
    purpose: str = ""
    price: Optional[float] = None
    theme_ids: tuple[str, ...] = ()
    pressure_pct: Optional[float] = None
    risk_amount: float = 0.0
    valid_until_event: Optional[int] = None

    def __post_init__(self) -> None:
        positive_int(self.qty, field_name="reservation.qty")
        finite_number(self.amount, field_name="reservation.amount", nonneg=True)
        finite_number(self.risk_amount, field_name="reservation.risk_amount",
                      nonneg=True)
        if self.price is not None:
            finite_number(self.price, field_name="reservation.price", positive=True)
        object.__setattr__(self, "theme_ids", _theme_tuple(self.theme_ids))

    def to_row(self) -> dict:
        return {"order_id": self.order_id, "code": self.code, "side": self.side,
                "qty": self.qty, "amount": self.amount, "status": self.status,
                "purpose": self.purpose, "price": self.price,
                "theme_ids": list(self.theme_ids),
                "pressure_pct": self.pressure_pct,
                "risk_amount": self.risk_amount,
                "valid_until_event": self.valid_until_event}


@dataclass
class LedgerState:
    cash: float
    revision: int = 0
    batches: list[Batch] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    reservations: list[Reservation] = field(default_factory=list)
    marks: dict[str, float] = field(default_factory=dict)
    event_cursor: int = -1
    event_time: str = ""
    trade_date: str = ""
    peak_equity: float = 0.0
    drawdown_pct: float = 0.0
    consecutive_losses: int = 0
    recovery_stage: int = 0
    recovery: dict = field(default_factory=dict)
    policy_states: list[dict] = field(default_factory=list)
    last_decision: dict = field(default_factory=dict)
    config_hash: str = ""
    snapshot_id: str = ""
    strategy_ref: Mapping = field(default_factory=dict)
    # ── A1/A5 运行事实（可变检查点，不进入批次不可变身份）─────────────────���
    #: 每个 code 的已知标记（价格 + 源时点/可得时点 + 语义/新鲜度）。
    mark_facts: dict[str, MarkFact] = field(default_factory=dict)
    #: 每批次的已知峰值/首日/半仓/成交事实。
    batch_facts: dict[str, BatchFacts] = field(default_factory=dict)
    #: 卖出净到账归属簿（G7 换仓）：父卖单 ID → {credited, spent}（元）。
    #: 只是**可部署约束**，总体现金仍唯一以 ``cash`` 为准；不建第二套现金总账。
    fund_credits: dict = field(default_factory=dict)
    #: 闭合交易统计（按完整 owner + 计划/入场组）。
    trade_stats: dict = field(default_factory=dict)
    #: append-only 闭合交易原始记录（供 C3b 推进连亏/归因）。
    closed_trades: list[dict] = field(default_factory=list)
    #: 账户输入 as_of 的逐批次/持仓市场检查点（trend/held_days/pressure/theme）。
    #: 只作显式假设留痕；趋势授权仍须本地 PIT 事实重评。
    position_facts: dict = field(default_factory=dict)

    def position_qty(self, code: str) -> int:
        return sum(batch.remaining for batch in self.batches if batch.code == code)

    def reservations_total(self) -> float:
        return sum(item.amount for item in self.reservations
                   if item.status == "reserved")

    def marked_equity(self) -> Optional[float]:
        total = float(self.cash)
        for batch in self.batches:
            if batch.remaining <= 0:
                continue
            price = self.marks.get(batch.code)
            if price is None:
                return None
            total += price * batch.remaining
        return total
