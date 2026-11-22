"""W5-S：``_connect_readonly`` 的 SQL 只读/零建库/新鲜 WAL 契约。"""

from __future__ import annotations

import sqlite3

import pytest


def _patch_db(monkeypatch, path):
    from dragon_quant.infra.storage import connection

    monkeypatch.setattr("dragon_quant.infra.storage.paths.DB_PATH", path)
    return connection


def test_readonly_rejects_writes_and_sees_committed_wal(tmp_path, monkeypatch):
    path = tmp_path / "dragon.db"
    writer = sqlite3.connect(str(path))
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)")
    writer.execute("INSERT INTO t (value) VALUES ('fresh')")
    writer.commit()  # 仍在活跃 WAL 中、尚未 checkpoint
    connection = _patch_db(monkeypatch, path)
    reader = connection._connect_readonly()
    assert reader is not None
    try:
        assert reader.execute("SELECT value FROM t").fetchone()[0] == "fresh"
        with pytest.raises(sqlite3.OperationalError):
            reader.execute("INSERT INTO t (value) VALUES ('write')")
    finally:
        reader.close()
        writer.close()


def test_readonly_missing_path_creates_nothing(tmp_path, monkeypatch):
    path = tmp_path / "missing" / "dragon.db"
    connection = _patch_db(monkeypatch, path)
    assert connection._connect_readonly() is None
    assert not path.exists()
    assert not path.parent.exists()


def test_connect_wal_and_schema_are_serialized_on_first_open(tmp_path,
                                                            monkeypatch):
    """结构性并发：8 线程首次连接全部成功且 schema 只建一次。"""
    import threading

    from dragon_quant.infra.storage import connection as db

    target = tmp_path / "dragon.db"
    monkeypatch.setattr("dragon_quant.infra.storage.paths.DB_PATH", target)
    monkeypatch.setattr(db, "DB_PATH", target)
    monkeypatch.setattr(db, "_schema_ready", False, raising=False)
    calls: list[int] = []
    guard = threading.Lock()
    real = db._ensure_schema

    def counting(conn):
        with guard:
            calls.append(1)
        return real(conn)

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
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert errors == []
    assert calls == [1]
    assert db._schema_ready is True
    assert target.is_file(), "首连必须真实打开声明的临时库"
    import sqlite3 as _sqlite
    conn = _sqlite.connect(str(target))
    try:
        names = [row[2] for row in conn.execute("PRAGMA database_list")]
    finally:
        conn.close()
    assert str(target.resolve()) in names
