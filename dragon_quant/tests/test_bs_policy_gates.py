"""W2 政策门禁：G1–G7 入场/试探/失效/回吐/主题/换仓。"""

from __future__ import annotations


import pytest

from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.tests._bs_policy_support import (
    _account,
    _candidate,
    _checks,
    _confirmation,
    _context,
    _long_hold_config,
    _plan,
    _position,
    _probe_state,
    _scenarios,
    _steps,
)

# ─── G1：绝对资格先于择强 ───────────────────────────────────────────────────


def test_g1_ineligible_rank_one_does_not_get_bought():
    ineligible = _candidate(code="600001", rank=1, eligible=False,
                            eligibility_reasons=("limit_down_open",))
    strong = _candidate(code="600002", rank=2, name="候选二",
                        price=20.0, pressure_pct=0.10,
                        evidence_checks=_checks(
                            "entry:breakout:600002", "entry:probe:600002",
                            "confirm:600002"))
    scenarios = bs.scenario_set_from_records(_steps({
        "up": {"600001": [10.0, 11.0, 12.0, 13.0, 14.0],
               "600002": [20.0, 20.4, 20.8, 21.2, 21.6]},
    }), spec=bs.teaching_fixture().scenario)
    bundle = bs.evaluate_account_policy(_context(
        candidates=(ineligible, strong), scenarios=scenarios))
    entry_one = [plan for plan in bundle.plans
                 if plan.code == "600001" and plan.plan_kind in
                 (bs.PLAN_BREAKOUT, bs.PLAN_PULLBACK, bs.PLAN_CONFIRM)]
    assert entry_one and all(not plan.executable for plan in entry_one)
    assert all("absolute_qualification_failed" in plan.blocked_reasons
               for plan in entry_one)
    chosen = next(plan for plan in bundle.plans
                  if plan.plan_id == bundle.selected_plan_id)
    assert chosen.code != "600001"


def test_g1_no_qualified_candidate_means_wait_not_forced_top_rank():
    only = _candidate(eligible=False)
    bundle = bs.evaluate_account_policy(_context(candidates=(only,)))
    assert bundle.selected_plan_id == _plan(bundle, bs.PLAN_WAIT).plan_id
    assert bundle.selected_actions[0].action == "wait"
    assert all(not plan.executable for plan in bundle.plans
               if plan.plan_kind in (bs.PLAN_BREAKOUT, bs.PLAN_CONFIRM))


def test_g1_unknown_market_state_blocks_new_risk():
    bundle = bs.evaluate_account_policy(_context(
        candidates=(_candidate(),),
        market=bs.MarketView(regime="unknown", new_risk_allowed=None)))
    assert all(not plan.executable for plan in bundle.plans
               if plan.plan_kind in (bs.PLAN_BREAKOUT, bs.PLAN_PROBE))
    assert any("market_new_risk_unknown" in plan.blocked_reasons
               for plan in bundle.plans)


# ─── G2：竞争入场政策，竞价缺口只约束相应政策 ───────────────────────────────


def test_g2_missing_auction_path_only_blocks_auction_policy():
    candidate = _candidate(evidence_checks=_checks(
        "entry:breakout:600001", "entry:confirm:600001",
        "entry:probe:600001", "confirm:600001"))
    bundle = bs.evaluate_account_policy(_context(candidates=(candidate,)))
    auction = _plan(bundle, bs.PLAN_AUCTION_CONFIRM)
    confirm = _plan(bundle, bs.PLAN_CONFIRM)
    assert auction is not None and not auction.executable
    assert "history_auction_path_missing" in auction.blocked_reasons
    assert confirm is not None and confirm.executable
    assert _plan(bundle, bs.PLAN_BREAKOUT).executable
    # 同一路径下，可执行入场与“什么都不做”的账户损益不同。
    probe = _plan(bundle, bs.PLAN_PROBE)
    wait = _plan(bundle, bs.PLAN_WAIT)
    assert bundle.outcome_matrix[probe.plan_id]["up"]["net_pnl"] != \
        bundle.outcome_matrix[wait.plan_id]["up"]["net_pnl"]


