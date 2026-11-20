"""C1 共享执行核心：风险边界、容量共享、下一事件成交与期末残余。

这些用例直接验证 ``execution_core``（W2/W3 唯一执行转移）的硬边界：

- ``RiskCaps``：``None`` 未知 → 不能新增；``0`` 零额度 → 不能新增；
- 同事件多笔买单按「基准占用 + 本事件已接受风险」逐笔核算，不能一起超额；
- 主题用显式有限元组，多主题同时计数，未知风险保守叠加到已知主题；
- 事件容量由同一事件内多笔订单**共享**，不能每单重新吃满；
- 观察后只在下一合格事件成交，期末残余保留不倒填。
"""

from __future__ import annotations

import pytest

from dragon_quant.review_account.execution_core import (
    ExecutionCostModel, ExecutionQuote, LedgerState, RiskBook, RiskCaps,
    TransitionCommand, TransitionLimits, check_new_risk, transition_event,
)
from dragon_quant.review_account.execution_core.runtime_facts import (
    TEACHING_QUOTE_MAX_AGE_MS,
)
from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.review_account.strategy.bs_policy.scenario_contracts import (
    ScenarioPath, ScenarioStep,
)
from dragon_quant.tests._bs_policy_support import _account

AT = "2026-11-19T15:00:00+08:00"
NEXT = "2026-11-20T15:00:00+08:00"


def _quote(ts, price=10.0, capacity=10_000, code="600002"):
    return ExecutionQuote(
        code=code, event_id=f"evt:{ts}", event_ts=ts,
        event_time=AT if ts == 1 else NEXT,
        trade_date="2026-11-19", price=price,
        available_at=AT if ts == 1 else NEXT, tradable=True,
        capacity_qty=capacity)


def _limits(caps, *, allow_unknown=False):
    return TransitionLimits(
        cost_model=ExecutionCostModel(),
        next_trade_date=lambda day: "2026-11-20", ttl_ms=None,
        risk_caps=caps, allow_unknown_risk=allow_unknown,
        max_age_ms=TEACHING_QUOTE_MAX_AGE_MS)


def _buy(code="600002", qty=1000, risk=100.0, themes=(), plan="p1"):
    return TransitionCommand(action="buy", code=code, qty=qty, risk_amount=risk,
                             risk_themes=tuple(themes), plan_id=plan)


# ─── 风险上限语义 ────────────────────────────────────────────────────────────


def test_risk_caps_zero_is_zero_allowance_and_none_is_unknown():
    # 0 = 零额度（不是不限）；未配置 = 未知，同样不能新增风险。
    assert check_new_risk(None, 100.0) == "risk_limit_unknown"
    assert check_new_risk(RiskCaps(per_trade=0.0), 100.0) == \
        "per_trade_risk_exceeded"
    teaching = RiskCaps.teaching_fixture()
    assert check_new_risk(teaching, 100.0) is None
    assert check_new_risk(teaching, 0.0) == "risk_amount_unknown"
    assert check_new_risk(teaching, 0.0, allow_unknown_risk=True) is None


def test_unknown_group_risk_counts_toward_known_theme_budget():
    book = RiskBook()
    book.add(30.0, ())  # 既有未知主题占用
    caps = RiskCaps(per_trade=1000.0, account=1000.0, unknown_group=1000.0,
                    per_theme={"A": 50.0})
    # 未知 30 + 本笔 30（主题 A）> 50 → 拒绝：未知风险保守叠加到已知主题。
    assert check_new_risk(caps, 30.0, ("A",), book=book) == \
        "theme_risk_exceeded"


def test_multi_theme_risk_is_counted_on_every_theme():
    caps = RiskCaps(per_trade=1000.0, account=5000.0, unknown_group=1000.0,
                    per_theme={"A": 1000.0, "B": 1000.0})
    book = RiskBook()
    assert check_new_risk(caps, 600.0, ("A", "B"), book=book) is None
    book.add(600.0, ("A", "B"))
    assert book.themes == {"A": 600.0, "B": 600.0}
    assert check_new_risk(caps, 600.0, ("A",), book=book) == \
        "theme_risk_exceeded"
    assert check_new_risk(caps, 600.0, ("B",), book=book) == \
        "theme_risk_exceeded"


