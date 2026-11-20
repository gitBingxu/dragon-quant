"""持续采集命令的离线 E2E（W0：计划 / 取数 / 会话 / 覆盖 / 调度模板）。

每个用例在 M0 沙箱中运行真实 ``python -m dragon_quant`` 子进程：``DQ_DATA_DIR``
指向沙箱 data 目录，TDX 由 fake provider 提供（scenario），网络与进程 guard
强制开启；绝不打开真实 dragon.db / assets.db。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from dragon_quant.tests.e2e.fixtures import seed_builders as sb
from dragon_quant.tests.e2e.harness.assertlib import sqlite_count, sqlite_rows
from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox
from dragon_quant.tests.e2e.harness.seed import seed_db

NOW = "2026-11-18T09:26:00+08:00"
TRADE_DATE = "2026-11-18"
SCENARIO_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "scenario_capture.json")


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


def run_cli(box: EnvSandbox, args: list[str], *, timeout: int = 120):
    return runner().run_cli(
        args, sandbox=box, timeout=timeout,
        env_override={
            "DQ_E2E_CLOCK_MODE": "runtime",
            "DQ_E2E_SCENARIO": str(SCENARIO_PATH),
        })


def _json(stdout: str) -> dict:
    return json.loads(stdout)


def _seed_pools(box: EnvSandbox) -> None:
    """dragons 镜像 + prep 快照（含 position_pct=0 的候选，验证不反选）。"""
    spec = sb.mirror_db_seed([
        sb.dragon_row("2026-11-17", "600001", "甲", 1, 88.0, ["AI算力"]),
    ])
    payload = {
        "trade_date": "2026-11-17",
        "version": "1",
        "review_candidates": [
            {"code": "600002", "name": "乙", "position_pct": 0},
        ],
    }
    spec["tables"]["prep_snapshots"] = {
        "columns": {
            "trade_date": "TEXT NOT NULL", "source": "TEXT NOT NULL",
            "result_json": "TEXT NOT NULL", "created_at": "TEXT",
        },
        "primary_key": ["trade_date", "source"],
        "rows": [{
            "trade_date": "2026-11-17", "source": "v2",
            "result_json": json.dumps(payload, ensure_ascii=False),
            "created_at": "2026-11-17 16:45:00",
        }],
    }
    seed_db(box, spec, db_name="assets.db")


def _freeze_plan(box: EnvSandbox) -> str:
    frozen = run_cli(box, ["assets", "capture", "plan", "--date", "20261118",
                           "--json"])
    assert frozen.returncode == 0, frozen.stderr
    return _json(frozen.stdout)["plan_id"]


def test_capture_group_help_and_schedule_template_write_nothing():
    with sandbox("cap1") as box:
        help_result = run_cli(box, ["assets", "capture", "-h"])
        assert help_result.returncode == 0
        assert "plan" in help_result.stdout and "coverage" in help_result.stdout

        result = run_cli(box, ["assets", "capture", "schedule",
                               "--kind", "cron", "--json"])
        assert result.returncode == 0, result.stderr
        payload = _json(result.stdout)
        assert payload["ok"] is True
        assert payload["installed"] is False
        assert "assets capture session" in payload["content"]
        # 模板输出不建库、不写系统配置。
        assert not (box.data_dir / "assets.db").exists()


def test_capture_plan_dry_run_registers_pools_without_writing():
    with sandbox("cap2") as box:
        _seed_pools(box)
        preview = run_cli(box, ["assets", "capture", "plan", "--date",
                                "20261118", "--dry-run", "--json"])
        assert preview.returncode == 0, preview.stderr
        payload = _json(preview.stdout)
        assert payload["dry_run"] is True
        assert payload["plan_id"] is None
        codes = [item["code"] for item in payload["plan"]["members"]]
        assert codes == ["600001", "600002"]
        reasons = {item["code"]: item["entry_reason"]
                   for item in payload["plan"]["members"]}
        assert reasons["600001"] == "dragon:2026-11-17"
        assert reasons["600002"] == "prep:2026-11-17"
        # dry-run 不落计划。
        assert sqlite_count(box.data_dir / "assets.db", "capture_plans") == 0


def test_capture_once_writes_observations_and_replay_is_idempotent():
    with sandbox("cap3") as box:
        _seed_pools(box)
        plan_id = _freeze_plan(box)
        assert sqlite_count(box.data_dir / "assets.db", "capture_plans") == 1

        args = ["assets", "capture", "once", "--plan-id", plan_id,
                "--topics", "auction_path,auction_final", "--json"]
        first = run_cli(box, args)
        assert first.returncode == 0, first.stderr
        payload = _json(first.stdout)
        assert payload["status"] == "complete"
        assert {item["status"] for item in payload["topics"]} == {"complete"}

        assets = box.data_dir / "assets.db"
        assert sqlite_count(assets, "capture_runs") == 1
        assert sqlite_count(assets, "market_observations") == 4
        run = sqlite_rows(assets, "capture_runs")[0]
        assert run["status"] == "complete"
        # 提交后不残留活跃租约。
        assert run["lease_owner"] is None

        again = run_cli(box, args)
        assert again.returncode == 0, again.stderr
        assert _json(again.stdout)["topics"][0]["reason"] == "already_complete"
        assert sqlite_count(assets, "market_observations") == 4


def test_capture_once_dry_run_never_calls_the_provider():
    with sandbox("cap4") as box:
        _seed_pools(box)
        plan_id = _freeze_plan(box)
        result = run_cli(box, ["assets", "capture", "once", "--plan-id", plan_id,
                               "--topics", "auction_path", "--dry-run",
                               "--json"])
        assert result.returncode == 0, result.stderr
        payload = _json(result.stdout)
        assert payload["status"] == "planned"
        assert payload["topics"][0]["scope"] == ["600001", "600002"]
        assets = box.data_dir / "assets.db"
        assert sqlite_count(assets, "capture_runs") == 0
        assert sqlite_count(assets, "market_observations") == 0
        assert "get_auction" not in box.call_log.read_text(encoding="utf-8")


def test_capture_session_runs_the_planned_auction_slot():
    with sandbox("cap5") as box:
        _seed_pools(box)
        plan_id = _freeze_plan(box)
        result = run_cli(box, ["assets", "capture", "session",
                               "--plan-id", plan_id,
                               "--phase", "auction", "--max-slots", "1",
                               "--interval-seconds", "0", "--json"])
        assert result.returncode == 0, result.stderr
        payload = _json(result.stdout)
        assert payload["plan_id"] == plan_id
        assert payload["slots"][0]["slot"] == "0926"
        assert payload["slots"][0]["status"] == "complete"
        assert payload["status"] == "complete"


def test_capture_coverage_reports_plan_observations_and_gaps():
    with sandbox("cap6") as box:
        _seed_pools(box)
        plan_id = _freeze_plan(box)
        collected = run_cli(box, ["assets", "capture", "once", "--plan-id",
                                  plan_id, "--topics", "auction_path",
                                  "--json"])
        assert collected.returncode == 0, collected.stderr

        coverage = run_cli(box, ["assets", "capture", "coverage",
                                 "--from", "20261118", "--to", "20261118",
                                 "--limit", "10", "--offset", "0", "--json"])
        assert coverage.returncode == 0, coverage.stderr
        payload = _json(coverage.stdout)
        assert [plan["plan_id"] for plan in payload["plans"]] == [plan_id]
        assert payload["observations"]
        assert "bar_groups" in payload and "interval_coverage" in payload
        assert payload["totals"]["observation_groups"] >= 1
        reasons = {gap["reason"] for gap in payload["gaps"]}
        # 未采的主题进入缺口清单，不谎称全覆盖。
        assert "not_collected" in reasons
        assert payload["gaps_truncated"] is False


def test_capture_coverage_on_missing_database_is_empty_and_creates_nothing():
    with sandbox("cap7") as box:
        result = run_cli(box, ["assets", "capture", "coverage", "--json"])
        assert result.returncode == 0, result.stderr
        payload = _json(result.stdout)
        assert payload["plans"] == []
        assert payload["observations"] == []
        assert not (box.data_dir / "assets.db").exists()
