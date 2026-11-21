"""W5-S：SQLite 真实数据目录 open 前阻断（主进程/线程/子进程）与自检。"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from dragon_quant.tests import sqlite_guard


def _with_temp_protection(tmp_path, monkeypatch):
    """把 guard 指向合成受保护目录与独立日志，避免污染会话违规记录。"""
    root = (tmp_path / "protected").resolve()
    root.mkdir()
    log = tmp_path / "guard.jsonl"
    monkeypatch.setattr(sqlite_guard, "_PROTECTED",
                        sqlite_guard._PROTECTED + (root,))
    monkeypatch.setattr(sqlite_guard, "_LOG_PATH", log)
    monkeypatch.setenv("DQ_SQLITE_GUARD_SELFTEST", "1")
    return root, log


def test_guard_denies_before_open_without_creating_file(tmp_path, monkeypatch):
    root, log = _with_temp_protection(tmp_path, monkeypatch)
    target = root / "dragon.db"
    with pytest.raises(RuntimeError):
        sqlite3.connect(str(target))
    assert not target.exists(), "阻断必须先于 open，不创建文件"
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert entries and entries[0]["target"] == str(target)
    assert entries[0]["pid"] == __import__("os").getpid()
    assert entries[0]["stack"]


def test_guard_denies_in_thread(tmp_path, monkeypatch):
    root, log = _with_temp_protection(tmp_path, monkeypatch)
    target = root / "assets.db"
    errors: list[BaseException] = []

    def worker():
        try:
            sqlite3.connect(str(target))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10)
    assert errors and isinstance(errors[0], RuntimeError)
    assert not target.exists()
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert entries and entries[-1]["thread"] != threading.get_ident()


def test_subprocess_injection_denies_and_logs(tmp_path):
    protected = tmp_path / "protected"
    protected.mkdir()
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    log = tmp_path / "child-guard.jsonl"
    repo = Path(__file__).resolve().parents[2]
    (site_dir / "sitecustomize.py").write_text(
        "from dragon_quant.tests import sqlite_guard\n"
        "sqlite_guard.install()\n", encoding="utf-8")
    target = protected / "dragon.db"
    code = (
        "import sqlite3, sys\n"
        f"target = {str(target)!r}\n"
        "try:\n"
        "    sqlite3.connect(target)\n"
        "except RuntimeError:\n"
        "    print('BLOCKED')\n"
        "else:\n"
        "    print('OPENED')\n"
    )
    env = {
        "PYTHONPATH": f"{site_dir}{__import__('os').pathsep}{repo}",
        "DQ_SQLITE_GUARD_PROTECTED": str(protected),
        "DQ_SQLITE_GUARD_LOG": str(log),
        "DQ_SQLITE_GUARD_SELFTEST": "1",
        "HOME": str(tmp_path),
    }
    completed = subprocess.run([sys.executable, "-c", code], env=env,
                               cwd=tmp_path, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert "BLOCKED" in completed.stdout
    assert not target.exists()
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert entries and entries[0]["pid"] != __import__("os").getpid()


def test_default_protected_dir_uses_real_home_not_test_home(monkeypatch):
    real_home = sqlite_guard._real_home()
    assert real_home is not None
    monkeypatch.setenv("HOME", "/tmp/not-the-real-home")
    roots = sqlite_guard.default_protected_dirs()
    assert roots and str(real_home) in str(roots[0])
    assert "not-the-real-home" not in str(roots[0])


def test_selftest_does_not_touch_real_protected_dir():
    before = {path.name: path.stat().st_mtime_ns
              for path in sqlite_guard.default_protected_dirs()
              if path.exists()}
    sqlite_guard.verify_selftest()
    after = {path.name: path.stat().st_mtime_ns
             for path in sqlite_guard.default_protected_dirs()
             if path.exists()}
    assert before == after