# ─── 同事件并发与容量共享 ────────────────────────────────────────────────────


def test_two_buys_in_one_event_cannot_overrun_the_same_budget():
    caps = RiskCaps(per_trade=1000.0, account=100.0, unknown_group=1000.0)
    state = LedgerState(cash=100_000.0)
    create = transition_event(
        state, event_time=1, trade_date="2026-11-18",
        quotes={"600002": _quote(1)},
        commands=[_buy(plan="p1", risk=60.0), _buy(plan="p2", risk=60.0)],
        limits=_limits(caps))
    # 第一笔 60 通过；第二笔看到的是**已接受后**的占用（60+60>100）。
    assert len(create.created) == 1
    assert any(item["reason"] == "account_risk_exceeded"
               for item in create.rejected)


def test_partial_fills_count_one_add_not_one_per_fill():
    plan = bs.PolicyPlan(
        plan_id="add", plan_kind=bs.PLAN_ADD, code="600001",
        target_qty=1000, risk_amount=100.0, executable=True,
        initial_entered=True, initial_adds=0,
        legs=(bs.PlanLeg(step=1, action="buy", code="600001",
                         qty_fraction=1.0, reason="add"),))
    path = _path([_step(1, 10.0, capacity=1000),
                  _step(2, 10.0, capacity=300),
                  _step(3, 10.0, capacity=1000)])
    outcome = _simulate(plan, path)
    assert outcome.timeline[1]["positions"]["600001"] == 300
    assert outcome.timeline[2]["positions"]["600001"] == 1000
    # 部分成交 + 补齐只算一次增配（不因多条成交腿重复计数）。
    assert outcome.timeline[-1]["policy_state"]["adds_done"] == 1


def test_capacity_is_shared_by_orders_in_the_same_event():
    caps = RiskCaps.teaching_fixture()
    state = LedgerState(cash=100_000.0)
    transition_event(
        state, event_time=1, trade_date="2026-11-18",
        quotes={"600002": _quote(1)},
        commands=[_buy(plan="p1", qty=1000), _buy(plan="p2", qty=1000)],
        limits=_limits(caps))
    matched = transition_event(
        state, event_time=2, trade_date="2026-11-19",
        quotes={"600002": _quote(2, capacity=600)}, commands=[],
        limits=_limits(caps))
    assert sum(fill.qty for fill in matched.fills) == 600
    reasons = [item["reason"] for item in matched.matches]
    assert "capacity_exhausted" in reasons


def test_partial_fill_keeps_risk_residual_in_order_and_batch():
    caps = RiskCaps.teaching_fixture()
    state = LedgerState(cash=100_000.0)
    transition_event(state, event_time=1, trade_date="2026-11-18",
                     quotes={"600002": _quote(1)},
                     commands=[_buy(qty=1000, risk=200.0, themes=("A",))],
                     limits=_limits(caps))
    transition_event(
        state, event_time=2, trade_date="2026-11-19",
        quotes={"600002": _quote(2, capacity=400)}, commands=[],
        limits=_limits(caps))
    order = state.orders[0]
    assert order.filled_qty == 400
    assert order.reserved_risk == pytest.approx(120.0)
    batch = state.batches[0]
    assert batch.initial_risk == pytest.approx(80.0)
    assert batch.risk_themes == ("A",)
    # 占用 = 已成交批次风险 80 + 剩余预留风险 120（单位都是元，不是资金预留）。
    book = RiskBook.from_state(state)
    assert book.total == pytest.approx(200.0)
    assert book.themes["A"] == pytest.approx(200.0)


# ─── 场景：下一事件成交 / 容量 / 期末残余（行为变化最小反例）────────────────


def _path(steps, *, fixture=True, calendar=None):
    # 显式执行日历包含最后 step 之后的下一真实交易日（T+1 可卖日不靠推断）。
    return ScenarioPath(
        scenario_id="p", kind="teaching_fixture",
        steps=tuple(steps), basis="unit", fixture=fixture,
        calendar=tuple(calendar or (DAYS + ("2026-11-26",))))


