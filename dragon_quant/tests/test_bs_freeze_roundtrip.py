"""C1 F1：真实 G7 事件序列与 G8 恢复种子的冻结往返重放一致。"""

from __future__ import annotations

from dragon_quant.research.bs_advice import freeze, experiment
from dragon_quant.research.bs_advice.account_file import AccountSnapshot
from dragon_quant.research.bs_advice.policy import LoadedPolicy
from dragon_quant.review_account.execution_core.matching import ExecutionCostModel
from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.review_account.strategy.bs_policy.scenario_contracts import (
    RecoveryState,
)
from dragon_quant.review_account.strategy.policy_replay import (
    ReplayInput, replay_account_policy,
)
from dragon_quant.tests.test_bs_policy_replay import (
    CALENDAR, _ref, _replay_events,
)


def _snapshot(config):
    batch = bs.BatchView(
        batch_id="incumbent", qty=1000, sellable_qty=1000,
        entry_date="2026-11-17", cost_price=10.0, strategy_ref=_ref(config),
        plan_id="legacy:600001", original_qty=1000, initial_risk=100.0)
    position = bs.PositionView(
        code="600001", name="持仓", qty=1000, sellable_qty=1000,
        cost_price=10.0, entry_date="2026-11-17", mark=10.5,
        theme_ids=("T1",), pressure_pct=0.10, trend="down", held_days=2,
        strategy_ref=_ref(config), batches=(batch,))
    return bs.AccountView(cash=100_000.0, positions=(position,))


def test_g7_event_sequence_and_g8_recovery_roundtrip():
    config = bs.teaching_fixture()
    view = _snapshot(config)
    recovery = RecoveryState(
        active=True, stage=1, trigger_trade_date="2026-11-17",
        trigger_event=CALENDAR and 1, target_snapshot_trade_date="2026-11-17",
        contraction_targets={"600001": {"batch_ids": ["incumbent"],
                                        "target_qty": 500,
                                        "remaining": {"incumbent": 1000}}})
    snapshot = AccountSnapshot(
        account=view, as_of="2026-11-17T16:00:00+08:00", recovery=recovery,
        mark_facts={}, batch_facts={}, fund_credits={})
    policy = LoadedPolicy(
        config=config, normalized=config.to_dict(),
        content_hash=config.content_hash, fixture=True, source="<test>")
    events = _replay_events(config)
    inputs = {
        "cost": experiment._cost_payload(ExecutionCostModel()),
        "participation": 0.1, "max_lag_ms": 120_000,
        "max_quote_age_ms": None, "order_ttl_ms": 86_400_000,
        "cohort_mode": "fixed", "evidence_manifest": {},
        "universe_snapshots": [], "decision_universes": [],
        "execution_coverage": {}, "execution_gaps": {},
        "events": experiment._events_payload(events),
    }
    pack = {"inputs": inputs,
            "pack": {"ref": _ref(config), "cost_model": ExecutionCostModel()},
            "calendar": list(CALENDAR), "date_from": CALENDAR[0],
            "date_to": CALENDAR[-1], "cohort_mode": "fixed"}
    frozen = freeze.freeze_experiment_inputs(
        account=snapshot, policy=policy, pack=pack)
    original = replay_account_policy(ReplayInput(
        ledger_id="roundtrip", account=view, config=config,
        strategy_ref=_ref(config), events=events, calendar=tuple(CALENDAR),
        max_quote_age_ms=None, cost_model=ExecutionCostModel(),
        recovery=recovery, mark_facts={}, batch_facts={}, fund_credits={}))
    replayed = freeze.replay_frozen(frozen, ledger_id="roundtrip")
    assert [record["fills"] for record in replayed["events"]] == \
        [record["fills"] for record in original.events]
    assert [record["decision_record"] for record in replayed["events"]] == \
        [record["decision_record"] for record in original.events]
    assert replayed["final_state"] == original.final_state
    loaded = freeze.load_experiment_inputs(frozen)
    assert loaded.account.recovery.to_dict() == recovery.to_dict()
