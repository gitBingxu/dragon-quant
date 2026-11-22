"""C3b3 C7：真实换仓（父卖 + 子买，卖出净到账资助）。

覆盖：同事件不成交、子买依赖本次事件分配的父卖稳定 ID、partial 卖只按真实
已实现净到账资助（不并入自由现金）、父腿拒绝/取消时子买不可成交、资金归属簿
重启保留、多个子单不双花同一笔卖款。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from dragon_quant.review_account.evidence import CapabilityCheck
from dragon_quant.review_account.execution_core.matching import ExecutionCostModel
from dragon_quant.review_account.execution_core.models import (
    Batch, LedgerState, SELLABLE_ON,
)
from dragon_quant.review_account.execution_core.risk import RiskCaps
from dragon_quant.review_account.execution_core.runtime_facts import (
    TEACHING_QUOTE_MAX_AGE_MS,
)
from dragon_quant.review_account.execution_core.transition import (
    TransitionCommand, TransitionLimits, transition_event,
)
from dragon_quant.review_account.ledger import AccountLedger
from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.review_account.strategy.account_engine import AccountEngine
from dragon_quant.review_account.strategy.account_runtime import (
    AccountPolicyRuntime,
)
from dragon_quant.tests._bs_policy_support import _scenarios
from dragon_quant.tests.support import patch_storage_dbs

_CST = timezone(timedelta(hours=8))

DAY1, DAY2, DAY3, DAY4, DAY5 = (
    "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24")
CALENDAR = (DAY1, DAY2, DAY3, DAY4, DAY5, "2026-11-25")
#: 触发日之后老仓下跌、候选后段上涨：换仓在共同路径上优于持有/普通入场。
SWITCH_PRICES = {"600001": [10.5, 10.5, 10.5, 9.9, 9.45],
                 "600002": [10.5, 10.5, 10.5, 10.5, 12.0]}


def _ms(day, hour=10):
    return int(datetime(2026, 11, day, hour, tzinfo=_CST).timestamp() * 1000)


T1, T2, T3, T4 = (_ms(18), _ms(19), _ms(20), _ms(23))


@contextmanager
def _env(tmp_path):
    dragon = str(tmp_path / "dragon.db")
    with patch_storage_dbs(dragon, str(tmp_path / "assets.db")), \
            patch("dragon_quant.infra.storage.paths.DB_PATH", Path(dragon)):
        yield


class _Event:
    def __init__(self, ts, rows, day):
        self.timestamp = ts
        self.trade_date = day
        self.rows = rows
        self.unavailable = {}
        self.last_closes = {}
        self.candidates = []
        self.phase = "bar"
        self.allow_buy = True
        self.evidence_available = ()


def _row(ts, price, *, capacity=10 ** 9):
    return {"execution_price": price, "execution_timestamp": ts,
            "available_at": ts, "available_qty": capacity, "tradable": True}


def _checks(*rules):
    return tuple(CapabilityCheck(rule_id=rule, capability="intraday_bars",
                                 status="usable", protects="new_risk")
                 for rule in rules)


def _candidate(price=10.5, code="600002"):
    return bs.CandidateView(
        code=code, name="候选二", eligible=True, tradable=True, price=price,
        pressure_pct=0.10, rank=1, theme_ids=("T2",),
        structure={"breakout_level": 10.2, "pullback_level": 9.8,
                   "confirm_level": 10.5, "probe_level": 10.5},
        evidence_checks=_checks(f"entry:confirm:{code}",
                                f"entry:breakout:{code}"))


def _ref(config):
    from dragon_quant.workspace import snapshots
    return snapshots.builtin_ref(strategy_id=bs.BS_ACCOUNT_STRATEGY_ID,
                                 params={"bs_policy": config.to_dict()})


def _seed_incumbent(ledger, config, *, qty=1000, cost=10.0, day=DAY1):
    state = ledger.load_state()
    state.batches = [Batch(
        batch_id="incumbent", code="600001", qty=qty, remaining=qty,
        entry_date="2026-11-17", cost_price=cost, initial_risk=100.0,
        strategy_ref=_ref(config), plan_id="legacy:600001",
        sellable_mode=SELLABLE_ON, sellable_date=day)]
    state.marks = {"600001": 10.5}
    ledger.commit_event(expected_revision=state.revision, event_id="seed",
                        source_hash="seed", content_hash="seed", state=state)


def _engine(ledger, config, *, ttl=None):
    return AccountEngine(
        ledger, AccountPolicyRuntime(config, strategy_ref=_ref(config)),
        ExecutionCostModel(), calendar=list(CALENDAR), order_ttl_ms=ttl,
        risk_caps=RiskCaps.teaching_fixture(),
        max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)


def _decide_kwargs(config, *, prices=None):
    series = prices or SWITCH_PRICES
    return {
        "scenarios": _scenarios(config, {"up": series},
                                trade_dates=list(CALENDAR[:5])),
        "market": bs.MarketView(regime="trend_up", new_risk_allowed=True),
        "pressures": {"600001": 0.10, "600002": 0.10},
        "themes": {"600001": ("T1",), "600002": ("T2",)},
        "trends": {"600001": "down", "600002": "up"},
        "held_days": {"600001": 2},
    }


def _decide_switch(engine, config, *, ts=T1, day=DAY1, capacity=10 ** 9):
    return engine.process_event(
        _Event(ts, {"600001": _row(ts, 10.5, capacity=capacity),
                    "600002": _row(ts, 10.5)}, day),
        decide=True, candidates=(_candidate(),),
        decision_kwargs=_decide_kwargs(config))


def _switch_orders(engine):
    state = engine.state()
    sells = [o for o in state.orders
             if o.side == "sell" and o.plan_id.startswith("switch:")]
    buys = [o for o in state.orders
            if o.side == "buy" and o.plan_id.startswith("switch:")]
    return sells, buys


def test_switch_creates_parent_sell_and_funded_child_no_same_event_fill(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g7-switch")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        _seed_incumbent(ledger, config)
        engine = _engine(ledger, config)
        first = _decide_switch(engine, config)
        assert first["bundle"].selected_plan_id.startswith("switch:")
        intents = [(i.action, i.code, i.qty, i.sequence)
                   for i in first["documented_intents"]]
        assert intents[0][:3] == ("sell", "600001", 1000)
        assert intents[1][:3] == ("buy", "600002", 900)
        assert first["fills"] == []                    # 同事件不成交
        sells, buys = _switch_orders(engine)
        assert len(sells) == 1 and len(buys) == 1
        assert buys[0].depends_on == (sells[0].order_id,)
        assert buys[0].family_id == sells[0].family_id == \
            first["bundle"].selected_plan_id
        assert sells[0].batch_ids == ("incumbent",)
        # 下一事件：父卖真实成交并入资金归属簿；子买本事件仍不成交。
        second = engine.process_event(
            _Event(T2, {"600001": _row(T2, 10.5), "600002": _row(T2, 10.5)},
                   DAY2))
        assert [f["side"] for f in second["fill_records"]] == ["sell"]
        credit = engine.state().fund_credits[sells[0].order_id]
        assert credit["credited"] > 0 and credit["spent"] == 0.0
        assert [o.code for o in _switch_orders(engine)[1]] == ["600002"]
        assert not [f for f in second["fill_records"] if f["side"] == "buy"]
        # 再下一事件：子买只动用真实净到账（不并入 10 万自由现金）。
        third = engine.process_event(
            _Event(T3, {"600001": _row(T3, 10.5),
                        "600002": _row(T3, 13.0)}, DAY3))
        buys_fills = [f for f in third["fill_records"] if f["side"] == "buy"]
        assert buys_fills and buys_fills[0]["qty"] < 900
        assert buys_fills[0]["price"] * buys_fills[0]["qty"] \
            <= credit["credited"] + 1e-6
        state = engine.state()
        assert state.cash > 0
        assert state.position_qty("600001") == 0
        assert state.position_qty("600002") > 0


def test_partial_sell_only_realized_net_funds_child(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g7-partial")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        _seed_incumbent(ledger, config)
        engine = _engine(ledger, config)
        _decide_switch(engine, config)
        sells, _buys = _switch_orders(engine)
        second = engine.process_event(
            _Event(T2, {"600001": _row(T2, 10.5, capacity=400),
                        "600002": _row(T2, 10.5)}, DAY2))
        assert [f["qty"] for f in second["fill_records"]] == [400]
        credit = engine.state().fund_credits[sells[0].order_id]["credited"]
        assert credit < 400 * 10.5 + 1e-6
        third = engine.process_event(
            _Event(T3, {"600001": _row(T3, 10.5, capacity=0),
                        "600002": _row(T3, 10.5)}, DAY3))
        fills = [f for f in third["fill_records"] if f["side"] == "buy"]
        assert fills
        notional = sum(f["price"] * f["qty"] + f["fee"] for f in fills)
        assert notional <= credit + 1e-6           # 只用已实现净卖款
        assert fills[0]["qty"] < 900
        # 父卖在同一事件新增成交不得**当场**解锁更多子买（严格更晚事件）。
        fourth = engine.process_event(
            _Event(T3 + 3_600_000,
                   {"600001": _row(T3 + 3_600_000, 10.5, capacity=1000),
                    "600002": _row(T3 + 3_600_000, 10.5)},
                   DAY3))
        assert [f["side"] for f in fourth["fill_records"]] == ["sell"]
        # 下一事件才可用新增净到账继续补子单。
        fifth = engine.process_event(
            _Event(_ms(23), {"600001": _row(_ms(23), 10.5, capacity=0),
                             "600002": _row(_ms(23), 10.5)}, DAY4))
        assert [f for f in fifth["fill_records"] if f["side"] == "buy"]
        # 重启：资金归属簿随检查点保留，剩余子单不越已实现净款。
        state = engine.state()
        assert state.fund_credits
        restarted = _engine(ledger, config)
        reloaded = restarted.state()
        assert reloaded.fund_credits == state.fund_credits


def test_switch_requires_sellable_parent(tmp_path):
    """无可用卖量时换仓计划不可执行：不产生父卖，也不提前建立子买。"""
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g7-rejected")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        # 当日买入批次 T+1 不可卖：父卖无可用量。
        _seed_incumbent(ledger, config, day=DAY2)   # sellable_date 晚于事件日
        engine = _engine(ledger, config)
        first = _decide_switch(engine, config)
        switch = [p for p in first["bundle"].plans
                  if p.plan_kind == bs.PLAN_SWITCH]
        assert switch and not switch[0].executable
        assert "no_sellable_quantity" in switch[0].blocked_reasons
        assert not [i for i in first["documented_intents"]
                    if i.plan_id.startswith("switch:")]
        sells, buys = _switch_orders(engine)
        assert not sells and not buys


def _quote(code, ts, day, price=10.0):
    from dragon_quant.review_account.execution_core.matching import (
        ExecutionQuote,
    )
    stamp = {T1: "2026-11-18", T2: "2026-11-19", T3: "2026-11-20",
             T4: "2026-11-23"}[ts]
    return ExecutionQuote(
        code=code, event_id=f"e:{ts}:{code}", event_ts=ts,
        event_time=f"{stamp}T10:00:00+08:00", trade_date=day,
        price=price, available_at=f"{stamp}T10:00:00+08:00",
        tradable=True, capacity_qty=10 ** 9)


def test_child_not_double_spending_one_sell_credit():
    """共享执行核心：两子单按真实预留扣减，不得双花同一笔卖出净到账。"""
    config = bs.teaching_fixture()
    owner = _ref(config)
    sell = Batch(batch_id="incumbent", code="600001", qty=1000, remaining=1000,
                 entry_date="2026-11-17", cost_price=10.0, initial_risk=1.0,
                 strategy_ref=owner, plan_id="switch:x",
                 sellable_mode=SELLABLE_ON, sellable_date=DAY1)
    state = LedgerState(cash=1000.0, batches=[sell], trade_date=DAY1,
                        marks={"600001": 10.0, "600002": 10.0,
                               "600003": 10.0})
    limits = TransitionLimits(
        cost_model=ExecutionCostModel(min_commission=0.0),
        next_trade_date=lambda day: DAY3, ttl_ms=None,
        risk_caps=RiskCaps.teaching_fixture(),
        max_age_ms=TEACHING_QUOTE_MAX_AGE_MS)
    sell_cmd = TransitionCommand(
        action="sell", code="600001", qty=1000, owner=owner,
        plan_id="switch:x", sequence=0, batch_ids=("incumbent",))
    r1 = transition_event(state, event_time=T1, trade_date=DAY1,
                          quotes={"600001": _quote("600001", T1, DAY1)},
                          commands=[sell_cmd], limits=limits)
    sell_id = r1.created[0]
    # 子买与父卖同事件建立（依赖父卖稳定 ID）；本事件都不成交。
    child = TransitionCommand(
        action="buy", code="600002", qty=1000, owner=owner,
        plan_id="switch:x", sequence=1, depends_on=(sell_id,),
        risk_amount=100.0)
    child_other = TransitionCommand(
        action="buy", code="600003", qty=1000, owner=owner,
        plan_id="switch:x", sequence=2, depends_on=(sell_id,),
        risk_amount=100.0)
    transition_event(state, event_time=T1, trade_date=DAY1,
                     quotes={"600001": _quote("600001", T1, DAY1),
                             "600002": _quote("600002", T1, DAY1),
                             "600003": _quote("600003", T1, DAY1)},
                     commands=[child, child_other], limits=limits)
    r2 = transition_event(state, event_time=T2, trade_date=DAY2,
                          quotes={"600001": _quote("600001", T2, DAY2)},
                          commands=[], limits=limits)
    assert [f.side for f in r2.fills] == ["sell"]
    credited = state.fund_credits[sell_id]["credited"]
    assert credited > 0
    r3 = transition_event(state, event_time=T3, trade_date=DAY3, quotes={
        code: _quote(code, T3, DAY3) for code in ("600002", "600003")},
        commands=[], limits=limits)
    spent = sum(f.price * f.qty + f.fee for f in r3.fills)
    assert r3.fills                                   # 至少真实成交一笔
    assert sum(f.qty for f in r3.fills) < 2000        # 第二子单不得双花
    assert spent <= credited + 1e-6
    assert state.fund_credits[sell_id]["spent"] <= credited + 1e-6


def test_cancelled_parent_cancels_unsupported_child():
    """父卖到期取消且零成交 → 依赖它的子买被撤，预留释放，不无限挂单。"""
    config = bs.teaching_fixture()
    owner = _ref(config)
    sell = Batch(batch_id="incumbent", code="600001", qty=1000, remaining=1000,
                 entry_date="2026-11-17", cost_price=10.0, initial_risk=1.0,
                 strategy_ref=owner, plan_id="switch:x",
                 sellable_mode=SELLABLE_ON, sellable_date=DAY1)
    state = LedgerState(cash=1000.0, batches=[sell], trade_date=DAY1,
                        marks={"600001": 10.0, "600002": 10.0})
    limits = TransitionLimits(
        cost_model=ExecutionCostModel(min_commission=0.0),
        next_trade_date=lambda day: DAY3, ttl_ms=None,
        risk_caps=RiskCaps.teaching_fixture(),
        max_age_ms=TEACHING_QUOTE_MAX_AGE_MS)
    sell_cmd = TransitionCommand(
        action="sell", code="600001", qty=1000, owner=owner,
        plan_id="switch:x", sequence=0, batch_ids=("incumbent",),
        valid_until_event=T1 + 1)
    r1 = transition_event(state, event_time=T1, trade_date=DAY1,
                          quotes={"600001": _quote("600001", T1, DAY1)},
                          commands=[sell_cmd], limits=limits)
    sell_id = r1.created[0]
    child_id = transition_event(
        state, event_time=T1, trade_date=DAY1,
        quotes={"600002": _quote("600002", T1, DAY1)},
        commands=[TransitionCommand(
            action="buy", code="600002", qty=100, owner=owner,
            plan_id="switch:x", sequence=1, depends_on=(sell_id,),
            risk_amount=10.0)], limits=limits).created[0]
    # T2 已过期：父卖取消（零成交），子买随之作废。
    r3 = transition_event(
        state, event_time=T3, trade_date=DAY3,
        quotes={"600002": _quote("600002", T3, DAY3)},
        commands=[], limits=limits)
    assert sell_id in r3.canceled and child_id in r3.canceled
    assert all(o.is_terminal for o in state.orders)
    assert all(r.status == "released" for r in state.reservations)


def test_multi_parent_credit_allocates_actual_consumption_no_overspend():
    """M4：多父卖额度悬殊时按事件开始可用额度实际分配，不得等额猜分摊/越界。"""
    from dragon_quant.review_account.execution_core.matching import (
        ExecutionQuote,
    )
    from dragon_quant.review_account.execution_core.models import Order

    config = bs.teaching_fixture()
    owner = _ref(config)
    parents = [
        Order(order_id="a", code="600001", side="sell", qty=10,
              status="filled", created_event=T1 - 1, filled_qty=10),
        Order(order_id="b", code="600003", side="sell", qty=10,
              status="filled", created_event=T1 - 1, filled_qty=10),
    ]
    # 现金包含两笔卖出的真实净到账（credits 之和），保持账实一致。
    state = LedgerState(cash=2000.0, orders=list(parents), trade_date=DAY1,
                        marks={"600002": 10.0, "600004": 10.0})
    state.fund_credits = {"a": {"credited": 100.0, "spent": 0.0},
                          "b": {"credited": 900.0, "spent": 0.0}}
    limits = TransitionLimits(
        cost_model=ExecutionCostModel(commission_rate=0.0, min_commission=0.0,
                                      stamp_tax_rate=0.0, buy_slippage=0.0,
                                      sell_slippage=0.0, lot_size=10),
        next_trade_date=lambda day: DAY3, ttl_ms=None,
        risk_caps=RiskCaps.teaching_fixture(),
        max_age_ms=TEACHING_QUOTE_MAX_AGE_MS)
    child = TransitionCommand(
        action="buy", code="600002", qty=80, owner=owner,
        plan_id="switch:x", sequence=1, depends_on=("a", "b"),
        risk_amount=100.0)
    r1 = transition_event(
        state, event_time=T1, trade_date=DAY1,
        quotes={"600002": ExecutionQuote(
            code="600002", event_id="e1", event_ts=T1,
            event_time="2026-11-18T10:00:00+08:00", trade_date=DAY1,
            price=10.0, available_at="2026-11-18T10:00:00+08:00",
            tradable=True, capacity_qty=10 ** 9)},
        commands=[child], limits=limits)
    # 先创建（本事件不成交），下一事件成交 800 元。
    r2 = transition_event(
        state, event_time=T2, trade_date=DAY2,
        quotes={"600002": ExecutionQuote(
            code="600002", event_id="e2", event_ts=T2,
            event_time="2026-11-19T10:00:00+08:00", trade_date=DAY2,
            price=10.0, available_at="2026-11-19T10:00:00+08:00",
            tradable=True, capacity_qty=10 ** 9)},
        commands=[], limits=limits)
    assert r1.created and [f.qty for f in r2.fills] == [80]
    credits = state.fund_credits
    assert credits["a"]["spent"] == pytest.approx(100.0)
    assert credits["b"]["spent"] == pytest.approx(700.0)
    assert credits["a"]["spent"] <= credits["a"]["credited"] + 1e-6
    assert credits["b"]["spent"] <= credits["b"]["credited"] + 1e-6
    # 后续子单只能看到真实剩余：a 0、b 200，不能沿用等额分摊的 500。
    later_a = TransitionCommand(
        action="buy", code="600002", qty=10, owner=owner,
        plan_id="switch:y", sequence=1, depends_on=("a",), risk_amount=10.0)
    later_b = TransitionCommand(
        action="buy", code="600004", qty=30, owner=owner,
        plan_id="switch:y", sequence=2, depends_on=("b",), risk_amount=10.0)
    transition_event(
        state, event_time=T3, trade_date=DAY3,
        quotes={"600002": ExecutionQuote(
            code="600002", event_id="e3", event_ts=T3,
            event_time="2026-11-20T10:00:00+08:00", trade_date=DAY3,
            price=10.0, available_at="2026-11-20T10:00:00+08:00",
            tradable=True, capacity_qty=10 ** 9),
            "600004": ExecutionQuote(
            code="600004", event_id="e3", event_ts=T3,
            event_time="2026-11-20T10:00:00+08:00", trade_date=DAY3,
            price=10.0, available_at="2026-11-20T10:00:00+08:00",
            tradable=True, capacity_qty=10 ** 9)},
        commands=[later_a, later_b], limits=limits)
    r4 = transition_event(
        state, event_time=T4, trade_date=DAY4,
        quotes={"600002": ExecutionQuote(
            code="600002", event_id="e4", event_ts=T4,
            event_time="2026-11-23T10:00:00+08:00", trade_date=DAY4,
            price=10.0, available_at="2026-11-23T10:00:00+08:00",
            tradable=True, capacity_qty=10 ** 9),
            "600004": ExecutionQuote(
            code="600004", event_id="e4", event_ts=T4,
            event_time="2026-11-23T10:00:00+08:00", trade_date=DAY4,
            price=10.0, available_at="2026-11-23T10:00:00+08:00",
            tradable=True, capacity_qty=10 ** 9)},
        commands=[], limits=limits)
    filled = {(f.code, f.qty) for f in r4.fills}
    assert ("600002", 10) not in filled       # a 已无剩余额度
    assert ("600004", 20) in filled           # b 仅剩 200 元
    total = (state.fund_credits["a"]["spent"]
             + state.fund_credits["b"]["spent"])
    assert total == pytest.approx(1000.0)
