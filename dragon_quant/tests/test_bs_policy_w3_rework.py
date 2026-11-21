"""W3 前置必改 R1/R2/R4 的回归（纯 W2 政策执行契约）。

- R1：保护义务按**剩余义务量**执行，不再隐式减半；容量/可卖量不足才分步，
      输出声明量/实际量/剩余量/时间一致，早期暴露不被“总卖量相同”掩盖。
- R2：同 code 有批次+无批次（旧行）账户聚合不整轮失败；旧行映射稳定单批，
      不凭空推断可卖量，其它可验证保护不被一行缺口吞掉。
- R4：纯存量退出/已退出政策报告 exited/held/pending_exit，不用 not_entered
      或空 policy_states 误导。
"""

from __future__ import annotations

from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.tests._bs_policy_support import (
    _account,
    _position,
    _steps,
)

CONFIG = bs.teaching_fixture()


def _path(prices, *, trade_dates=None, tradable=None):
    steps = _steps({"path": {"600001": prices}}, trade_dates=trade_dates,
                   tradable=tradable)[0]["steps"]
    return bs.scenario_set_from_records(
        [{"scenario_id": "path", "kind": "teaching_fixture",
          "fixture": True, "steps": steps}], spec=CONFIG.scenario).paths[0]


def _reduce_plan(qty):
    return bs.PolicyPlan(
        plan_id="reduce", plan_kind=bs.PLAN_PARTIAL_REDUCE, code="600001",
        executable=True,
        protection=(bs.ProtectionObligation(
            obligation_id="giveback:600001", code="600001", kind="giveback",
            action="reduce", qty=qty, executable=True),))


# ─── R1 ──────────────────────────────────────────────────────────────────────


def test_r1_reduce_obligation_sells_declared_remaining_in_one_supporting_event():
    # 义务已声明半仓 500 股，必须在首个可成交事件一次卖出 500，而不是 250+250。
    position = _position(code="600001", qty=1000, sellable_qty=1000,
                         cost_price=10.0, mark=10.0)
    account = _account(cash=10_000.0, positions=(position,))
    path = _path([10.0, 10.0, 10.0, 10.0, 10.0])
    outcome = bs.simulate_plan(_reduce_plan(500), path, account=account,
                               spec=CONFIG.scenario)
    # step1 观察/下单，step2 首个支持事件一次卖出声明量 500（不是 250+250）。
    assert outcome.timeline[0]["positions"]["600001"] == 1000
    second = outcome.timeline[1]
    assert second["positions"]["600001"] == 500
    exits = second["policy_state"]["exits"]
    assert len(exits) == 1
    assert exits[0]["declared_qty"] == 500
    assert exits[0]["sold_qty"] == 500
    assert exits[0]["remaining_qty"] == 0


def test_r1_capacity_shortfall_splits_across_events_without_second_halving():
    # 当日新增批次不可卖（T+1）：step1 卖 0，step2（次日）一次卖剩余 500。
    batch = bs.BatchView("b", 1000, 0, entry_date="2026-11-18", cost_price=10.0)
    position = bs.PositionView(code="600001", qty=1000, sellable_qty=0,
                               cost_price=10.0, mark=10.0, batches=(batch,))
    account = _account(cash=10_000.0, positions=(position,))
    path = _path([10.0] * 5,
                 trade_dates=["2026-11-18", "2026-11-19", "2026-11-19",
                              "2026-11-19", "2026-11-19"])
    outcome = bs.simulate_plan(_reduce_plan(500), path, account=account,
                               spec=CONFIG.scenario)
    assert outcome.timeline[0]["positions"]["600001"] == 1000
    assert not outcome.timeline[0]["policy_state"]["exits"]
    assert outcome.timeline[1]["positions"]["600001"] == 1000  # 释放日观察，
    third = outcome.timeline[2]                                # 下一事件成交
    assert third["positions"]["600001"] == 500
    exits = third["policy_state"]["exits"]
    assert exits[0]["declared_qty"] == 500
    assert exits[0]["sold_qty"] == 500


