"""BS 账户入口离线 E2E：真实 CLI 子进程 + 临时根 + 网络隔离。

覆盖：``research bs-advice`` 观察模式 envelope（无配置→受限并保留冻结保护）、
``review-account`` 选择 BS 但缺显式账户/配置时明确拒绝（不套旧 buy-all）、
``strategy list`` 暴露新稳定 ID 且默认选择不变。
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox

NOW = "2026-11-18T16:00:00+08:00"


def runner():
    from dragon_quant.tests.e2e.harness import runner as module

    return module


@contextmanager
def sandbox(case_id: str):
    box = EnvSandbox.for_case(case_id, now=NOW)
    try:
        yield box
    finally:
        box.cleanup()


def _json(result) -> dict:
    return json.loads(result.stdout)


def _account_file(box) -> str:
    from dragon_quant.review_account.strategy.bs_policy import teaching_fixture
    from dragon_quant.workspace import snapshots

    config = teaching_fixture()
    ref = snapshots.builtin_ref(strategy_id="builtin:bs_account_decision",
                                params={"bs_policy": config.to_dict()})
    payload = {
        "schema_version": 1, "currency": "CNY", "shares_unit": "share",
        "as_of": NOW, "cash": 50000.0,
        "positions": [{
            "code": "600001", "mark": 8.0,
            "mark_source": {"price": 8.0, "source": "tdx",
                            "available_at": "2026-11-18T15:05:00+08:00"},
            "batches": [{
                "batch_id": "orig-b1", "qty": 1000, "sellable_qty": 1000,
                "entry_date": "2026-11-17", "cost_price": 10.0,
                "original_qty": 1000, "initial_risk": 300.0,
                "plan_id": "p1", "strategy_ref": ref}]}],
    }
    path = box.root / "account.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_bs_advice_cli_observation_envelope_without_config():
    with sandbox("bs-advice-observe") as box:
        account = _account_file(box)
        result = runner().run_cli(
            ["research", "bs-advice", "--account-file", account,
             "--cutoff", NOW, "--json"], sandbox=box)
        assert result.returncode == 0, result.stderr
        payload = _json(result)
        assert payload["schema_version"] == "research_v1"
        data = payload["data"]
        assert data["status"] == "restricted"
        assert "policy_config_missing" in data["missing_capabilities"]
        assert data["decision"] is None
        assert data["protections"], "冻结保护不得因缺少新配置被取消"
        assert data["persisted"] is False and data["orders_placed"] is False


def test_review_account_bs_without_explicit_inputs_is_rejected():
    with sandbox("bs-review-reject") as box:
        result = runner().run_cli(
            ["review-account", "--strategy", "builtin:bs_account_decision",
             "--from", "20261118", "--to", "20261119"], sandbox=box)
        assert result.returncode == 2
        assert "research bs-advice" in result.stderr
        assert "Traceback" not in result.stderr


def test_strategy_list_exposes_bs_id_and_keeps_default():
    with sandbox("bs-strategy-list") as box:
        result = runner().run_cli(["strategy", "list", "--json"], sandbox=box)
        assert result.returncode == 0, result.stderr
        payload = _json(result)
        ids = [item["id"] for item in payload["strategies"]]
        assert "builtin:bs_account_decision" in ids
        assert payload["current"] == "builtin:dragon_pullback_daily"


def test_saved_workspace_bs_selection_enters_account_protocol():
    with sandbox("bs-workspace-select") as box:
        workspace = str(box.root / "ws")
        assert runner().run_cli(
            ["workspace", "init", workspace, "--json"], sandbox=box).returncode == 0
        use = runner().run_cli(
            ["strategy", "use", "builtin:bs_account_decision",
             "--workspace", workspace, "--json"], sandbox=box)
        assert use.returncode == 0, use.stderr
        result = runner().run_cli(
            ["review-account", "--workspace", workspace,
             "--from", "20261118", "--to", "20261119"], sandbox=box)
        assert result.returncode == 2
        assert "research bs-advice" in result.stderr
        assert "Traceback" not in result.stderr


def test_bs_ledger_missing_is_structured_error():
    with sandbox("bs-ledger-missing") as box:
        result = runner().run_cli(
            ["research", "bs-ledger", "--ledger-id", "bsrun_missing", "--json"],
            sandbox=box)
        assert result.returncode == 1
        payload = _json(result)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "ledger_missing"


def test_bs_experiment_list_and_confirmation_gate():
    with sandbox("bs-experiment") as box:
        listing = runner().run_cli(
            ["research", "bs-experiment", "list", "--json"], sandbox=box)
        assert listing.returncode == 0, listing.stderr
        assert _json(listing)["data"] == []
        run = runner().run_cli(
            ["research", "bs-experiment", "run", "--account-file", "a.json",
             "--bs-policy", "p.json", "--from", "20261118", "--to", "20261119",
             "--json"], sandbox=box)
        assert run.returncode == 2
        assert _json(run)["error"]["code"] == "confirmation_required"


_PREP = r'''
import json, os, pathlib
from datetime import date, timedelta, datetime
from dragon_quant.infra.assets import observations, db as assets_db
from dragon_quant.infra.calibration import PRODUCTION_CALIBRATIONS, binding_for
from dragon_quant.scan.storage import dragons as dragon_store
root = pathlib.Path(os.environ["DQ_DATA_DIR"]); db = root / "assets.db"
cid = PRODUCTION_CALIBRATIONS[0].calibration_id
days = []; cursor = date(2026, 10, 20)
while cursor <= date(2026, 11, 19):
    if cursor.weekday() < 5: days.append(cursor.isoformat())
    cursor += timedelta(days=1)
rise = len(days) - 7
closes = [9.4 + 1.2 * i / max(1, rise - 1) for i in range(rise)] + [10.1] * 7
for i, day in enumerate(days):
    close = round(closes[min(i, len(closes) - 1)], 4)
    high = 10.15 if day == "2026-11-17" else close
    ts = f"{day}T15:05:00+08:00"
    observations.append_bar(
        trade_date=day, market="SH", code="600001", period="daily",
        interval_start=f"{day}T09:30:00+08:00",
        interval_end=f"{day}T15:00:00+08:00", open=close, high=high,
        low=close, close=close, volume=1_000_000, amount=close * 1_000_000,
        source="tdx", observed_at=ts, available_at=ts, completeness="final",
        time_semantics="verified", unit_semantics="verified",
        adjustment="none", db_path=db,
        calibration=binding_for(cid, calibrated_at=ts, source="tdx",
                                period="daily"))
for start, end, close in (("09:59:00", "10:00:00", 10.0),
                          ("10:00:00", "10:01:00", 10.3),
                          ("10:01:00", "10:02:00", 10.05)):
    at = (datetime.fromisoformat(f"2026-11-18T{end}+08:00")
          + timedelta(seconds=30)).isoformat()
    observations.append_bar(
        trade_date="2026-11-18", market="SH", code="600001", period="1min",
        interval_start=f"2026-11-18T{start}+08:00",
        interval_end=f"2026-11-18T{end}+08:00", open=close, high=close,
        low=close, close=close, volume=1_000_000, amount=close * 1_000_000,
        source="tdx", observed_at=at, available_at=at, completeness="final",
        time_semantics="verified", unit_semantics="verified",
        adjustment="none", db_path=db,
        calibration=binding_for(cid, calibrated_at=at, source="tdx",
                                period="1min"))
dragon_store.save_dragons("2026-11-17", [{
    "code": "600001", "name": "龙一", "rank": 1, "composite_score": 90.0,
    "is_true_dragon": True, "scan_id": "scan-1"}], version="v2")
assets_db.save_prep_snapshot("2026-11-17", {
    "review_candidates": [{"code": "600001", "name": "龙一",
                           "trade_date": "2026-11-17",
                           "eligibility": "eligible", "position_pct": 5.0}],
    "market": {"state": "strong", "score": 80.0, "coeff": 1.0,
               "partial": False, "missing_inputs": []}}, source="v2")
conn = assets_db._connect(create=False)
conn.execute("UPDATE prep_snapshots SET created_at=? WHERE trade_date=?",
             ("2026-11-17T16:50:00+08:00", "2026-11-17"))
conn.commit(); conn.close()
'''


def test_bs_experiment_cross_restart_replay_without_source():
    import subprocess
    import sys

    from dragon_quant.tests.test_bs_advice import _cash_account, _config

    with sandbox("bs-cross-restart") as box:
        prepared = subprocess.run([sys.executable, "-c", _PREP],
                                  env=box.env(), capture_output=True,
                                  text=True)
        assert prepared.returncode == 0, prepared.stderr
        account = box.root / "account.json"
        policy = box.root / "policy.json"
        config = _config()
        account.write_text(json.dumps(_cash_account(config)), encoding="utf-8")
        policy.write_text(json.dumps(config.to_dict()), encoding="utf-8")
        run = runner().run_cli(
            ["research", "bs-experiment", "run",
             "--account-file", str(account), "--bs-policy", str(policy),
             "--from", "20261118", "--to", "20261118", "--confirm", "--json"],
            sandbox=box)
        assert run.returncode == 0, run.stderr
        report = _json(run)["data"]
        assert report["status"] == "completed"
        assert report["metrics"]["bs"]["denominator"]["fills"] >= 1
        experiment_id = report["id"]
        digest = report["pack_digest"]
        # 删除原始源，后续 show/replay 必须只读冻结包。
        (box.data_dir / "assets.db").unlink()
        shown = runner().run_cli(
            ["research", "bs-experiment", "show", "--experiment-id",
             str(experiment_id), "--json"], sandbox=box)
        assert shown.returncode == 0, shown.stderr
        shown_data = _json(shown)["data"]
        assert shown_data["replayable"] is True
        assert "frozen" not in shown_data["request"]
        full = runner().run_cli(
            ["research", "bs-experiment", "show", "--experiment-id",
             str(experiment_id), "--include-frozen", "--json"], sandbox=box)
        assert full.returncode == 0, full.stderr
        assert _json(full)["data"]["frozen"]["digest"] == digest
        replay = runner().run_cli(
            ["research", "bs-experiment", "replay", "--experiment-id",
             str(experiment_id), "--json"], sandbox=box)
        assert replay.returncode == 0, replay.stderr
        replay_data = _json(replay)["data"]
        assert replay_data["digest"] == digest
        assert len(replay_data["fills"]) == report["metrics"]["bs"]["denominator"]["fills"]


def test_bs_experiment_resume_failed_full_experiment_without_source():
    import subprocess
    import sys

    from dragon_quant.tests.test_bs_advice import _cash_account, _config

    with sandbox("bs-resume-full") as box:
        prepared = subprocess.run([sys.executable, "-c", _PREP],
                                  env=box.env(), capture_output=True,
                                  text=True)
        assert prepared.returncode == 0, prepared.stderr
        account = box.root / "account.json"
        policy = box.root / "policy.json"
        config = _config()
        account.write_text(json.dumps(_cash_account(config)), encoding="utf-8")
        policy.write_text(json.dumps(config.to_dict()), encoding="utf-8")
        run = runner().run_cli(
            ["research", "bs-experiment", "run",
             "--account-file", str(account), "--bs-policy", str(policy),
             "--from", "20261118", "--to", "20261118", "--confirm", "--json"],
            sandbox=box)
        assert run.returncode == 0, run.stderr
        report = _json(run)["data"]
        experiment_id = report["id"]
        # 模拟运行中失败：把同一 ID 标失败，再删源后 resume。
        subprocess.run([sys.executable, "-c", f"""