def test_g2_waiting_and_not_participating_are_formal_alternatives():
    bundle = bs.evaluate_account_policy(_context(candidates=(_candidate(),)))
    wait = _plan(bundle, bs.PLAN_WAIT)
    assert wait is not None and wait.executable
    assert wait.plan_id in {item.plan_id for item in bundle.comparisons}


def test_g2_confirm_plan_requires_its_own_evidence():
    candidate = _candidate(evidence_checks=_checks(
        "entry:breakout:600001", "entry:probe:600001", "confirm:600001"))
    bundle = bs.evaluate_account_policy(_context(candidates=(candidate,)))
    confirm = _plan(bundle, bs.PLAN_CONFIRM)
    assert not confirm.executable
    assert any("evidence_missing" in reason
               for reason in confirm.blocked_reasons)


# ─── G3：试探 → 确认 → 预算内增配，不摊平 ──────────────────────────────────


def test_g3_probe_then_add_uses_existing_probe_state_and_budgets():
    config = bs.teaching_fixture()
    empty = bs.evaluate_account_policy(_context(
        config=config, candidates=(_candidate(),)))
    probe = _plan(empty, bs.PLAN_PROBE)
    empty_add = _plan(empty, bs.PLAN_ADD)
    assert probe.executable
    assert not empty_add.executable
    assert "probe_not_filled" in empty_add.blocked_reasons
    assert empty_add.add_evidence_rule == "confirm:600001"
    assert probe.risk_amount <= config.sizing.probe_risk + 1e-9

    candidate = _candidate(
        structure={"breakout_level": 10.2, "pullback_level": 9.8,
                   "confirm_level": 10.2, "probe_level": 10.0},
        confirmations=(_confirmation(),))
    state = _probe_state()
    account = _account(cash=100_000.0, positions=(_position(),))
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=account, candidates=(candidate,),
        policy_states=(state,)))
    add = _plan(bundle, bs.PLAN_ADD)
    assert add.executable
    assert add.risk_amount <= config.sizing.add_step_risk + 1e-9
    assert add.initial_entered is True
    assert add.probe_plan_id == state.probe_plan_id
    assert "obs-confirm" in add.entry_evidence_ids
    assert any("consumes probe state" in note for note in add.notes)
    wait = _plan(bundle, bs.PLAN_WAIT)
    assert bundle.outcome_matrix[add.plan_id]["up"]["net_pnl"] > \
        bundle.outcome_matrix[wait.plan_id]["up"]["net_pnl"]


def test_g3_add_requires_independent_confirmation_evidence():
    candidate = _candidate(evidence_checks=_checks(
        "entry:breakout:600001", "entry:probe:600001"))
    bundle = bs.evaluate_account_policy(_context(candidates=(candidate,)))
    add = _plan(bundle, bs.PLAN_ADD)
    assert not add.executable
    assert any("confirm:600001" in reason for reason in add.blocked_reasons)


def test_g3_failed_probe_never_adds_after_it():
    config = bs.teaching_fixture()
    bundle = bs.evaluate_account_policy(_context(
        config=config, candidates=(_candidate(),)))
    add = _plan(bundle, bs.PLAN_ADD)
    assert not add.executable
    assert "probe_not_filled" in add.blocked_reasons
    assert add.legs == ()
    assert any("failed probe never triggers averaging down" in note
               for note in add.notes)


# ─── G4：价格/时间失效与灾难保护；触发不是成交保证 ─────────────────────────


