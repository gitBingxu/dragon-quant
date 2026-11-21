"""W2 保护义务与逐事件推进：G8/B1/B2。"""

from __future__ import annotations


import pytest

from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.tests._bs_policy_support import (
    _account,
    _candidate,
    _context,
    _plan,
    _position,
    _steps,
)

# ─── G8：收缩限制新增，存量处理与分阶段恢复独立 ────────────────────────────


def test_g8_drawdown_blocks_new_risk_but_not_reductions():
    position = _position(mark=9.0)
    account = _account(cash=10_000.0, positions=(position,),
                       drawdown_pct=12.0)
    bundle = bs.evaluate_account_policy(_context(
        account=account, candidates=(_candidate(),)))
    assert all(not plan.executable for plan in bundle.plans
               if plan.plan_kind in (bs.PLAN_BREAKOUT, bs.PLAN_PROBE,
                                     bs.PLAN_CONFIRM))
    assert any("recovery_restricted" in plan.blocked_reasons
               for plan in bundle.plans if plan.plan_kind == bs.PLAN_PROBE)
    assert _plan(bundle, bs.PLAN_FULL_EXIT).executable or \
        _plan(bundle, bs.PLAN_PARTIAL_REDUCE).executable


def _recovery_confirmations(config, *, available_at="2026-11-18T09:00:00+08:00"):
    return tuple(
        bs.RecoveryConditionConfirmation(
            rule_id=rule, evidence_ids=(f"evidence:{rule}",),
            source="pit_verified", available_at=available_at, version="v1")
        for rule in config.recovery.conditions)


def test_g8_recovery_is_staged_and_never_auto_maximum():
    config = bs.teaching_fixture()
    candidate = _candidate()
    state = bs.RecoveryState(
        active=True, stage=1, days_since_trigger=2,
        trigger_event=1, trigger_trade_date="2026-11-17",
        last_advanced_trade_date="2026-11-18",
        confirmations=_recovery_confirmations(config))
    account = _account(drawdown_pct=12.0)
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=account, candidates=(candidate,),
        recovery=state))
    probe = _plan(bundle, bs.PLAN_PROBE)
    # 本事件实际授权是**已锁存** stage 1（0.5）；proposed 2（0.75）只表示下一
    # 交易日可推进，不在同一事件提前抬高授权，也绝不自动满额 1.0。
    assert bundle.recovery["stage"] == 1
    assert bundle.recovery["proposed_stage"] == 2
    assert bundle.recovery["scale"] == pytest.approx(0.5)
    assert probe.executable
    assert probe.risk_amount == pytest.approx(
        config.sizing.probe_risk * 0.5, rel=1e-9)


def test_g8_recovery_without_conditions_stays_restricted():
    config = bs.teaching_fixture()
    state = bs.RecoveryState(active=True, stage=0, days_since_trigger=5,
                             conditions_met=tuple(config.recovery.conditions))
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=_account(drawdown_pct=12.0),
        candidates=(_candidate(),), recovery=state))
    probe = _plan(bundle, bs.PLAN_PROBE)
    assert not probe.executable
    assert "recovery_restricted" in probe.blocked_reasons
    recovery_plan = _plan(bundle, bs.PLAN_RECOVERY)
    assert recovery_plan is not None
    assert any("missing_condition" in reason
               for reason in recovery_plan.blocked_reasons)



# ─── B1：强制保护先于可选方案，等待不能绕开 ────────────────────────────────


def test_b1_forced_exit_survives_wait_and_future_rally():
    position = _position(cost_price=10.0, mark=8.0, trend="down")
    account = _account(cash=10_000.0, positions=(position,))
    # 未来先跌后涨：等待/不操作也不能把已失效持仓留到反弹。
    scenarios = bs.scenario_set_from_records(
        _steps({"rally": {"600001": [8.0, 10.0, 12.0, 15.0, 20.0]}}),
        spec=bs.teaching_fixture().scenario)
    bundle = bs.evaluate_account_policy(_context(
        account=account, scenarios=scenarios))
    exits = [action for action in bundle.selected_actions
             if action.action == "exit" and action.code == "600001"]
    assert exits, bundle.selected_actions
    assert exits[0].obligation_id
    wait = _plan(bundle, bs.PLAN_WAIT)
    assert wait.executable
    assert wait.protection and wait.protection[0].code == "600001"
    # 所有可比较方案都继承同一保护义务。
    for plan in bundle.plans:
        if plan.executable:
            assert any(item.code == "600001" for item in plan.protection)


