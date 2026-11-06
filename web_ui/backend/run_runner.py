"""scan / prep / auction 共享单飞作业：显式 argv + 复用 ``BsRunner`` 状态机。

三个命令共用**一个**作业位（任一在跑时其余返回忙），避免并发抢数据源与 SQLite
写；与既有 BS / 回测作业互不影响。Web 层不 import 命令实现、不建连接、不写库：
所有写入都发生在 ``python -m dragon_quant <command>`` 子进程里。

锁 / 线程 / 输出尾部截断 / ``terminate`` 幂等语义全部复用 ``BsRunner``，
本模块只补「这是哪个 job 的参数」这一层元数据。
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Mapping, Sequence

from .bs_runner import BsJobBusy, BsRunner, JobSnapshot

__all__ = ["RunJobBusy", "RunJobRunner"]

#: 复用既有作业忙异常（路由按命令文案转 409）。
RunJobBusy = BsJobBusy


class RunJobRunner:
    """共享单飞作业；``popener`` 可注入（测试）。"""

    def __init__(
        self,
        *,
        popener: Callable[..., Any] | None = None,
        output_lines: int = 60,
        output_chars: int = 6000,
    ) -> None:
        self._job = BsRunner(
            popener=popener,
            output_lines=output_lines,
            output_chars=output_chars,
        )
        self._lock = threading.Lock()
        self._params: dict[str, Any] | None = None
        self._started_at: str | None = None

    def start(
        self,
        *,
        job: str,
        label: str,
        argv: Sequence[str],
        params: Mapping[str, Any],
    ) -> JobSnapshot:
        """启动子进程；已有作业在跑时抛 ``RunJobBusy``（不覆盖元数据）。"""
        snapshot = self._job.start_command(
            argv, job=job, label=label, trigger="manual")
        with self._lock:
            self._params = dict(params)
            self._started_at = snapshot.started_at
        return snapshot

    def status(self) -> dict[str, Any]:
        snapshot = self._job.status()
        last = snapshot.get("last_run")
        with self._lock:
            params = (
                dict(self._params)
                if last is not None and last.get("started_at") == self._started_at
                else None
            )
        return {
            "state": snapshot["state"],
            "job": (last or {}).get("job"),
            "label": (last or {}).get("label"),
            "params": params,
            "last_run": last,
        }

    def terminate(self, timeout: float = 3.0) -> None:
        self._job.terminate(timeout=timeout)
