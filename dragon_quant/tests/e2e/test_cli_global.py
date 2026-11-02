"""M1A global CLI surface: G1-G3, G6-G8.

These cases never reach a provider method: help/version/parse errors exit before
dispatch, and group-without-action handlers are local.  G4 (default scan) and
G5 (hidden alias) need the M1B scan fixture and are deliberately absent here.
"""

from __future__ import annotations

import json

import pytest

from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox
from dragon_quant.tests.e2e.harness.sentinel import RealDataSentinel

NOW = "2026-09-11T15:30:00+08:00"
TOP_LEVEL_COMMANDS = (
    "scan",
    "logs",
    "data",
    "blacklist",
    "review",
    "review-account",
    "dragons",
    "prep",
    "auction",
    "premarket",
    "assets",
    "factor",
    "buy",
    "sell",
    "vpa",
    "storage",
)


def runner():
    from dragon_quant.tests.e2e.harness import runner as module

    return module


@pytest.fixture
def sandbox():
    box = EnvSandbox.for_case("cli-global", now=NOW)
    try:
        yield box
    finally:
        box.cleanup()


def test_g1_help_lists_every_top_level_command_and_hides_scan_v2(sandbox):
    result = runner().run_cli(["-h"], sandbox=sandbox)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "Usage:" in result.stdout
    assert "Commands:" in result.stdout
    for command in TOP_LEVEL_COMMANDS:
        assert f"    {command}" in result.stdout, f"help 缺少命令 {command}"
    assert "scan_v2" not in result.stdout, "隐藏别名不得出现在帮助中"


def test_g2_version_matches_package_version(sandbox):
    long_flag = runner().run_cli(["--version"], sandbox=sandbox)
    short_flag = runner().run_cli(["-v"], sandbox=sandbox)
    for result in (long_flag, short_flag):
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "dragon-quant 0.8.0"


def test_g3_unknown_command_exits_two_with_usage(sandbox):
    result = runner().run_cli(["foo"], sandbox=sandbox)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "usage:" in result.stderr
    assert "invalid choice" in result.stderr


def test_g6_invalid_source_choice_exits_two(sandbox):
    result = runner().run_cli(["logs", "--source", "v3"], sandbox=sandbox)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "--source" in result.stderr
    assert "invalid choice" in result.stderr


def test_g7_help_has_no_provider_calls_and_keeps_real_data_untouched(sandbox):
    sentinel = RealDataSentinel()
    before = sentinel.snapshot()

    result = runner().run_cli(["-h"], sandbox=sandbox)
    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout

    sentinel.assert_unchanged(before, sentinel.snapshot())
    # No provider is constructed on the help path, so the call log stays absent.
    assert not sandbox.call_log.exists()
    # B1: help is side-effect free — no data directory, database, cache or
    # report is materialised, and every storage path stays under the sandbox.
    for name in ("dragon.db", "assets.db", "prep.db"):
        database = sandbox.data_dir / name
        assert sandbox.root in database.resolve().parents
        assert not database.exists(), f"-h 不得创建 {name}"
    for name in ("cookies", "cache", "logs", "results", "shared"):
        assert not (sandbox.data_dir / name).exists(), f"-h 不得创建 {name}/"


def test_g8_group_without_action_behavior_is_pinned(sandbox):
    for command in ("logs", "data", "storage"):
        result = runner().run_cli([command], sandbox=sandbox)
        assert result.returncode == 0, (command, result.stderr)
        assert result.stdout == "", (command, result.stdout)
        assert result.stderr == "", (command, result.stderr)

    blacklist = runner().run_cli(["blacklist"], sandbox=sandbox)
    assert blacklist.returncode == 0, blacklist.stderr
    assert "黑名单为空" in blacklist.stdout

    assets = runner().run_cli(["assets"], sandbox=sandbox)
    assert assets.returncode == 2
    payload = json.loads(assets.stdout)
    assert payload["ok"] is False
    assert payload["command"] == "assets"
    assert payload["failed"][0]["reason"] == "未知 assets 子命令"

    factor = runner().run_cli(["factor"], sandbox=sandbox)
    assert factor.returncode == 2
    assert factor.stdout == ""
    assert "未知 factor 子命令" in factor.stderr

    factor_fin = runner().run_cli(["factor", "fin"], sandbox=sandbox)
    assert factor_fin.returncode == 2
    assert factor_fin.stdout == ""
    assert "未知 factor fin 子命令" in factor_fin.stderr
