"""数据资产独立库（assets.db）持久化门面。

与 dragon.db 物理隔离：原始数据资产不可再生（prep 采集、auction 当日竞价），
算法结果可重算，均独立存库，避免与可重算的扫描产物同生命周期。

表职责：
  - ``prep_assets``       盘后复盘原始资产（UNIQUE(trade_date, asset_type)）
  - ``auction_assets``    集合竞价原始盘口明细（UNIQUE(trade_date, code)）
  - ``prep_snapshots``    盘后复盘计算结果（UNIQUE(trade_date, source)）
  - ``auction_snapshots`` 竞价强度计算结果（UNIQUE(trade_date, code, source)），
                          本期**仅建表、不接线**；写入契约见下。

设计要点（沿用原 prep.db 门面约定）：
  - 独立 ``_connect/_ensure_schema/_lock``（WAL + busy_timeout + 每操作独立连接 +
    操作内建 schema），不做 import 时建库——测试 patch ``assets_db._connect`` 即可完全隔离。
  - 原始资产表 UPSERT 幂等；prep 采集段经 ``save_prep_assets`` 单事务批量写入
    （含 collection_report），ON CONFLICT 同时刷新 created_at（语义=最后采集时间）。
  - JSON 序列化统一 ``allow_nan=False`` + ``sort_keys=True``（重算确定性 + 尽早暴露 NaN）。

``auction_snapshots.analysis_json`` 契约（下一里程碑写入者必须遵循，不得另立字段）::

    {
      "version": "1",              # 结果 schema 版本
      "code": "600172",
      "is_weak": false,
      "strength": 72.5,
      "direction": "bull",         # bull / bear / neutral
      "level": "强",                # 强 / 中 / 弱
      "dims": {
        "direction": {"dim": "direction", "score": 70.0, "weight": 0.25, "details": {}},
        "supply":    {"dim": "supply",    "score": 80.0, "weight": 0.30, "details": {}},
        "vol_price": {"dim": "vol_price", "score": 65.0, "weight": 0.25, "details": {}},
        "tail_grab": {"dim": "tail_grab", "score": 60.0, "weight": 0.20, "details": {}}
      },
      "reject_reason": null,
      "unverified": true,
      "rank": 1
    }

即 ``AuctionVerdict``（dragon_quant/auction/types.py）的序列化；``source`` 列记录
算法/口径血缘（默认 ``v2``）。
"""

import json
import sqlite3
import threading
from typing import Optional

from dragon_quant.storage.paths import ASSETS_DB_PATH

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

CREATE TABLE IF NOT EXISTS prep_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date   TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'v2',
    result_json  TEXT NOT NULL,
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(trade_date, source)
);

CREATE TABLE IF NOT EXISTS auction_assets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date   TEXT NOT NULL,
    code         TEXT NOT NULL,
    name         TEXT,
    items_json   TEXT NOT NULL,
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(trade_date, code)
);

-- 本期仅建表，不提供读写 API（算法接线见模块 docstring 的契约）
CREATE TABLE IF NOT EXISTS auction_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT NOT NULL,
    code          TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'v2',
    analysis_json TEXT NOT NULL,
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(trade_date, code, source)
);
"""

# 形状探针：防止把旧 dragon.db / 旧形状库手工放到 assets.db 路径后静默写错列。
_REQUIRED_COLUMNS = {
    "prep_assets": ("trade_date", "asset_type", "payload_json"),
    "prep_snapshots": ("trade_date", "source", "result_json"),
    "auction_assets": ("trade_date", "code", "items_json"),
    "auction_snapshots": ("trade_date", "code", "source", "analysis_json"),
}


class AssetsDbSchemaError(RuntimeError):
    """assets.db 已有同名表但列形状不兼容（通常是误放了旧库文件）。"""


def _connect(create: bool = True) -> Optional[sqlite3.Connection]:
    """打开 assets.db。

    ``create=False`` 用于只读路径：库文件不存在时返回 None，绝不因查询而建库
    （避免 CLI 查询/测试在数据目录落下空库）。
    """
    if not create and not ASSETS_DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(ASSETS_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    # 跨进程（prep/auction 定时任务重叠）可能出现短暂 BUSY，交由 SQLite 等待吸收。
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _assert_schema_shape(conn: sqlite3.Connection):
    for table, required in _REQUIRED_COLUMNS.items():
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing = [c for c in required if c not in cols]
        if missing:
            raise AssetsDbSchemaError(
                f"assets.db schema mismatch: table {table} missing columns {missing}. "
                "若该文件是从旧 dragon.db/prep.db 手工复制而来，请改用正确路径或删除后重建。"
            )


def _ensure_schema(conn: sqlite3.Connection):
    conn.executescript(BASE_SCHEMA)
    _assert_schema_shape(conn)
    conn.commit()


def _dumps(payload) -> str:
    """统一序列化：NaN 早暴露、键序稳定（--recompute 字节级确定性）。"""
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)


# ─── prep 资产 / 快照 ──────────────────────────────────────────────────────


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
    conn = _connect(create=False)
    if conn is None:
        return None
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
    conn = _connect(create=False)
    if conn is None:
        return {}
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
    conn = _connect(create=False)
    if conn is None:
        return None
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
    conn = _connect(create=False)
    if conn is None:
        return False
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT 1 FROM prep_snapshots WHERE trade_date = ? AND source = ?",
            (trade_date, source),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# ─── auction 原始资产 ──────────────────────────────────────────────────────


def save_auction_asset(trade_date: str, code: str, name: str,
                       items: list[dict]) -> None:
    """UPSERT 竞价原始明细。items 为 [{'time','price','matched','unmatched'}, ...]。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO auction_assets(trade_date, code, name, items_json) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(trade_date, code) DO UPDATE SET "
                "name=excluded.name, items_json=excluded.items_json, "
                "created_at=datetime('now','localtime')",
                (trade_date, code, name, _dumps(items)),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def get_auction_asset(trade_date: str, code: str) -> Optional[dict]:
    """读取某日某股竞价原始明细，无记录返回 None。"""
    conn = _connect(create=False)
    if conn is None:
        return None
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT trade_date, code, name, items_json FROM auction_assets "
            "WHERE trade_date = ? AND code = ?",
            (trade_date, code),
        ).fetchone()
        if not row:
            return None
        return {
            "trade_date": row[0],
            "code": row[1],
            "name": row[2],
            "items": json.loads(row[3]) if row[3] else [],
        }
    finally:
        conn.close()


def list_auction_assets(trade_date: str) -> list[dict]:
    """列出某交易日全部竞价原始明细，按 code 排序。"""
    conn = _connect(create=False)
    if conn is None:
        return []
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT trade_date, code, name, items_json FROM auction_assets "
            "WHERE trade_date = ? ORDER BY code",
            (trade_date,),
        ).fetchall()
        return [
            {
                "trade_date": r[0],
                "code": r[1],
                "name": r[2],
                "items": json.loads(r[3]) if r[3] else [],
            }
            for r in rows
        ]
    finally:
        conn.close()