def test_b1_multiple_invalidated_positions_all_retained():
    one = _position(code="600001", cost_price=10.0, mark=8.0, trend="down")
    two = _position(code="600002", cost_price=10.0, mark=9.0, trend="down")
    account = _account(cash=10_000.0, positions=(one, two))
    scenarios = bs.scenario_set_from_records(
        _steps({"flat": {"600001": [8.0] * 5, "600002": [9.0] * 5}}),
        spec=bs.teaching_fixture().scenario)
    bundle = bs.evaluate_account_policy(_context(
        account=account, scenarios=scenarios))
    codes = {action.code for action in bundle.selected_actions
             if action.action == "exit"}
    assert {"600001", "600002"} <= codes
    obligations = bundle.action_limits["protection_obligations"]
    assert {item["code"] for item in obligations} >= {"600001", "600002"}


def test_b1_no_scenarios_still_outputs_protection_intent():
    position = _position(cost_price=10.0, mark=8.0, trend="down")
    bundle = bs.evaluate_account_policy(_context(
        account=_account(cash=10_000.0, positions=(position,)),
        scenarios=bs.ScenarioSet(paths=(), insufficiency=("insufficient_data",))))
    assert bundle.status == "insufficient_data"
    exits = [action for action in bundle.selected_actions
             if action.action == "exit"]
    assert exits
    assert bundle.action_limits["protection_obligations"]
    assert all(not plan.executable for plan in bundle.plans
               if plan.plan_kind in (bs.PLAN_BREAKOUT, bs.PLAN_PROBE,
                                     bs.PLAN_ADD))


def test_b1_untradable_protection_keeps_pending_risk_not_fake_fill():
    position = _position(cost_price=10.0, mark=8.0, trend="down")
    account = _account(cash=10_000.0, positions=(position,))
    scenarios = bs.scenario_set_from_records(
        _steps({"locked": {"600001": [8.0] * 5}},
               tradable={"600001": False}),
        spec=bs.teaching_fixture().scenario)
    bundle = bs.evaluate_account_policy(_context(
        account=account, scenarios=scenarios))
    wait = _plan(bundle, bs.PLAN_WAIT)
    outcome = bundle.outcome_matrix[wait.plan_id]["locked"]
    assert outcome["net_pnl"] is not None
    assert any(action.obligation_id for action in bundle.selected_actions)
    last = outcome["timeline"][-1]
    assert last["positions"]["600001"] == 1000
    assert "600001" in last["policy_state"]["pending_exits"]


# ─── B2：逐事件状态转移，不用末态回画过去 ──────────────────────────────────


def test_b2_timeline_does_not_borrow_future_account_state():
    config = bs.teaching_fixture()
    plan = bs.PolicyPlan(
        plan_id="late-buy", plan_kind=bs.PLAN_BREAKOUT, code="600001",
        target_qty=1000, stop_price=9.4, time_limit_steps=5,
        legs=(bs.PlanLeg(step=2, action="buy", code="600001",
                         qty_fraction=1.0, reason="late"),),
        executable=True)
    steps = _steps({"flat": {"600001": [10.0, 20.0, 20.0, 20.0, 20.0]}})[0]["steps"]
    path = bs.scenario_set_from_records(
        [{"scenario_id": "flat", "kind": "teaching_fixture",
          "fixture": True, "steps": steps}], spec=config.scenario).paths[0]
    outcome = bs.simulate_plan(plan, path, account=_account(cash=100_000.0),
                               spec=config.scenario)
    first, second, third = (outcome.timeline[0], outcome.timeline[1],
                            outcome.timeline[2])
    assert first["positions"] == {}
    assert first["cash"] == 100_000.0
    assert first["equity"] == pytest.approx(100_000.0)
    # C1：step2 观察到的入场只在**后续**有支持的事件成交，不在观察当根填单。
    assert second["positions"] == {}
    assert third["positions"]["600001"] == 1000
    assert third["cash"] < 100_000.0
    assert third["batches"]["600001"][0]["origin"] == "purchase"


