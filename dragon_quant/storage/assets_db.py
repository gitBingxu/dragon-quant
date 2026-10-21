"""数据资产独立库（assets.db）持久化门面。

与 dragon.db 物理隔离：原始数据资产不可再生（prep 采集、auction 当日竞价），
算法结果可重算，均独立存库，避免与可重算的扫描产物同生命周期。

表职责：
  - ``prep_assets``       盘后复盘原始资产（UNIQUE(trade_date, asset_type)）
  - ``auction_assets``    集合竞价原始资产（UNIQUE(trade_date, code)）：
                          ``items_json`` 盘口明细 + ``quote_json`` 行情快照
  - ``prep_snapshots``    盘后复盘计算结果（UNIQUE(trade_date, source)）
  - ``auction_snapshots`` 竞价强度计算结果（UNIQUE(trade_date, code, source)），
                          由 ``auction`` 采集/``auction --recompute`` 写入。

设计要点（沿用原 prep.db 门面约定）：
  - 独立 ``_connect/_ensure_schema/_lock``（WAL + busy_timeout + 每操作独立连接 +
    操作内建 schema），不做 import 时建库——测试 patch ``assets_db._connect`` 即可完全隔离。
  - 原始资产表 UPSERT 幂等；prep 采集段经 ``save_prep_assets`` 单事务批量写入
    （含 collection_report），ON CONFLICT 同时刷新 created_at（语义=最后采集时间）。
  - JSON 序列化统一 ``allow_nan=False`` + ``sort_keys=True``（重算确定性 + 尽早暴露 NaN）。

schema 升级（``_COLUMN_MIGRATIONS``）：
  - 旧库缺 ``auction_assets.quote_json`` 时，**写路径**首次访问自动
    ``ALTER TABLE ADD COLUMN``（幂等；跨进程 duplicate column 竞态会复查后视为成功）。
  - **读路径不执行列迁移（不 ALTER 既有表）**：探测列是否存在，缺列时以 ``NULL``
    读取（``quote=None``），因此在只读文件系统/并发读时不会因补列失败而报错。
    （``BASE_SCHEMA`` 的 ``CREATE TABLE IF NOT EXISTS`` 与 WAL PRAGMA 仍会执行。）
  - ``_REQUIRED_COLUMNS`` 只含核心列（wrong-DB 识别）；写路径迁移后再用
    ``_WRITE_REQUIRED_COLUMNS``（含 ``quote_json``）断言补列完整性。

``auction_assets.quote_json`` 契约：``Quote``（dragon_quant/models/types.py）的
JSON 序列化（snake_case 全字段），非有限浮点清洗为 ``null``；无行情时为 ``NULL``。
它是「快照 = 原始资产纯函数」的输入之一：``--recompute`` 只读该列，绝不触网。

``auction_snapshots.analysis_json`` 契约（写入者必须遵循，不得另立字段）::

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
    quote_json   TEXT,
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(trade_date, code)
);

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

# 形状探针（读路径 / wrong-DB 识别）：只校验 NOT NULL 核心列，旧库缺 quote_json 也能读。
_REQUIRED_COLUMNS = {
    "prep_assets": ("trade_date", "asset_type", "payload_json"),
    "prep_snapshots": ("trade_date", "source", "result_json"),
    "auction_assets": ("trade_date", "code", "items_json"),
    "auction_snapshots": ("trade_date", "code", "source", "analysis_json"),
}

# 写路径探针：迁移后必须包含可空新增列，补列失败立即以 AssetsDbSchemaError 暴露。
_WRITE_REQUIRED_COLUMNS = {
    **_REQUIRED_COLUMNS,
    "auction_assets": ("trade_date", "code", "items_json", "quote_json"),
}

# 表驱动列迁移：新增可空列 = 注册一行；只在写路径执行。
_COLUMN_MIGRATIONS = {
    "auction_assets": (("quote_json", "TEXT"),),
}


class AssetsDbSchemaError(RuntimeError):
    """assets.db 已有同名表但列形状不兼容（通常是误放了旧库文件）。"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


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


def _assert_schema_shape(conn: sqlite3.Connection, full: bool = False):
    required_map = _WRITE_REQUIRED_COLUMNS if full else _REQUIRED_COLUMNS
    for table, required in required_map.items():
        missing = [c for c in required if c not in _columns(conn, table)]
        if missing:
            raise AssetsDbSchemaError(
                f"assets.db schema mismatch: table {table} missing columns {missing}. "
                "若该文件是从旧 dragon.db/prep.db 手工复制而来，请改用正确路径或删除后重建。"
            )