def test_r1_exit_obligation_sells_total_remaining_not_partial():
    position = _position(code="600001", qty=1000, sellable_qty=1000,
                         cost_price=10.0, mark=10.0)
    account = _account(cash=0.0, positions=(position,))
    plan = bs.PolicyPlan(
        plan_id="exit", plan_kind=bs.PLAN_FULL_EXIT, code="600001",
        executable=True,
        protection=(bs.ProtectionObligation(
            obligation_id="disaster:600001", code="600001", kind="disaster",
            action="exit", qty=1000, executable=True),))
    outcome = bs.simulate_plan(plan, _path([10.0] * 5), account=account,
                               spec=CONFIG.scenario)
    assert outcome.timeline[0]["positions"]["600001"] == 1000
    # 义务在下单后的首个支持事件一次卖清 1000 股（不拆成半仓）。
    assert outcome.timeline[1]["positions"] == {}
    assert outcome.timeline[1]["policy_state"]["exits"][0]["sold_qty"] == 1000


# ─── R2 ──────────────────────────────────────────────────────────────────────


def test_r2_mixed_legacy_and_batched_rows_aggregate_without_failing():
    legacy = bs.PositionView(code="600002", qty=500, sellable_qty=500,
                             cost_price=9.0, entry_date="2026-11-17")
    batched = bs.PositionView(
        code="600002", qty=1000, sellable_qty=1000, cost_price=10.0,
        batches=(bs.BatchView("real", 1000, 1000, entry_date="2026-11-18",
                              strategy_ref={"strategy_id": "user:demo"}),))
    account = bs.AccountView(cash=5_000.0, positions=(legacy, batched))
    aggregated = account.aggregated_positions()
    assert len(aggregated) == 1
    assert aggregated[0].qty == 1500
    batches = aggregated[0].batches
    assert {batch.batch_id for batch in batches} == {
        "real", "legacy:600002:2026-11-17:500:9.0"}
    # 稳定单批 ID 可重复计算（不在每次聚合漂移）。
    again = account.aggregated_positions()[0].batches
    assert [batch.batch_id for batch in again] == [batch.batch_id
                                                   for batch in batches]


def test_r2_mixed_rows_round_does_not_swallow_valid_protection():
    legacy = bs.PositionView(code="600001", qty=1000, sellable_qty=1000,
                             cost_price=10.0, mark=8.0, trend="down",
                             entry_date="2026-11-17")
    batched = bs.PositionView(
        code="600001", qty=200, sellable_qty=200, cost_price=10.0, mark=10.0,
        trend="down",
        batches=(bs.BatchView("real", 200, 200, entry_date="2026-11-18"),))
    account = bs.AccountView(cash=10_000.0, positions=(legacy, batched))
    scenarios = bs.scenario_set_from_records(
        _steps({"flat": {"600001": [8.0] * 5}}), spec=CONFIG.scenario)
    bundle = bs.evaluate_account_policy(bs.PolicyContext(
        decision_time="2026-11-18T16:00:00+08:00", config=CONFIG,
        account=account, candidates=(), market=bs.MarketView(
            regime="trend_up", new_risk_allowed=True),
        scenarios=scenarios))
    obligations = bundle.action_limits["protection_obligations"]
    assert any(item["code"] == "600001" for item in obligations)


# ─── R4 ──────────────────────────────────────────────────────────────────────


def test_r4_pure_exit_reports_exited_not_not_entered():
    position = _position(code="600001", qty=1000, sellable_qty=1000,
                         cost_price=10.0, mark=10.0)
    account = _account(cash=0.0, positions=(position,))
    plan = bs.PolicyPlan(
        plan_id="exit", plan_kind=bs.PLAN_FULL_EXIT, code="600001",
        executable=True,
        protection=(bs.ProtectionObligation(
            obligation_id="disaster:600001", code="600001", kind="disaster",
            action="exit", qty=1000, executable=True),))
    outcome = bs.simulate_plan(plan, _path([10.0] * 5), account=account,
                               spec=CONFIG.scenario)
    assert outcome.terminal_status == "exited"
    states = {item["code"]: item for item in outcome.policy_states}
    assert states["600001"]["status"] == "exited"
    assert states["600001"]["qty"] == 0
    # 原时间线保留（不是把已退出压成空状态）。
    assert len(outcome.timeline) == 5


def test_r4_fully_held_reports_held_not_entered():
    position = _position(code="600001", qty=1000, sellable_qty=1000,
                         cost_price=10.0, mark=10.0)
    account = _account(cash=0.0, positions=(position,))
    outcome = bs.simulate_plan(
        bs.PolicyPlan(plan_id="wait", plan_kind=bs.PLAN_WAIT),
        _path([10.0] * 5), account=account, spec=CONFIG.scenario)
    assert outcome.terminal_status == "held"
    assert [item["status"] for item in outcome.policy_states] == ["held"]
