"""W2 新主题压力、批次真值与确认/PIT 契约（B3–B5）。"""

from __future__ import annotations


import pytest

from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.tests._bs_policy_support import (
    _account,
    _candidate,
    _checks,
    _confirmation,
    _context,
    _fake_snapshot,
    _plan,
    _position,
    _probe_state,
    _scenarios,
    _state_context,
    _steps,
)

# ─── B3：新主题/未知组压力 ──────────────────────────────────────────────────


def test_b3_new_theme_includes_existing_unknown_and_is_rejected():
    unknown = _position(code="900001", theme_ids=(), mark=10.0, qty=1000,
                        pressure_pct=0.1)
    candidate = _candidate(code="600003", price=10.0, theme_ids=("NEW_THEME",),
                           pressure_pct=0.1,
                           evidence_checks=_checks(
                               "entry:breakout:600003",
                               "entry:probe:600003", "confirm:600003"))
    config = bs.teaching_fixture(budgets={
        **bs.teaching_fixture().budgets.to_dict(), "per_theme": 1_500.0,
        "unknown_group": 2_000.0, "per_trade": 1_000.0, "account": 3_000.0})
    scenarios = _scenarios(config, prices={
        "up": {"900001": [10.0] * 5, "600003": [10.0, 10.1, 10.2, 10.3, 10.4]},
    })
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=_account(cash=100_000.0, positions=(unknown,)),
        candidates=(candidate,), scenarios=scenarios))
    entry = _plan(bundle, bs.PLAN_BREAKOUT, "600003")
    assert not entry.executable
    check = next(item for item in bundle.risk_checks
                 if item["check"] == "per_theme" and item["values"])
    assert check["values"]["NEW_THEME"] >= 2_000.0
    assert "theme_pressure_budget_exceeded" in entry.blocked_reasons


def test_b3_empty_account_first_theme_budget():
    config = bs.teaching_fixture(budgets={
        **bs.teaching_fixture().budgets.to_dict(), "per_theme": 400.0,
        "account": 3_000.0, "per_trade": 1_000.0, "unknown_group": 1_000.0})
    bundle = bs.evaluate_account_policy(_context(
        config=config, candidates=(_candidate(theme_ids=("T1",),
                                              pressure_pct=0.1),)))
    entry = _plan(bundle, bs.PLAN_BREAKOUT)
    assert not entry.executable
    assert "theme_pressure_budget_exceeded" in entry.blocked_reasons


def test_b3_reservation_with_new_theme_is_counted():
    reservation = bs.Reservation(code="600004", side="buy", qty=1000,
                                 price=10.0, theme_ids=("NEW_THEME",),
                                 pressure_pct=0.1)
    candidate = _candidate(code="600003", price=10.0,
                           theme_ids=("NEW_THEME",), pressure_pct=0.1,
                           evidence_checks=_checks(
                               "entry:breakout:600003",
                               "entry:probe:600003", "confirm:600003"))
    config = bs.teaching_fixture(budgets={
        **bs.teaching_fixture().budgets.to_dict(), "per_theme": 1_500.0,
        "per_trade": 1_000.0, "account": 3_000.0})
    scenarios = _scenarios(config, prices={
        "up": {"600004": [10.0] * 5, "600003": [10.0, 10.1, 10.2, 10.3, 10.4]},
    })
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=_account(cash=100_000.0, reservations=(reservation,)),
        candidates=(candidate,), scenarios=scenarios))
    entry = _plan(bundle, bs.PLAN_BREAKOUT, "600003")
    assert not entry.executable


def test_b3_unknown_group_without_known_theme():
    unknown = _position(code="900002", theme_ids=(), mark=10.0, qty=2000,
                        pressure_pct=0.1)
    config = bs.teaching_fixture(budgets={
        **bs.teaching_fixture().budgets.to_dict(), "unknown_group": 1_000.0,
        "per_theme": 3_000.0, "account": 4_000.0, "per_trade": 1_000.0})
    scenarios = _scenarios(config, prices={
        "up": {"900002": [10.0] * 5, "600003": [10.0, 10.1, 10.2, 10.3, 10.4]}})
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=_account(cash=100_000.0, positions=(unknown,)),
        candidates=(_candidate(code="600003", price=10.0, theme_ids=("T1",)),),
        scenarios=scenarios))
    entry = _plan(bundle, bs.PLAN_BREAKOUT, "600003")
    assert not entry.executable
    assert "unknown_pressure_budget_exceeded" in entry.blocked_reasons


# ─── B4：批次真值/T+1 ──────────────────────────────────────────────────────


