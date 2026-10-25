"""Assertion helpers for E2E CLI results, files and temporary SQLite databases.

Standard library only.  Databases are opened read-only (``mode=ro``) so a test
helper can never migrate or modify one; every identifier is validated before it
is interpolated into SQL.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ORDER_BY = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\s+(?:ASC|DESC))?(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*(?:\s+(?:ASC|DESC))?)*$",
    re.IGNORECASE,
)
BUSINESS_DB_FILES = ("dragon.db", "assets.db", "prep.db")


# ─── process results ────────────────────────────────────────────────────────


def assert_exit(result, expected: int = 0) -> None:
    if result.returncode != expected:
        raise AssertionError(
            f"exit {result.returncode} != {expected}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def assert_stdout_empty(result) -> None:
    if result.stdout:
        raise AssertionError(f"stdout 应为空，实际: {result.stdout!r}")


def assert_stderr_empty(result) -> None:
    if result.stderr:
        raise AssertionError(f"stderr 应为空，实际: {result.stderr!r}")


def assert_stdout_contains(result, needle: str) -> None:
    if needle not in result.stdout:
        raise AssertionError(f"stdout 缺少 {needle!r}: {result.stdout!r}")


def assert_stderr_contains(result, needle: str) -> None:
    if needle not in result.stderr:
        raise AssertionError(f"stderr 缺少 {needle!r}: {result.stderr!r}")


def parse_stdout_json(result) -> Any:
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise AssertionError(f"stdout 不是合法 JSON: {result.stdout!r}") from exc


def assert_json_keys_in_order(payload: Mapping, keys: Sequence[str]) -> None:
    actual = list(payload.keys())
    expected = list(keys)
    if actual[: len(expected)] != expected:
        raise AssertionError(f"JSON 键顺序不符: {actual!r} != {expected!r}")


# ─── files ──────────────────────────────────────────────────────────────────


def assert_file(path: str | Path, *, contains: str | None = None) -> None:
    target = Path(path)
    if not target.is_file():
        raise AssertionError(f"文件不存在: {target}")
    if contains is not None:
        text = target.read_text(encoding="utf-8")
        if contains not in text:
            raise AssertionError(f"文件 {target} 缺少 {contains!r}")


def assert_no_file(path: str | Path) -> None:
    target = Path(path)
    if target.exists():
        raise AssertionError(f"文件不应存在: {target}")


def list_files(root: str | Path) -> list[str]:
    base = Path(root)
    if not base.exists():
        return []
    return sorted(
        path.relative_to(base).as_posix()
        for path in base.rglob("*")
        if path.is_file()
    )


# ─── sqlite (read-only) ─────────────────────────────────────────────────────


def _quote(identifier: str) -> str:
    if not _IDENT.match(identifier):
        raise ValueError(f"非法 SQL 标识符: {identifier!r}")
    return f'"{identifier}"'


def _connect_readonly(path: str | Path) -> sqlite3.Connection:
    target = Path(path).resolve()
    connection = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def sqlite_tables(db_path: str | Path) -> list[str]:
    with closing(_connect_readonly(db_path)) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    return [row["name"] for row in rows]


def sqlite_rows(
    db_path: str | Path,
    table: str,
    *,
    order_by: str | None = None,
) -> list[dict]:
    sql = f"SELECT * FROM {_quote(table)}"
    if order_by is not None:
        if not _ORDER_BY.match(order_by.strip()):
            raise ValueError(f"非法 order_by: {order_by!r}")
        sql += f" ORDER BY {order_by}"
    with closing(_connect_readonly(db_path)) as connection:
        rows = connection.execute(sql).fetchall()
    return [dict(row) for row in rows]


def sqlite_count(db_path: str | Path, table: str) -> int:
    with closing(_connect_readonly(db_path)) as connection:
        row = connection.execute(
            f"SELECT COUNT(*) AS n FROM {_quote(table)}"
        ).fetchone()
    return int(row["n"])


def assert_tables(db_path: str | Path, expected: Iterable[str]) -> None:
    actual = sqlite_tables(db_path)
    wanted = sorted(expected)
    if actual != wanted:
        raise AssertionError(f"表集合不符: {actual!r} != {wanted!r}")


def assert_row_count(db_path: str | Path, table: str, expected: int) -> None:
    actual = sqlite_count(db_path, table)
    if actual != expected:
        raise AssertionError(f"{table} 行数 {actual} != {expected}")


def assert_no_business_db(data_dir: str | Path) -> None:
    base = Path(data_dir)
    present = []
    for name in BUSINESS_DB_FILES:
        for suffix in ("", "-wal", "-shm"):
            candidate = base / f"{name}{suffix}"
            if candidate.exists():
                present.append(candidate.name)
    if present:
        raise AssertionError(f"不应出现业务数据库文件: {sorted(present)!r}")
