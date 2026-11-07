"""共享严格只读连接（``mode=ro``，零 DDL / 零 WAL 配置 / 零 sidecar 兜底）。

只读语义（方案 KR3）：

* 库文件不存在 → 返回 ``None``（调用方按「缺库」可解释降级，绝不建库）；
* 打开后只执行 ``PRAGMA busy_timeout``（连接级，不落盘）与只读 SELECT；
* ``mode=ro`` 无法打开且 ``-wal`` 为空/不存在时，才用 ``immutable=1``
  只读打开（没有未 checkpoint 的事务可丢，也不会创建 ``-shm``）；
* ``-wal`` 非空却缺少可写 sidecar 时抛 :class:`ReadonlyUnavailable`，
  由调用方给出明确的 ``readonly_unavailable``，绝不回退到读写连接。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


class ReadonlyUnavailable(RuntimeError):
    """源文件无法在零副作用条件下安全只读（例如未 checkpoint 的 WAL）。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


def _exists(path: Path) -> bool:
    return Path(path).is_file()


def _connect(uri: str, timeout_ms: int) -> sqlite3.Connection:
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute(f"PRAGMA busy_timeout={int(timeout_ms)}")
        conn.execute("SELECT 1")
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def open_readonly(path: Path | str, *, timeout_ms: int = 5000
                  ) -> Optional[sqlite3.Connection]:
    """Open ``path`` strictly read-only, or ``None`` when the file is absent.

    Sidecar policy（sentinel-tested）:

    * ``-wal`` 非空但 ``-shm`` 缺失 → 立即 ``readonly_unavailable``，**不尝试**
      ``mode=ro``：可写目录下 SQLite 会为只读连接创建 ``-shm``，属于写副作用。
    * 存在 ``-shm``（无论 WAL 是否为空）→ ``mode=ro`` 读取；WAL 已提交事务可见，
      连接失败即 fail-closed，绝不改用 ``immutable`` 丢弃 WAL。
    * 无 ``-wal`` / ``-wal`` 为空且无 ``-shm`` → ``immutable=1`` 直接只读（没有
      未 checkpoint 事务可丢，也不会创建 sidecar）。
    * 损坏 / 权限等真实故障 → ``readonly_unavailable``，不回退写连接。
    """
    db_path = Path(path).resolve()
    if not _exists(db_path):
        return None
    wal = Path(f"{db_path}-wal")
    shm = Path(f"{db_path}-shm")
    wal_size = wal.stat().st_size if wal.exists() else 0
    has_shm = shm.exists()
    if wal_size and not has_shm:
        raise ReadonlyUnavailable(
            "wal_not_checkpointed",
            "数据库存在未 checkpoint 的 WAL 且缺少 -shm；只读连接无法在不创建"
            " sidecar 的情况下安全读取，拒绝使用 immutable 丢弃已提交事务。",
        )
    if has_shm:
        try:
            return _connect(f"{db_path.as_uri()}?mode=ro", timeout_ms)
        except sqlite3.Error as exc:
            raise ReadonlyUnavailable(
                "readonly_open_failed", f"{type(exc).__name__}: {exc}") from exc
    try:
        return _connect(f"{db_path.as_uri()}?mode=ro&immutable=1", timeout_ms)
    except sqlite3.Error as exc:
        raise ReadonlyUnavailable(
            "readonly_open_failed", f"{type(exc).__name__}: {exc}") from exc


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
    return {str(row[0]) for row in rows}


def has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return row is not None


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]


def date_range(conn: sqlite3.Connection, table: str, column: str
               ) -> tuple[Optional[str], Optional[str]]:
    """``(min, max)`` of a declared date column; empty table → ``(None, None)``.

    ``table`` / ``column`` 只能来自内部数据集注册表（不是用户输入），本函数不
    拼接任何未校验的外部字符串。
    """
    available = columns(conn, table)
    if column not in available:
        raise sqlite3.OperationalError(f"table {table} has no column {column}")
    row = conn.execute(f"SELECT MIN({column}), MAX({column}) FROM {table}").fetchone()
    if not row:
        return None, None
    return row[0], row[1]