def test_b4_batch_truth_never_falls_back_to_aggregate_sellable():
    with pytest.raises(bs.PolicyInputError):
        bs.PositionView(
            code="600001", qty=1000, sellable_qty=1000, cost_price=10.0,
            mark=10.0, batches=(bs.BatchView("today", 1000, 0),))


def test_b4_batch_consistency_and_bounds_are_validated():
    with pytest.raises(bs.PolicyInputError):
        bs.BatchView("bad", 100, 101)
    with pytest.raises(bs.PolicyInputError):
        bs.PositionView(
            code="600001", qty=1000, sellable_qty=0, cost_price=10.0,
            batches=(bs.BatchView("a", 900, 0),))
    with pytest.raises(bs.PolicyInputError):
        bs.PositionView(code="600001", qty=100, sellable_qty=200,
                       cost_price=10.0)


def test_b4_same_code_positions_aggregate_without_overwriting_batches():
    first = _position(code="600001", qty=1000, sellable_qty=1000)
    second = _position(code="600001", qty=500, sellable_qty=500,
                       entry_date="2026-11-17")
    account = _account(cash=10_000.0, positions=(first, second))
    aggregated = account.aggregated_positions()
    assert len(aggregated) == 1
    assert aggregated[0].qty == 1500
    assert aggregated[0].sellable() == 1500
    scenarios = bs.scenario_set_from_records(
        _steps({"flat": {"600001": [10.0] * 5}}),
        spec=bs.teaching_fixture().scenario)
    bundle = bs.evaluate_account_policy(_context(
        account=account, scenarios=scenarios))
    wait = _plan(bundle, bs.PLAN_WAIT)
    assert bundle.outcome_matrix[wait.plan_id]["flat"]["timeline"][0][
        "positions"]["600001"] == 1500


def test_b4_no_sellable_batch_keeps_pending_obligation():
    batch = bs.BatchView("locked", 1000, 0, entry_date="2026-11-18")
    position = bs.PositionView(
        code="600001", qty=1000, sellable_qty=0, cost_price=10.0,
        mark=8.0, theme_ids=("T1",), pressure_pct=0.1, trend="down",
        batches=(batch,))
    bundle = bs.evaluate_account_policy(_context(
        account=_account(cash=10_000.0, positions=(position,))))
    obligations = bundle.action_limits["protection_obligations"]
    assert obligations and obligations[0]["executable"] is False
    assert "no_sellable_quantity" in obligations[0]["reasons"]
    selected = [action for action in bundle.selected_actions
                if action.obligation_id]
    assert selected and selected[0].executable is False


# ─── B5：确认身份/PIT/fixture ──────────────────────────────────────────────


def test_b5_snapshot_after_decision_is_rejected():
    snapshot = _fake_snapshot(cutoff="2026-11-19T09:00:00+08:00")
    with pytest.raises(bs.PolicyInputError):
        bs.evaluate_account_policy(_context(
            candidates=(_candidate(),), snapshot=snapshot))


def test_b5_confirmation_reusing_probe_evidence_is_not_independent():
    confirmation = _confirmation(evidence_ids=("obs-probe",))
    bundle = bs.evaluate_account_policy(_state_context(
        confirmation=confirmation))
    add = _plan(bundle, bs.PLAN_ADD)
    assert not add.executable
    assert "confirmation_not_independent" in add.blocked_reasons


def test_b5_confirmation_must_be_available_and_match_snapshot():
    future = _confirmation(available_at="2026-11-20T09:00:00+08:00")
    bundle = bs.evaluate_account_policy(_state_context(confirmation=future))
    add = _plan(bundle, bs.PLAN_ADD)
    assert "confirmation_not_available" in add.blocked_reasons

    stale = _confirmation(snapshot_id="snap-old")
    bundle = bs.evaluate_account_policy(_state_context(
        confirmation=stale, snapshot=_fake_snapshot(snapshot_id="snap-1")))
    add = _plan(bundle, bs.PLAN_ADD)
    assert "confirmation_snapshot_mismatch" in add.blocked_reasons


def test_b5_max_adds_state_blocks_further_increase():
    state = _probe_state(adds_done=2)
    bundle = bs.evaluate_account_policy(_state_context(
        confirmation=_confirmation(), state=state))
    add = _plan(bundle, bs.PLAN_ADD)
    assert "add_limit_reached" in add.blocked_reasons
    transition = bundle.audit["policy_state_transitions"][0]
    assert transition["from_adds"] == 2

    bundle_ok = bs.evaluate_account_policy(_state_context(
        confirmation=_confirmation(), state=_probe_state()))
    transition_ok = bundle_ok.audit["policy_state_transitions"][0]
    assert transition_ok["executable"] is True
    assert transition_ok["to_adds"] == 1


