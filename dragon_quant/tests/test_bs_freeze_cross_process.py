"""C2 前置：真实 G3 链冻结 JSON 跨进程逐事件重放等价（非 fixture）。"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from dragon_quant.review_account.execution_core.matching import ExecutionCostModel
from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.review_account.strategy.policy_replay import (
    ReplayEvent, ReplayInput, replay_account_policy,
)
from dragon_quant.research.bs_advice import experiment, freeze
from dragon_quant.research.bs_advice.account_file import AccountSnapshot
from dragon_quant.research.bs_advice.policy import LoadedPolicy
from dragon_quant.tests.test_bs_g3_real_chain import (
    CALENDAR, DAY1, DAY2, DAY3, DAY4, DAY5, T1, T2, T3, T4,
    TEACHING_QUOTE_MAX_AGE_MS, _candidate, _confirmation, _ref, _row,
    _scenarios_up,
)

_CHILD = textwrap.dedent(
    """
    import json
    import sys

    repo_root, payload_path, out_path = sys.argv[1:4]
    sys.path.insert(0, repo_root)
    from dragon_quant.tests import sqlite_guard

    sqlite_guard.install()
    sqlite_guard.verify_selftest()
    from dragon_quant.research.bs_advice import freeze

    with open(payload_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    result = freeze.replay_frozen(payload)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
    """)


def _chain(config):
    market = bs.MarketView(regime="trend_up", new_risk_allowed=True)
    scenarios = _scenarios_up(config)
    kwargs = {"scenarios": scenarios, "market": market,
              "pressures": {"600001": 0.10}, "themes": {"600001": ("T1",)}}
    probe = _candidate(10.0, ("entry:probe:600001",))
    add = _candidate(10.4, ("confirm:600001",),
                     confirmations=(_confirmation(),),
                     version=config.content_hash)
    events = (
        ReplayEvent(timestamp=T1, trade_date=DAY1,
                    rows={"600001": _row(T1, 10.0)}, candidates=(probe,),
                    decision_kwargs=dict(kwargs), decide=True),
        ReplayEvent(timestamp=T2, trade_date=DAY2,
                    rows={"600001": _row(T2, 10.1)}),
        ReplayEvent(timestamp=T3, trade_date=DAY3,
                    rows={"600001": _row(T3, 10.4)}, candidates=(add,),
                    decision_kwargs=dict(kwargs), decide=True),
        ReplayEvent(timestamp=T4, trade_date=DAY4,
                    rows={"600001": _row(T4, 10.5, capacity=250)}),
        ReplayEvent(timestamp=T4 + 60_000, trade_date=DAY4,
                    rows={"600001": _row(T4 + 60_000, 10.5, capacity=1000)},
                    candidates=(add,), decision_kwargs=dict(kwargs),
                    decide=True),
    )
    return events


def _freeze_payload(config, events):
    account = bs.AccountView(cash=100_000.0, positions=())
    cost = ExecutionCostModel()
    snapshot = AccountSnapshot(
        account=account, as_of="2026-11-18T09:00:00+08:00", recovery=None,
        policy_states=(), pending_orders=(), core_reservations=(),
        mark_facts={}, batch_facts={}, fund_credits={})
    policy = LoadedPolicy(config=config, normalized=config.to_dict(),
                          content_hash=config.content_hash,
                          fixture=bool(config.fixture), source="<g3-test>")
    pack = {
        "inputs": {
            "cost": experiment._cost_payload(cost), "participation": 0.1,
            "max_lag_ms": 120_000,
            "max_quote_age_ms": TEACHING_QUOTE_MAX_AGE_MS,
            "order_ttl_ms": None, "cohort_mode": "rolling",
            "evidence_manifest": {}, "universe_snapshots": [],
            "execution_coverage": {}, "execution_gaps": [],
            "events": experiment._events_payload(events)},
        "pack": {"ref": _ref(config)}, "calendar": list(CALENDAR),
        "date_from": DAY1, "date_to": DAY5,
        "decision_universes": (), "study": {}, "enhanced": {}}
    return snapshot, cost, freeze.freeze_experiment_inputs(
        account=snapshot, policy=policy, pack=pack)


def _original(config, events, cost):
    return replay_account_policy(ReplayInput(
        ledger_id="bs-frozen-replay",
        account=bs.AccountView(cash=100_000.0, positions=()),
        config=config, strategy_ref=_ref(config), events=events,
        calendar=tuple(CALENDAR), cost_model=cost,
        max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS, order_ttl_ms=None))


def _fees(result):
    return sum(float(fill.get("fee") or 0.0) for event in result["events"]
               for fill in event.get("fills") or ())


def test_g3_real_chain_freezes_and_replays_identically_in_new_process(tmp_path):
    config = bs.teaching_fixture(
        fixture=False, profile_id="g3-real", profile_version="real-1",
        label="real chain")
    events = _chain(config)
    _, cost, frozen = _freeze_payload(config, events)
    original = _original(config, events, cost)
    assert [len(event["fills"]) for event in original.events] == [0, 1, 0, 1, 1]
    assert [fill["qty"] for event in original.events
            for fill in event.get("fills") or ()] == [400, 250, 150], \
        "probe→confirm→partial add→later fill 必须真实成交"

    payload_path = tmp_path / "frozen.json"
    out_path = tmp_path / "replay.json"
    payload_path.write_text(json.dumps(frozen), encoding="utf-8")
    repo_root = str(Path(__file__).resolve().parents[2])
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _CHILD, repo_root, str(payload_path),
         str(out_path)],
        cwd=repo_root, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    replayed = json.loads(out_path.read_text(encoding="utf-8"))

    assert replayed["digest"] == frozen["digest"]
    assert replayed["impl_version"] == freeze.EXECUTION_IMPL_VERSION
    assert len(replayed["events"]) == len(original.events)
    for index, (expected, actual) in enumerate(
            zip(original.events, replayed["events"])):
        assert actual["decision_record"] == expected["decision_record"], index
        assert actual["execution_record"] == expected["execution_record"], index
        assert actual["fills"] == expected["fills"], index
        assert actual["state"]["equity"] == pytest.approx(
            expected["state"]["equity"]), index
        assert actual["state"]["cash"] == pytest.approx(
            expected["state"]["cash"]), index
    assert replayed["final_state"] == json.loads(
        json.dumps(original.final_state))
    assert replayed["fees"] == pytest.approx(_fees(
        {"events": original.events}), abs=1e-9)
    # 确认事实/批次/预留原样保持：冻结后重放仍有真实持仓批次。
    assert replayed["final_state"]["batches"]
    assert replayed["final_state"]["cash"] == pytest.approx(
        original.final_state["cash"], abs=1e-6)