DAYS = ("2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24",
        "2026-11-25")


def _step(index, price, *, capacity=None, tradable=True):
    kwargs = ({"capacity": {"600001": capacity}}
              if capacity is not None else {})
    day = DAYS[index - 1]
    return ScenarioStep(index=index, at=f"{day}T15:00:00+08:00",
                        prices={"600001": price}, tradable={"600001": tradable},
                        trade_date=day, **kwargs)


def _plan(legs, *, risk=100.0):
    return bs.PolicyPlan(plan_id="p", plan_kind=bs.PLAN_BREAKOUT,
                         code="600001", target_qty=1000, risk_amount=risk,
                         legs=tuple(legs), executable=True)


def _simulate(plan, path, *, execution=None):
    config = bs.teaching_fixture()
    return bs.simulate_plan(
        plan, path, account=_account(cash=100_000.0),
        spec=config.scenario, execution=execution)


def test_observation_fills_only_on_later_supported_event():
    """行为变化反例 1：step2 观察到的入场只在 step3 成交（不再同 step 倒填）。"""
    plan = _plan([bs.PlanLeg(step=2, action="buy", code="600001",
                             qty_fraction=1.0, reason="entry")])
    path = _path([_step(1, 10.0, capacity=1000),
                  _step(2, 10.0, capacity=1000),
                  _step(3, 10.2, capacity=1000)])
    outcome = _simulate(plan, path)
    assert outcome.timeline[1]["positions"] == {}
    assert outcome.timeline[2]["positions"]["600001"] == 1000


def test_capacity_shortfall_leaves_order_unfilled_not_fabricated():
    """行为变化反例 2：显式容量不足 → 未成交（而不是默认无限容量全成）。"""
    plan = _plan([bs.PlanLeg(step=1, action="buy", code="600001",
                             qty_fraction=1.0, reason="entry")])
    path = _path([_step(1, 10.0, capacity=1000),
                  _step(2, 10.0, capacity=0),
                  _step(3, 10.0, capacity=1000)])
    outcome = _simulate(plan, path)
    assert outcome.timeline[1]["positions"] == {}
    # 下一事件容量恢复后才成交（残余挂单保留）。
    assert outcome.timeline[2]["positions"]["600001"] == 1000
    assert any("capacity" in item for item in outcome.unfilled)


def test_unknown_capacity_is_restricted_not_infinite():
    plan = _plan([bs.PlanLeg(step=1, action="buy", code="600001",
                             qty_fraction=1.0, reason="entry")])
    from dragon_quant.review_account.strategy.bs_policy.scenario_execution import (
        ScenarioExecution,
    )
    path = _path([_step(1, 10.0), _step(2, 10.0)], fixture=False,
                 calendar=("2026-11-19", "2026-11-20"))
    outcome = _simulate(
        plan, path,
        execution=ScenarioExecution(
            risk_caps=RiskCaps.teaching_fixture(),
            max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS))
    assert all(step["positions"] == {} for step in outcome.timeline)
    assert any("capacity_unknown" in item for item in outcome.unfilled)


def test_last_event_new_order_keeps_residual_without_backfill():
    """行为变化反例 3：最后一事件才触发/下单 → 不成交，残余保留不倒填。"""
    plan = _plan([bs.PlanLeg(step=3, action="buy", code="600001",
                             qty_fraction=1.0, reason="late")])
    path = _path([_step(1, 10.0, capacity=1000),
                  _step(2, 10.0, capacity=1000),
                  _step(3, 10.0, capacity=1000)])
    outcome = _simulate(plan, path)
    assert outcome.timeline[-1]["positions"] == {}
    assert outcome.timeline[-1]["reservations"]
    assert outcome.terminal_status == "not_entered"


# ─── S1/S2：非法风险输入 fail closed；未知存量风险限制新增 ────────────────────


