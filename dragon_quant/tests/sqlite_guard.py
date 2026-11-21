"""测试进程的 SQLite 真实库 open 阻断（纵深防御，非生产安全框架）。

在 pytest 加载应用模块前安装 ``sqlite3.connect`` audit hook：任何线程/子进程
尝试打开**真实 OS 用户**默认数据目录（忽略测试 HOME/DQ_DATA_DIR）下的 SQLite
文件都会在 open 之前被拒绝，并记录 PID/时间/目标/有限调用栈到临时证据文件。
自检覆盖主进程、线程与 Python 子进程；被拒绝的尝试不会创建任何文件。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

_STATE_LOCK = threading.Lock()
_INSTALLED = False
_PROTECTED: tuple[Path, ...] = ()
_LOG_PATH: Path | None = None


def _real_home() -> Path | None:
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    except Exception:
        home = os.environ.get("USERPROFILE")
        return Path(home).resolve() if home else None


def default_protected_dirs() -> tuple[Path, ...]:
    home = _real_home()
    if home is None:
        return ()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        return ((appdata / "dragon-quant").resolve(),)
    if sys.platform == "darwin":
        return ((home / "Library" / "Application Support" / "dragon-quant").resolve(),)
    xdg = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return ((xdg / "dragon-quant").resolve(),)


def _configured_roots() -> tuple[Path, ...]:
    defaults = default_protected_dirs()
    raw = os.environ.get("DQ_SQLITE_GUARD_PROTECTED")
    if not raw:
        return defaults
    extra = tuple(Path(item).expanduser().resolve()
                  for item in raw.split(os.pathsep) if item.strip())
    return tuple(dict.fromkeys(defaults + extra))


def _normalize(target) -> Path | None:
    if target is None:
        return None
    if isinstance(target, bytes):
        target = target.decode("utf-8", "replace")
    text = str(target)
    if text in ("", ":memory:", "file::memory:"):
        return None
    if text.startswith("file:"):
        parsed = urlsplit(text)
        path = unquote(parsed.path)
        if not path or path == ":memory:":
            return None
        target = path
    try:
        return Path(os.path.realpath(os.path.abspath(str(target))))
    except (OSError, ValueError):
        return None


def protected(target) -> bool:
    resolved = _normalize(target)
    if resolved is None:
        return False
    for root in _PROTECTED:
        if resolved == root or root in resolved.parents:
            return True
    return False


def _log_path() -> Path:
    assert _LOG_PATH is not None
    return _LOG_PATH


def _record(target, selftest: bool) -> None:
    stack: list[str] = []
    frame = sys._getframe(1)
    while frame is not None and len(stack) < 12:
        stack.append(f"{frame.f_code.co_filename}:{frame.f_lineno}")
        frame = frame.f_back
    payload = {
        "ts": time.time(),
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "target": str(target),
        "selftest": bool(selftest),
        "stack": stack,
    }
    try:
        with _log_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _deny(target, selftest: bool) -> None:
    _record(target, selftest)
    raise RuntimeError(
        "SQLite 真实数据目录访问被测试 guard 阻断（不打开、不创建）: "
        f"{target}")


def install(*, log_path=None) -> Path:
    """安装 audit hook（幂等）；必须在应用模块打开真实库之前调用。"""
    global _INSTALLED, _PROTECTED, _LOG_PATH
    with _STATE_LOCK:
        _PROTECTED = _configured_roots()
        if log_path is not None:
            _LOG_PATH = Path(log_path)
        elif _LOG_PATH is None:
            configured = os.environ.get("DQ_SQLITE_GUARD_LOG")
            _LOG_PATH = (Path(configured) if configured else
                         Path(tempfile.gettempdir()) / (
                             f"dq-sqlite-guard-{os.getpid()}.jsonl"))
        if not _INSTALLED:
            def _hook(event, args):
                if event != "sqlite3.connect" or not args:
                    return
                db = args[0]
                if protected(db):
                    _deny(db, selftest=os.environ.get(
                        "DQ_SQLITE_GUARD_SELFTEST") == "1")

            sys.addaudithook(_hook)
            _INSTALLED = True
    return _log_path()


def installed() -> bool:
    return _INSTALLED


def log_violations(log_path=None) -> list[dict]:
    path = Path(log_path) if log_path is not None else _LOG_PATH
    if path is None or not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not item.get("selftest"):
            entries.append(item)
    return entries


def verify_selftest() -> None:
    """自检：合成“受保护”目录被阻断且不创建文件；普通临时库可连接。

    不触碰真实数据目录；真实路径判定由 ``protected()`` 与子进程 override 覆盖。
    """
    import sqlite3

    global _PROTECTED
    synthetic = Path(tempfile.mkdtemp(prefix="dq-guard-selftest-")).resolve()
    with _STATE_LOCK:
        previous = _PROTECTED
        _PROTECTED = previous + (synthetic,)
    os.environ["DQ_SQLITE_GUARD_SELFTEST"] = "1"
    try:
        probe = synthetic / "blocked.db"
        try:
            sqlite3.connect(str(probe))
        except RuntimeError:
            pass
        else:
            raise RuntimeError("SQLite guard 自检失败：受保护路径未被阻断")
        if probe.exists():
            raise RuntimeError("SQLite guard 自检失败：受保护路径被创建")
        allowed = Path(tempfile.mkdtemp(prefix="dq-guard-ok-"))
        temp = allowed / "ok.db"
        conn = sqlite3.connect(str(temp))
        conn.close()
        import shutil
        shutil.rmtree(allowed, ignore_errors=True)
    finally:
        os.environ.pop("DQ_SQLITE_GUARD_SELFTEST", None)
        with _STATE_LOCK:
            _PROTECTED = previous
        import shutil
        shutil.rmtree(synthetic, ignore_errors=True)


__all__ = [
    "default_protected_dirs", "install", "installed", "log_violations",
    "protected", "verify_selftest",
]
