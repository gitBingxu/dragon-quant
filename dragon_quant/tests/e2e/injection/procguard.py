"""Deny child-process creation inside E2E business processes.

Python's socket guard cannot see network performed by non-Python children
(Node/Chromium).  Until M0's incident, a real Playwright fallback could download
a browser through ``python -m playwright install``; this guard makes any such
escape fail before the OS process exists.

The policy is default-deny with an explicit empty allow-list.  Every known
process-creation primitive is covered, including the lowest-level CPython
``_posixsubprocess.fork_exec`` used by ``subprocess.Popen``.
"""

from __future__ import annotations

import os
import subprocess

INSTALLED = False
ALLOWED_PREFIXES: tuple[tuple[str, ...], ...] = ()


class SubprocessBlocked(RuntimeError):
    """Raised when E2E child code attempts to create an OS subprocess."""


def _blocked(api: str):
    def guard(*args: object, **kwargs: object):
        raise SubprocessBlocked(f"E2E 禁止创建子进程: {api}")

    guard.E2E_BLOCKED = True  # type: ignore[attr-defined]
    guard.__name__ = f"blocked_{api.replace('.', '_')}"
    return guard


def _patch_subprocess() -> None:
    for name in ("Popen", "run", "call", "check_call", "check_output"):
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
        _posixsubprocess.fork_exec = _blocked("_posixsubprocess.fork_exec")  # type: ignore[assignment]
    if hasattr(subprocess, "_fork_exec"):
        subprocess._fork_exec = _blocked("subprocess._fork_exec")  # type: ignore[attr-defined]


def install() -> None:
    global INSTALLED
    if INSTALLED:
        return
    _patch_subprocess()
    _patch_os()
    _patch_lowest_level()
    _patch_asyncio()
    INSTALLED = True
