"""采集运行与跨进程租约（fencing token 保持原事务边界）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from dragon_quant.infra.assets import db as assets_db

from .schema import LeaseLostError, ObservationStoreError
from .store import _iso_now, _loads_strict, _open_read, _safe_rollback


_RUN_COLUMNS = (
    "run_id, plan_id, trade_date, phase, slot, status, started_at, finished_at, "
    "deadline_at, lease_owner, lease_token, lease_expires_at, report_json, created_at"
)


def _run_row(row) -> dict:
    return {
        "run_id": row[0],
        "plan_id": row[1],
        "trade_date": row[2],
        "phase": row[3],
        "slot": row[4],
        "status": row[5],
        "started_at": row[6],
        "finished_at": row[7],
        "deadline_at": row[8],
        "lease_owner": row[9],
        "lease_token": row[10],
        "lease_expires_at": row[11],
        "report": _loads_strict(row[12], column="capture_runs.report_json", default={}),
        "created_at": row[13],
    }


def ensure_run(*, plan_id: str, trade_date: str, phase: str, slot: str,
               deadline_at: Optional[str], created_at: str,
               db_path=None) -> dict:
    """幂等创建运行行（``UNIQUE(plan_id, phase, slot)`` 防重入）。"""
    _iso_now(created_at)
    run_id = f"{plan_id}:{phase}:{slot}"
    conn = assets_db._connect(path=db_path)
    try:
        assets_db._ensure_schema(conn)
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT OR IGNORE INTO capture_runs(run_id, plan_id, trade_date, "
                "phase, slot, status, deadline_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'planned', ?, ?)",
                (run_id, plan_id, trade_date, phase, slot, deadline_at, created_at),
            )
            conn.execute("COMMIT")
        except BaseException:
            _safe_rollback(conn)
            raise
        return get_run(run_id, db_path=db_path)
    finally:
        conn.close()


def get_run(run_id: str, *, db_path=None, strict: bool = True) -> Optional[dict]:
    conn = _open_read(db_path, strict=strict)
    if conn is None:
        return None
    try:
        row = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM capture_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return _run_row(row) if row else None
    except sqlite3.Error as exc:
        raise ObservationStoreError(
            f"读取 capture_runs 失败: {type(exc).__name__}: {exc}",
            reason="read_error") from exc
    finally:
        conn.close()


def count_runs(date_from: Optional[str] = None,
               date_to: Optional[str] = None, *, plan_id: Optional[str] = None,
               db_path=None, strict: bool = True) -> int:
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
        if plan_id:
            clauses.append("plan_id = ?")
            params.append(plan_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return int(conn.execute(
            f"SELECT COUNT(*) FROM capture_runs{where}", tuple(params)
        ).fetchone()[0])
    except sqlite3.Error as exc:
        raise ObservationStoreError(
            f"统计 capture_runs 失败: {type(exc).__name__}: {exc}",
            reason="read_error") from exc
    finally:
        conn.close()


def list_runs(date_from: Optional[str] = None, date_to: Optional[str] = None,
              *, plan_id: Optional[str] = None, limit: Optional[int] = None,
              offset: int = 0, db_path=None, strict: bool = True) -> list[dict]:
    conn = _open_read(db_path, strict=strict)
    if conn is None:
        return []
    try:
        clauses, params = [], []
        if date_from:
            clauses.append("trade_date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("trade_date <= ?")
            params.append(date_to)
        if plan_id:
            clauses.append("plan_id = ?")
            params.append(plan_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (f"SELECT {_RUN_COLUMNS} FROM capture_runs{where} "
               "ORDER BY trade_date, phase, slot")
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend((int(limit), int(offset)))
        return [_run_row(row) for row in conn.execute(sql, tuple(params))]
    except sqlite3.Error as exc:
        raise ObservationStoreError(
            f"读取 capture_runs 失败: {type(exc).__name__}: {exc}",
            reason="read_error") from exc
    finally:
        conn.close()


def _validate_lease_row(row, *, owner: str, token: int, now: str,
                        run_id: str) -> None:
    if row is None:
        raise ObservationStoreError(f"运行不存在: {run_id}")
    lease_owner, lease_token, expires = row
    if int(lease_token or 0) != int(token):
        raise LeaseLostError(
            f"fencing token 已失效: run={run_id} token={token} "
            f"current={lease_token}")
    if (lease_owner or "") != owner:
        raise LeaseLostError(
            f"租约持有者不匹配: run={run_id} owner={owner} "
            f"current={lease_owner}")
    if expires and expires <= now:
        raise LeaseLostError(f"租约已过期，拒绝旧 worker 提交: run={run_id}")


def _acquire_tx(conn: sqlite3.Connection, run_id: str, *, owner: str,
                ttl_seconds: int, now: str) -> Optional[int]:
    """短事务抢占/续租；``None`` = 其他活跃 worker 持有租约。

    调用方必须在 BEGIN IMMEDIATE 事务内调用（网络请求绝不在此事务内）。
    """
    row = conn.execute(
        "SELECT lease_owner, lease_token, lease_expires_at FROM capture_runs "
        "WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ObservationStoreError(f"运行不存在: {run_id}")
    lease_owner, token, expires = row
    if lease_owner and lease_owner != owner and expires and expires > now:
        return None
    new_token = int(token or 0) + 1
    new_expires = datetime.fromisoformat(now) + timedelta(seconds=ttl_seconds)
    conn.execute(
        "UPDATE capture_runs SET lease_owner = ?, lease_token = ?, "
        "lease_expires_at = ? WHERE run_id = ?",
        (owner, new_token, new_expires.isoformat(), run_id),
    )
    return new_token


def acquire_lease(run_id: str, *, owner: str, ttl_seconds: int, now: str,
                  db_path=None) -> Optional[int]:
    """跨进程租约 + 递增 fencing token；``None`` 表示有活跃持有者。"""
    _iso_now(now)
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds 必须为正")
    conn = assets_db._connect(path=db_path)
    try:
        assets_db._ensure_schema(conn)
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            token = _acquire_tx(conn, run_id, owner=owner,
                                ttl_seconds=ttl_seconds, now=now)
            conn.execute("COMMIT")
            return token
        except BaseException:
            _safe_rollback(conn)
            raise
    finally:
        conn.close()


def release_lease(run_id: str, *, token: int, now: str, db_path=None) -> None:
    """显式释放：只有当前 token 的持有者能释放。"""
    _iso_now(now)
    conn = assets_db._connect(path=db_path)
    try:
        assets_db._ensure_schema(conn)
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE capture_runs SET lease_owner = NULL, lease_expires_at = NULL "
                "WHERE run_id = ? AND lease_token = ?",
                (run_id, int(token)),
            )
            conn.execute("COMMIT")
        except BaseException:
            _safe_rollback(conn)
            raise
    finally:
        conn.close()


def start_run(run_id: str, *, owner: str, token: int, started_at: str,
              db_path=None) -> None:
    """在租约保护下把运行置为 running（同样校验 owner/token/有效期）。"""
    _iso_now(started_at)
    conn = assets_db._connect(path=db_path)
    try:
        assets_db._ensure_schema(conn)
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT lease_owner, lease_token, lease_expires_at FROM capture_runs "
                "WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            _validate_lease_row(row, owner=owner, token=token, now=started_at,
                                run_id=run_id)
            conn.execute(
                "UPDATE capture_runs SET status = 'running', started_at = ? "
                "WHERE run_id = ?",
                (started_at, run_id),
            )
            conn.execute("COMMIT")
        except BaseException:
            _safe_rollback(conn)
            raise
    finally:
        conn.close()
