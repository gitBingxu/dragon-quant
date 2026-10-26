"""dragon.db 唯一连接入口（``sqlite3.connect`` 只允许出现在本模块）。

职责边界：
- 连接创建（WAL / foreign_keys）与 ``_lock`` / ``_schema_lock``；
- 首次连接惰性建目录 + schema（``_schema_ready`` 双重检查，失败不置 ready 可重试）;
- 公开幂等 ``init_db()``。

DDL 在 :mod:`dragon_quant.infra.storage.schema`，source/表名在
:mod:`dragon_quant.infra.storage.identifiers`。``_ensure_schema`` 在本模块以模块
级名字再导出，保证对 ``db._ensure_schema`` 的 patch 继续作用于首连守卫。
"""

from __future__ import annotations

import sqlite3
import threading

from dragon_quant.infra.storage.paths import DB_PATH
from dragon_quant.infra.storage.schema import _ensure_schema

_lock = threading.Lock()
# 首次连接 schema 守卫：模块导入时不建库，首次真实连接才建目录 + schema。
# 独立于 ``_lock``（领域模块在持有 ``_lock`` 时调用 ``_connect``，同一把锁会自锁）。
_schema_lock = threading.Lock()
_schema_ready = False


def _connect() -> sqlite3.Connection:
    """打开 dragon.db；首次连接惰性建目录 + schema（B1：导入期零副作用）。"""
    from dragon_quant.infra.storage.paths import ensure_data_dirs

    ensure_data_dirs()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_ready(conn)
    return conn


def _ensure_ready(conn: sqlite3.Connection):
    """双重检查锁：并发首连只构建一次 schema；失败不置 ready 以便重试。"""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        try:
            # 直接调用 _ensure_schema，绝不递归回 _connect。
            _ensure_schema(conn)
        except BaseException:
            conn.close()
            raise
        _schema_ready = True


def init_db():
    """幂等初始化：建目录 + schema（保留公开 API，兼容被 patch 的 ``_connect``）。

    不在这里改写 ``_schema_ready``：被 patch 的连接指向临时库，若标记全局 ready
    会让之后真实路径的首连跳过守卫。真实路径的 ``_connect`` 已自行置位。
    """
    with _lock:
        conn = _connect()
        try:
            # 被 patch 的 _connect 不会走首连守卫，这里显式兜底 schema。
            _ensure_schema(conn)
        finally:
            conn.close()


# 模块导入**不再**调用 init_db()：导入包 / `-h` / `-v` 必须零目录与零数据库
# 副作用。首次真实存储访问由 ``_connect`` 的 schema 守卫惰性完成。