def _apply_column_migrations(conn: sqlite3.Connection):
    """幂等补列；跨进程竞态的 duplicate column 复查后视为成功。"""
    for table, columns in _COLUMN_MIGRATIONS.items():
        for name, decl in columns:
            if name in _columns(conn, table):
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            except sqlite3.OperationalError:
                if name not in _columns(conn, table):
                    raise


def _ensure_schema(conn: sqlite3.Connection, migrate: bool = True):
    """建表 + 可选迁移 + 形状断言。

    ``migrate=True`` 仅用于写路径：旧库在此补列，随后按写入契约做全量断言。
    ``migrate=False`` 用于读路径：不执行列迁移（不 ALTER 既有表；建表/WAL PRAGMA
    仍执行），缺列由调用方按 NULL 兼容读取。
    """
    conn.executescript(BASE_SCHEMA)
    # 先做核心形状断言：误放的旧 dragon.db/prep.db 在此直接拦截，
    # 绝不先对错误库执行 ALTER。合法旧库通过后再补列并做 full 断言。
    _assert_schema_shape(conn, full=False)
    if migrate:
        _apply_column_migrations(conn)
        _assert_schema_shape(conn, full=True)
    conn.commit()


def _dumps(payload) -> str:
    """统一序列化：NaN 早暴露、键序稳定（--recompute 字节级确定性）。"""
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)


# ─── prep 资产 / 快照 ──────────────────────────────────────────────────────


def _upsert_prep_asset(conn: sqlite3.Connection, trade_date: str,
                       asset_type: str, payload) -> None:
    conn.execute(
        "INSERT INTO prep_assets(trade_date, asset_type, payload_json) VALUES (?, ?, ?) "
        "ON CONFLICT(trade_date, asset_type) DO UPDATE SET "
        "payload_json=excluded.payload_json, "
        "created_at=datetime('now','localtime')",
        (trade_date, asset_type, _dumps(payload)),
    )


