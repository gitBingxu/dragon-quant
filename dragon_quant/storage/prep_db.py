"""盘后复盘独立资产库（prep.db）持久化门面。

与 dragon.db 物理隔离：prep 复盘的原始数据资产（prep_assets，不可再生）
与计算结果（prep_snapshots）独立存库，避免与可重算的扫描产物同生命周期。

设计要点（见 review_account/prep因子扩展技术方案.md §4/§5.4）：
  - 独立 _connect/_ensure_schema/_lock（WAL + 每操作独立连接 + 操作内建 schema），
    不做 import 时建库——测试 patch ``prep_db._connect`` 即可完全隔离。
  - prep_assets：UNIQUE(trade_date, asset_type)，UPSERT 幂等；采集段经
    ``save_prep_assets`` 单事务批量写入（含 collection_report），
    ON CONFLICT 同时刷新 created_at（语义=最后采集时间）。
  - JSON 序列化统一 allow_nan=False + sort_keys=True（重算确定性 + 尽早暴露 NaN）。
"""

import json
import sqlite3
import threading
from typing import Optional

from dragon_quant.storage.paths import PREP_DB_PATH

_lock = threading.Lock()

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS prep_assets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date   TEXT NOT NULL,
    asset_type   TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(trade_date, asset_type)
);

CREATE INDEX IF NOT EXISTS idx_prep_assets_date ON prep_assets(trade_date);

CREATE TABLE IF NOT EXISTS prep_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date   TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'v2',
    result_json  TEXT NOT NULL,
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(trade_date, source)
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(PREP_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema(conn: sqlite3.Connection):
    conn.executescript(BASE_SCHEMA)
    conn.commit()


def _dumps(payload: dict) -> str:
    """统一序列化：NaN 早暴露、键序稳定（--recompute 字节级确定性）。"""
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)


def save_prep_assets(trade_date: str, payloads: dict[str, dict], report: dict) -> None:
    """单事务 UPSERT 整日全部资产 + collection_report。

    payloads: {asset_type: payload}；report 以 asset_type="collection_report" 落库。
    任一写入失败 → 整体回滚（不留「部分新部分旧」的混合状态）。
    """
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            items = list(payloads.items()) + [("collection_report", report)]
            conn.execute("BEGIN")
            for asset_type, payload in items:
                conn.execute(
                    "INSERT INTO prep_assets(trade_date, asset_type, payload_json) VALUES (?, ?, ?) "
                    "ON CONFLICT(trade_date, asset_type) DO UPDATE SET "
                    "payload_json=excluded.payload_json, "
                    "created_at=datetime('now','localtime')",
                    (trade_date, asset_type, _dumps(payload)),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def get_prep_asset(trade_date: str, asset_type: str) -> Optional[dict]:
    """读取单日单类资产，无记录返回 None。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT payload_json FROM prep_assets WHERE trade_date = ? AND asset_type = ?",
            (trade_date, asset_type),
        ).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


def get_prep_assets(trade_date: str) -> dict[str, dict]:
    """读取整日全部资产 {asset_type: payload}（compute 段唯一入口）。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT asset_type, payload_json FROM prep_assets WHERE trade_date = ?",
            (trade_date,),
        ).fetchall()
        return {r[0]: json.loads(r[1]) for r in rows}
    finally:
        conn.close()


def save_prep_snapshot(trade_date: str, result: dict, source: str = "v2") -> None:
    """UPSERT 复盘快照（trade_date+source 唯一）。result 为完整复盘结果 dict。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO prep_snapshots(trade_date, source, result_json) VALUES (?, ?, ?) "
                "ON CONFLICT(trade_date, source) DO UPDATE SET "
                "result_json=excluded.result_json, "
                "created_at=datetime('now','localtime')",
                (trade_date, source, _dumps(result)),
            )
            conn.commit()
        finally:
            conn.close()


def get_prep_snapshot(trade_date: str, source: str = "v2") -> Optional[dict]:
    """读取某复盘日的快照，无记录返回 None。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT result_json FROM prep_snapshots WHERE trade_date = ? AND source = ?",
            (trade_date, source),
        ).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


def has_prep_snapshot(trade_date: str, source: str = "v2") -> bool:
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT 1 FROM prep_snapshots WHERE trade_date = ? AND source = ?",
            (trade_date, source),
        ).fetchone()
        return row is not None
    finally:
        conn.close()