def test_b5_probability_future_maturity_is_unqualified():
    config = bs.teaching_fixture()
    probability = bs.ProbabilityInput(
        event_id="e1", policy_id=config.policy_id,
        profile_version=config.profile_version,
        horizon_days=config.scenario.horizon_days, matured=True,
        sample_size=100, calibration_ref="cal", calibration_version="v1",
        applicability=("all",),
        event_definition="close_above_entry", training_end="2026-10-01",
        label_matured_at="2026-11-25", calibration_available_at="2026-10-20",
        probabilities={"up": 0.4, "down": 0.3, "flat": 0.3})
    bundle = bs.evaluate_account_policy(_context(
        config=config, candidates=(_candidate(),), probability=probability))
    assert bundle.audit["probability"]["qualified"] is False
    assert any("label_matured_at_future" in reason
               for reason in bundle.audit["probability"]["reasons"])


def test_b5_historical_path_after_decision_is_rejected():
    config = bs.teaching_fixture()
    steps = _steps({"h": {"600001": [10.0] * 5}})[0]["steps"]
    with pytest.raises(bs.ScenarioError):
        bs.scenario_set_from_records(
            [{"scenario_id": "h", "kind": "historical_joint", "steps": steps,
              "mature": True,
              "provenance": {"window_end": "2026-12-01T15:00:00+08:00",
                             "matured_at": "2026-12-01T15:00:00+08:00"}}],
            spec=config.scenario, decision_time="2026-11-18T16:00:00+08:00")


def test_b1_time_disaster_giveback_and_contraction_obligations_are_visible():
    time_position = _position(code="600001", held_days=5, mark=10.0)
    disaster_position = _position(code="600002", cost_price=10.0, mark=8.8,
                                  trend="down")
    bundle = bs.evaluate_account_policy(_context(
        account=_account(cash=50_000.0,
                         positions=(time_position, disaster_position))))
    kinds = {item["kind"] for item in
             bundle.action_limits["protection_obligations"]}
    assert {"time_invalidation", "disaster"} <= kinds
    codes = {action.code for action in bundle.selected_actions
             if action.obligation_id}
    assert {"600001", "600002"} <= codes

    profitable = _position(code="600003", cost_price=5.0, mark=12.0,
                           theme_ids=("T3",))
    config = bs.teaching_fixture(budgets={
        **bs.teaching_fixture().budgets.to_dict(), "giveback": 100.0})
    giveback_bundle = bs.evaluate_account_policy(_context(
        config=config, account=_account(cash=50_000.0,
                                        positions=(profitable,))))
    kinds = {item["kind"] for item in
             giveback_bundle.action_limits["protection_obligations"]}
    assert "giveback" in kinds

    contraction = bs.evaluate_account_policy(_context(
        account=_account(cash=50_000.0, drawdown_pct=12.0,
                         positions=(_position(code="600004", mark=10.0),))))
    kinds = {item["kind"] for item in
             contraction.action_limits["protection_obligations"]}
    assert "contraction" in kinds


def test_b2_giveback_exit_is_executed_in_timeline():
    config = bs.teaching_fixture(budgets={
        **bs.teaching_fixture().budgets.to_dict(), "giveback": 100.0})
    position = _position(code="600001", cost_price=5.0, mark=12.0,
                         theme_ids=("T1",))
    scenarios = bs.scenario_set_from_records(
        _steps({"down": {"600001": [12.0, 11.5, 10.0, 9.0, 8.0]}}),
        spec=config.scenario)
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=_account(cash=10_000.0, positions=(position,)),
        scenarios=scenarios))
    wait = _plan(bundle, bs.PLAN_WAIT)
    outcome = bundle.outcome_matrix[wait.plan_id]["down"]
    exits = [item for item in outcome["timeline"][-1]["policy_state"]["exits"]]
    assert any(item.get("reason") == "giveback_exit"
               or item.get("kind") == "giveback" for item in exits)
    assert outcome["timeline"][-1]["positions"].get("600001", 0) == 0


