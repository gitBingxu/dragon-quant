"""BS 派生数据生成作业：命令委派 + 单飞状态机（标准库 only）。

Web 层**不 import** ``dragon_quant.bs_eval``、不建连接、不写库：所有写入都发生在
``python -m dragon_quant bs-eval`` 子进程里。本模块只 import 标准库；构造
``BsRunner`` 不创建目录、不连库、不 spawn（惰性）。

状态机：``idle → running → succeeded | failed``；``running`` 时重复启动抛
``BsJobBusy``（HTTP 层转 409）。同一时刻最多一个子进程（单飞），由
``threading.Lock`` 保证。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Sequence

JobState = Literal["idle", "running", "succeeded", "failed"]
JobMode = Literal["incremental", "force"]
JobTrigger = Literal["auto", "manual"]

_TZ = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def default_command(mode: JobMode) -> list[str]:
    """``python -m dragon_quant bs-eval [--force]``（固定 v2）。"""
    argv = [sys.executable, "-m", "dragon_quant", "bs-eval", "--source", "v2"]
    if mode == "force":
        argv.append("--force")
    return argv


@dataclass(frozen=True)
class JobSnapshot:
    """最近一次任务快照；``running`` 期间结果字段为 ``None``。

    ``job`` / ``label`` 只有 ``start_command`` 路径会填（scan / prep / auction
    共享单飞作业），BS / 回测作业保持 ``None``，既有响应对应键值不变。
    """

    mode: str
    trigger: JobTrigger
    started_at: str
    finished_at: str | None = None
    exit_code: int | None = None
    output_tail: str | None = None
    job: str | None = None
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "trigger": self.trigger,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "output_tail": self.output_tail,
            "job": self.job,
            "label": self.label,
        }


class BsJobBusy(RuntimeError):
    """已有任务在运行（路由转 409）。"""


class BsRunner:
    """单飞 BS 生成任务；``command_factory`` / ``popener`` 可注入（测试）。"""

    def __init__(
        self,
        *,
        command_factory: Callable[[JobMode], Sequence[str]] | None = None,
        popener: Callable[..., Any] | None = None,
        output_lines: int = 50,
        output_chars: int = 4000,
    ) -> None:
        self._command_factory = command_factory or default_command
        self._popener = popener or subprocess.Popen
        self._output_lines = output_lines
        self._output_chars = output_chars
        self._lock = threading.Lock()
        self._state: JobState = "idle"
        self._last_run: JobSnapshot | None = None
        self._proc: Any | None = None

    # ─── public API ───────────────────────────────────────────────────────

    def start_incremental(self, trigger: JobTrigger) -> JobSnapshot:
        return self._start("incremental", trigger)

    def start_force(self, trigger: JobTrigger) -> JobSnapshot:
        return self._start("force", trigger)

    def start_command(self, argv: Sequence[str], *, job: str, label: str,
                      trigger: JobTrigger = "manual") -> JobSnapshot:
        """以显式 argv 启动任务（scan / prep / auction 共享单飞作业）。

        argv 由注册表按白名单字段组装，**以列表传给 Popen**，绝不拼 shell。
        """
        return self._start(job, trigger, argv=list(argv), job=job, label=label)

    def status(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self._last_run
            return {
                "state": self._state,
                "last_run": None if snapshot is None else snapshot.to_dict(),
            }

    def terminate(self, timeout: float = 3.0) -> None:
        """幂等终止当前子进程：``terminate`` → ``wait`` → 超时 ``kill``。"""
        with self._lock:
            proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001 - 关闭服务不得被子进程异常阻塞
            pass
        try:
            proc.wait(timeout=timeout)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    # ─── internals ────────────────────────────────────────────────────────

    def _spawn_env(self) -> dict[str, str]:
        # 显式 UTF-8：Windows 子进程输出不再按 locale 解码成乱码。
        return {**os.environ, "PYTHONIOENCODING": "utf-8"}

    def _tail(self, text: str | bytes | None) -> str:
        if text is None:
            return ""
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        stripped = text.strip()
        if not stripped:
            return ""
        last_lines = "\n".join(stripped.splitlines()[-self._output_lines:])
        return last_lines[-self._output_chars:]

    def _start(self, mode: str, trigger: JobTrigger, *,
               argv: Sequence[str] | None = None,
               job: str | None = None,
               label: str | None = None) -> JobSnapshot:
        command = list(argv) if argv is not None else list(self._command_factory(mode))
        with self._lock:
            if self._state == "running":
                raise BsJobBusy("BS 数据生成已在进行中")
            snapshot = JobSnapshot(mode=mode, trigger=trigger, started_at=_now(),
                                   job=job, label=label)
            self._last_run = snapshot
            try:
                proc = self._popener(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=self._spawn_env(),
                )
            except Exception as exc:
                self._state = "failed"
                self._last_run = replace(
                    snapshot,
                    finished_at=_now(),
                    output_tail=f"spawn_failed: {type(exc).__name__}: {exc}",
                )
                raise
            self._proc = proc
            self._state = "running"
            threading.Thread(
                target=self._watch,
                args=(proc, snapshot),
                name="bs-runner-watch",
                daemon=True,
            ).start()
        return snapshot

    def _watch(self, proc: Any, snapshot: JobSnapshot) -> None:
        try:
            output, _ = proc.communicate()
        except Exception as exc:  # noqa: BLE001 - communicate 失败也归为 failed
            output = f"{type(exc).__name__}: {exc}"
        exit_code = getattr(proc, "returncode", None)
        with self._lock:
            self._proc = None
            self._state = "succeeded" if exit_code == 0 else "failed"
            self._last_run = replace(
                snapshot,
                finished_at=_now(),
                exit_code=exit_code,
                output_tail=self._tail(output),
            )


__all__ = [
    "BsJobBusy",
    "BsRunner",
    "JobMode",
    "JobSnapshot",
    "JobState",
    "JobTrigger",
    "default_command",
]
