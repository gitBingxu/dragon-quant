"""``state.json`` 增量水位与跨平台进程锁。

设计要点：
  - 路径不在 import 期绑定：``default_paths(data_dir)`` 在调用期解析
    ``dragon_quant.storage.paths.DATA_DIR``，支持 ``--target-dir`` 与测试隔离。
  - ``state.json`` 只由 push 写入；pull 永不读写（避免隔离恢复污染推送水位）。
  - 锁是目录（``os.mkdir`` 原子创建）；Windows 上**禁止** ``os.kill(pid, 0)``
    （会终止进程），一律按年龄判定陈旧。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dragon_quant.assets_sync.errors import LockBusyError

STATE_SCHEMA = 1
LOCK_STALE_SECONDS = 6 * 3600


@dataclass(frozen=True)
class SyncPaths:
    state_path: Path | None
    lock_path: Path
    tmp_dir: Path


def default_paths(data_dir=None) -> SyncPaths:
    if data_dir is None:
        from dragon_quant.storage import paths as storage_paths
        data_dir = storage_paths.DATA_DIR
    base = Path(data_dir)
    return SyncPaths(
        state_path=base / "assets_sync" / "state.json",
        lock_path=base / "assets_sync.lock",
        tmp_dir=base / "tmp" / "assets_sync",
    )


def now_iso(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone(timedelta(hours=8)))
    return moment.isoformat(timespec="seconds")


def new_state(hostname: str | None = None) -> dict:
    return {
        "schema": STATE_SCHEMA,
        "hostname": hostname or socket.gethostname(),
        "updated_at": "",
        "last_success_trade_date": "",
        "dates": {},
    }


def load_state(path) -> dict:
    """读取 state；缺失/损坏/schema 未知 → 空骨架（state 只是缓存）。"""
    if path is None:
        return new_state()
    path = Path(path)
    if not path.exists():
        return new_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return new_state()
    if not isinstance(data, dict) or data.get("schema") != STATE_SCHEMA:
        return new_state()
    data.setdefault("dates", {})
    if not isinstance(data["dates"], dict):
        data["dates"] = {}
    return data


def save_state(path, state: dict) -> None:
    """原子写：同目录临时文件 + ``os.replace``。"""
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _pid_alive(pid) -> bool | None:
    """POSIX 探活；Windows 返回 None（禁止 os.kill）。"""
    if sys.platform == "win32":
        return None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


class ProcessLock:
    """目录锁：``<data_dir>/assets_sync.lock``。

    仅当 PID 不存在（同 host / POSIX）或目录年龄超过 ``stale_seconds``
    （跨 host 或 Windows）时才允许清理，避免误清活着的任务。
    """

    def __init__(self, path, *, hostname: str | None = None,
                 now: datetime | None = None, stale_seconds: int = LOCK_STALE_SECONDS):
        self.path = Path(path)
        self.hostname = hostname or socket.gethostname()
        self._now = now
        self.stale_seconds = stale_seconds

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in (0, 1):
            try:
                self.path.mkdir()
                break
            except FileExistsError:
                if attempt == 0 and self._is_stale():
                    shutil.rmtree(self.path, ignore_errors=True)
                    continue
                raise LockBusyError(f"进程锁被占用: {self.path}") from None
        info = {
            "pid": os.getpid(),
            "hostname": self.hostname,
            "started_at": now_iso(self._now),
        }
        (self.path / "owner.json").write_text(
            json.dumps(info, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def release(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def _owner(self) -> dict:
        try:
            data = json.loads((self.path / "owner.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _age_seconds(self, owner: dict) -> float:
        started_at = owner.get("started_at")
        try:
            started = datetime.fromisoformat(str(started_at))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            now = self._now or datetime.now(timezone.utc)
            return (now - started).total_seconds()
        except (TypeError, ValueError):
            try:
                mtime = datetime.fromtimestamp(self.path.stat().st_mtime, tz=timezone.utc)
                now = self._now or datetime.now(timezone.utc)
                return (now - mtime).total_seconds()
            except OSError:
                return float("inf")

    def _is_stale(self) -> bool:
        owner = self._owner()
        age = self._age_seconds(owner)
        if owner.get("hostname") == self.hostname:
            alive = _pid_alive(owner.get("pid"))
            if alive is True:
                return False
            if alive is False:
                return True
        return age > self.stale_seconds