def test_invalid_risk_caps_or_amounts_fail_closed():
    from dragon_quant.review_account.execution_core.models import LedgerError

    for bad in (float("nan"), float("inf"), True, -1.0):
        with pytest.raises(LedgerError):
            RiskCaps(per_trade=bad)
        with pytest.raises(LedgerError):
            RiskCaps(theme_default=bad)
        with pytest.raises(LedgerError):
            RiskCaps(per_theme={"T1": bad})
        with pytest.raises(LedgerError):
            RiskBook().add(bad, ("T1",))
        with pytest.raises(LedgerError):
            check_new_risk(RiskCaps.teaching_fixture(), bad, ("T1",))


def test_unknown_holding_risk_restricts_new_risk_unless_explicit_fixture():
    from dragon_quant.review_account.execution_core.models import Batch, LedgerState

    state = LedgerState(cash=100_000.0, batches=[Batch(
        batch_id="legacy", code="600001", qty=1000, remaining=1000,
        entry_date="2026-11-19", cost_price=10.0)])  # initial_risk 未声明
    book = RiskBook.from_state(state)
    assert book.has_unknown_holding and book.total == 0.0
    caps = RiskCaps.teaching_fixture()
    assert check_new_risk(caps, 900.0, ("T1",), book=book) == \
        "risk_unknown_holding"
    assert check_new_risk(caps, 900.0, ("T1",), book=book,
                          allow_unknown_risk=True) is None


def test_unknown_theme_risk_stacks_on_existing_known_theme():
    # main S2 反例：T1 已有 900 占用，新增未知主题 200 须按 T1 额度拒绝。
    book = RiskBook()
    book.add(900.0, ("T1",))
    caps = RiskCaps(per_trade=1000.0, account=2000.0, unknown_group=1000.0,
                    theme_default=1000.0)
    assert check_new_risk(caps, 200.0, (), book=book) == "theme_risk_exceeded"
    assert check_new_risk(caps, 100.0, (), book=book) is None


def test_scenario_unknown_holding_fails_closed_without_fixture_assumption():
    from dragon_quant.review_account.execution_core.risk import RiskCaps
    from dragon_quant.review_account.strategy.bs_policy.scenario_execution import (
        ScenarioExecution,
    )

    plan = _plan([bs.PlanLeg(step=1, action="buy", code="600001",
                             qty_fraction=1.0, reason="entry")])
    path = _path([_step(1, 10.0, capacity=1000),
                  _step(2, 10.0, capacity=1000)], fixture=False)
    account = _account(
        cash=100_000.0,
        positions=(bs.PositionView(code="600001", qty=1000, sellable_qty=1000,
                                   cost_price=10.0, entry_date="2026-11-17",
                                   mark=10.0),))
    config = bs.teaching_fixture()
    outcome = bs.simulate_plan(
        plan, path, account=account, spec=config.scenario,
        execution=ScenarioExecution(risk_caps=RiskCaps(
            per_trade=1000.0, account=5000.0, unknown_group=5000.0,
            theme_default=5000.0), max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS))
    # 存量风险未知（场景/账本入口一致）→ 新风险受限，不出成交。
    assert all(step["positions"].get("600001") == 1000
               or step["positions"] == {} for step in outcome.timeline)
    assert any("risk_unknown_holding" in item for item in outcome.unfilled)


def test_scenario_rejects_unconstructible_batch_instead_of_fabricating():
    plan = _plan([bs.PlanLeg(step=1, action="buy", code="600001",
                             qty_fraction=1.0, reason="entry")])
    path = _path([_step(1, 10.0, capacity=1000),
                  _step(2, 10.0, capacity=1000)])
    config = bs.teaching_fixture()
    no_cost = _account(
        cash=100_000.0,
        positions=(bs.PositionView(code="600001", qty=1000, sellable_qty=1000,
                                   cost_price=0.0, entry_date="2026-11-17",
                                   mark=10.0),))
    outcome = bs.simulate_plan(plan, path, account=no_cost,
                               spec=config.scenario)
    assert outcome.net_pnl is None
    assert "account_cost_missing" in outcome.reasons

    no_date = _account(
        cash=100_000.0,
        positions=(bs.PositionView(code="600001", qty=1000, sellable_qty=1000,
                                   cost_price=10.0, mark=10.0),))
    outcome = bs.simulate_plan(plan, path, account=no_date,
                               spec=config.scenario)
    assert outcome.net_pnl is None
    assert "account_entry_date_missing" in outcome.reasons


