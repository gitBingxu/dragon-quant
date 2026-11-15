"""Repository-scoped mutual exclusion for release runs.

A single POSIX advisory lock (``flock``) in the git *common* dir is used
regardless of ``--status-dir`` and for every linked worktree of the same
repository; the kernel releases it when the process dies, so there is no
stale-lock guessing and no unsafe "remove and retry" path.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from release.common import ReleaseError


class RepoLock:
    def __init__(self, common_dir: Path):
        self.path = Path(common_dir) / "dq-release.lock"
        self._handle = None

    def acquire(self) -> "RepoLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(handle)
            raise ReleaseError(
                "lock",
                f"另一个发布进程正持有仓库锁 {self.path}；并发发布被拒绝",
            ) from exc
        os.ftruncate(handle, 0)
        os.write(handle, f"pid={os.getpid()}\n".encode("utf-8"))
        self._handle = handle
        return self

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle, fcntl.LOCK_UN)
        finally:
            os.close(self._handle)
            self._handle = None

    def __enter__(self) -> "RepoLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()