import os
from dragon_quant.review_account import storage
storage.update_review_account_experiment({experiment_id}, status='failed',
    report={{'status':'failed','expected_events':0,'completed_events':0}})
"""], env=box.env(), check=True, capture_output=True)
        (box.data_dir / "assets.db").unlink()
        resumed = runner().run_cli(
            ["research", "bs-experiment", "resume", "--experiment-id",
             str(experiment_id), "--json"], sandbox=box)
        assert resumed.returncode == 0, resumed.stderr
        data = _json(resumed)["data"]
        assert data["status"] == "completed"
        assert data["expected_events"] == data["completed_events"]
        assert data["metrics"]["bs"]["denominator"]["fills"] >= 1
        assert data["metrics"]["baseline_no_new_risk"] is not None
        assert "bs" in data["variants"]


def test_bs_experiment_study_and_enhanced_by_id_cross_restart():
    import subprocess
    import sys

    from dragon_quant.review_account.strategy.bs_policy import teaching_fixture
    from dragon_quant.tests.test_bs_advice import _cash_account, _config

    with sandbox("bs-study-enhanced") as box:
        prepared = subprocess.run([sys.executable, "-c", _PREP],
                                  env=box.env(), capture_output=True,
                                  text=True)
        assert prepared.returncode == 0, prepared.stderr
        config = _config()
        account = box.root / "account.json"
        policy = box.root / "policy.json"
        study_path = box.root / "study.json"
        account.write_text(json.dumps(_cash_account(config)), encoding="utf-8")
        policy.write_text(json.dumps(config.to_dict()), encoding="utf-8")
        variant = teaching_fixture(profile_id="e2e-variant",
                                   profile_version="real-1", fixture=False)
        study = {
            "schema_version": 1, "study_id": "e2e-study",
            "evaluation_cutoff": "2026-11-18", "tail_threshold_pct": 5.0,
            "variants": [
                {"name": "no_new_risk", "kind": "baseline_no_new_risk"},
                {"name": "base", "kind": "policy",
                 "config": config.to_dict()},
                {"name": "variant", "kind": "policy",
                 "config": variant.to_dict()}]}
        study_path.write_text(json.dumps(study), encoding="utf-8")
        first = runner().run_cli(
            ["research", "bs-experiment", "run", "--account-file",
             str(account), "--bs-policy", str(policy), "--from", "20261118",
             "--to", "20261118", "--confirm", "--study-file",
             str(study_path), "--json"], sandbox=box)
        assert first.returncode == 0, first.stderr
        first_data = _json(first)["data"]
        assert first_data["status"] == "completed"
        by_variant = first_data["metrics"]["comparison"]["study"]["by_variant"]
        assert set(by_variant) == {"no_new_risk", "base", "variant"}
        assert by_variant["variant"]["config_hash"] == variant.content_hash
        assert by_variant["variant"]["gates"] is not None
        assert by_variant["variant"]["funding"] is not None
        assert first_data["metrics"]["bs"]["tail_threshold_source"] == \
            "study_explicit"
        # 真实 pipeline：replay 事件无 rows，但按 timestamp join 冻结输入参考价。
        slippage = first_data["metrics"]["bs"]["slippage"]
        assert slippage["basis"] == "realized_from_frozen_reference_price"
        assert slippage["realized"]["buy"]["fills"] >= 1
        assert slippage["realized"]["buy"]["rate_pct"] is not None

        second = runner().run_cli(
            ["research", "bs-experiment", "run", "--account-file",
             str(account), "--bs-policy", str(policy), "--from", "20261118",
             "--to", "20261118", "--confirm", "--study-file",
             str(study_path), "--enhanced-experiment-id",
             str(first_data["id"]), "--json"], sandbox=box)
        assert second.returncode == 0, second.stderr
        second_data = _json(second)["data"]
        assert second_data["id"] != first_data["id"], "研究身份含增强来源"
        assert second_data["data_modes"]["enhanced_frozen"]["status"] == \
            "insufficient_data"
        assert second_data["data_modes"]["enhanced_frozen"]["reason"] == \
            "enhanced_evidence_not_distinct"
        assert second_data["metrics"]["capability_effect"]["status"] == \
            "insufficient_data"

        detailed = runner().run_cli(
            ["research", "bs-experiment", "show", "--experiment-id",
             str(second_data["id"]), "--include-details", "--json"],
            sandbox=box)
        assert detailed.returncode == 0, detailed.stderr
        assert "variants" in _json(detailed)["data"]["report"]

        subprocess.run([sys.executable, "-c", f"""
