"""Workspace / strategy 命令的离线 E2E（WS1–WS5）。

每个用例在 M0 沙箱中运行真实 ``python -m dragon_quant`` 子进程；
``DQ_DATA_DIR`` 隔离，workspace 目录放在沙箱 root 下，不触网、不打开真实库。
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox

NOW = "2026-11-11T10:00:00+08:00"


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
    return runner().run_cli(args, sandbox=box, timeout=timeout,
                            env_override={"DQ_E2E_CLOCK_MODE": "runtime"})


def _json(stdout: str) -> dict:
    return json.loads(stdout)


def _workspace(box: EnvSandbox, name: str = "ws"):
    return str(box.root / name)


def test_ws1_init_list_validate_and_use():
    with sandbox("ws1") as box:
        workspace = _workspace(box)
        init = run_cli(box, ["workspace", "init", workspace, "--json"])
        assert init.returncode == 0, init.stderr
        payload = _json(init.stdout)
        assert payload["ok"] is True
        assert payload["current"] == "builtin:dragon_pullback_daily"
        assert payload["created_files"]

        listing = run_cli(box, ["strategy", "list", "--workspace", workspace, "--json"])
        assert listing.returncode == 0, listing.stderr
        data = _json(listing.stdout)
        ids = [item["id"] for item in data["strategies"]]
        assert ids == ["builtin:dragon_pullback_daily",
                       "user:example_momentum", "user:example_meanrev"]
        assert all(item["valid"] for item in data["strategies"])

        validate = run_cli(box, ["strategy", "validate", "user:example_momentum",
                                 "--workspace", workspace, "--json"])
        assert validate.returncode == 0, validate.stderr
        assert _json(validate.stdout)["detail"]["parameters"]["min_open_gap_pct"] == 1.0

        use = run_cli(box, ["strategy", "use", "user:example_meanrev",
                            "--workspace", workspace, "--json"])
        assert use.returncode == 0, use.stderr
        used = _json(use.stdout)
        assert used["current"] == "user:example_meanrev"
        assert used["revision"] >= 2

        current = run_cli(box, ["strategy", "current", "--json"])
        assert current.returncode == 0, current.stderr
        assert _json(current.stdout)["strategy_id"] == "user:example_meanrev"


def test_ws2_list_never_imports_user_code():
    with sandbox("ws2") as box:
        workspace = _workspace(box)
        assert run_cli(box, ["workspace", "init", workspace, "--json"]).returncode == 0
        strategy_py = f"{workspace}/strategies/example_momentum/strategy.py"
        # 在模块顶层抛错：list 若导入用户代码就会失败。
        with open(strategy_py, "w", encoding="utf-8") as handle:
            handle.write("raise RuntimeError('imported!')\n")
        listing = run_cli(box, ["strategy", "list", "--workspace", workspace, "--json"])
        assert listing.returncode == 0, listing.stderr
        assert _json(listing.stdout)["strategies"][1]["valid"] is True
        # validate 是显式执行路径，必须失败并带策略 ID。
        validate = run_cli(box, ["strategy", "validate", "user:example_momentum",
                                 "--workspace", workspace, "--json"])
        assert validate.returncode == 1
        assert "imported" in validate.stdout or "失败" in validate.stdout


def test_ws3_check_reports_invalid_descriptor():
    with sandbox("ws3") as box:
        workspace = _workspace(box)
        assert run_cli(box, ["workspace", "init", workspace, "--json"]).returncode == 0
        descriptor = f"{workspace}/strategies/example_meanrev/strategy.json"
        data = json.loads(open(descriptor, encoding="utf-8").read())
        data["id"] = "user:wrong_id"
        open(descriptor, "w", encoding="utf-8").write(json.dumps(data))
        checked = run_cli(box, ["workspace", "check", "--workspace", workspace, "--json"])
        assert checked.returncode == 0, checked.stderr
        payload = _json(checked.stdout)
        assert payload["ok"] is False
        assert any("user:wrong_id" in err or "id 必须" in err for err in payload["errors"])


def test_ws4_list_without_workspace_shows_builtin_only():
    with sandbox("ws4") as box:
        listing = run_cli(box, ["strategy", "list", "--json"])
        assert listing.returncode == 0, listing.stderr
        data = _json(listing.stdout)
        assert data["workspace"] is None
        assert [item["id"] for item in data["strategies"]] == ["builtin:dragon_pullback_daily"]


USER_STRATEGY = '''
import os
from pathlib import Path

Path({marker!r}).write_text(str(os.getpid()))
from dragon_quant.strategy_sdk import StrategyIntent


class AlwaysBuy:
    def __init__(self, params):
        self.params = dict(params or {{}})

    def evaluate_entry(self, ctx):
        if ctx.phase != "open" or not ctx.row:
            return None
        return StrategyIntent(action="buy", reason_code="cli_threshold_buy",
                              priority=100, position_pct=50.0,
                              evidence={{"pid": os.getpid()}})

    def evaluate_exit(self, ctx):
        if ctx.phase != "late" or not ctx.position:
            return None
        return StrategyIntent(action="sell", reason_code="cli_same_day_exit",
                              priority=100, exit_fraction=1.0)


def create_strategy(params):
    return AlwaysBuy(params)
'''


def test_ws6_cli_review_account_runs_user_strategy_in_worker():
    """D3：真实 CLI 子进程里，用户策略在 worker 内执行（PID 与 CLI 进程不同）。"""
    from dragon_quant.tests.e2e.test_review_account import (
        SCENARIO, account_dragon, seed_account,
    )

    with sandbox("ws6") as box:
        seed_account(box, dragons=[account_dragon()])
        workspace = _workspace(box)
        assert run_cli(box, ["workspace", "init", workspace, "--json"]).returncode == 0
        strategy_pid = box.root / "strategy.pid"
        cli_pid = box.root / "cli.pid"
        with open(f"{workspace}/strategies/example_momentum/strategy.py", "w",
                  encoding="utf-8") as handle:
            handle.write(USER_STRATEGY.format(marker=str(strategy_pid)))

        # 用 runpy 跑真实 CLI 入口：同一进程先记录 CLI PID，再执行 ``-m dragon_quant``
        # 的等价入口，从而能与 worker 内用户代码 PID 直接比较。
        argv = ["review-account", "--from", "20260904", "--to", "20260908",
                "--workspace", workspace, "--strategy", "user:example_momentum",
                "--json"]
        code = (
            "import os, runpy, sys\n"
            f"open({str(cli_pid)!r}, 'w').write(str(os.getpid()))\n"
            f"sys.argv = ['dragon_quant', *{argv!r}]\n"
            "runpy.run_module('dragon_quant', run_name='__main__')\n"
        )
        result = runner().run_python(
            code, sandbox=box, timeout=180,
            env_override={"DQ_E2E_SCENARIO": str(SCENARIO),
                          "DQ_E2E_CLOCK_MODE": "runtime",
                          # procguard 只放行唯一的用户策略 worker 子进程；
                          # worker 继承同一沙箱注入，仍受 netguard/procguard 约束。
                          "DQ_E2E_ALLOW_WORKER": "1"})
        assert result.returncode == 0, (result.stdout, result.stderr)

        payload = _json(result.stdout)
        assert payload["ok"] is True
        assert payload["trade_count"] > 0, payload
        assert payload["strategy_id"] == "user:example_momentum"
        assert payload["strategy_ref"]["strategy_id"] == "user:example_momentum"
        assert payload["strategy_ref"]["snapshot_id"]

        assert cli_pid.exists() and strategy_pid.exists()
        assert int(strategy_pid.read_text()) != int(cli_pid.read_text())

        # 放行名单只覆盖唯一 worker argv：其它子进程仍 fail closed。
        probe = runner().run_python(
            "import subprocess, sys\n"
            "try:\n"
            "    subprocess.Popen([sys.executable, '-c', 'print(1)'])\n"
            "except Exception as exc:\n"
            "    print(type(exc).__name__)\n"
            "else:\n"
            "    raise SystemExit('worker allow-list leaked')\n",
            sandbox=box, timeout=60,
            env_override={"DQ_E2E_SCENARIO": str(SCENARIO),
                          "DQ_E2E_CLOCK_MODE": "runtime",
                          "DQ_E2E_ALLOW_WORKER": "1"})
        assert probe.returncode == 0, probe.stderr
        assert "SubprocessBlocked" in probe.stdout


def test_ws5_unknown_strategy_use_fails_without_partial_write():
    with sandbox("ws5") as box:
        workspace = _workspace(box)
        assert run_cli(box, ["workspace", "init", workspace, "--json"]).returncode == 0
        before = json.loads(open(f"{workspace}/workspace.json", encoding="utf-8").read())
        use = run_cli(box, ["strategy", "use", "user:does_not_exist",
                            "--workspace", workspace, "--json"])
        assert use.returncode == 1
        assert "未知策略" in use.stdout
        after = json.loads(open(f"{workspace}/workspace.json", encoding="utf-8").read())
        assert after == before  # 失败不留下半写
