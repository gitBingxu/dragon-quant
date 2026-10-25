"""M8 / B1：导入期零副作用 + 首次存储访问惰性初始化。

覆盖：
- ``import dragon_quant`` / ``import dragon_quant.storage.paths`` / ``storage.db``
  与 ``-h`` / ``-v`` 不创建任何数据目录、数据库、WAL/SHM、缓存或报告；
- 包根 ``scan`` / ``run_review`` 为 PEP 562 惰性导出，函数身份不变；
- ``ensure_data_dirs()`` 幂等；
- 首次 ``db._connect()`` 建目录 + schema，失败不置 ready 且可重试，并发首连只建一次；
- ``init_db()`` 幂等且兼容被 patch 的 ``_connect``；
- ``assets_db`` 读路径不建库、写路径先建目录；
- ``DataCache`` 与 ``StorageManager`` 只在实际使用时落盘。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_SUBDIRS = ("cookies", "cache", "logs", "results", "shared")


# ─── 子进程隔离（导入副作用只能跨进程观测） ─────────────────────────────────


def _isolated_env(sandbox: Path, *, data_root: Path) -> dict[str, str]:
    """干净环境：剥离所有 DQ_*，HOME/XDG/TMP 指向一次性目录。"""

    env = {k: v for k, v in os.environ.items() if not k.startswith("DQ_")}
    home = sandbox / "home"
    env.update({
        "DQ_DATA_DIR": str(data_root),
        "HOME": str(home),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "TMPDIR": str(sandbox / "tmp"),
        "PYTHONPATH": str(REPO_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


def _run_child(sandbox: Path, *, data_root: Path, code: str | None = None,
               args: list[str] | None = None) -> subprocess.CompletedProcess:
    for directory in (sandbox / "home", sandbox / "tmp"):
        directory.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-c", code] if code is not None else [
        sys.executable, "-m", "dragon_quant", *(args or [])]
    return subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        env=_isolated_env(sandbox, data_root=data_root),
        capture_output=True,
        text=True,
        timeout=180,
    )


def _assert_no_paths_created(data_root: Path, home: Path) -> None:
    assert not data_root.exists(), f"数据目录被导入期创建: {data_root}"
    platform_defaults = (
        home / "Library" / "Application Support" / "dragon-quant",
        home / ".local" / "share" / "dragon-quant",
        home / "AppData" / "Roaming" / "dragon-quant",
    )
    for default in platform_defaults:
        assert not default.exists(), f"平台默认数据目录被创建: {default}"


class TestNoImportSideEffects:
    def test_paths_import_creates_nothing(self, tmp_path: Path):
        data_root = tmp_path / "data"
        result = _run_child(
            tmp_path, data_root=data_root,
            code="import dragon_quant.storage.paths as p; print(p.DATA_DIR)")

        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()) == data_root
        _assert_no_paths_created(data_root, tmp_path / "home")

    def test_storage_db_import_creates_nothing(self, tmp_path: Path):
        data_root = tmp_path / "data"
        result = _run_child(
            tmp_path, data_root=data_root,
            code="import dragon_quant.storage.db  # noqa: F401")

        assert result.returncode == 0, result.stderr
        _assert_no_paths_created(data_root, tmp_path / "home")

    def test_storage_package_boundary_is_documented_and_lazy(self, tmp_path: Path):
        data_root = tmp_path / "data"
        expected = [
            "db", "assets_db", "dragons_mirror", "finance", "manager", "paths",
            "blacklist", "signals", "scans", "dragons", "logs", "review_account",
        ]
        code = "\n".join([
            "import sys",
            "import dragon_quant.storage as storage",
            f"assert storage.__all__ == {expected!r}",
            "assert storage.__doc__ and '子模块' in storage.__doc__",
            "for name in storage.__all__:",
            "    assert f'dragon_quant.storage.{name}' not in sys.modules, name",
            "print('ok')",
        ])
        result = _run_child(tmp_path, data_root=data_root, code=code)

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"
        _assert_no_paths_created(data_root, tmp_path / "home")

    def test_package_import_defers_heavy_modules_and_keeps_identity(
            self, tmp_path: Path):
        data_root = tmp_path / "data"
        code = "\n".join([
            "import sys",
            "import dragon_quant",
            "assert 'dragon_quant.orchestrator' not in sys.modules",
            "assert 'dragon_quant.review' not in sys.modules",
            "assert 'dragon_quant.storage.db' not in sys.modules",
            "from dragon_quant import scan, run_review",
            "import dragon_quant.orchestrator as orchestrator",
            "import dragon_quant.review as review",
            "assert scan is orchestrator.scan",
            "assert run_review is review.run_review",
            "assert dragon_quant.scan is scan",
            "assert dragon_quant.run_review is run_review",
            "print('ok')",
        ])
        result = _run_child(tmp_path, data_root=data_root, code=code)

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"
        _assert_no_paths_created(data_root, tmp_path / "home")

    @pytest.mark.parametrize("flag", ["-h", "--version", "-v"])
    def test_help_and_version_create_nothing(self, tmp_path: Path, flag: str):
        data_root = tmp_path / "data"
        result = _run_child(tmp_path, data_root=data_root, args=[flag])

        assert result.returncode == 0, result.stderr
        _assert_no_paths_created(data_root, tmp_path / "home")


# ─── 目录初始化 ─────────────────────────────────────────────────────────────


def _patch_paths_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    from dragon_quant.storage import paths

    monkeypatch.setattr(paths, "DATA_DIR", root)
    monkeypatch.setattr(paths, "COOKIE_DIR", root / "cookies")
    monkeypatch.setattr(paths, "CACHE_DIR", root / "cache")
    monkeypatch.setattr(paths, "LOG_DIR", root / "logs")
    monkeypatch.setattr(paths, "RESULTS_DIR", root / "results")
    monkeypatch.setattr(paths, "SHARED_DIR", root / "shared")
    monkeypatch.setattr(paths, "DB_PATH", root / "dragon.db")
    monkeypatch.setattr(paths, "ASSETS_DB_PATH", root / "assets.db")


class TestEnsureDataDirs:
    def test_ensure_data_dirs_is_idempotent(self, tmp_path, monkeypatch):
        from dragon_quant.storage import paths

        root = tmp_path / "dq"
        _patch_paths_root(monkeypatch, root)

        paths.ensure_data_dirs()
        paths.ensure_data_dirs()

        for name in DATA_SUBDIRS:
            assert (root / name).is_dir()


# ─── 首次连接 schema 守卫 ───────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """把 dragon.db 指向一次性目录，并重置 schema ready 标志。"""

    from dragon_quant.storage import db

    root = tmp_path / "dq"
    _patch_paths_root(monkeypatch, root)
    monkeypatch.setattr(db, "DB_PATH", root / "dragon.db")
    monkeypatch.setattr(db, "_schema_ready", False, raising=False)
    return root


class TestFirstConnection:
    def test_first_connection_creates_dirs_and_schema(self, fresh_db):
        from dragon_quant.storage import db

        conn = db._connect()
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert "scans_v2" in tables
            assert "scan_stocks_v2" in tables
            assert "dragons_v2" in tables
            assert "scan_logs_v2" in tables
            assert "vpa_analysis" in tables
        finally:
            conn.close()

        for name in DATA_SUBDIRS:
            assert (fresh_db / name).is_dir()
        assert (fresh_db / "dragon.db").is_file()
        assert db._schema_ready is True

    def test_failed_schema_initialization_is_retryable(
            self, fresh_db, monkeypatch):
        from dragon_quant.storage import db

        real_ensure = db._ensure_schema
        calls = {"n": 0}

        def flaky(conn):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("schema boom")
            return real_ensure(conn)

        monkeypatch.setattr(db, "_ensure_schema", flaky)

        with pytest.raises(RuntimeError, match="schema boom"):
            db._connect()
        assert db._schema_ready is False, "失败不得置 ready"

        conn = db._connect()
        try:
            conn.execute("SELECT COUNT(*) FROM scans_v2").fetchone()
        finally:
            conn.close()

        assert calls["n"] == 2
        assert db._schema_ready is True

    def test_concurrent_first_connections_initialize_schema_once(
            self, fresh_db, monkeypatch):
        from dragon_quant.storage import db

        real_ensure = db._ensure_schema
        lock = threading.Lock()
        calls: list[int] = []

        def counting(conn):
            with lock:
                calls.append(1)
            return real_ensure(conn)

        monkeypatch.setattr(db, "_ensure_schema", counting)

        workers = 8
        barrier = threading.Barrier(workers)
        errors: list[BaseException] = []

        def worker():
            try:
                barrier.wait(timeout=30)
                conn = db._connect()
                try:
                    conn.execute("SELECT COUNT(*) FROM scans_v2").fetchone()
                finally:
                    conn.close()
            except BaseException as exc:  # pragma: no cover - 失败时诊断
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert errors == []
        assert len(calls) == 1, f"schema 构建次数应为 1，实际 {len(calls)}"
        assert db._schema_ready is True

    def test_init_db_is_idempotent_with_patched_connect(self, tmp_path, monkeypatch):
        from dragon_quant.storage import db

        target = tmp_path / "patched.db"
        monkeypatch.setattr(db, "_connect", lambda: sqlite3.connect(str(target)))
        monkeypatch.setattr(db, "_schema_ready", False, raising=False)

        db.init_db()
        db.init_db()

        conn = sqlite3.connect(target)
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert {"scans_v2", "dragons_v2"} <= tables
        # A patched connection must not mark the real path ready; the next real
        # first connection has to run the schema guard itself.
        assert db._schema_ready is False


# ─── assets.db ──────────────────────────────────────────────────────────────


class TestAssetsDbLaziness:
    def test_read_path_creates_nothing_and_write_path_creates(
            self, tmp_path, monkeypatch):
        from dragon_quant.storage import assets_db

        root = tmp_path / "dq"
        _patch_paths_root(monkeypatch, root)
        monkeypatch.setattr(assets_db, "ASSETS_DB_PATH", root / "assets.db")

        assert assets_db._connect(create=False) is None
        assert not (root / "assets.db").exists()
        assert not root.exists(), "读路径连数据根都不应创建"

        assets_db.save_prep_snapshot("2026-05-19", {"probe": True})

        assert (root / "assets.db").is_file()
        for name in DATA_SUBDIRS:
            assert (root / name).is_dir()
        payload = assets_db.get_prep_snapshot("2026-05-19")
        assert payload == {"probe": True}


# ─── cache / manager ────────────────────────────────────────────────────────


class TestWriteTimeInitialization:
    def test_data_cache_defers_directory_creation(self, tmp_path):
        from dragon_quant.cache.data_cache import DataCache

        cache_dir = tmp_path / "cache"
        cache = DataCache(cache_dir=cache_dir)

        assert not cache_dir.exists(), "构造 DataCache 不得建目录"
        cache.set("probe", {"value": 1})

        stored = cache_dir / "probe.json"
        assert stored.is_file()
        assert json.loads(stored.read_text(encoding="utf-8"))["data"] == {"value": 1}

    def test_storage_manager_read_and_clear_do_not_create_database(
            self, tmp_path, monkeypatch):
        from dragon_quant.storage import db, manager

        root = tmp_path / "dq"
        db_path = root / "dragon.db"
        _patch_paths_root(monkeypatch, root)
        for module in (manager, db):
            monkeypatch.setattr(module, "DATA_DIR", root, raising=False)
            monkeypatch.setattr(module, "COOKIE_DIR", root / "cookies", raising=False)
            monkeypatch.setattr(module, "CACHE_DIR", root / "cache", raising=False)
            monkeypatch.setattr(module, "LOG_DIR", root / "logs", raising=False)
            monkeypatch.setattr(module, "RESULTS_DIR", root / "results", raising=False)
            monkeypatch.setattr(module, "SHARED_DIR", root / "shared", raising=False)
            monkeypatch.setattr(module, "DB_PATH", db_path, raising=False)
            monkeypatch.setattr(module, "ASSETS_DB_PATH", root / "assets.db", raising=False)
            monkeypatch.setattr(module, "LEGACY_PREP_DB_PATH", root / "prep.db",
                                raising=False)

        mgr = manager.StorageManager()
        mgr.status()
        mgr.size()
        assert not root.exists(), "只读状态/体积查询不得创建数据目录"

        result = mgr.clear_all()
        assert result["protected_databases"] == []
        assert not db_path.exists(), "clear --all 不得因日志清理而建空库"
