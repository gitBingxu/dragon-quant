"""只读连接、严格 JSON、状态探测与 canonical 哈希。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Optional

from dragon_quant.infra.assets import db as assets_db

from .schema import (
    CAPTURE_TABLES,
    MISSING_DATABASE,
    READY,
    UNINITIALIZED,
    CorruptEvidence,
    ObservationStoreError,
    StoreUninitialized,
)

def canonical_json(payload) -> str:
    """稳定序列化：键序固定、NaN 早暴露（重放与内容哈希的唯一口径）。"""
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)


def content_hash(payload) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _loads_strict(text, *, column: str, default=None):
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CorruptEvidence(f"{column} JSON 损坏: {type(exc).__name__}: {exc}",
                              reason="corrupt_json") from exc


def _iso_now(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ObservationStoreError("时间戳必须是非空 ISO 字符串")
    return value


def _safe_rollback(conn: sqlite3.Connection) -> None:
    """回滚但绝不掩盖原始异常（可能已由内层回滚或无活跃事务）。"""
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


# ─── 连接与只读状态 ──────────────────────────────────────────────────────────


def _tables_present(conn: sqlite3.Connection) -> bool:
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    return set(CAPTURE_TABLES) <= names


def store_state(db_path=None) -> str:
    """``ready`` / ``uninitialized`` / ``missing_database``（只读探测）。"""
    conn = assets_db._connect(create=False, path=db_path)
    if conn is None:
        return MISSING_DATABASE
    try:
        return READY if _tables_present(conn) else UNINITIALIZED
    except sqlite3.Error as exc:
        raise ObservationStoreError(
            f"无法检查采集表: {type(exc).__name__}: {exc}",
            reason="read_error") from exc
    finally:
        conn.close()


def _open_read(db_path, *, strict: bool) -> Optional[sqlite3.Connection]:
    """只读连接：绝不建库、建表、迁移。

    ``strict=True`` 时未初始化抛 :class:`StoreUninitialized`；``False`` 时返回
    ``None``（供「首次运行合法无数据」的调用方使用）。
    """
    conn = assets_db._connect(create=False, path=db_path)
    if conn is None:
        if strict:
            raise StoreUninitialized(f"assets.db 不存在: {db_path or '默认路径'}",
                                     reason=MISSING_DATABASE)
        return None
    try:
        if not _tables_present(conn):
            conn.close()
            if strict:
                raise StoreUninitialized(
                    "采集表尚未初始化（capture_* / market_* 不存在）")
            return None
        return conn
    except sqlite3.Error as exc:
        conn.close()
        raise ObservationStoreError(
            f"采集表探测失败: {type(exc).__name__}: {exc}",
            reason="read_error") from exc


# ─── 计划 ────────────────────────────────────────────────────────────────────
