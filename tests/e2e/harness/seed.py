"""Synthetic SQLite seeding for E2E tests.

Standard library only.  ``seed_db`` accepts a fixture name or an inline spec and
creates a brand-new database *inside the sandbox data directory*; it refuses to
overwrite an existing file and validates every identifier before SQL
interpolation.  No production module is imported.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLUMN_TYPE = re.compile(r"^[A-Za-z0-9_ ()]+$")
_DB_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.db$")


def _check_ident(name: str, kind: str) -> str:
    if not isinstance(name, str) or not _IDENT.match(name):
        raise ValueError(f"非法标识符 ({kind}): {name!r}")
    return name


def _quote(name: str) -> str:
    return f'"{_check_ident(name, "identifier")}"'


def _column_type(declaration: object) -> str:
    text = str(declaration).strip()
    if not _COLUMN_TYPE.match(text):
        raise ValueError(f"非法列类型: {declaration!r}")
    return text


def _encode(value: object):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    raise ValueError(f"无法写入 SQLite 的 JSON 值: {value!r}")


def create_sqlite(path: str | Path, spec: dict) -> Path:
    """Create ``path`` from ``{"tables": {name: {columns, rows, primary_key}}}``."""

    target = Path(path).resolve()
    if target.exists():
        raise ValueError(f"目标数据库已存在，拒绝覆盖: {target}")
    if not isinstance(spec, dict):
        raise ValueError("seed spec 必须是 JSON 对象")
    tables = spec.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise ValueError("seed spec 必须包含非空 tables 对象")

    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    try:
        with connection:
            for table, config in tables.items():
                _check_ident(table, "table")
                if not isinstance(config, dict):
                    raise ValueError(f"表 {table} 配置必须是对象")
                columns = config.get("columns")
                if not isinstance(columns, dict) or not columns:
                    raise ValueError(f"表 {table} 必须声明非空 columns")
                clauses = []
                for column, declaration in columns.items():
                    _check_ident(column, "column")
                    clauses.append(f'"{column}" {_column_type(declaration)}')
                primary_key = config.get("primary_key") or []
                if primary_key:
                    key_columns = ", ".join(
                        f'"{_check_ident(item, "primary key")}"' for item in primary_key
                    )
                    clauses.append(f"PRIMARY KEY ({key_columns})")
                connection.execute(
                    f'CREATE TABLE "{table}" ({", ".join(clauses)})'
                )

                rows = config.get("rows") or []
                if not isinstance(rows, list):
                    raise ValueError(f"表 {table} rows 必须是数组")
                names = list(columns)
                quoted = ", ".join(f'"{name}"' for name in names)
                placeholders = ", ".join("?" for _ in names)
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError(f"表 {table} 行必须是对象")
                    extra = set(row) - set(names)
                    if extra:
                        raise ValueError(f"表 {table} 行含未声明列: {sorted(extra)!r}")
                    connection.execute(
                        f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})',
                        [_encode(row.get(name)) for name in names],
                    )
    finally:
        connection.close()
    return target


def fixture_path(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "seed" / f"{name}.json"


def load_seed_fixture(name: str) -> dict:
    return json.loads(fixture_path(name).read_text(encoding="utf-8"))


def seed_db(sandbox, fixture, *, db_name: str = "dragon.db") -> Path:
    """Seed ``<sandbox.data_dir>/<db_name>``; never escapes the sandbox root."""

    if not _DB_NAME.match(str(db_name)) or Path(db_name).name != str(db_name):
        raise ValueError(f"非法数据库名: {db_name!r}")
    spec = load_seed_fixture(fixture) if isinstance(fixture, str) else fixture

    root = Path(sandbox.root).resolve()
    data_dir = Path(sandbox.data_dir).resolve()
    target = (data_dir / db_name).resolve()
    if target.parent != data_dir or root not in target.parents:
        raise ValueError(f"数据库路径越界: {target}")
    return create_sqlite(target, spec)
