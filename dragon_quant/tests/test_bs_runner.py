"""``BsRunner``：命令工厂 / 状态机 / 单飞 / 输出截断 / 终止（全部离线 fake）。"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from web_ui.backend.bs_runner import (
    BsJobBusy,
    BsRunner,
    default_command,
)


class FakeProc:
    """最小 ``Popen`` 替身；``gate`` 未释放时 ``communicate`` 阻塞（模拟长任务）。"""

    def __init__(self, *, output: str = "", returncode: int = 0,
                 gate: threading.Event | None = None) -> None:
        self.output = output
        self.final_code = returncode
        self.returncode: int | None = None
        self.gate = gate
        self.terminated = 0
        self.killed = 0

    def communicate(self):
        if self.gate is not None:
            self.gate.wait(timeout=5)
        if self.returncode is None:
            self.returncode = self.final_code
        return self.output, None

    def wait(self, timeout: float | None = None):
        if self.returncode is None and self.gate is not None:
            self.gate.wait(timeout)
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1
        if self.returncode is None:
            self.returncode = -15
        if self.gate is not None:
            self.gate.set()

    def kill(self) -> None:
        self.killed += 1
        if self.returncode is None:
            self.returncode = -9
        if self.gate is not None:
            self.gate.set()


def _wait_done(runner: BsRunner, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = runner.status()
        if status["state"] != "running":
            return status
        time.sleep(0.01)
    raise AssertionError("runner 未在超时内完成")


def test_construction_has_no_io_and_status_is_idle():
    with patch("subprocess.Popen") as popen:
        runner = BsRunner()
    popen.assert_not_called()
    assert runner.status() == {"state": "idle", "last_run": None}


def test_default_command_maps_modes_to_argv():
    incremental = default_command("incremental")
    assert incremental[:5] == [
        sys.executable, "-m", "dragon_quant", "bs-eval", "--source"]
    assert incremental[-1] == "v2"
    assert "--force" not in incremental
    assert default_command("force")[-1] == "--force"


def test_success_path_records_exit_zero_and_output():
    seen: dict = {}
    output = "BS 回放完成：run #12\n扫描 120 · 评估 37\n"

    def popener(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return FakeProc(output=output, returncode=0)

    runner = BsRunner(popener=popener)
    snapshot = runner.start_incremental(trigger="auto")
    assert snapshot.mode == "incremental"
    assert snapshot.trigger == "auto"
    assert snapshot.finished_at is None
    assert snapshot.exit_code is None
    assert snapshot.output_tail is None

    status = _wait_done(runner)
    assert status["state"] == "succeeded"
    run = status["last_run"]
    assert run["mode"] == "incremental"
    assert run["trigger"] == "auto"
    assert run["exit_code"] == 0
    assert run["finished_at"]
    assert "BS 回放完成" in run["output_tail"]

    assert seen["argv"] == default_command("incremental")
    assert seen["kwargs"]["stderr"] is subprocess.STDOUT
    assert seen["kwargs"]["stdin"] is subprocess.DEVNULL
    assert seen["kwargs"]["encoding"] == "utf-8"
    assert seen["kwargs"]["env"]["PYTHONIOENCODING"] == "utf-8"


def test_command_factory_receives_mode_for_incremental_and_force():
    modes: list[str] = []

    def factory(mode):
        modes.append(mode)
        return ["fake-command", mode]

    runner = BsRunner(command_factory=factory,
                      popener=lambda argv, **kwargs: FakeProc(returncode=0))
    runner.start_incremental("auto")
    _wait_done(runner)
    runner.start_force("manual")
    status = _wait_done(runner)
    assert modes == ["incremental", "force"]
    assert status["last_run"]["mode"] == "force"
    assert status["last_run"]["trigger"] == "manual"


def test_nonzero_exit_marks_failed_with_exit_code_and_output():
    runner = BsRunner(popener=lambda argv, **kwargs: FakeProc(
        output="❌ bs-eval 失败：db gone\n", returncode=1))
    runner.start_force("manual")
    status = _wait_done(runner)
    assert status["state"] == "failed"
    run = status["last_run"]
    assert run["exit_code"] == 1
    assert "db gone" in run["output_tail"]
    assert run["finished_at"]


def test_spawn_exception_marks_failed_and_reraises():
    def boom(argv, **kwargs):
        raise OSError("no interpreter")

    runner = BsRunner(popener=boom)
    with pytest.raises(OSError):
        runner.start_incremental("auto")
    status = runner.status()
    assert status["state"] == "failed"
    run = status["last_run"]
    assert run["exit_code"] is None
    assert run["output_tail"] == "spawn_failed: OSError: no interpreter"
    assert run["finished_at"]


def test_single_flight_rejects_second_start_until_finished():
    gate = threading.Event()
    runner = BsRunner(popener=lambda argv, **kwargs: FakeProc(gate=gate))
    runner.start_incremental("auto")

    with pytest.raises(BsJobBusy):
        runner.start_force("manual")

    status = runner.status()
    assert status["state"] == "running"
    assert status["last_run"]["mode"] == "incremental"
    assert status["last_run"]["finished_at"] is None

    gate.set()
    assert _wait_done(runner)["state"] == "succeeded"

    runner.start_force("manual")
    assert _wait_done(runner)["state"] == "succeeded"


def test_output_tail_truncates_lines_and_chars_with_chinese_utf8():
    lines = [f"第{i}行：" + "龙" * 50 for i in range(1, 81)]
    runner = BsRunner(
        popener=lambda argv, **kwargs: FakeProc(
            output="\n".join(lines), returncode=1),
        output_lines=10,
        output_chars=120,
    )
    runner.start_incremental("auto")
    tail = _wait_done(runner)["last_run"]["output_tail"]
    assert len(tail) <= 120
    assert "第80行" in tail
    assert "第1行" not in tail
    assert tail.count("\n") <= 9


def test_terminate_is_idempotent_and_leaves_failed_state():
    gate = threading.Event()
    proc = FakeProc(gate=gate)
    runner = BsRunner(popener=lambda argv, **kwargs: proc)
    runner.start_incremental("auto")

    runner.terminate(timeout=0.2)
    status = _wait_done(runner)
    assert status["state"] == "failed"
    assert proc.terminated == 1

    runner.terminate()
    assert proc.terminated == 1