def test_b2_add_confirmation_trigger_not_met_does_not_increase_position():
    config = bs.teaching_fixture()
    candidate = _candidate(
        structure={"breakout_level": 10.2, "pullback_level": 9.8,
                   "confirm_level": 10.5, "probe_level": 10.0},
        confirmations=(_confirmation(),))
    scenarios = bs.scenario_set_from_records(
        _steps({"down": {"600001": [10.0, 10.0, 10.0, 10.0, 10.0]}}),
        spec=config.scenario)
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=_account(cash=100_000.0,
                                        positions=(_position(held_days=0),)),
        candidates=(candidate,), scenarios=scenarios,
        policy_states=(_probe_state(),)))
    add = _plan(bundle, bs.PLAN_ADD)
    assert add.executable
    outcome = bundle.outcome_matrix[add.plan_id]["down"]
    assert outcome["timeline"][-1]["positions"]["600001"] == 1000
    assert any("trigger_not_met" in item for item in outcome["unfilled"])


def test_b3_same_theme_positions_sum_with_proposal():
    one = _position(code="600001", theme_ids=("T1",), mark=10.0, qty=1000,
                    pressure_pct=0.1)
    two = _position(code="600002", theme_ids=("T1",), mark=10.0, qty=1000,
                    pressure_pct=0.1)
    candidate = _candidate(code="600003", price=10.0, theme_ids=("T1",),
                           pressure_pct=0.1,
                           evidence_checks=_checks(
                               "entry:breakout:600003",
                               "entry:probe:600003", "confirm:600003"))
    config = bs.teaching_fixture(budgets={
        **bs.teaching_fixture().budgets.to_dict(), "per_theme": 2_500.0,
        "account": 5_000.0, "per_trade": 1_000.0})
    scenarios = _scenarios(config, prices={
        "up": {"600001": [10.0] * 5, "600002": [10.0] * 5,
               "600003": [10.0, 10.1, 10.2, 10.3, 10.4]}})
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=_account(cash=100_000.0, positions=(one, two)),
        candidates=(candidate,), scenarios=scenarios))
    theme_checks = [item for item in bundle.risk_checks
                    if item["check"] == "per_theme" and item["values"]]
    check = max(theme_checks,
                key=lambda item: max(item["values"].values()))
    # 两笔同主题存量 + 拟新增，全部计入同一主题额度。
    assert check["values"]["T1"] == pytest.approx(3_000.0)
    entry = _plan(bundle, bs.PLAN_BREAKOUT, "600003")
    assert "theme_pressure_budget_exceeded" in entry.blocked_reasons


def test_b4_batch_sellable_date_releases_and_exit_then_fills():
    config = bs.teaching_fixture()
    batch = bs.BatchView("tomorrow", 1000, 0, entry_date="2026-11-18",
                         sellable_date="2026-11-20")
    position = bs.PositionView(
        code="600001", qty=1000, sellable_qty=0, cost_price=10.0,
        mark=8.0, theme_ids=("T1",), pressure_pct=0.1, trend="down",
        batches=(batch,))
    scenarios = bs.scenario_set_from_records(
        _steps({"path": {"600001": [8.0] * 5}},
               trade_dates=["2026-11-19", "2026-11-20", "2026-11-21",
                            "2026-11-22", "2026-11-23"]),
        spec=config.scenario)
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=_account(cash=10_000.0, positions=(position,)),
        scenarios=scenarios))
    wait = _plan(bundle, bs.PLAN_WAIT)
    outcome = bundle.outcome_matrix[wait.plan_id]["path"]
    timeline = outcome["timeline"]
    assert timeline[0]["positions"]["600001"] == 1000  # 尚未到可卖日
    assert timeline[1]["positions"]["600001"] == 1000  # 释放日观察，不在同根成交
    assert timeline[2]["positions"] == {}              # 释放后的下一事件执行保护退出


def test_b1_one_untradable_position_does_not_hide_other_protection():
    one = _position(code="600001", cost_price=10.0, mark=8.0, trend="down")
    two = _position(code="600002", cost_price=10.0, mark=9.0, trend="down")
    account = _account(cash=10_000.0, positions=(one, two))
    scenarios = bs.scenario_set_from_records(
        _steps({"path": {"600001": [8.0] * 5, "600002": [9.0] * 5}},
               tradable_by_step={index: {"600001": False}
                                 for index in range(1, 6)}),
        spec=bs.teaching_fixture().scenario)
    bundle = bs.evaluate_account_policy(_context(
        account=account, scenarios=scenarios))
    exits = [action for action in bundle.selected_actions
             if action.obligation_id]
    assert {action.code for action in exits} >= {"600001", "600002"}
    wait = _plan(bundle, bs.PLAN_WAIT)
    last = bundle.outcome_matrix[wait.plan_id]["path"]["timeline"][-1]
    assert last["positions"]["600001"] == 1000      # 不可成交保留
    assert "600001" in last["policy_state"]["pending_exits"]
    assert "600002" not in last["positions"]        # 另一只已执行保护
