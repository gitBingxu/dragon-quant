"""唯一的执行状态转移（W2 场景 / W3 账本共用的纯实现）。

两个适配器（W2 ``simulate`` 与 W3 ``AccountEngine``）各自把外部路径/命令适配成
本模块的 :class:`TransitionEvent` + :class:`TransitionCommand`，然后调用**同一个**
:func:`transition_event`：订单创建/触发/到期/取消、成交/部分成交、费用/现金、
批次 T+1、容量分配、风险预留与保护义务时序都由这里实现（不是只共享费率）。

本模块不读时钟/env/数据库；``state`` 是可变 :class:`LedgerState` 工作副本，
由调用方在事务内/纯计算内提供；返回的审计供适配器读取。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Optional

from .matching import (
    ExecutionCostModel, ExecutionQuote, _owner_key, _released, match_order,
)
from .models import (
    Fill, LedgerError, LedgerState, Order, OrderStatus, Reservation,
)
from .risk import RiskBook, RiskCaps, check_new_risk
from .settlement import settle_fills

_EPS = 1e-9


@dataclass(frozen=True)
class TransitionCommand:
    """一条下单命令（来自政策意图/场景计划腿/保护义务）。"""

    action: str
    code: str
    qty: int
    owner: Mapping = field(default_factory=dict)
    plan_id: str = ""
    obligation_id: str = ""
    limit_price: Optional[float] = None
    trigger_kind: str = ""
    trigger_price: Optional[float] = None
    evidence_rule: str = ""
    evidence_ids: tuple = ()
    depends_on: tuple = ()
    sequence: int = 0
    valid_until_event: Optional[int] = None
    risk_amount: float = 0.0
    risk_themes: tuple = ()
    pressure_pct: Optional[float] = None
    #: 交易家族（G3 probe + 其 add 归并同一闭合组；空 = 无家族）。
    family_id: str = ""
    #: 冻结的价格失效线（低于即撤销未成新风险余量；None=未知，不推断失效）。
    invalidate_below: Optional[float] = None
    #: 明确批次范围（保护义务的 owner/batch scope）；空 = 该归属全部批次。
    batch_ids: tuple = ()


@dataclass
class TransitionLimits:
    cost_model: ExecutionCostModel
    next_trade_date: Callable[[str], Optional[str]]
    ttl_ms: Optional[int] = 86_400_000
    risk_caps: Optional[RiskCaps] = None
    #: 逐事件风险占用基准；``None`` 时由 ``state`` 的批次与未成交买单重算。
    risk_book: Optional[RiskBook] = None
    #: 显式教学/fixture 假设：允许风险金额未知的买单通过（不表示生产放宽）。
    allow_unknown_risk: bool = False
    #: 可部署资金上限（元）；``None`` = 由账户现金决定。用于不把卖出款再投资。
    cash_limit: Optional[float] = None
    #: 显式**新鲜度规格**（年龄毫秒）；``None`` = 未声明/未知 → 新增风险受限。
    #: 卖出/减仓（保护）不受此限制，继续按真实新执行支持评估。
    max_age_ms: Optional[int] = None


@dataclass
class TransitionResult:
    fills: list[Fill] = field(default_factory=list)
    canceled: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    matches: list[dict] = field(default_factory=list)


def transition_event(state: LedgerState, *, event_time: int,
                     trade_date: str,
                     quotes: Mapping[str, ExecutionQuote],
                     commands: Iterable[TransitionCommand],
                     limits: TransitionLimits,
                     evidence_available: Iterable[str] = (),
                     capacity_override: Optional[Mapping[str, int]] = None,
                     match: bool = True) -> TransitionResult:
    result = TransitionResult()
    _expire(state, event_time, result)
    _cancel_orphans(state, result)
    _create_orders(state, commands, quotes, limits, event_time, trade_date,
                   result)
    if match:
        _match(state, quotes, limits, event_time, trade_date, result,
               evidence_available, capacity_override)
    return result


# ── 到期/取消 ───────────────────────────────────────────────────────────────
def _cancel(state: LedgerState, order: Order, result: TransitionResult) -> None:
    state.orders = [
        (order.with_status(OrderStatus.CANCELLED.value, locked_qty=0,
                           reserved_amount=0.0, reserved_risk=0.0)
         if item.order_id == order.order_id else item)
        for item in state.orders]
    state.reservations = [
        _released(item) if item.order_id == order.order_id else item
        for item in state.reservations]
    result.canceled.append(order.order_id)


def _expire(state: LedgerState, event_time: int,
            result: TransitionResult) -> None:
    for order in list(state.orders):
        if order.is_terminal:
            continue
        if order.valid_until_event is not None \
                and event_time > order.valid_until_event:
            _cancel(state, order, result)


def _cancel_orphans(state: LedgerState, result: TransitionResult) -> None:
    """依赖的父腿已取消/拒绝且**零成交**时，撤掉不会再有授权的子单。

    已 FILLED（或部分成交）的父腿是合法依赖，保留；父腿仍挂单也保留等待。
    不在同一事件把「父腿新下单」当作已完成，因此不会提前解锁。
    """
    by_id = {order.order_id: order for order in state.orders}
    for order in list(state.orders):
        if order.is_terminal or order.side != "buy" or not order.depends_on:
            continue
        for dep in order.depends_on:
            parent = by_id.get(dep)
            if parent is None:
                continue
            if parent.is_terminal and parent.filled_qty <= 0:
                _cancel(state, order, result)
                break


# ── 下单（含 L2 风险上限、G7 depends_on）──────────────────────────────────
def _depends_on_ready(state: LedgerState, command: TransitionCommand) -> bool:
    """下单时只拒绝**确定不可能**的依赖：缺父腿，或父腿已取消/拒绝。

    - 已 FILLED 的父腿是合法依赖（G7 真实成交支持）；
    - 仍在挂单/部分成交的父腿允许下单，但撮合时点（严格更晚事件）再校验，
      不得在同一事件内提前解锁；
    - 取消/拒绝且无满足成交的父腿永久拒绝。
    """
    if not command.depends_on:
        return True
    by_id = {order.order_id: order for order in state.orders}
    for dep in command.depends_on:
        order = by_id.get(dep)
        if order is None:
            return False
        if order.status in (OrderStatus.CANCELLED.value,
                            OrderStatus.REJECTED.value):
            return False
    return True


def _has_open_order(state: LedgerState, command: TransitionCommand) -> bool:
    owner = _owner_key(command.owner)
    scope = frozenset(command.batch_ids or ())
    for order in state.orders:
        if order.code != command.code or order.side != command.action:
            continue
        if order.is_terminal or _owner_key(order.strategy_ref) != owner:
            continue
        if frozenset(order.batch_ids or ()) != scope:
            continue
        if command.obligation_id:
            # 保护义务的去重身份是义务+scope：选中计划变化不应再下一份。
            if order.obligation_id == command.obligation_id:
                return True
        elif order.plan_id == command.plan_id \
                and order.obligation_id == command.obligation_id:
            return True
    return False


def _price_reference(command: TransitionCommand,
                     quotes: Mapping[str, ExecutionQuote],
                     state: LedgerState) -> Optional[float]:
    """新风险的价格参考：显式限价优先，其次**本事件**可执行报价。

    P1：不再回退到可能过期的历史 ``state.marks``——无事实支持的旧估值不能
    授权新增风险（其仅用于估值/峰值历史）。
    """
    if command.limit_price is not None:
        return float(command.limit_price)
    quote = quotes.get(command.code)
    if quote is not None and quote.tradable and quote.price is not None \
            and quote.price > 0:
        return float(quote.price)
    return None


def _locked_by_batch(state: LedgerState, code: str, owner_key: tuple
                     ) -> dict[str, int]:
    """按批次真实分配现有卖单的锁量（兼容无 scope 旧单与相交 scope）。

    共享实现：新的卖出命令与旧单都必须看到同一份逐批锁量，避免 ``[]`` 与
    ``[b]``、``[a,b]`` 与 ``[b,c]`` 这类相交范围各自声称全量可卖。
    """
    batches = sorted(
        (batch for batch in state.batches
         if batch.code == code and _owner_key(batch.strategy_ref) == owner_key),
        key=lambda item: (item.entry_date, item.batch_id))
    locks: dict[str, int] = {}
    for order in state.orders:
        if order.side != "sell" or order.is_terminal or order.code != code:
            continue
        if _owner_key(order.strategy_ref) != owner_key:
            continue
        left = order.quantity_remaining
        if left <= 0:
            continue
        scope = frozenset(order.batch_ids or ())
        for batch in batches:
            if left <= 0:
                break
            if scope and batch.batch_id not in scope:
                continue
            room = max(0, batch.remaining - locks.get(batch.batch_id, 0))
            take = min(left, room)
            if take <= 0:
                continue
            locks[batch.batch_id] = locks.get(batch.batch_id, 0) + take
            left -= take
    return locks


def _unlocked_sellable(state: LedgerState, command: TransitionCommand,
                       trade_date: str) -> int:
    owner = _owner_key(command.owner)
    scope = frozenset(command.batch_ids or ())
    batches = [batch for batch in state.batches
               if batch.code == command.code
               and _owner_key(batch.strategy_ref) == owner
               and (not scope or batch.batch_id in scope)]
    locks = _locked_by_batch(state, command.code, owner)
    available = 0
    for batch in batches:
        sellable = batch.sellable(trade_date)
        if sellable <= 0:
            continue
        available += max(0, sellable - min(locks.get(batch.batch_id, 0),
                                            sellable))
    return available


def _create_orders(state: LedgerState, commands: Iterable[TransitionCommand],
                   quotes: Mapping[str, ExecutionQuote], limits: TransitionLimits,
                   event_time: int, trade_date: str,
                   result: TransitionResult) -> None:
    model = limits.cost_model
    book = limits.risk_book if limits.risk_book is not None \
        else RiskBook.from_state(state)
    for command in commands:
        if _has_open_order(state, command):
            continue
        if not _depends_on_ready(state, command):
            result.rejected.append({"code": command.code,
                                    "reason": "depends_on_unfilled"})
            continue
        themes = tuple(str(item) for item in command.risk_themes or ())
        if command.action == "buy":
            # P1（main 决策2）：生产新增风险必须有**显式且合法**的新鲜度规格；
            # 未声明=unknown → 新增风险受限（不是默认新鲜）。既有保护（卖出/减仓）
            # 不受影响，继续按真实新执行支持评估。
            if limits.max_age_ms is None:
                result.rejected.append({
                    "code": command.code,
                    "reason": "freshness_model_missing"})
                continue
            reason = check_new_risk(
                limits.risk_caps, command.risk_amount, themes, book=book,
                allow_unknown_risk=limits.allow_unknown_risk)
            if reason:
                result.rejected.append({"code": command.code, "reason": reason})
                continue
            price_ref = _price_reference(command, quotes, state)
            if price_ref is None:
                result.rejected.append({"code": command.code,
                                        "reason": "no_price_reference"})
                continue
            qty = _lot_floor(command.qty, model.lot_size)
            if qty <= 0:
                continue
            amount = model.execution_price(price_ref, "buy") * qty
            reserved = amount + model.fee(amount, "buy")
            risk = max(0.0, float(command.risk_amount))
            locked = 0
            # 同事件内已接受的风险立即计入，避免两笔命令按同一旧预算各自通过。
            book.add(risk, themes)
        else:
            qty = min(command.qty,
                      _unlocked_sellable(state, command, trade_date))
            if qty <= 0:
                result.rejected.append({"code": command.code,
                                        "reason": "no_sellable"})
                continue
            reserved = 0.0
            risk = 0.0
            locked = qty
        valid_until = command.valid_until_event
        if valid_until is None and limits.ttl_ms is not None:
            valid_until = event_time + limits.ttl_ms
        order_id = _order_id(command, event_time)
        order = Order(
            order_id=order_id, code=command.code, side=command.action, qty=qty,
            status=OrderStatus.RESERVED.value, created_event=event_time,
            limit_price=command.limit_price, reserved_amount=reserved,
            reserved_risk=risk, locked_qty=locked, valid_until_event=valid_until,
            trigger_kind=command.trigger_kind,
            trigger_price=command.trigger_price,
            evidence_rule=command.evidence_rule,
            evidence_ids=tuple(command.evidence_ids or ()),
            depends_on=tuple(command.depends_on or ()),
            sequence=int(command.sequence or 0), risk_amount=risk,
            risk_themes=themes, strategy_ref=command.owner,
            plan_id=command.plan_id, obligation_id=command.obligation_id,
            batch_ids=tuple(command.batch_ids or ()),
            family_id=command.family_id or "",
            invalidate_below=command.invalidate_below,
            reason=command.obligation_id or command.plan_id)
        state.orders.append(order)
        state.reservations.append(Reservation(
            order_id=order_id, code=command.code, side=command.action, qty=qty,
            amount=reserved, status="reserved",
            purpose="risk" if command.action == "buy" else "exit",
            price=(price_ref if command.action == "buy" else None),
            theme_ids=themes, pressure_pct=command.pressure_pct,
            risk_amount=risk, valid_until_event=valid_until))
        result.created.append(order_id)


# ── 撮合（共享容量）────────────────────────────────────────────────────────
def _match(state: LedgerState, quotes: Mapping[str, ExecutionQuote],
           limits: TransitionLimits, event_time: int, trade_date: str,
           result: TransitionResult, evidence_available: Iterable[str],
           capacity_override: Optional[Mapping[str, int]]) -> None:
    capacity: dict[str, Optional[int]] = {}
    evidence = set(evidence_available or ())
    # 依赖必须**先成交**：用事件开始时的成交量快照判定，避免同一事件内
    # “先卖后买”被当作原子交换（G7 不假设原子性）。换仓资助同理：只用事件
    # 开始前已实现的净卖款，本事件新增成交留到后续事件。
    filled_snapshot = {order.order_id: order.filled_qty for order in state.orders}
    funding_credit = {
        key: float((value or {}).get("credited", 0.0) or 0.0)
        for key, value in (getattr(state, "fund_credits", {}) or {}).items()}
    for order in list(state.orders):
        if order.is_terminal or order.created_event >= event_time:
            continue
        quote = quotes.get(order.code)
        if quote is None:
            continue
        if order.code not in capacity:
            capacity[order.code] = (
                capacity_override.get(order.code)
                if capacity_override and order.code in capacity_override
                else quote.capacity_qty)
        remaining_cap = capacity[order.code]
        nxt = limits.next_trade_date(trade_date) if order.side == "buy" else None
        match = match_order(order, quote, state, limits.cost_model,
                            next_trade_date=nxt,
                            capacity_remaining=remaining_cap,
                            evidence_available=evidence,
                            filled_snapshot=filled_snapshot,
                            cash_limit=limits.cash_limit,
                            funding_credit=funding_credit)
        if match.capacity_used and remaining_cap is not None:
            capacity[order.code] = remaining_cap - match.capacity_used
        _apply(state, match, result, event_time, funding_credit)
        result.matches.append({"order_id": order.order_id,
                               "code": order.code, "side": order.side,
                               "plan_id": order.plan_id,
                               "status": match.status, "reason": match.reason})


def _apply(state: LedgerState, match, result: TransitionResult,
           event_time: int = 0, funding_credit=None) -> None:
    batches_before = {batch.batch_id: batch for batch in state.batches}
    state.cash += match.cash_delta
    if match.batch_updates:
        by_id = {batch.batch_id: batch for batch in state.batches}
        for update in match.batch_updates:
            by_id[update.batch_id] = update
        state.batches = [by_id[key] for key in sorted(by_id)]
    state.orders = [match.order if order.order_id == match.order.order_id
                    else order for order in state.orders]
    if match.order.filled_qty > 0 or match.order.is_terminal:
        updated = []
        for item in state.reservations:
            if item.order_id != match.order.order_id:
                updated.append(item)
            elif match.order.is_terminal or match.order.reserved_amount <= 0:
                updated.append(_released(item))
            else:
                updated.append(Reservation(
                    order_id=item.order_id, code=item.code, side=item.side,
                    qty=match.order.quantity_remaining,
                    amount=match.order.reserved_amount, status="reserved",
                    purpose=item.purpose, price=item.price,
                    theme_ids=item.theme_ids, pressure_pct=item.pressure_pct,
                    risk_amount=match.order.reserved_risk,
                    valid_until_event=item.valid_until_event))
        state.reservations = updated
    if match.fills:
        _update_fund_credits(state, match, funding_credit)
        # A5：闭合交易结算承担连亏/已实现净额；买入成交不重置连亏，部分卖出
        # 不单独计数（原实现两处错误在此移除）。
        settle_fills(state, fills=match.fills, batches_before=batches_before,
                     event_ts=int(event_time))
    result.fills.extend(match.fills)


def _update_fund_credits(state: LedgerState, match, credited_snapshot=None
                         ) -> None:
    """G7 资金归属：父卖真实净到账入账、子买真实消耗按父卖**实际分配**记账。

    - 每一笔真实卖成交都入账（子单可能稍后才创建，不能丢失已实现净到账）；
      只有被子单 ``depends_on`` 指向的父卖才可用于资助。
    - 子买消耗按撮合时**事件开始**的逐父可用额度做有界分配（先到水位分配），
      不再等额猜分摊；每父 ``0 <= spent <= credited``，总消耗不超真实净款。
    - 新事件新增卖款不在本事件支持子买（budget 用同一 credited 快照）。
    """
    credits = {key: dict(value)
               for key, value in (getattr(state, "fund_credits", {}) or {}).items()}
    by_id = {order.order_id: order for order in state.orders}
    sell_parents = [str(dep) for dep in (match.order.depends_on or ())
                    if dep in by_id and by_id[dep].side == "sell"]
    for fill in match.fills:
        if fill.side == "sell":
            entry = dict(credits.get(fill.order_id) or {})
            entry["credited"] = round(
                float(entry.get("credited", 0.0))
                + float(fill.price) * int(fill.qty) - float(fill.fee), 6)
            entry.setdefault("spent", 0.0)
            credits[fill.order_id] = entry
        elif fill.side == "buy" and sell_parents:
            amount = round(float(fill.price) * int(fill.qty)
                           + float(fill.fee), 9)
            left = amount
            for parent in sorted(sell_parents):
                entry = dict(credits.get(parent) or {})
                credited = (float((credited_snapshot or {}).get(parent, 0.0))
                            if credited_snapshot is not None
                            else float(entry.get("credited", 0.0) or 0.0))
                spent = float(entry.get("spent", 0.0) or 0.0)
                available = max(0.0, credited - spent)
                take = min(left, available)
                if take > 0:
                    entry["spent"] = round(spent + take, 6)
                    entry.setdefault("credited", 0.0)
                    credits[parent] = entry
                    left = round(left - take, 9)
                if left <= 0:
                    break
            if left > 1e-6:
                raise LedgerError(
                    "换仓资金分配超过可用净卖款，拒绝错误预算")
    state.fund_credits = credits


def _lot_floor(qty: float, lot: int) -> int:
    return int(qty // lot) * lot if lot > 0 else int(qty)


def _order_id(command: TransitionCommand, event_time: int) -> str:
    from .identity import stable_order_id

    return stable_order_id(
        event_time=event_time, code=command.code, action=command.action,
        plan_id=command.plan_id, obligation_id=command.obligation_id,
        sequence=command.sequence, batch_ids=command.batch_ids,
        owner=command.owner)