def test_b2_entry_stop_exits_on_later_event_and_keeps_equity_timeline():
    config = bs.teaching_fixture()
    plan = bs.PolicyPlan(
        plan_id="entry", plan_kind=bs.PLAN_BREAKOUT, code="600001",
        target_qty=1000, stop_price=9.4, disaster_price=9.0,
        time_limit_steps=5,
        legs=(bs.PlanLeg(step=1, action="buy", code="600001",
                         qty_fraction=1.0, trigger_price=9.9,
                         trigger_mode="above", reason="entry"),),
        executable=True)
    # step2 成交后，当根不反向激活保护；step4 再次跌破结构线才在 step5 执行退出。
    steps = _steps({"path": {"600001": [10.0, 9.2, 12.0, 9.3, 12.0]}})[0]["steps"]
    path = bs.scenario_set_from_records(
        [{"scenario_id": "path", "kind": "teaching_fixture", "fixture": True,
          "steps": steps}], spec=config.scenario).paths[0]
    outcome = bs.simulate_plan(plan, path, account=_account(cash=100_000.0),
                               spec=config.scenario)
    states = {item["code"]: item for item in outcome.policy_states}
    assert states["600001"]["status"] == "exited"
    assert outcome.timeline[1]["positions"]["600001"] == 1000
    assert outcome.timeline[2]["positions"]["600001"] == 1000
    assert outcome.timeline[4]["positions"] == {}
    exits = outcome.timeline[4]["policy_state"]["exits"]
    assert any(item["reason"] == "price_invalidation_exit" for item in exits)


def test_b2_t1_uses_trade_date_not_step_index():
    config = bs.teaching_fixture()
    plan = bs.PolicyPlan(
        plan_id="t1", plan_kind=bs.PLAN_PROBE, code="600001",
        target_qty=1000, time_limit_steps=5,
        legs=(
            bs.PlanLeg(step=1, action="buy", code="600001",
                       qty_fraction=1.0, reason="buy"),
            bs.PlanLeg(step=2, action="sell", code="600001",
                       qty_fraction=1.0, reason="same_day_sell"),
            bs.PlanLeg(step=3, action="sell", code="600001",
                       qty_fraction=1.0, reason="next_day_sell"),
        ), executable=True)
    steps = _steps(
        {"path": {"600001": [10.0, 10.0, 10.0, 10.0, 10.0]}},
        trade_dates=["2026-11-19", "2026-11-19", "2026-11-20",
                     "2026-11-20", "2026-11-21"])[0]["steps"]
    path = bs.scenario_set_from_records(
        [{"scenario_id": "path", "kind": "teaching_fixture", "fixture": True,
          "steps": steps}], spec=config.scenario).paths[0]
    outcome = bs.simulate_plan(plan, path, account=_account(cash=100_000.0),
                               spec=config.scenario)
    # 同一交易日不能卖（当日卖出腿被拒），下一交易日的腿观察后再下一事件成交。
    assert outcome.timeline[1]["positions"]["600001"] == 1000
    assert outcome.timeline[2]["positions"]["600001"] == 1000
    assert outcome.timeline[3]["positions"] == {}
    assert any("no_sellable" in item for item in outcome.unfilled)


def test_b2_scenario_confirmation_not_available_blocks_add_leg():
    config = bs.teaching_fixture()
    plan = bs.PolicyPlan(
        plan_id="confirm", plan_kind=bs.PLAN_ADD, code="600001",
        target_qty=1000, executable=True,
        legs=(bs.PlanLeg(step=3, action="buy", code="600001",
                         qty_fraction=1.0, reason="confirmed_add",
                         evidence_rule="confirm:600001"),))
    steps = _steps(
        {"path": {"600001": [10.0] * 5}},
        evidence_by_step={4: ("confirm:600001",)})[0]["steps"]
    path = bs.scenario_set_from_records(
        [{"scenario_id": "path", "kind": "teaching_fixture", "fixture": True,
          "steps": steps}], spec=config.scenario).paths[0]
    # 只在 step1 出现的旧确认不能用于 step3 的增配（模拟只按该步到达事实）。
    outcome = bs.simulate_plan(plan, path, account=_account(cash=100_000.0),
                               spec=config.scenario)
    assert outcome.timeline[2]["positions"] == {}
    assert any("evidence_not_available" in item for item in outcome.unfilled)