def save_prep_assets(trade_date: str, payloads: dict[str, dict],
                     report: Optional[dict] = None) -> None:
    """单事务（BEGIN IMMEDIATE）UPSERT 资产 + 逐条合并 collection_report。

    payloads: {asset_type: payload}（可为部分资产）。
    report:   {asset_type: entry}，**仅本次实际执行的 collector 条目**。非空时在同一事务内
              先读存量 collection_report 再按键合并（未出现的键沿用存量）；空/None → 不触碰
              report 行（整日零采集时原样保留，语义 = 该日期资产当前最佳状态）。
              存量 report 非 dict（畸形 JSON/手工改库）→ 视为空 dict 重建，并写
              ``report_reset="malformed"`` 审计键。
    payloads 与 report 皆空 → 直接 no-op（不开启事务）。
    任一序列化/写入失败 → 整体回滚（不留「部分新部分旧」的混合状态）。
    """
    if not payloads and not report:
        return
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            for asset_type, payload in payloads.items():
                _upsert_prep_asset(conn, trade_date, asset_type, payload)
            if report:
                row = conn.execute(
                    "SELECT payload_json FROM prep_assets "
                    "WHERE trade_date = ? AND asset_type = 'collection_report'",
                    (trade_date,),
                ).fetchone()
                merged: dict = {}
                reset = False
                if row:
                    try:
                        old = json.loads(row[0])
                    except (json.JSONDecodeError, TypeError, ValueError):
                        old = None
                    if isinstance(old, dict):
                        merged = old
                    else:
                        reset = True
                merged.update(report)
                if reset:
                    merged["report_reset"] = "malformed"
                _upsert_prep_asset(conn, trade_date, "collection_report", merged)
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
        _ensure_schema(conn, migrate=False)
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
        _ensure_schema(conn, migrate=False)
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
        _ensure_schema(conn, migrate=False)
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
        _ensure_schema(conn, migrate=False)
        row = conn.execute(
            "SELECT 1 FROM prep_snapshots WHERE trade_date = ? AND source = ?",
            (trade_date, source),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# ─── auction 原始资产 ──────────────────────────────────────────────────────


def _auction_quote_expr(conn: sqlite3.Connection) -> str:
    """读路径兼容：旧库无 quote_json 时以 NULL 读取，不触发 schema 写。"""
    return "quote_json" if "quote_json" in _columns(conn, "auction_assets") else "NULL"


def _load_json(text, default):
    """逐行解析 JSON；损坏时返回 (default, error)，绝不把整批读路径打断。"""
    if not text:
        return default, None
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return default, f"{type(exc).__name__}: {exc}"


def save_auction_asset(trade_date: str, code: str, name: str,
                       items: list[dict],
                       quote: Optional[dict] = None) -> None:
    """UPSERT 竞价原始资产（items + 行情快照）。

    items: [{'time','price','matched','unmatched'}, ...]
    quote: Quote 的 JSON 化 dict；无行情时 None。
    行情不可再生：本次 quote=None 时保留该行已有 quote_json（COALESCE），
    避免同日重采的瞬时行情故障抹掉上次成功快照。
    """
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)   # 写路径：旧库在此补 quote_json 列
            conn.execute(
                "INSERT INTO auction_assets(trade_date, code, name, items_json, quote_json) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(trade_date, code) DO UPDATE SET "
                "name=excluded.name, items_json=excluded.items_json, "
                "quote_json=COALESCE(excluded.quote_json, auction_assets.quote_json), "
                "created_at=datetime('now','localtime')",
                (trade_date, code, name, _dumps(items),
                 _dumps(quote) if quote is not None else None),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def get_auction_asset(trade_date: str, code: str) -> Optional[dict]:
    """读取某日某股竞价原始资产，无记录返回 None；无行情时 quote=None。

    ``items_error`` / ``quote_error`` 标记该列 JSON 损坏（逐行隔离，不抛异常）。
    """
    conn = _connect(create=False)
    if conn is None:
        return None
    try:
        _ensure_schema(conn, migrate=False)
        quote_expr = _auction_quote_expr(conn)
        row = conn.execute(
            f"SELECT trade_date, code, name, items_json, {quote_expr} FROM auction_assets "
            "WHERE trade_date = ? AND code = ?",
            (trade_date, code),
        ).fetchone()
        if not row:
            return None
        items, items_error = _load_json(row[3], [])
        quote, quote_error = _load_json(row[4], None)
        return {
            "trade_date": row[0],
            "code": row[1],
            "name": row[2],
            "items": items,
            "quote": quote,
            "items_error": items_error,
            "quote_error": quote_error,
        }
    finally:
        conn.close()


def list_auction_assets(trade_date: str) -> list[dict]:
    """列出某交易日全部竞价原始资产，按 code 排序；无行情时 quote=None。

    单行 JSON 损坏不会中断整批：返回 ``items_error`` / ``quote_error`` 交由调用方隔离。
    """
    conn = _connect(create=False)
    if conn is None:
        return []
    try:
        _ensure_schema(conn, migrate=False)
        quote_expr = _auction_quote_expr(conn)
        rows = conn.execute(
            f"SELECT trade_date, code, name, items_json, {quote_expr} FROM auction_assets "
            "WHERE trade_date = ? ORDER BY code",
            (trade_date,),
        ).fetchall()
        result = []
        for r in rows:
            items, items_error = _load_json(r[3], [])
            quote, quote_error = _load_json(r[4], None)
            result.append({
                "trade_date": r[0],
                "code": r[1],
                "name": r[2],
                "items": items,
                "quote": quote,
                "items_error": items_error,
                "quote_error": quote_error,
            })
        return result
    finally:
        conn.close()


# ─── auction 强度快照 ──────────────────────────────────────────────────────


def replace_auction_snapshots(trade_date: str, payloads: dict[str, dict],
                              source: str = "v2") -> int:
    """整日原子替换竞价强度快照，返回写入行数。

    payloads 为空 → no-op（不 DELETE）：避免行情/评分全面失败时清空既有快照。
    payloads 非空 → 单事务删除该日该 source 旧行 + 批量插入，保证
    「快照 code 集合 = 本次可评分资产集合」，同日重跑幂等。
    """
    if not payloads:
        return 0
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute("BEGIN")
            conn.execute(
                "DELETE FROM auction_snapshots WHERE trade_date = ? AND source = ?",
                (trade_date, source),
            )
            for code in sorted(payloads):
                conn.execute(
                    "INSERT INTO auction_snapshots(trade_date, code, source, analysis_json) "
                    "VALUES (?, ?, ?, ?)",
                    (trade_date, code, source, _dumps(payloads[code])),
                )
            conn.commit()
            return len(payloads)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
