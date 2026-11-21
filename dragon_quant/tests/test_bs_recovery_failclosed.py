"""W4a：生产非 fixture 旧恢复行不得以当前持仓重建过去触发 scope。"""

from __future__ import annotations

from dragon_quant.review_account.strategy.bs_policy import (
    AccountView, BatchView, MarketView, PolicyContext, PositionView,
    teaching_fixture,
)
from dragon_quant.review_account.strategy.bs_policy.recovery import (
    _recovery_decision,
)
from dragon_quant.review_account.strategy.bs_policy.scenario_contracts import (
    RecoveryState,
)


def _account(*, drawdown=None):
    batch = BatchView(batch_id="b1", qty=1000, sellable_qty=1000,
                      entry_date="2026-11-10", cost_price=10.0,
                      original_qty=1000)
    position = PositionView(code="600001", qty=1000, sellable_qty=1000,
                            cost_price=10.0, entry_date="2026-11-10",
                            mark=8.0, batches=(batch,))
    return AccountView(cash=50000.0, drawdown_pct=drawdown,
                       positions=(position,))


def _context(config, account, state):
    return PolicyContext(
        decision_time="2026-11-18T16:00:00+08:00", config=config,
        account=account, recovery_state=state, current_valuation_known=True,
        market=MarketView())


def test_latched_old_episode_missing_targets_is_fail_closed():
    config = teaching_fixture(fixture=False)
    state = RecoveryState(active=True, stage=0,
                          trigger_trade_date="2026-11-10", trigger_event=1)
    decision = _recovery_decision(_context(config, _account(), state), config,
                                  positions_account=_account())
    assert decision["active"] is True
    assert decision["contraction_targets"] == {}
    assert "target_snapshot_missing" in decision["reasons"]
    assert decision["can_resume"] is False
    assert decision["scale"] < 1.0, "新增风险仍须受限"


def test_new_real_trigger_may_snapshot_current_facts():
    config = teaching_fixture(fixture=False)
    decision = _recovery_decision(
        _context(config, _account(drawdown=20.0), RecoveryState(active=False)),
        config, positions_account=_account(drawdown=20.0))
    assert decision["active"] is True
    assert decision["contraction_targets"]
    assert "target_snapshot_missing" not in decision["reasons"]


def test_complete_seed_keeps_existing_targets():
    config = teaching_fixture(fixture=False)
    targets = {"600001": {"batch_ids": ["b1"], "target_qty": 500,
                          "remaining": {"b1": 1000}}}
    state = RecoveryState(active=True, stage=0,
                          trigger_trade_date="2026-11-10", trigger_event=1,
                          contraction_targets=targets,
                          target_snapshot_trade_date="2026-11-10")
    decision = _recovery_decision(_context(config, _account(), state), config,
                                  positions_account=_account())
    assert decision["contraction_targets"] == targets
    assert decision["target_snapshot_trade_date"] == "2026-11-10"
    assert "target_snapshot_missing" not in decision["reasons"]


def test_teaching_fixture_keeps_explicit_rebuild_compatibility():
    config = teaching_fixture(fixture=True)
    state = RecoveryState(active=True, stage=0,
                          trigger_trade_date="2026-11-10", trigger_event=1)
    decision = _recovery_decision(_context(config, _account(), state), config,
                                  positions_account=_account())
    assert decision["contraction_targets"]
    assert "target_snapshot_missing" not in decision["reasons"]
