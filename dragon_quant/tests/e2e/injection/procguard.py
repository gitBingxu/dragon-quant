"""Deny child-process creation inside E2E business processes.

Python's socket guard cannot see network performed by non-Python children
(Node/Chromium).  Until M0's incident, a real Playwright fallback could download
a browser through ``python -m playwright install``; this guard makes any such
escape fail before the OS process exists.

The policy is default-deny.  Every known process-creation primitive is covered,
including the lowest-level CPython ``_posixsubprocess.fork_exec`` used by
``subprocess.Popen``.

唯一例外（显式开关）：``DQ_E2E_ALLOW_WORKER=1`` 时放行**唯一 argv**
``[sys.executable, "-m", "dragon_quant.workspace.worker"]``——用户策略 worker 是
Python 子进程，会经 ``PYTHONPATH`` 继承同一沙箱与 netguard/procguard，因此仍
无法触网或再派生非 Python 子进程。其它任何命令依旧 fail closed。
"""

from __future__ import annotations

import os
import subprocess
import sys

INSTALLED = False
ALLOWED_PREFIXES: tuple[tuple[str, ...], ...] = ()
_WORKER_MODULE = "dragon_quant.workspace.worker"


class SubprocessBlocked(RuntimeError):
    """Raised when E2E child code attempts to create an OS subprocess."""


def _worker_allowed(args: object) -> bool:
    """仅放行 ``python -m dragon_quant.workspace.worker``（需显式开关）。"""
    if os.environ.get("DQ_E2E_ALLOW_WORKER") != "1":
        return False
    try:
        command = list(args)  # type: ignore[arg-type]
    except TypeError:
        return False
    return (len(command) == 3
            and str(command[0]) == sys.executable
            and str(command[1]) == "-m"
            and str(command[2]) == _WORKER_MODULE)


def _blocked(api: str):
    def guard(*args: object, **kwargs: object):
        raise SubprocessBlocked(f"E2E 禁止创建子进程: {api}")

    guard.E2E_BLOCKED = True  # type: ignore[attr-defined]
    guard.__name__ = f"blocked_{api.replace('.', '_')}"
    return guard


def _patch_subprocess() -> None:
    original_popen = subprocess.Popen

    def guarded_popen(*args: object, **kwargs: object):
        command = args[0] if args else kwargs.get("args")
        if _worker_allowed(command):
            return original_popen(*args, **kwargs)  # type: ignore[misc]
        raise SubprocessBlocked("E2E 禁止创建子进程: subprocess.Popen")

    guarded_popen.E2E_BLOCKED = True  # type: ignore[attr-defined]
    guarded_popen.__name__ = "guarded_subprocess_Popen"
    subprocess.Popen = guarded_popen  # type: ignore[assignment]
    for name in ("run", "call", "check_call", "check_output"):
        if hasattr(subprocess, name):
            setattr(subprocess, name, _blocked(f"subprocess.{name}"))


def _patch_os() -> None:
    names = (
        "system",
        "popen",
        "fork",
        "forkpty",
        "posix_spawn",
        "posix_spawnp",
        "spawnv",
        "spawnve",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "startfile",
    )
    for name in names:
        if hasattr(os, name):
            setattr(os, name, _blocked(f"os.{name}"))


def _patch_asyncio() -> None:
    try:
        import asyncio
    except ImportError:  # pragma: no cover - stdlib always present
        return
    blocked = _blocked("asyncio.create_subprocess")
    asyncio.create_subprocess_exec = blocked  # type: ignore[assignment]
    asyncio.create_subprocess_shell = blocked  # type: ignore[assignment]
    try:
        import asyncio.subprocess as async_subprocess
    except ImportError:  # pragma: no cover
        return
    async_subprocess.create_subprocess_exec = blocked  # type: ignore[assignment]
    async_subprocess.create_subprocess_shell = blocked  # type: ignore[assignment]


def _patch_lowest_level() -> None:
    try:
        import _posixsubprocess
    except ImportError:  # pragma: no cover - Windows
        return
    if hasattr(_posixsubprocess, "fork_exec"):
        original = _posixsubprocess.fork_exec

        def guarded_fork_exec(args, *rest, **kwargs):
            if _worker_allowed(args):
                return original(args, *rest, **kwargs)
            raise SubprocessBlocked(
                "E2E 禁止创建子进程: _posixsubprocess.fork_exec")

        _posixsubprocess.fork_exec = guarded_fork_exec  # type: ignore[assignment]
    if hasattr(subprocess, "_fork_exec"):
        original_subprocess = subprocess._fork_exec

        def guarded_subprocess_fork_exec(args, *rest, **kwargs):
            if _worker_allowed(args):
                return original_subprocess(args, *rest, **kwargs)
            raise SubprocessBlocked("E2E 禁止创建子进程: subprocess._fork_exec")

        subprocess._fork_exec = guarded_subprocess_fork_exec  # type: ignore[attr-defined]


def install() -> None:
    global INSTALLED
    if INSTALLED:
        return
    _patch_subprocess()
    _patch_os()
    _patch_lowest_level()
    _patch_asyncio()
    INSTALLED = True
