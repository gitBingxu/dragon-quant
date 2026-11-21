"""保守撮合（W2/W3 共用）：决策事件之后的**下一合格执行事件**按可成交价/容量撮合。

纪律（主验收 L2/L3/L6/L7）：

- 决策事件**不在同一根**反向成交：``quote.event_ts <= order.created_event`` → 未成交。
- 报价必须带**真实源执行时点与可得时点**且未过期/未到未来；容量未知或为 0、
  停牌、封死涨跌停、价无效一律未成交（不得刷新旧 quote 时钟、不得全量吞容量）。
- 容量在同事件内由引擎跨订单共享扣减（本函数接收剩余容量并回报已用）。
- 结构化触发/到期/依赖：``price_above``/``price_below``/``evidence`` 未满足保持
  等待；``depends_on`` 只在依赖**已真实成交**后才允许成交（G7）；``valid_until_event``
  到期取消（不得无限存活）。
- T+1 只卖 ``sellable_mode`` 已到期的批次；未知可卖日不可卖。
- 费用/滑点/整手共用 :mod:`execution_model`；买腿现金不足只缩量到可负担整手。
- L4：卖腿只消耗与订单**完整冻结身份**相同的批次。
- L2：成交把订单风险按成交比例转入批次 ``initial_risk``，剩余风险留在
  ``order.reserved_risk``/预留；资金预留与风险金额单位不混用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

from dragon_quant.review_account.execution_model import (
    EXECUTION_MODEL_VERSION, apply_slippage, fee_amount, lot_floor,
)

from .identity import ref_identity
from .models import (
    SELLABLE_ON, Batch, Fill, LedgerState, Order, OrderStatus, Reservation,
)

_EPS = 1e-9


def _released(reservation: Reservation) -> Reservation:
    return Reservation(order_id=reservation.order_id, code=reservation.code,
                       side=reservation.side, qty=reservation.qty, amount=0.0,
                       status="released", purpose=reservation.purpose,
                       price=reservation.price, theme_ids=reservation.theme_ids,
                       pressure_pct=reservation.pressure_pct, risk_amount=0.0,
                       valid_until_event=reservation.valid_until_event)


def owner_identity(ref: Optional[Mapping]) -> tuple:
    """完整冻结身份（复用既有 ``ref_identity`` 语义）：workspace/strategy/snapshot/
    params_hash/factor_versions 全部参与；任一变化即非同一归属（L4）。"""
    return ref_identity(ref)


@dataclass(frozen=True)
class ExecutionCostModel:
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    buy_slippage: float = 0.002
    sell_slippage: float = 0.002
    lot_size: int = 100

    def fee(self, amount: float, side: str) -> float:
        return fee_amount(amount, side, commission_rate=self.commission_rate,
                          min_commission=self.min_commission,
                          stamp_tax_rate=self.stamp_tax_rate)

    def execution_price(self, price: float, side: str) -> float:
        return apply_slippage(price, side, buy_slippage=self.buy_slippage,
                              sell_slippage=self.sell_slippage)


@dataclass(frozen=True)
class ExecutionQuote:
    """一个执行事件的单只报价（引擎从已验证事件行构造，保留源时点/容量）。

    ``available_at``/``event_time`` 为 ISO 字符串；``capacity_qty`` 为**本事件**
    该 code 允许成交的最大股数（``None`` = 未知 → 不可成交）。
    """

    code: str
    event_id: str
    event_ts: int
    event_time: str
    trade_date: str
    price: Optional[float]
    available_at: str
    tradable: bool
    capacity_qty: Optional[int]
    reason: str = ""
    stale: bool = False
    model_version: str = EXECUTION_MODEL_VERSION


@dataclass
class MatchResult:
    order: Order
    fills: list[Fill] = field(default_factory=list)
    batch_updates: list[Batch] = field(default_factory=list)
    cash_delta: float = 0.0
    reservation_release: float = 0.0
    status: str = ""
    reason: str = ""
    unfilled: bool = False
    capacity_used: int = 0


def _settle(order: Order, filled: int) -> str:
    if filled >= order.qty:
        return OrderStatus.FILLED.value
    if filled > 0:
        return OrderStatus.PARTIALLY_FILLED.value
    return order.status


def _hold(order: Order, reason: str, *, reject: bool = False,
          cancel: bool = False) -> MatchResult:
    if cancel:
        new = order if order.is_terminal else order.with_status(
            OrderStatus.CANCELLED.value)
        return MatchResult(order=new, status=new.status, unfilled=True,
                           reason=reason)
    if order.filled_qty > 0:
        return MatchResult(order=order, status=order.status, unfilled=True,
                           reason=reason)
    status = OrderStatus.REJECTED.value if reject else OrderStatus.PENDING.value
    return MatchResult(order=order.with_status(status), status=status,
                       unfilled=True, reason=reason)


def _trigger_blocked(order: Order, price: Optional[float],
                     evidence_available: Iterable[str]) -> str:
    if order.trigger_kind == "price_above":
        if order.trigger_price is None or price is None \
                or price < order.trigger_price - _EPS:
            return "trigger_not_met"
    elif order.trigger_kind == "price_below":
        if order.trigger_price is None or price is None \
                or price > order.trigger_price + _EPS:
            return "trigger_not_met"
    elif order.trigger_kind == "evidence":
        if not order.evidence_rule \
                or order.evidence_rule not in set(evidence_available):
            return "evidence_not_available"
    return ""


def _dependency_blocked(order: Order, state: LedgerState,
                        filled_snapshot: Optional[Mapping[str, int]] = None
                        ) -> str:
    if not order.depends_on:
        return ""
    filled = (dict(filled_snapshot) if filled_snapshot is not None
              else {item.order_id: item.filled_qty for item in state.orders})
    if all(filled.get(dep, 0) > 0 for dep in order.depends_on):
        return ""
    return "depends_on_unfilled"


def _quote_invalid(quote: ExecutionQuote) -> str:
    if not quote.event_time or not quote.available_at:
        return "quote_time_missing"
    if quote.available_at > quote.event_time:
        return "quote_from_future"
    if quote.stale:
        return "quote_stale"
    if quote.price is None or quote.price <= 0:
        return quote.reason or "price_missing"
    if not quote.tradable:
        return quote.reason or "untradable"
    if quote.capacity_qty is None:
        return "capacity_unknown"
    if quote.capacity_qty <= 0:
        return "capacity_zero"
    return ""


def _owner_key(ref) -> tuple:
    return owner_identity(ref)


def _same_owner(batch: Batch, order: Order) -> bool:
    return _owner_key(batch.strategy_ref) == _owner_key(order.strategy_ref)


def _risk_allocated(order: Order, qty: int, remaining: int) -> float:
    """按成交比例把订单剩余风险转入批次；整单成交时转入全部剩余风险。"""
    if remaining <= 0 or qty <= 0:
        return 0.0
    if qty >= remaining:
        return float(order.reserved_risk)
    share = order.reserved_risk * qty / remaining
    return min(float(order.reserved_risk), share)


def _sell_funding_limit(state: LedgerState, order: Order,
                        credited_snapshot: Optional[Mapping[str, float]] = None
                        ) -> Optional[float]:
    """G7 换仓买腿的可部署额度：只取**自己依赖的真实父卖单**已实现净到账。

    免费/其它来源现金不自动并入换仓（父卖单失败则额度为 0，依赖同时阻止成交）。
    多个子单按真实预留扣减，不能双花同一笔卖出款。没有卖单父腿（如 G3 依赖
    父买单）返回 ``None``，沿用普通现金规则。
    """
    deps = tuple(str(item) for item in (order.depends_on or ()))
    if not deps:
        return None
    by_id = {item.order_id: item for item in state.orders}
    parents = [by_id[dep] for dep in deps
               if dep in by_id and by_id[dep].side == "sell"]
    if not parents:
        return None
    credits = getattr(state, "fund_credits", {}) or {}
    parent_ids = {parent.order_id for parent in parents}
    # 先到先得：只为**更早**的同父子单保留额度（同事件按稳定 order_id），
    # 避免两笔子单互相把对方的预留当作已消耗而死锁；已真实消耗在 spent 里。
    me = (int(order.created_event), order.order_id)

    def _earlier(item) -> bool:
        return (int(item.created_event), item.order_id) < me

    siblings = sum(
        float(item.reserved_amount) for item in state.orders
        if item.order_id != order.order_id and item.side == "buy"
        and not item.is_terminal and _earlier(item)
        and parent_ids & set(item.depends_on or ()))
    total = 0.0
    for parent in parents:
        entry = credits.get(parent.order_id) or {}
        if credited_snapshot is not None:
            # 只用**事件开始前**已实现的净到账：父卖在同一事件的新增成交
            # 不在本事件解锁子买（严格更晚的成交事件）。
            credited = float(credited_snapshot.get(parent.order_id, 0.0) or 0.0)
        else:
            credited = float(entry.get("credited", 0.0) or 0.0)
        spent = float(entry.get("spent", 0.0) or 0.0)
        total += max(0.0, credited - spent)
    return max(0.0, total - siblings)


def _match_buy(order: Order, quote: ExecutionQuote, state: LedgerState,
               model: ExecutionCostModel, next_trade_date: Optional[str],
               capacity_remaining: int,
               cash_limit: Optional[float] = None,
               funding_limit: Optional[float] = None) -> MatchResult:
    if not quote.trade_date:
        # 没有真实交易日不能记批次（不推断伪日期）；保持未成交/受限。
        return _hold(order, "trade_date_missing")
    if next_trade_date is None:
        # 执行日历不足（无法确认 T+1 可卖日）：不得新增成交，保留挂单/受限原因。
        return _hold(order, "trade_calendar_insufficient")
    exec_price = model.execution_price(quote.price, "buy")
    if order.limit_price is not None and exec_price > order.limit_price + _EPS:
        return _hold(order, "above_limit")
    remaining = order.quantity_remaining
    budget_qty = min(remaining, capacity_remaining)
    if budget_qty <= 0:
        return _hold(order, "capacity_exhausted")
    others = sum(item.amount for item in state.reservations
                 if item.status == "reserved" and item.order_id != order.order_id)
    if funding_limit is not None:
        # G7：同父卖的其他子单由 funding_limit 按真实预留扣减，不能再当作
        # 普通现金占用重复扣一次（否则两笔子单会互相把对方挤成现金不足）。
        by_id = {item.order_id: item for item in state.orders}
        sell_parents = {str(dep) for dep in (order.depends_on or ())
                        if dep in by_id and by_id[dep].side == "sell"}
        others = sum(
            item.amount for item in state.reservations
            if item.status == "reserved" and item.order_id != order.order_id
            and not (sell_parents
                     and sell_parents & set(
                         (by_id.get(item.order_id).depends_on or ())
                         if by_id.get(item.order_id) is not None else ())))
    spendable = state.cash - others
    if cash_limit is not None:
        # 适配器显式授权的可部署资金（如 reinvest_cash=False 时不含卖出款）。
        spendable = min(spendable, float(cash_limit))
    if funding_limit is not None:
        # G7 换仓：买腿只能动用其父卖单真实已实现净到账（扣除其它子单预留）。
        spendable = min(spendable, float(funding_limit))
    qty = budget_qty
    amount = exec_price * qty
    fee = model.fee(amount, "buy")
    if amount + fee > spendable + _EPS:
        affordable = lot_floor(spendable / (exec_price * model.lot_size), 1) \
            * model.lot_size
        qty = min(affordable, budget_qty)
        if qty <= 0:
            return _hold(order, "insufficient_cash")
        amount = exec_price * qty
        fee = model.fee(amount, "buy")
        if amount + fee > spendable + _EPS:
            return _hold(order, "insufficient_cash")
    risk = _risk_allocated(order, qty, remaining)
    risk_left = max(0.0, float(order.reserved_risk) - risk)
    fill_id = f"{order.order_id}:fill:{order.filled_qty + qty}"
    batch = Batch(
        batch_id=f"{order.order_id}:batch:{order.filled_qty + qty}",
        code=order.code, qty=qty, remaining=qty, entry_date=quote.trade_date,
        cost_price=exec_price, fee=fee, initial_risk=risk,
        risk_themes=order.risk_themes,
        strategy_ref=order.strategy_ref, plan_id=order.plan_id,
        obligation_id=order.obligation_id, fill_id=fill_id, source="runtime",
        sellable_mode=SELLABLE_ON, sellable_date=next_trade_date,
        family_id=order.family_id or "")
    fill = Fill(fill_id=fill_id, order_id=order.order_id, code=order.code,
                side="buy", price=exec_price, qty=qty, fee=fee,
                event_id=quote.event_id, event_hash=quote.event_id,
                batch_id=batch.batch_id, realized_pnl=-fee)
    status = OrderStatus.FILLED.value if qty >= remaining \
        else OrderStatus.PARTIALLY_FILLED.value
    leftover = remaining - qty
    reserved_left = 0.0
    if leftover > 0:
        left_amount = exec_price * leftover
        reserved_left = left_amount + model.fee(left_amount, "buy")
    return MatchResult(
        order=order.with_status(status, filled_qty=order.filled_qty + qty,
                                reserved_amount=reserved_left,
                                reserved_risk=risk_left),
        fills=[fill], batch_updates=[batch], cash_delta=-(amount + fee),
        reservation_release=float(order.reserved_amount) - reserved_left,
        status=status, capacity_used=qty)


def _match_sell(order: Order, quote: ExecutionQuote, state: LedgerState,
                model: ExecutionCostModel,
                capacity_remaining: int) -> MatchResult:
    scope = frozenset(order.batch_ids or ())
    owned = [batch for batch in state.batches
             if batch.code == order.code and _same_owner(batch, order)
             and (not scope or batch.batch_id in scope)]
    available = min(sum(batch.sellable(quote.trade_date) for batch in owned),
                    order.quantity_remaining, capacity_remaining)
    if available <= 0:
        return _hold(order, "no_sellable")
    exec_price = model.execution_price(quote.price, "sell")
    remaining, allocation = available, []
    for batch in sorted(owned, key=lambda item: (item.entry_date, item.batch_id)):
        if remaining <= 0:
            break
        sellable = min(batch.sellable(quote.trade_date), remaining)
        if sellable <= 0:
            continue
        allocation.append((batch, sellable))
        remaining -= sellable
    want = available - remaining
    total_amount = exec_price * want
    total_fee = model.fee(total_amount, "sell")
    fills: list[Fill] = []
    batch_updates: list[Batch] = []
    sold_so_far = 0
    for batch, move in allocation:
        share = (total_fee * move / want) if want else 0.0
        realized = exec_price * move - share - batch.cost_price * move
        sold_so_far += move
        batch_updates.append(batch.with_remaining(batch.remaining - move))
        fills.append(Fill(
            fill_id=f"{order.order_id}:fill:{order.filled_qty + sold_so_far}",
            order_id=order.order_id, code=order.code, side="sell",
            price=exec_price, qty=move, fee=share, event_id=quote.event_id,
            event_hash=quote.event_id, batch_id=batch.batch_id,
            realized_pnl=realized))
    status = _settle(order, order.filled_qty + want)
    return MatchResult(
        order=order.with_status(status, filled_qty=order.filled_qty + want,
                                reserved_amount=0.0, reserved_risk=0.0,
                                locked_qty=0),
        fills=fills, batch_updates=batch_updates,
        cash_delta=total_amount - total_fee, status=status, capacity_used=want)


def match_order(order: Order, quote: ExecutionQuote, state: LedgerState,
                model: ExecutionCostModel, *,
                next_trade_date: Optional[str] = None,
                capacity_remaining: Optional[int] = None,
                evidence_available: Iterable[str] = (),
                filled_snapshot: Optional[Mapping[str, int]] = None,
                cash_limit: Optional[float] = None,
                funding_credit: Optional[Mapping[str, float]] = None
                ) -> MatchResult:
    """对单个挂单在执行事件上做一次保守撮合（纯函数）。"""
    if order.is_terminal:
        return MatchResult(order=order, status=order.status, reason="terminal")
    if quote.event_ts <= order.created_event:
        return _hold(order, "no_later_event")
    if order.valid_until_event is not None \
            and quote.event_ts > order.valid_until_event:
        return _hold(order, "expired", cancel=True)
    if order.code != quote.code:
        return MatchResult(order=order, status=order.status,
                           reason="code_mismatch")
    invalid = _quote_invalid(quote)
    if invalid:
        return _hold(order, invalid)
    blocked = _trigger_blocked(order, quote.price, evidence_available)
    if blocked:
        return _hold(order, blocked)
    dependency = _dependency_blocked(order, state, filled_snapshot)
    if dependency:
        return _hold(order, dependency)
    capacity = capacity_remaining if capacity_remaining is not None \
        else quote.capacity_qty
    if capacity is None or capacity <= 0:
        return _hold(order, "capacity_exhausted")
    if order.side == "buy":
        return _match_buy(order, quote, state, model, next_trade_date, capacity,
                          cash_limit,
                          _sell_funding_limit(state, order, funding_credit))
    return _match_sell(order, quote, state, model, capacity)