def test_g4_price_invalidation_blocks_hold_but_exit_remains():
    position = _position(cost_price=10.0, mark=9.2)  # 跌破 -6% 结构线
    account = _account(cash=10_000.0, positions=(position,))
    bundle = bs.evaluate_account_policy(_context(account=account))
    hold = _plan(bundle, bs.PLAN_HOLD)
    exit_plan = _plan(bundle, bs.PLAN_FULL_EXIT)
    assert not hold.executable
    assert "price_invalidation_triggered" in hold.blocked_reasons
    assert exit_plan.executable


def test_g4_time_invalidation_blocks_hold():
    position = _position(held_days=5)
    bundle = bs.evaluate_account_policy(_context(
        account=_account(positions=(position,))))
    hold = _plan(bundle, bs.PLAN_HOLD)
    assert not hold.executable
    assert "time_invalidation_triggered" in hold.blocked_reasons


def test_g4_trigger_is_not_a_fill():
    config = bs.teaching_fixture()
    position = _position(cost_price=10.0, mark=9.2)
    account = _account(cash=10_000.0, positions=(position,))
    scenarios = bs.scenario_set_from_records(
        _steps({"down": {"600001": [9.1, 9.0, 8.9, 8.8, 8.7]}},
               tradable_by_step={2: {"600001": False},
                                 3: {"600001": False}}),
        spec=config.scenario)
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=account, scenarios=scenarios))
    exit_plan = _plan(bundle, bs.PLAN_FULL_EXIT)
    outcome = bundle.outcome_matrix[exit_plan.plan_id]["down"]
    wait_outcome = bundle.outcome_matrix[_plan(bundle, bs.PLAN_WAIT).plan_id]["down"]
    assert outcome["net_pnl"] == pytest.approx(wait_outcome["net_pnl"])
    assert any("not_tradable" in item for item in outcome["unfilled"])


def test_g4_disaster_protection_forces_full_exit_over_partial():
    position = _position(cost_price=10.0, mark=8.8)  # 跌破 -10% 灾难线
    bundle = bs.evaluate_account_policy(_context(
        account=_account(positions=(position,))))
    reduce = _plan(bundle, bs.PLAN_PARTIAL_REDUCE)
    exit_plan = _plan(bundle, bs.PLAN_FULL_EXIT)
    assert not reduce.executable
    assert "disaster_protection_requires_full_exit" in reduce.blocked_reasons
    assert exit_plan.executable


# ─── G5：趋势持有 vs 部分退/全退；回吐预算独立于本金风险 ───────────────────


def test_g5_valid_trend_within_giveback_budget_allows_hold():
    position = _position(cost_price=10.0, mark=11.0, trend="up")
    config = bs.teaching_fixture()
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=_account(positions=(position,))))
    hold = _plan(bundle, bs.PLAN_HOLD)
    assert hold.executable
    # 到达盈利不自动清仓：exit 也存在，但允许继续持有。
    assert _plan(bundle, bs.PLAN_FULL_EXIT).executable


def test_g5_giveback_budget_exceeded_revokes_hold_authorization():
    position = _position(cost_price=5.0, mark=12.0, trend="up")  # 浮盈 7000
    config = bs.teaching_fixture()
    config = bs.teaching_fixture(budgets={
        **config.budgets.to_dict(), "giveback": 100.0})
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=_account(positions=(position,))))
    hold = _plan(bundle, bs.PLAN_HOLD)
    assert not hold.executable
    assert "giveback_budget_exceeded" in hold.blocked_reasons


def test_g5_principal_risk_budget_is_separate_from_giveback():
    position = _position(cost_price=10.0, mark=9.0, trend="up")  # 无浮盈
    config = bs.teaching_fixture(budgets={
        **bs.teaching_fixture().budgets.to_dict(), "held_principal": 100.0})
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=_account(positions=(position,))))
    checks = {item["check"]: item for item in bundle.risk_checks}
    assert checks["held_principal"]["passed"] is False