import os
from dragon_quant.review_account import storage
storage.update_review_account_experiment({second_data['id']}, status='failed',
    report={{'status':'failed','expected_events':0,'completed_events':0}})
"""], env=box.env(), check=True, capture_output=True)
        (box.data_dir / "assets.db").unlink()
        resumed = runner().run_cli(
            ["research", "bs-experiment", "resume", "--experiment-id",
             str(second_data["id"]), "--json"], sandbox=box)
        assert resumed.returncode == 0, resumed.stderr
        resumed_data = _json(resumed)["data"]
        assert resumed_data["status"] == "completed"
        assert set(resumed_data["metrics"]["comparison"]["study"]
                   ["by_variant"]) == {"no_new_risk", "base", "variant"}
        assert resumed_data["data_modes"]["enhanced_frozen"]["reason"] == \
            "enhanced_evidence_not_distinct"
        replayed = runner().run_cli(
            ["research", "bs-experiment", "replay", "--experiment-id",
             str(second_data["id"]), "--json"], sandbox=box)
        assert replayed.returncode == 0, replayed.stderr
        replay_data = _json(replayed)["data"]
        assert replay_data["digest"] == second_data["pack_digest"]
        assert set(replay_data["metrics"]["comparison"]["study"]
                   ["by_variant"]) == {"no_new_risk", "base", "variant"}
