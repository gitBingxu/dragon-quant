"""观察计划的查询与写入（无运行/租约逻辑）。"""

from __future__ import annotations

import sqlite3
from typing import Optional

from dragon_quant.infra.assets import db as assets_db

from .schema import ObservationStoreError
from .store import (
    _iso_now,
    _loads_strict,
    _open_read,
    _safe_rollback,
    canonical_json,
    content_hash,
)


def _plan_row(row) -> dict:
    (
        plan_id, trade_date, revision, parent_plan_id, pool_json,
        members_json, task_config_json, config_version, content_hash_text,
        created_at,
    ) = row
    return {
        "plan_id": plan_id,
        "trade_date": trade_date,
        "revision": revision,
        "parent_plan_id": parent_plan_id,
        "pool": _loads_strict(pool_json, column="capture_plans.pool_json", default={}),
        "members": _loads_strict(members_json,
                                 column="capture_plans.members_json", default=[]),
        "task_config": _loads_strict(task_config_json,
                                     column="capture_plans.task_config_json",
                                     default={}),
        "config_version": config_version,
        "content_hash": content_hash_text,
        "created_at": created_at,
    }


_PLAN_COLUMNS = (
    "plan_id, trade_date, revision, parent_plan_id, pool_json, members_json, "
    "task_config_json, config_version, content_hash, created_at"
)


def _plan_query(sql: str, params: tuple, *, db_path, strict: bool) -> list[dict]:
    conn = _open_read(db_path, strict=strict)
    if conn is None:
        return []
    try:
        return [_plan_row(row) for row in conn.execute(sql, params)]
    except sqlite3.Error as exc:
        raise ObservationStoreError(
            f"读取 capture_plans 失败: {type(exc).__name__}: {exc}",
            reason="read_error") from exc
    finally:
        conn.close()


def get_plan(plan_id: str, *, db_path=None, strict: bool = True) -> Optional[dict]:
    rows = _plan_query(
        f"SELECT {_PLAN_COLUMNS} FROM capture_plans WHERE plan_id = ?",
        (plan_id,), db_path=db_path, strict=strict)
    return rows[0] if rows else None


def latest_plan(trade_date: str, *, db_path=None,
                strict: bool = True) -> Optional[dict]:
    rows = _plan_query(
        f"SELECT {_PLAN_COLUMNS} FROM capture_plans WHERE trade_date = ? "
        "ORDER BY revision DESC LIMIT 1",
        (trade_date,), db_path=db_path, strict=strict)
    return rows[0] if rows else None


def previous_plan(before_date: str, *, db_path=None,
                  strict: bool = False) -> Optional[dict]:
    rows = _plan_query(
        f"SELECT {_PLAN_COLUMNS} FROM capture_plans WHERE trade_date < ? "
        "ORDER BY trade_date DESC, revision DESC LIMIT 1",
        (before_date,), db_path=db_path, strict=strict)
    return rows[0] if rows else None


def list_plans(date_from: Optional[str] = None, date_to: Optional[str] = None,
               *, limit: Optional[int] = None, offset: int = 0,
               db_path=None, strict: bool = True) -> list[dict]:
    clauses, params = [], []
    if date_from:
        clauses.append("trade_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("trade_date <= ?")
        params.append(date_to)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (f"SELECT {_PLAN_COLUMNS} FROM capture_plans{where} "
           "ORDER BY trade_date, revision")
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend((int(limit), int(offset)))
    return _plan_query(sql, tuple(params), db_path=db_path, strict=strict)



def count_plans(date_from: Optional[str] = None,
                date_to: Optional[str] = None, *, db_path=None,
                strict: bool = True) -> int:
    conn = _open_read(db_path, strict=strict)
    if conn is None:
        return 0
    try:
        clauses, params = [], []
        if date_from:
            clauses.append("trade_date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("trade_date <= ?")
            params.append(date_to)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return int(conn.execute(
            f"SELECT COUNT(*) FROM capture_plans{where}", tuple(params)
        ).fetchone()[0])
    except sqlite3.Error as exc:
        raise ObservationStoreError(
            f"统计 capture_plans 失败: {type(exc).__name__}: {exc}",
            reason="read_error") from exc
    finally:
        conn.close()


def save_plan(*, trade_date: str, pool: dict, members: list[dict],
              task_config: dict, config_version: str, created_at: str,
              db_path=None) -> dict:
    """冻结一版计划；内容与最新版本相同时幂等返回既有行。

    计划不可变：内容不同只能追加 ``revision``，不会 UPDATE 旧版本。
    """
    _iso_now(created_at)
    body = {
        "trade_date": trade_date,
        "pool": pool,
        "members": members,
        "task_config": task_config,
        "config_version": config_version,
    }
    digest = content_hash(body)
    conn = assets_db._connect(path=db_path)
    try:
        assets_db._ensure_schema(conn)
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT plan_id, revision, content_hash FROM capture_plans "
                "WHERE trade_date = ? ORDER BY revision DESC LIMIT 1",
                (trade_date,),
            ).fetchone()
            if row is not None and row[2] == digest:
                conn.execute("COMMIT")
                existing = get_plan(row[0], db_path=db_path)
                return {"created": False, "plan": existing}
            revision = (row[1] + 1) if row is not None else 1
            parent = row[0] if row is not None else None
            plan_id = f"cp_{trade_date.replace('-', '')}_r{revision}"
            conn.execute(
                "INSERT INTO capture_plans(plan_id, trade_date, revision, "
                "parent_plan_id, pool_json, members_json, task_config_json, "
                "config_version, content_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (plan_id, trade_date, revision, parent,
                 canonical_json(pool), canonical_json(members),
                 canonical_json(task_config), config_version, digest, created_at),
            )
            conn.execute("COMMIT")
        except BaseException:
            _safe_rollback(conn)
            raise
        return {
            "created": True,
            "plan": get_plan(plan_id, db_path=db_path),
        }
    finally:
        conn.close()


# ─── 运行与租约 ──────────────────────────────────────────────────────────────