def test_g5_trend_unverified_does_not_extend_hold_authorization():
    position = _position(trend="unknown")
    bundle = bs.evaluate_account_policy(_context(
        account=_account(positions=(position,))))
    hold = _plan(bundle, bs.PLAN_HOLD)
    assert not hold.executable
    assert "trend_unverified" in hold.blocked_reasons


# ─── G6：主题共振、未知组与全账户压力 ──────────────────────────────────────


def test_g6_unknown_theme_is_added_to_every_known_theme_budget():
    known = _position(code="600001", theme_ids=("T1",), mark=10.0)
    unknown = _position(code="600002", theme_ids=(), mark=10.0)
    account = _account(cash=50_000.0, positions=(known, unknown))
    candidate = _candidate(code="600003", price=10.0, theme_ids=("T1",),
                           evidence_checks=_checks(
                               "entry:breakout:600003",
                               "entry:probe:600003", "confirm:600003"))
    config = bs.teaching_fixture(budgets={
        **bs.teaching_fixture().budgets.to_dict(), "per_theme": 1_500.0,
        "unknown_group": 2_000.0})
    scenarios = _scenarios(config, prices={
        "up": {"600001": [10.0] * 5, "600002": [10.0] * 5,
               "600003": [10.0, 10.1, 10.2, 10.3, 10.4]},
    })
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=account, candidates=(candidate,),
        scenarios=scenarios))
    entry = _plan(bundle, bs.PLAN_BREAKOUT, "600003")
    assert not entry.executable
    assert "theme_pressure_budget_exceeded" in entry.blocked_reasons
    theme_check = next(item for item in bundle.risk_checks
                       if item["check"] == "per_theme")
    assert theme_check["values"]["T1"] > 1_500.0
    # 放宽主题额度后，同一候选与路径可以通过压力门禁。
    relaxed = bs.teaching_fixture(budgets={
        **bs.teaching_fixture().budgets.to_dict(), "per_theme": 3_000.0,
        "unknown_group": 2_000.0})
    relaxed_bundle = bs.evaluate_account_policy(_context(
        config=relaxed, account=account, candidates=(candidate,),
        scenarios=_scenarios(relaxed, prices={
            "up": {"600001": [10.0] * 5, "600002": [10.0] * 5,
                   "600003": [10.0, 10.1, 10.2, 10.3, 10.4]}})))
    assert _plan(relaxed_bundle, bs.PLAN_BREAKOUT, "600003").executable


def test_g6_unknown_pressure_blocks_any_new_risk():
    unknown = _position(code="600002", pressure_pct=None)
    account = _account(positions=(unknown,))
    candidate = _candidate(code="600003", price=10.0)
    scenarios = _scenarios(bs.teaching_fixture(), prices={
        "up": {"600002": [10.0] * 5, "600003": [10.0, 10.1, 10.2, 10.3, 10.4]},
    })
    bundle = bs.evaluate_account_policy(_context(
        account=account, candidates=(candidate,), scenarios=scenarios))
    entry = _plan(bundle, bs.PLAN_BREAKOUT, "600003")
    assert not entry.executable
    assert any("pressure_unknown" in reason or
               "held_principal" in reason for reason in entry.blocked_reasons)


def test_g6_account_budget_includes_reservations_and_stock():
    position = _position(code="600001", mark=10.0, qty=2000,
                         theme_ids=("T1",))
    reservation = bs.Reservation(code="600002", side="buy", qty=2000,
                                 price=10.0, theme_ids=("T2",),
                                 pressure_pct=0.1)
    account = _account(cash=40_000.0, positions=(position,),
                       reservations=(reservation,))
    config = bs.teaching_fixture(budgets={
        **bs.teaching_fixture().budgets.to_dict(), "account": 4_000.0,
        "per_theme": 3_000.0, "unknown_group": 3_000.0,
        "per_trade": 1_000.0})
    scenarios = _scenarios(config, prices={
        "up": {"600001": [10.0] * 5, "600002": [10.0] * 5,
               "600003": [10.0, 10.1, 10.2, 10.3, 10.4]},
    })
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=account,
        candidates=(_candidate(code="600003", price=10.0,
                               evidence_checks=_checks(
                                   "entry:breakout:600003",
                                   "entry:probe:600003",
                                   "confirm:600003")),),
        scenarios=scenarios))
    entry = _plan(bundle, bs.PLAN_BREAKOUT, "600003")
    assert not entry.executable
    assert "account_pressure_budget_exceeded" in entry.blocked_reasons


