"""W5-I：真实公共 G3/G5/G8 冻结包删源跨进程重放 + 持久实验生命周期。

- 三份包均由当前 public producer（``build_experiment_pack`` /
  ``run_bs_experiment``）+ 临时 raw 本地资产生成，``fixture=False``；
- 源存在时保存每事件 DecisionRecord（含 runtime_audit/保护 scope）、
  ExecutionRecord、fills、state、final_state 与 digest/impl_version；
- 只删除**本测试 tmp_path 内**的 assets.db(+WAL/SHM)，新 ``-I`` 子进程装好
  SQLite/网络 guard 与临时 HOME/DQ 根后，从落盘 canonical frozen JSON 重放；
- 再用既有 ``research.bs_advice.service`` 的 replay/resume/new_attempt 验证
  复杂包持久恢复与幂等（源已删除，frozen 身份不变）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from dragon_quant.infra.calibration import PRODUCTION_CALIBRATIONS
from dragon_quant.research import bs_advice
from dragon_quant.research.bs_advice import assembly, experiment, freeze
from dragon_quant.research.bs_advice import service
from dragon_quant.review_account.strategy.bs_policy import teaching_fixture
from dragon_quant.tests.test_bs_advice import _cash_account, _minute, _readers
from dragon_quant.tests.test_bs_public_facts import _rising_daily
import dragon_quant.tests.test_bs_public_position_lifecycle as g5
from dragon_quant.tests.support import patch_storage_dbs
import dragon_quant.tests.test_bs_public_recovery_lifecycle as g8

_CHILD = textwrap.dedent(
    """
    import json
    import os
    import sys

    repo_root, payload_path, out_path, ledger_id, guard_root = sys.argv[1:6]
    os.environ["HOME"] = guard_root
    os.environ["DQ_DATA_DIR"] = guard_root
    sys.path.insert(0, repo_root)
    from dragon_quant.tests.e2e.injection import netguard

    netguard.install(allow_localhost=False)
    if not netguard.is_patched():
        raise SystemExit("network guard not installed")
    from dragon_quant.tests import sqlite_guard

    sqlite_guard.install(log_path=os.path.join(guard_root, "sqlite-guard.jsonl"))
    sqlite_guard.verify_selftest()
    from dragon_quant.research.bs_advice import freeze

    with open(payload_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    result = freeze.replay_frozen(payload, ledger_id=ledger_id)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
    """)


def _canonical(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _delete_source(db_path: Path, root: Path) -> None:
    """只删除本测试临时根内的原始 assets 库及其 WAL/SHM。"""
    root = root.resolve()
    assert root in Path(db_path).resolve().parents, (db_path, root)
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db_path) + suffix)
        assert root in path.resolve().parents, (path, root)
        if path.exists():
            path.unlink()
    assert not Path(db_path).exists()


def _assert_child_replay(pack, variant, tmp_path, *, label):
    original = _canonical({
        "digest": pack["digest"],
        "impl_version": freeze.EXECUTION_IMPL_VERSION,
        "events": variant["events"],
        "final_state": variant["final_state"]})
    frozen_path = tmp_path / f"{label}-frozen.json"
    original_path = tmp_path / f"{label}-original.json"
    out_path = tmp_path / f"{label}-replayed.json"
    frozen_path.write_text(json.dumps(pack["freeze"], ensure_ascii=False),
                           encoding="utf-8")
    original_path.write_text(json.dumps(original, ensure_ascii=False),
                             encoding="utf-8")
    guard_root = tmp_path / f"{label}-child-root"
    guard_root.mkdir()
    repo_root = str(Path(__file__).resolve().parents[2])
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _CHILD, repo_root, str(frozen_path),
         str(out_path), f"bs-exp:{pack['digest'][:16]}", str(guard_root)],
        cwd=repo_root, capture_output=True, text=True,
        env={"HOME": str(guard_root), "DQ_DATA_DIR": str(guard_root),
             "PATH": "/usr/bin:/bin"})
    assert completed.returncode == 0, completed.stderr
    replayed = json.loads(out_path.read_text(encoding="utf-8"))
    assert replayed["digest"] == original["digest"]
    assert replayed["impl_version"] == original["impl_version"]
    assert len(replayed["events"]) == len(original["events"])
    for index, (expected, actual) in enumerate(zip(original["events"],
                                                   replayed["events"])):
        assert actual["decision_record"] == expected["decision_record"], index
        assert actual["execution_record"] == expected["execution_record"], index
        assert actual["fills"] == expected["fills"], index
        assert actual["state"] == expected["state"], index
        audit = actual["decision_record"].get("runtime_audit") or {}
        if "trend_audit" in audit or "held_days_by_batch" in audit:
            assert audit == (expected["decision_record"]
                             .get("runtime_audit") or {}), index
    assert replayed["final_state"] == original["final_state"]
    fees = sum(float(fill.get("fee") or 0.0) for event in replayed["events"]
               for fill in event.get("fills") or ())
    assert replayed["fees"] == fees
    return replayed


def _g3_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: PRODUCTION_CALIBRATIONS[0].provenance)
    db_path = tmp_path / "assets.db"
    days = _rising_daily(db_path)
    for start, end, close in (("09:59:00", "10:00:00", 9.4),
                              ("10:00:00", "10:01:00", 10.9),
                              ("10:01:00", "10:02:00", 12.0),
                              ("10:02:00", "10:03:00", 12.0)):
        _minute(db_path, "2026-11-18", start=start, end=end, close=close)
    config = teaching_fixture(fixture=False, entry_mode="probe_then_confirm")
    account = bs_advice.parse_account_file(_cash_account(config))
    policy = bs_advice.load_policy_payload(config.to_dict())
    calendar = days + ["2026-11-18", "2026-11-19"]
    pack = bs_advice.build_experiment_pack(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, calendar=calendar,
        **_readers(days))
    result = bs_advice.run_bs_experiment(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, calendar=calendar, pack=pack)
    return db_path, pack, result["variants"]["bs"]


def test_public_g3_pack_replays_after_source_deletion_in_new_process(
        tmp_path, monkeypatch):
    """正式配置 G3 probe/add：删源后新进程重放 decision/execution/fill/state 全等。"""
    db_path, pack, variant = _g3_inputs(tmp_path, monkeypatch)
    plans = [intent["plan_id"] for event in variant["events"]
             for intent in (event.get("decision_record") or {})
             .get("intents") or ()]
    assert any(plan.startswith("probe:") for plan in plans), plans
    assert any(plan.startswith("add:") for plan in plans), plans
    assert [fill["qty"] for event in variant["events"]
            for fill in event.get("fills") or ()]
    _delete_source(db_path, tmp_path)
    replayed = _assert_child_replay(pack, variant, tmp_path, label="g3")
    assert replayed["final_state"]["batches"]


def test_public_g5_mixed_scope_pack_replays_after_source_deletion(
        tmp_path, monkeypatch):
    """G5 mixed owner/batch 包：删源后新进程重放保护 scope/趋势审计/fill 一致。"""
    config = g5._config()
    payload = g5._payload(config, [
        g5._batch("old-a", entry="2026-11-09", ref=g5._ref(config)),
        g5._batch("new-b", entry="2026-11-17", cost=10.0,
                  ref=g5._ref(config), plan="p2", family="f2")])
    db_path = tmp_path / "assets.db"
    pack, variant = g5._experiment(
        tmp_path, monkeypatch, config=config, payload=payload,
        minutes={"2026-11-18": {"600001": 10.1}}, days=["2026-11-18"])
    assert db_path.exists()
    obligations = [tuple(item["batch_ids"])
                   for event in variant["events"]
                   for item in ((event.get("decision_record") or {})
                                .get("bundle") or {})
                   .get("audit", {}).get("protection_obligations") or ()]
    assert sorted(set(obligations)) == [("old-a:sellable",)]
    sells = [fill["batch_id"] for event in variant["events"]
             for fill in event.get("fills") or () if fill["side"] == "sell"]
    assert sorted(sells) == ["new-b:sellable", "old-a:sellable"]
    _delete_source(db_path, tmp_path)
    _assert_child_replay(pack, variant, tmp_path, label="g5")


def test_public_g8_recovery_pack_replays_after_source_deletion(
        tmp_path, monkeypatch):
    """G8 多日恢复包：删源后新进程重放收缩/恢复状态与 fill 一致。"""
    payload = g8._cash_with_recovery(g8._config())
    db_path = tmp_path / "assets.db"
    pack, variant = g8._experiment(tmp_path, monkeypatch, payload=payload)
    assert db_path.exists()
    graduated = [event for event in variant["events"]
                 if (event.get("recovery") or {}).get("graduated_trade_date")]
    assert graduated, "G8 包必须包含毕业事件"
    _delete_source(db_path, tmp_path)
    _assert_child_replay(pack, variant, tmp_path, label="g8")


def _g8_service_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: PRODUCTION_CALIBRATIONS[0].provenance)
    db_path = tmp_path / "assets.db"
    history = g8._history(db_path)
    for day in g8.DAYS:
        g8._minute(db_path, day, start="09:59:00", end="10:00:00", close=10.0)
        g8._minute(db_path, day, start="10:00:00", end="10:01:00", close=10.1)
    config = g8._config()
    account = bs_advice.parse_account_file(
        g8._cash_with_recovery(config))
    policy = bs_advice.load_policy_payload(config.to_dict())
    calendar = history + g8.DAYS + ["2026-11-27"]
    readers = {
        "dragons_reader": lambda a, b: [{
            "code": "600001", "name": "龙一", "trade_date": history[-1],
            "scan_id": "s", "is_true_dragon": True}],
        "prep_reader": lambda day: (None, {"error": None})
        if day not in g8.DAYS else g8._prep_reader(day),
        "calendar_reader": lambda as_of: calendar,
        "plan_reader": lambda day: None,
    }
    return {
        "account": account, "policy": policy, "db_path": db_path,
        "calendar": calendar, "readers": readers,
        "date_from": g8.DAYS[0], "date_to": g8.DAYS[-1]}


def _fills_of(report):
    events = ((report.get("variants") or {}).get("bs") or {}).get("events") or ()
    return [dict(fill) for event in events for fill in event.get("fills") or ()]


def _persisted_fills(experiment_id):
    row = experiment.show_bs_experiment(experiment_id, include_details=True)
    return _fills_of(dict(row.get("report") or {}))


def test_persistent_experiment_resume_replay_and_new_attempt_without_source(
        tmp_path, monkeypatch):
    """持久实验：同 digest 幂等、显式 new_attempt append-only、删源后 replay/resume 一致。"""
    inputs = _g8_service_inputs(tmp_path, monkeypatch)
    # 持久实验记录写入本测试独立 dragon.db，不污染 session 级共享存储。
    storage_patch = patch_storage_dbs(tmp_path / "dragon.db")
    storage_patch.__enter__()
    try:
        lock_path = tmp_path / "bs.lock"
        common = dict(
            account=inputs["account"], policy=inputs["policy"],
            date_from=inputs["date_from"], date_to=inputs["date_to"],
            confirm=True, lock_path=lock_path, db_path=inputs["db_path"],
            calendar=inputs["calendar"], **inputs["readers"])
        first = service.run_experiment_flow(**common)
        experiment_id = int(first["id"])
        digest = str(first["pack_digest"])
        fills = _fills_of(first)
        final_state = (first["variants"]["bs"]["final_state"])
        assert fills and final_state["recovery"]
        # 同 digest 幂等：再次运行返回同一 ID 与同一 fill（不重复成交）。
        again = service.run_experiment_flow(**common)
        assert int(again["id"]) == experiment_id
        assert _persisted_fills(experiment_id) == fills
        # 显式 new_attempt 才新增 append-only 记录；旧 run 报告不变。
        fresh = service.run_experiment_flow(**common, new_attempt=True)
        assert int(fresh["id"]) != experiment_id
        assert _fills_of(fresh) == fills
        assert _persisted_fills(experiment_id) == fills
        # 删除源后：持久 run 仍可 replay/resume，frozen 身份与成交不变。
        _delete_source(inputs["db_path"], tmp_path)
        replayed = experiment.replay_bs_experiment(experiment_id)
        assert replayed["persisted"] is False
        assert int(replayed["experiment_id"]) == experiment_id
        assert replayed["digest"] == digest
        assert replayed["impl_version"] == freeze.EXECUTION_IMPL_VERSION
        assert [dict(item) for item in replayed["fills"]] == fills
        assert _canonical(replayed["variants"]["bs"]["final_state"]) == \
            _canonical(final_state)
        resumed = service.resume_experiment_flow(
            experiment_id=experiment_id, lock_path=lock_path)
        assert int(resumed["id"]) == experiment_id
        assert _fills_of(resumed) == fills
        row = experiment.show_bs_experiment(experiment_id, include_frozen=True)
        assert row["frozen_digest"] == digest
        # 同 frozen 的 running 行（模拟中断）可在无源环境从冻结重算并完成。
        running_id = experiment.save_experiment_run(
            request={"date_from": inputs["date_from"],
                     "date_to": inputs["date_to"]},
            expected_events=len(row["frozen"]["events"]),
            frozen=row["frozen"], run_digest=digest)
        recovered = service.resume_experiment_flow(
            experiment_id=running_id, lock_path=lock_path)
        assert _fills_of(recovered) == fills
        retried = service.resume_experiment_flow(
            experiment_id=running_id, lock_path=lock_path)
        assert _fills_of(retried) == fills, "重试不得重复成交"
        assert _canonical(recovered["variants"]["bs"]["final_state"]) == \
            _canonical(final_state)
    finally:
        storage_patch.close()