# ─── S4：依赖父腿已真实成交不得误拒；取消/拒绝仍拒绝 ───────────────────────


def _filled_parent(state, **overrides):
    from dragon_quant.review_account.execution_core.models import Order, OrderStatus

    values = dict(order_id="parent", code="600001", side="sell", qty=1000,
                  status=OrderStatus.FILLED.value, created_event=1,
                  filled_qty=1000)
    values.update(overrides)
    order = Order(**values)
    state.orders.append(order)
    return order


def test_dependency_on_filled_parent_is_allowed():
    state = LedgerState(cash=100_000.0)
    _filled_parent(state)
    command = TransitionCommand(action="buy", code="600002", qty=100,
                                plan_id="dep", risk_amount=50.0,
                                depends_on=("parent",))
    create = transition_event(
        state, event_time=2, trade_date="2026-11-19",
        quotes={"600002": _quote(2)}, commands=[command],
        limits=_limits(RiskCaps.teaching_fixture()))
    assert create.created
    assert not any(item["reason"] == "depends_on_unfilled"
                   for item in create.rejected)


def test_dependency_on_cancelled_or_rejected_parent_is_refused():
    from dragon_quant.review_account.execution_core.models import OrderStatus

    for status in (OrderStatus.CANCELLED.value, OrderStatus.REJECTED.value):
        state = LedgerState(cash=100_000.0)
        _filled_parent(state, status=status, filled_qty=0)
        create = transition_event(
            state, event_time=2, trade_date="2026-11-19",
            quotes={"600002": _quote(2)},
            commands=[TransitionCommand(action="buy", code="600002", qty=100,
                                        plan_id="dep", risk_amount=50.0,
                                        depends_on=("parent",))],
            limits=_limits(RiskCaps.teaching_fixture()))
        assert not create.created
        assert any(item["reason"] == "depends_on_unfilled"
                   for item in create.rejected)


def test_unfilled_parent_blocks_match_until_later_fill():
    state = LedgerState(cash=100_000.0)
    parent = _filled_parent(state, status="pending", filled_qty=0)
    create = transition_event(
        state, event_time=2, trade_date="2026-11-19",
        quotes={"600001": _quote(2, code="600001"),
                "600002": _quote(2)},
        commands=[TransitionCommand(action="buy", code="600002", qty=100,
                                    plan_id="dep", risk_amount=50.0,
                                    depends_on=("parent",))],
        limits=_limits(RiskCaps.teaching_fixture()))
    assert create.created  # 挂单允许；未成交父腿在撮合时再拦
    # 父腿仍未成交：买腿不得在同一/下一事件提前解锁。
    matched = transition_event(
        state, event_time=3, trade_date="2026-11-20",
        quotes={"600001": _quote(3, code="600001"),
                "600002": _quote(3)}, commands=[],
        limits=_limits(RiskCaps.teaching_fixture()))
    assert any(item["reason"] == "depends_on_unfilled"
               for item in matched.matches)
    assert parent.order_id == "parent"


def test_missing_next_trade_date_keeps_buy_unfilled_not_fabricated():
    # S3：执行日历不足（无法确定 T+1 可卖日）→ 不成交，保留挂单/受限原因。
    plan = _plan([bs.PlanLeg(step=1, action="buy", code="600001",
                             qty_fraction=1.0, reason="entry")])
    path = _path([_step(1, 10.0, capacity=1000),
                  _step(2, 10.0, capacity=1000)],
                 calendar=("2026-11-19", "2026-11-20"))
    outcome = _simulate(plan, path)
    assert all(step["positions"] == {} for step in outcome.timeline)
    assert any("trade_calendar_insufficient" in item for item in outcome.unfilled)
    # 挂单仍保留（剩余未成交），没有被伪造成交或静默丢弃。
    assert outcome.timeline[-1]["reservations"]
