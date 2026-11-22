"""W2 场景适配器：把外部路径/计划/账户映射到共享执行核心（纯计算）。

与 W3 ``AccountEngine`` 的差异只在**输入/结果适配**：本模块把场景 step 变成
``ExecutionQuote``（源时点/可得时点/显式容量/交易日历），把计划腿/保护义务/退出
政策变成 ``TransitionCommand``，调用**同一个** :func:`execution_core.transition_event`
后读回账本状态。它不复制撮合/费用/T+1/容量/预留规则，也不写数据库。

执行支持显式化（C1）：容量来自 step 的显式 ``capacity``；fixture 路径缺省时使用
**声明过的固定教学容量**（不是无限）；非 fixture 路径容量未知 → 不成交。交易日历
来自路径显式声明或 step 的 ``trade_date``；不做 ``~000001`` 之类伪日期推断。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping, Optional

from dragon_quant.review_account.execution_core.matching import (
    ExecutionCostModel, ExecutionQuote,
)
from dragon_quant.review_account.execution_core.models import (
    SELLABLE_AFTER, SELLABLE_ON, Batch, LedgerState,
)
from dragon_quant.review_account.execution_core.risk import RiskBook, RiskCaps
from dragon_quant.review_account.execution_core.runtime_facts import (
    MARK_LAST_CLOSE, MARK_QUOTE, TEACHING_QUOTE_MAX_AGE_MS, MarkFact,
    apply_fill_facts, mark_prices, reduce_batch_facts, validate_max_age_ms,
)
from dragon_quant.review_account.execution_core.transition import (
    TransitionCommand, TransitionLimits, TransitionResult, transition_event,
)

from .account import AccountView
from .config import ScenarioSpec
from .scenario_contracts import ScenarioPath, ScenarioStep

#: 教学 fixture 缺省容量（股/事件）。显式有限假设，不是“无限”；生产路径不给默认。
FIXTURE_CAPACITY_QTY = 1_000_000


class ScenarioInputError(ValueError):
    """账户快照不足以构造真实批次（成本/入场日缺失）：显式拒绝，不伪造事实。"""

    def __init__(self, reason: str, code: str = "") -> None:
        super().__init__(f"{reason}:{code}" if code else reason)
        self.reason = reason
        self.code = code


@dataclass(frozen=True)
class ScenarioExecution:
    """场景执行支持声明（显式；默认按 fixture/生产分别受限）。"""

    calendar: tuple[str, ...] = ()
    risk_caps: Optional[RiskCaps] = None
    allow_unknown_risk: Optional[bool] = None
    fixture_capacity: Optional[int] = None
    #: 显式新鲜度规格（年龄毫秒）；``None`` = 未知（新增风险受限）。fixture 路径
    #: 若未显式给出，由 ``ScenarioRun.build`` 显式装配教学假设（不流入生产默认）。
    max_quote_age_ms: Optional[int] = None

    def resolved_max_age_ms(self, fixture: bool) -> Optional[int]:
        value = validate_max_age_ms(self.max_quote_age_ms)
        if value is None and fixture:
            return TEACHING_QUOTE_MAX_AGE_MS
        return value


def _cost(view, position, code: str) -> float:
    value = getattr(view, "cost_price", 0.0) or position.cost_price
    if value is None or value <= 0:
        # 成本未知不能伪造成 1e-6；显式拒绝不可构造的批次。
        raise ScenarioInputError("account_cost_missing", code)
    return float(value)


def _entry_date(view, position, code: str) -> str:
    value = getattr(view, "entry_date", "") or position.entry_date
    if not value:
        raise ScenarioInputError("account_entry_date_missing", code)
    return value


def _batch_from_view(view, position, asof: str) -> list[Batch]:
    """把只读批次映射为账本批次；成本/入场日缺失显式拒绝（不伪造事实）。"""
    base_cost = _cost(view, position, position.code)
    entry = _entry_date(view, position, position.code)
    sellable_date = getattr(view, "sellable_date", None)
    sellable_qty = int(view.sellable_qty)
    family_id = str(getattr(view, "family_id", "") or "")
    initial_risk = float(getattr(view, "initial_risk", 0.0) or 0.0)
    batches: list[Batch] = []
    if bool(getattr(view, "ledger_origin", False)) \
            or str(view.batch_id).endswith((":sellable", ":locked")):
        # 已是账本批次身份（投影视图再次进入场景）：不再二次加后缀，保持与
        # 计划动作/订单的 batch scope 一致；可卖模式/日期按账本事实复原。
        qty = int(view.qty)
        if sellable_qty > 0:
            # as-of 决策时已可卖：场景步骤是相对决策后的共同路径，按已释放
            # 处理（与账本本事件可卖事实一致）。
            mode, release = SELLABLE_ON, asof
        else:
            # 未释放（T+1/未来可卖日）：相对场景窗口内保持不可卖，明确 pending，
            # 不把未到期批次冒充可退。
            mode = str(getattr(view, "sellable_mode", "") or SELLABLE_AFTER)
            release = sellable_date or entry
        if qty > 0:
            batches.append(Batch(
                batch_id=str(view.batch_id), code=position.code,
                qty=qty, remaining=qty, entry_date=entry,
                cost_price=base_cost,
                strategy_ref=view.strategy_ref or position.strategy_ref,
                plan_id=view.plan_id, source="position",
                sellable_mode=mode, sellable_date=release,
                family_id=family_id, initial_risk=initial_risk))
        return batches
    if sellable_qty > 0:
        # 只读视图已声明「as-of 时点可卖」的 legacy 数量：绑定显式 asof 日期。
        batches.append(Batch(
            batch_id=f"{view.batch_id}:sellable", code=position.code,
            qty=sellable_qty, remaining=sellable_qty,
            entry_date=entry, cost_price=base_cost,
            strategy_ref=view.strategy_ref or position.strategy_ref,
            plan_id=view.plan_id, source="position",
            sellable_mode=SELLABLE_ON, sellable_date=asof,
            family_id=family_id, initial_risk=initial_risk))
    locked = int(view.qty) - sellable_qty
    if locked > 0:
        if sellable_date:
            batches.append(Batch(
                batch_id=f"{view.batch_id}:locked", code=position.code,
                qty=locked, remaining=locked, entry_date=entry,
                cost_price=base_cost,
                strategy_ref=view.strategy_ref or position.strategy_ref,
                plan_id=view.plan_id, source="position",
                sellable_mode=SELLABLE_ON, sellable_date=sellable_date,
                family_id=family_id, initial_risk=initial_risk))
        else:
            batches.append(Batch(
                batch_id=f"{view.batch_id}:locked", code=position.code,
                qty=locked, remaining=locked, entry_date=entry,
                cost_price=base_cost,
                strategy_ref=view.strategy_ref or position.strategy_ref,
                plan_id=view.plan_id, source="position",
                sellable_mode=SELLABLE_AFTER, sellable_date=entry,
                family_id=family_id, initial_risk=initial_risk))
    return batches


def _legacy_batches(position, asof: str) -> list[Batch]:
    base_cost = _cost(position, position, position.code)
    entry = _entry_date(position, position, position.code)
    batches: list[Batch] = []
    if position.sellable_qty > 0:
        batches.append(Batch(
            batch_id=f"legacy:{position.code}:sellable", code=position.code,
            qty=position.sellable_qty, remaining=position.sellable_qty,
            entry_date=entry, cost_price=base_cost,
            strategy_ref=position.strategy_ref, source="position",
            sellable_mode=SELLABLE_ON, sellable_date=asof))
    locked = position.qty - position.sellable_qty
    if locked > 0:
        batches.append(Batch(
            batch_id=f"legacy:{position.code}:locked", code=position.code,
            qty=locked, remaining=locked, entry_date=entry,
            cost_price=base_cost, strategy_ref=position.strategy_ref,
            source="position_locked", sellable_mode=SELLABLE_AFTER,
            sellable_date=entry))
    return batches


def seed_ledger_state(account: AccountView, *, asof: str = "",
                      recovery=None, policy_states=(),
                      pending_orders=(), reservations=(),
                      mark_facts=None, batch_facts=None,
                      fund_credits=None) -> LedgerState:
    """只读账户快照 → 账本工作状态（W2 场景与公共 replay 共用的种子适配）。

    完整带出现金/峰值/回撤/连亏与批次真值（family/归属/可卖日），不伪造成本或
    入场日（缺失显式拒绝）。可选检查点（恢复锁存/政策状态/未成订单/预留/标记
    事实/逐批事实/资金归属）按传入的**已校验**值写入工作状态，供 advice 与
    review/replay 共用同一账户适配，不另造平行账本；非空检查点缺证明由调用方
    显式拒绝，不能接受后丢弃。账户快照的 ``mark`` 只是起始旧估值：保留为
    ``last_close`` 事实，待真实事件行情覆盖，不当作可执行价或买后峰值。
    """
    state = LedgerState(
        cash=float(account.cash or 0.0),
        peak_equity=float(account.peak_equity or 0.0),
        drawdown_pct=float(account.drawdown_pct or 0.0),
        consecutive_losses=int(account.consecutive_losses or 0))
    for position in account.positions:
        record = {
            "trend": str(getattr(position, "trend", "unknown") or "unknown"),
            "held_days": getattr(position, "held_days", None),
            "pressure_pct": getattr(position, "pressure_pct", None),
            "theme_ids": list(getattr(position, "theme_ids", ()) or ()),
            "as_of": asof, "source": "account_input_checkpoint"}
        if position.batches:
            for view in position.batches:
                state.position_facts[str(view.batch_id)] = dict(record)
        else:
            state.position_facts[f"code:{position.code}"] = dict(record)
    for position in account.aggregated_positions():
        views = list(position.batches) or [None]
        for view in views:
            if view is None:
                state.batches.extend(_legacy_batches(position, asof))
            else:
                state.batches.extend(_batch_from_view(view, position, asof))
    for position in account.positions:
        price = position.mark
        if price is not None and price > 0:
            state.marks[position.code] = float(price)
            state.mark_facts[position.code] = MarkFact(
                code=position.code, price=float(price), source_ts=-1,
                available_at=-1, kind=MARK_LAST_CLOSE)
    if recovery is not None:
        row = (recovery.to_dict() if hasattr(recovery, "to_dict")
               else dict(recovery))
        state.recovery = row
        state.recovery_stage = int(row.get("stage") or 0)
    if policy_states:
        state.policy_states = [
            item.to_dict() if hasattr(item, "to_dict") else dict(item)
            for item in policy_states]
    if pending_orders:
        state.orders = list(pending_orders)
    if reservations:
        state.reservations = list(reservations)
    if mark_facts:
        state.mark_facts.update(dict(mark_facts))
        state.marks.update({code: fact.price
                            for code, fact in mark_facts.items()
                            if getattr(fact, "executable", False)})
    if batch_facts:
        state.batch_facts = dict(batch_facts)
    if fund_credits:
        state.fund_credits = dict(fund_credits)
    return state


def execution_from_config(config) -> ScenarioExecution:
    """从显式 BS 政策配置构造场景执行限额（不把 fixture 灌生产，不自动无限）。"""
    budgets = config.budgets
    caps = RiskCaps(per_trade=float(budgets.per_trade),
                    account=float(budgets.account),
                    unknown_group=float(budgets.unknown_group),
                    theme_default=float(budgets.per_theme))
    # 教学 fixture 显式允许“存量风险未知”的假设；生产配置保持 fail closed。
    # 场景模拟（W2）每个 step 都**显式声明**自己的观察价与事件时点，因此模拟执行
    # 侧显式声明新鲜度规格（研究/仿真假设）；生产账户路径（W3 AccountEngine）仍
    # 要求调用方按 provider/周期显式装配，未声明即新增风险受限。
    return ScenarioExecution(risk_caps=caps,
                             allow_unknown_risk=bool(config.fixture),
                             max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)


def _execution_model(spec: ScenarioSpec) -> ExecutionCostModel:
    return ExecutionCostModel(
        commission_rate=spec.commission_rate, min_commission=spec.min_commission,
        stamp_tax_rate=spec.stamp_tax_rate, buy_slippage=spec.buy_slippage,
        sell_slippage=spec.sell_slippage, lot_size=spec.lot_size)


@dataclass
class ScenarioRun:
    """一个场景路径的执行状态（纯计算，不落库，不读时钟）。"""

    path: ScenarioPath
    spec: ScenarioSpec
    execution: ScenarioExecution
    state: LedgerState
    cost_model: ExecutionCostModel
    allow_unknown_risk: bool
    calendar: tuple[str, ...]
    risk_caps: Optional[RiskCaps]
    max_quote_age_ms: Optional[int] = None
    last_prices: dict = field(default_factory=dict)
    unfilled: list[str] = field(default_factory=list)
    execution_cost: float = 0.0
    traded_notional: float = 0.0
    pending_exits: dict[str, list[str]] = field(default_factory=dict)
    deployable: Optional[float] = None
    step_fills: list = field(default_factory=list)

    @classmethod
    def build(cls, account: AccountView, path: ScenarioPath,
              spec: ScenarioSpec, *,
              execution: Optional[ScenarioExecution] = None) -> "ScenarioRun":
        execution = execution or ScenarioExecution()
        calendar = tuple(execution.calendar or path.trade_dates())
        # 没有真实交易日不能推断伪日期；有持仓时由调用方显式报告受限。
        asof = calendar[0] if calendar else ""
        state = seed_ledger_state(account, asof=asof)
        risk_caps = execution.risk_caps
        if risk_caps is None and path.fixture:
            risk_caps = RiskCaps.teaching_fixture()
        allow_unknown = (execution.allow_unknown_risk
                         if execution.allow_unknown_risk is not None
                         else bool(path.fixture))
        return cls(path=path, spec=spec, execution=execution, state=state,
                   cost_model=_execution_model(spec),
                   allow_unknown_risk=allow_unknown, calendar=calendar,
                   risk_caps=risk_caps,
                   max_quote_age_ms=execution.resolved_max_age_ms(
                       bool(path.fixture)),
                   deployable=(None if spec.reinvest_cash
                               else float(account.cash or 0.0)))

    # ── 事件适配 ────────────────────────────────────────────────────────────
    def next_trade_date(self, day: str) -> Optional[str]:
        for item in self.calendar:
            if item > day:
                return item
        return None

    def capacity_for(self, step: ScenarioStep, code: str) -> Optional[int]:
        declared = step.capacity_qty(code)
        if declared is not None:
            return declared
        if self.path.fixture:
            return (self.execution.fixture_capacity
                    if self.execution.fixture_capacity is not None
                    else FIXTURE_CAPACITY_QTY)
        return None

    def quote(self, step: ScenarioStep, code: str) -> ExecutionQuote:
        price = step.price(code)
        return ExecutionQuote(
            code=code, event_id=f"step:{self.path.scenario_id}:{step.index}",
            event_ts=step.index, event_time=step.at or "",
            trade_date=step.trade_date, price=price,
            available_at=step.support_at(), tradable=step.can_fill(code),
            capacity_qty=self.capacity_for(step, code))

    def _prepare_event(self, step: ScenarioStep):
        facts = dict(self.state.mark_facts)
        for code in step.prices:
            price = step.price(code)
            if price is not None and price > 0:
                self.last_prices[code] = price
                facts[code] = MarkFact(
                    code=code, price=float(price), source_ts=int(step.index),
                    available_at=int(step.index), kind=MARK_QUOTE,
                    stale=not step.can_fill(code),
                    bar_high=(step.bar_highs.get(code)
                              if step.bar_complete else None),
                    bar_complete=bool(step.bar_complete),
                    interval_start_ts=step.interval_start,
                    interval_end_ts=step.interval_end)
        self.state.mark_facts = facts
        self.state.marks = mark_prices(facts)
        self.state.trade_date = step.trade_date
        quotes = {code: self.quote(step, code) for code in step.prices}
        limits = TransitionLimits(
            cost_model=self.cost_model,
            next_trade_date=self.next_trade_date, ttl_ms=None,
            risk_caps=self.risk_caps,
            risk_book=RiskBook.from_state(self.state),
            allow_unknown_risk=self.allow_unknown_risk,
            cash_limit=self.deployable,
            max_age_ms=self.max_quote_age_ms)
        return quotes, limits

    def execute(self, step: ScenarioStep,
                commands: Iterable[TransitionCommand]) -> TransitionResult:
        """与 AccountEngine 同一顺序：观察→命令/撤单→撮合既有挂单。

        新命令在本事件创建（``created_event == event_time``）不会同事件成交；
        观察用的持仓/可卖量是**本事件撮合前**的状态，两个适配器语义一致。
        """
        quotes, limits = self._prepare_event(step)
        batches_before = {batch.batch_id: batch for batch in self.state.batches}
        prepared = [self._budget(command, quotes) for command in commands or ()]
        result = transition_event(
            self.state, event_time=step.index, trade_date=step.trade_date,
            quotes=quotes, commands=[item for item in prepared if item is not None],
            limits=limits, evidence_available=step.evidence_available)
        self.state.batch_facts = apply_fill_facts(
            reduce_batch_facts(
                self.state.batch_facts, batches=self.state.batches,
                marks=self.state.mark_facts, event_ts=int(step.index),
                trade_date=step.trade_date, calendar=self.calendar),
            fills=result.fills, batches_before=batches_before,
            event_ts=int(step.index))
        self.step_fills = list(result.fills)
        self._book_fills(step, result)
        self._record_unfilled(step, result)
        return result

    def _budget(self, command: TransitionCommand,
                quotes: Mapping[str, ExecutionQuote]
                ) -> Optional[TransitionCommand]:
        """``reinvest_cash=False`` 时买入只能用初始可部署资金（不能动用卖出款）。"""
        if command.action != "buy" or self.deployable is None:
            return command
        quote = quotes.get(command.code)
        price = (command.limit_price if command.limit_price is not None
                 else (quote.price if quote is not None else None))
        if price is None or price <= 0:
            return command
        exec_price = self.cost_model.execution_price(price, "buy")
        lot = self.spec.lot_size
        affordable = int(self.deployable // (exec_price * lot)) * lot
        if affordable <= 0:
            self.unfilled.append(
                f"{command.code}:buy:{command.plan_id}:insufficient_cash")
            return None
        if affordable >= command.qty:
            return command
        return replace(command, qty=affordable)

    def _book_fills(self, step: ScenarioStep, result: TransitionResult) -> None:
        for fill in result.fills:
            reference = self.last_prices.get(fill.code)
            if reference is None:
                continue
            amount = fill.price * fill.qty
            if fill.side == "buy":
                self.execution_cost += (fill.price - reference) * fill.qty + fill.fee
                if self.deployable is not None:
                    self.deployable = max(0.0, self.deployable
                                          - (amount + fill.fee))
            else:
                self.execution_cost += (reference - fill.price) * fill.qty + fill.fee
            self.traded_notional += amount

    def _record_unfilled(self, step: ScenarioStep,
                         result: TransitionResult) -> None:
        for item in result.rejected:
            self.unfilled.append(
                f"{item['code']}:{step.index}:{item.get('plan', 'plan')}:"
                f"{item['reason']}")
        for match in result.matches:
            reason = match.get("reason") or ""
            if not reason or reason == "no_later_event":
                continue
            label = _label(reason)
            self.unfilled.append(
                f"{match.get('code', '')}:{step.index}:{label}:{reason}")

    # ── 结果读取 ────────────────────────────────────────────────────────────
    def positions(self, trade_date: str = "") -> dict[str, int]:
        """当前持仓数量（只读；``trade_date`` 保留给调用方语义对齐）。"""
        return {batch.code: self.state.position_qty(batch.code)
                for batch in self.state.batches if batch.remaining > 0}

    def sellable(self, trade_date: str) -> dict[str, int]:
        values: dict[str, int] = {}
        for batch in self.state.batches:
            if batch.remaining <= 0:
                continue
            values[batch.code] = values.get(batch.code, 0) + batch.sellable(trade_date)
        return values

    def owned_qty(self, code: str, owner: Mapping,
                  batch_ids: Iterable[str] = ()) -> int:
        """本计划冻结归属的持仓数量；``batch_ids`` 非空时限定批次 scope。"""
        from dragon_quant.review_account.execution_core.identity import (
            ref_identity,
        )
        key = ref_identity(owner)
        scope = frozenset(batch_ids or ())
        return sum(batch.remaining for batch in self.state.batches
                   if batch.code == code
                   and ref_identity(batch.strategy_ref) == key
                   and (not scope or batch.batch_id in scope))

    def filled_buy_for_plan(self, code: str, owner: Mapping,
                            plan_id: str):
        """本 run 内**已真实成交的父买单**（G3 add 的依赖来源）。"""
        from dragon_quant.review_account.execution_core.identity import (
            ref_identity,
        )

        if not plan_id:
            return None
        key = ref_identity(owner)
        for order in self.state.orders:
            if order.side != "buy" or order.code != code:
                continue
            if order.plan_id != plan_id or order.filled_qty <= 0:
                continue
            if ref_identity(order.strategy_ref) != key:
                continue
            return order
        return None

    def has_prior_fill(self, code: str, owner: Mapping, *, prior_plan_id: str,
                       family_id: str) -> bool:
        """账户已持有同家族真实批次（probe 已成交）→ 允许后续 add 依存。"""
        from dragon_quant.review_account.execution_core.identity import (
            ref_identity,
        )

        key = ref_identity(owner)
        for batch in self.state.batches:
            if batch.code != code or batch.remaining <= 0:
                continue
            if ref_identity(batch.strategy_ref) != key:
                continue
            if (prior_plan_id and batch.plan_id == prior_plan_id) \
                    or (family_id and (batch.family_id == family_id
                                       or batch.plan_id == family_id)):
                return True
        return False

    def owned_sellable(self, code: str, trade_date: str,
                       owner: Mapping, batch_ids: Iterable[str] = ()) -> int:
        """本计划冻结归属的可卖批次；``batch_ids`` 非空时限定批次 scope。"""
        from dragon_quant.review_account.execution_core.identity import (
            ref_identity,
        )
        key = ref_identity(owner)
        scope = frozenset(batch_ids or ())
        return sum(batch.sellable(trade_date) for batch in self.state.batches
                   if batch.code == code
                   and ref_identity(batch.strategy_ref) == key
                   and (not scope or batch.batch_id in scope))

    def batch_state(self) -> dict:
        result: dict[str, list[dict]] = {}
        for batch in sorted(self.state.batches, key=lambda item: item.batch_id):
            if batch.remaining <= 0:
                continue
            result.setdefault(batch.code, []).append({
                "qty": batch.remaining,
                "sellable": batch.sellable(self.state.trade_date) > 0,
                "release_mode": batch.sellable_mode,
                "release_date": batch.sellable_date or "",
                "origin": "purchase" if batch.source == "runtime" else "position",
                "family_id": batch.family_id or "",
                "plan_id": batch.plan_id or "",
            })
        return result

    def equity(self) -> Optional[float]:
        return self.state.marked_equity()

    def open_sell(self, code: str) -> list:
        return [order for order in self.state.orders
                if order.code == code and order.side == "sell"
                and not order.is_terminal and order.quantity_remaining > 0]


def _label(reason: str) -> str:
    if reason in ("untradable", "not_tradable", "quote_from_future",
                  "quote_time_missing", "quote_stale", "price_missing",
                  "limit_up_locked", "limit_down_locked"):
        return "not_tradable"
    return reason
