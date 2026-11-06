"""research 离线 E2E：真实 CLI 子进程 + 临时数据根 + 网络隔离。

覆盖数据集发现/字段契约、查询参数拒绝、空态降级、账户/信号/快照/策略读回与
实验只读/确认门禁；不打开真实库、不触网、不写业务数据。
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox

NOW = "2026-11-06T15:30:00+08:00"


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


def test_research_help_lists_every_action():
    with sandbox("research-help") as box:
        result = runner().run_cli(["research", "-h"], sandbox=box)
        assert result.returncode == 0, result.stderr
        for action in ("datasets", "schema", "query", "scan-quality", "account",
                       "signals", "snapshots", "strategy", "experiments"):
            assert action in result.stdout


def test_research_datasets_and_schema_contracts_without_data():
    with sandbox("research-catalog") as box:
        datasets = runner().run_cli(["research", "datasets", "--json"], sandbox=box)
        assert datasets.returncode == 0, datasets.stderr
        payload = _json(datasets)
        assert payload["schema_version"] == "research_v1"
        names = {row["name"] for row in payload["data"]}
        assert len(names) == 17
        assert "scan_candidates" in names and "experiments" in names
        assert all(row["availability"]["status"] == "database_missing"
                   for row in payload["data"])

        schema = runner().run_cli(
            ["research", "schema", "--dataset", "scan_candidates", "--json"],
            sandbox=box)
        assert schema.returncode == 0, schema.stderr
        body = _json(schema)["data"]
        assert body["table"] == "scan_stocks_v2"
        assert {field["name"] for field in body["fields"]} >= {
            "scan_id", "code", "composite_score", "reject_reason"}
        assert "--scan-id" in body["filter_arguments"].values()


def test_research_query_rejects_unknown_and_injected_arguments():
    with sandbox("research-query-invalid") as box:
        for args in (
            ["research", "query", "--dataset", "sqlite_master", "--json"],
            ["research", "query", "--dataset", "dragons", "--fields", "nope", "--json"],
            ["research", "query", "--dataset", "dragons", "--code", "600001' OR 1=1",
             "--json"],
            ["research", "query", "--dataset", "dragons", "--run-id", "1", "--json"],
        ):
            result = runner().run_cli(args, sandbox=box)
            assert result.returncode == 2, (args, result.stdout, result.stderr)
            error = _json(result)
            assert error["ok"] is False
            assert error["error"]["code"] in ("invalid_request", "invalid_cursor")


def test_research_scan_quality_empty_is_explainable():
    with sandbox("research-quality-empty") as box:
        result = runner().run_cli(
            ["research", "scan-quality", "--from", "20261101", "--to", "20261106",
             "--json"], sandbox=box)
        assert result.returncode == 0, result.stderr
        payload = _json(result)
        assert payload["ok"] is True
        assert payload["meta"]["empty_reason"] in (
            "database_missing", "base_read_failed", None)
        assert not any((box.data_dir / name).exists()
                       for name in ("dragon.db", "assets.db"))


def test_research_read_only_commands_degrade_without_writing():
    with sandbox("research-readonly-empty") as box:
        commands = (
            ["research", "account", "--run-id", "1", "--json"],
            ["research", "account", "--run-id", "1", "--view", "trades", "--json"],
            ["research", "signals", "--json"],
            ["research", "snapshots", "--kind", "prep", "--json"],
            ["research", "snapshots", "--kind", "auction", "--json"],
            ["research", "strategy", "--kind", "scan", "--json"],
            ["research", "strategy", "--kind", "account", "--run-id", "1", "--json"],
            ["research", "experiments", "list", "--run-id", "1", "--json"],
            ["research", "experiments", "show", "--experiment-id", "1", "--json"],
        )
        for args in commands:
            result = runner().run_cli(args, sandbox=box)
            assert result.returncode == 0, (args, result.stderr)
            payload = _json(result)
            assert payload["ok"] is True, args
            assert payload["meta"]["data_quality"] in ("ok", "degraded", "empty"), args
            if args[1] not in ("strategy",):
                assert payload["meta"].get("empty_reason"), args
        assert not (box.data_dir / "locks").exists()


def test_research_experiments_run_requires_real_run_and_confirm():
    with sandbox("research-experiments-gate") as box:
        no_confirm = runner().run_cli(
            ["research", "experiments", "run", "--run-id", "1", "--json"],
            sandbox=box)
        assert no_confirm.returncode == 2
        assert _json(no_confirm)["error"]["code"] == "confirmation_required"

        unknown_rule = runner().run_cli(
            ["research", "experiments", "run", "--run-id", "1", "--confirm",
             "--sell-rule", "no_such_rule", "--json"], sandbox=box)
        assert unknown_rule.returncode == 2
        assert "未知 sell-rule" in _json(unknown_rule)["error"]["message"]
        assert not (box.data_dir / "locks").exists()
