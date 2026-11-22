"""采集会话稳定入口：一次取数 / 有界会话 / 计划解析（兼容重导出）。"""

from __future__ import annotations

from .collect import collect_once
from .session_run import run_session
from .session_setup import new_worker_id, resolve_plan

__all__ = ["collect_once", "run_session", "resolve_plan", "new_worker_id"]