# ─── G7：持有/退出/换旧买新：费用与未成交路径 ──────────────────────────────


def test_g7_switch_sell_failure_skips_buy_without_atomic_assumption():
    config = _long_hold_config()
    position = _position(code="600001", mark=10.0, trend="flat")
    # 买腿触发已满足：若卖腿未成交，买腿只能因 depends_on 被挡住。
    candidate = _candidate(code="600002", price=10.0, rank=1,
                           theme_ids=("T2",),
                           structure={"breakout_level": 9.5,
                                      "pullback_level": 9.5,
                                      "confirm_level": 9.5,
                                      "probe_level": 9.5},
                           evidence_checks=_checks(
                               "entry:breakout:600002",
                               "entry:probe:600002", "confirm:600002"))
    account = _account(cash=10_000.0, positions=(position,))
    scenarios = bs.scenario_set_from_records(
        _steps({"flat": {"600001": [10.0] * 5, "600002": [10.0] * 5}},
               tradable_by_step={index: {"600001": False}
                                 for index in range(2, 6)}),
        spec=config.scenario)
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=account, candidates=(candidate,),
        scenarios=scenarios))
    switch = next(plan for plan in bundle.plans
                  if plan.plan_kind == bs.PLAN_SWITCH)
    assert switch.executable
    outcome = bundle.outcome_matrix[switch.plan_id]["flat"]
    # 卖腿始终无法成交 → 买腿不得凭账户原有现金抢先成交。
    assert all(step["positions"].get("600002", 0) == 0
               for step in outcome["timeline"])
    assert any("not_tradable" in item for item in outcome["unfilled"])
    assert any("depends_on_unfilled" in item for item in outcome["unfilled"])


def test_g7_switch_pays_both_sides_so_costs_make_it_worse_than_hold():
    config = _long_hold_config()
    position = _position(code="600001", mark=10.0, trend="flat")
    candidate = _candidate(code="600002", price=10.0, theme_ids=("T2",),
                           evidence_checks=_checks(
                               "entry:breakout:600002",
                               "entry:probe:600002", "confirm:600002"))
    account = _account(cash=10_000.0, positions=(position,))
    scenarios = bs.scenario_set_from_records(
        _steps({"flat": {"600001": [10.0] * 5, "600002": [10.0] * 5}}),
        spec=config.scenario)
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=account, candidates=(candidate,),
        scenarios=scenarios))
    switch = next(plan for plan in bundle.plans
                  if plan.plan_kind == bs.PLAN_SWITCH)
    wait = _plan(bundle, bs.PLAN_WAIT)
    switch_pnl = bundle.outcome_matrix[switch.plan_id]["flat"]["net_pnl"]
    wait_pnl = bundle.outcome_matrix[wait.plan_id]["flat"]["net_pnl"]
    assert switch_pnl < wait_pnl


def test_g7_no_sellable_quantity_blocks_switch():
    position = _position(sellable_qty=0, trend="flat")
    candidate = _candidate(code="600002", price=10.0,
                           evidence_checks=_checks(
                               "entry:breakout:600002",
                               "entry:probe:600002", "confirm:600002"))
    bundle = bs.evaluate_account_policy(_context(
        account=_account(positions=(position,)), candidates=(candidate,)))
    switch = next(plan for plan in bundle.plans
                  if plan.plan_kind == bs.PLAN_SWITCH)
    assert not switch.executable
    assert "no_sellable_quantity" in switch.blocked_reasons
