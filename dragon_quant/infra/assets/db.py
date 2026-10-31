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
  - ``dragons``           ``dragon.db.dragons_v2`` 的全量镜像（列 schema 一致），
                          由存储门面锁外 best-effort 双写 + ``assets backfill-dragons``
                          回填；权威仍是 dragon.db，本表供 prep / review-account /
                          buy / sell / auction 的候选池读取。
  - ``factor_values`` / ``factor_registry`` / ``factor_build_runs`` /
    ``factor_sector_snapshots`` / ``factor_model_snapshots``
                          因子层五表（DDL 亦在本库 ``schema.py``），**SQL 仓储归**
                          ``infra/factor/sql.py``，领域门面归 ``infra/factor/storage.py``；
                          本模块只负责连接/建表/事务原语，不含因子语义。
  - ``fin_observed_snapshots`` / ``fin_report_files`` / ``fin_report_records`` /
    ``fin_field_calibrations`` / ``fin_xdxr_records``
                          财务原始链路五表，**SQL 仓储归**
                          ``infra/factor/finance_store.py``；同样只由 infra 层直接访问。

> 依赖方向：``infra.assets`` 是叶子，**不 import** ``infra.factor``；因子/财务
> SQL 反向依赖本模块的 ``_connect`` / ``_ensure_schema`` / ``_lock``。

设计要点（沿用原 prep.db 门面约定）：
  - 独立 ``_connect/_ensure_schema/_lock``（WAL + busy_timeout + 每操作独立连接 +
    操作内建 schema），不做 import 时建库——测试 patch ``infra.assets.db._connect``
    即可完全隔离。
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

即 ``AuctionVerdict``（review_account/auction/types.py）的序列化；``source`` 列记录
算法/口径血缘（默认 ``v2``）。
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Literal, Optional, overload

from dragon_quant.infra.assets.schema import (
    BASE_SCHEMA,
    _COLUMN_MIGRATIONS,
    _REQUIRED_COLUMNS,
    _WRITE_REQUIRED_COLUMNS,
)
from dragon_quant.infra.storage.paths import ASSETS_DB_PATH

_lock = threading.Lock()


class AssetsDbSchemaError(RuntimeError):
    """assets.db 已有同名表但列形状不兼容（通常是误放了旧库文件）。"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


@overload
def _connect(create: Literal[True] = True, path=None) -> sqlite3.Connection: ...
@overload
def _connect(create: Literal[False], path=None) -> Optional[sqlite3.Connection]: ...
@overload
def _connect(create: bool = True, path=None) -> Optional[sqlite3.Connection]: ...
def _connect(create: bool = True, path=None) -> Optional[sqlite3.Connection]:
    """打开 assets.db。

    ``create=False`` 用于只读路径：库文件不存在时返回 None，绝不因查询而建库
    （避免 CLI 查询/测试在数据目录落下空库）。

    ``path`` 为 None 时才读模块级 ``ASSETS_DB_PATH``；显式传入用于
    ``assets pull --target-dir`` 与测试隔离（路径在调用期解析）。
    """
    db_path = ASSETS_DB_PATH if path is None else path
    if not create and not Path(db_path).exists():
        return None
    if create:
        if path is None:
            # 默认库：首次真实写访问才建数据根 + 子目录（B1 惰性初始化）。
            from dragon_quant.infra.storage.paths import ensure_data_dirs

            ensure_data_dirs()
        else:
            # 显式路径（--target-dir / 测试隔离）：只建目标父目录，不碰主数据根。
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    # 跨进程（prep/auction 定时任务重叠）可能出现短暂 BUSY，交由 SQLite 等待吸收。
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _connect_readonly(path=None) -> Optional[sqlite3.Connection]:
    """纯只读连接（``mode=ro``）：不执行 WAL PRAGMA、不建库/建表/建 sidecar。

    供 dry-run / 审计计数 / 盘前只读上下文使用；写路径仍走 :func:`_connect`。
    缺表 / 锁等待由调用方（或 Adapter）捕获 ``sqlite3.Error``，绝不自动建表。

    WAL 回退：SQLite 只有在 ``-shm`` 存在时才能用 ``mode=ro`` 打开 WAL 库；
    干净的库（已 checkpoint、sidecar 被移除或为 0 字节）会报
    ``unable to open database file``。此时若 ``-wal`` 不存在或为 0 字节，
    用 ``immutable=1`` 只读打开（没有未 checkpoint 的事务可丢，且不建 sidecar）；
    若 ``-wal`` 非空则无法安全只读，原样抛错交由 Adapter 降级。
    """

    db_path = ASSETS_DB_PATH if path is None else path
    if not Path(db_path).exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        conn = None
    if conn is not None:
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("SELECT 1")      # sqlite3 连接是惰性的：强制真实打开
            return conn
        except sqlite3.OperationalError:
            conn.close()
    wal = Path(f"{db_path}-wal")
    if wal.exists() and wal.stat().st_size:
        raise sqlite3.OperationalError("unable to open database file")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("SELECT 1")
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


@overload
def get_prep_snapshot(trade_date: str, source: str = "v2",
                      with_meta: Literal[False] = False) -> Optional[dict]: ...
@overload
def get_prep_snapshot(trade_date: str, source: str = "v2",
                      with_meta: Literal[True] = True) -> tuple[Optional[dict], dict]: ...
def get_prep_snapshot(trade_date: str, source: str = "v2",
                      with_meta: bool = False):
    """读取某复盘日的快照，无记录返回 None。

    ``with_meta=True`` 时返回 ``(result | None, {"created_at": ...})``；默认
    ``False`` 保持原返回值，既有调用方零影响（因子层血缘读取用，方案 §15.2）。
    """
    conn = _connect(create=False)
    if conn is None:
        return (None, {"created_at": None}) if with_meta else None
    try:
        _ensure_schema(conn, migrate=False)
        row = conn.execute(
            "SELECT result_json, created_at FROM prep_snapshots "
            "WHERE trade_date = ? AND source = ?",
            (trade_date, source),
        ).fetchone()
        if row is None:
            return (None, {"created_at": None}) if with_meta else None
        payload = json.loads(row[0])
        if with_meta:
            return payload, {"created_at": row[1]}
        return payload
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


def get_previous_prep_snapshot(before_date: str, source: str = "v2", *,
                               readonly: bool = False
                               ) -> tuple[Optional[dict], dict]:
    """读取**严格早于** ``before_date`` 的最近一盘前快照（prev 注入，只读）。

    返回 ``(payload | None, meta)``，``meta = {"trade_date", "created_at", "error"}``：

    - 库不存在 / 无更早快照 → ``(None, {... None})``（首日无 prev 是合法状态）；
    - JSON 损坏 → ``(None, {"trade_date": 该日, "error": "..."})``，调用方据此标
      ``prev_snapshot_corrupt``；
    - ``trade_date < ?`` 严格小于，绝不返回当日快照（避免自己和自己比）。

    ``readonly=True`` 走 :func:`_connect_readonly`（``mode=ro``、零 DDL、零
    WAL/shm sidecar）；缺表 / 锁等待等 ``sqlite3.Error`` 由调用方捕获，绝不建表。
    默认 ``False`` 保持既有行为（写路径连接 + 读路径 schema 兼容）。
    """

    empty_meta = {"trade_date": None, "created_at": None, "error": None}
    if readonly:
        conn = _connect_readonly()
        if conn is None:
            return None, dict(empty_meta)
        try:
            row = conn.execute(
                "SELECT result_json, trade_date, created_at FROM prep_snapshots "
                "WHERE trade_date < ? AND source = ? "
                "ORDER BY trade_date DESC LIMIT 1",
                (before_date, source),
            ).fetchone()
        finally:
            conn.close()
    else:
        conn = _connect(create=False)
        if conn is None:
            return None, dict(empty_meta)
        try:
            _ensure_schema(conn, migrate=False)
            row = conn.execute(
                "SELECT result_json, trade_date, created_at FROM prep_snapshots "
                "WHERE trade_date < ? AND source = ? "
                "ORDER BY trade_date DESC LIMIT 1",
                (before_date, source),
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return None, dict(empty_meta)
    payload, error = _load_json(row[0], None)
    meta = {"trade_date": row[1], "created_at": row[2],
            "error": None if error is None else error}
    if error is not None or not isinstance(payload, dict):
        return None, meta
    return payload, meta


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


def list_auction_history(before_date: str, codes=None,
                         limit: int = 5) -> dict[str, dict]:
    """只读历史末点匹配量（供竞价量比），绝不建库 / 建表 / 写库。

    返回 ``{code: {"dates": [...], "last_matched_lots": [...]}}``：取
    ``trade_date < before_date`` 的**不同交易日**降序最多 ``limit`` 个；某日某
    code 无资产则不计入该 code 的 ``history_n``。损坏行逐行跳过。
    """
    conn = _connect(create=False)
    if conn is None:
        return {}
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='auction_assets'"
        ).fetchone()
        if not exists:
            return {}
        date_rows = conn.execute(
            "SELECT DISTINCT trade_date FROM auction_assets "
            "WHERE trade_date < ? ORDER BY trade_date DESC LIMIT ?",
            (before_date, int(limit)),
        ).fetchall()
        dates = [row[0] for row in date_rows]
        if not dates:
            return {}
        placeholders = ",".join("?" for _ in dates)
        params = list(dates)
        query = (f"SELECT trade_date, code, items_json FROM auction_assets "
                 f"WHERE trade_date IN ({placeholders})")
        wanted = {str(code) for code in (codes or []) if code}
        if wanted:
            query += " AND code IN (" + ",".join("?" for _ in wanted) + ")"
            params.extend(sorted(wanted))
        rows = conn.execute(query, params).fetchall()
        result: dict[str, dict] = {}
        order = {date: position for position, date in enumerate(dates)}
        collected: dict[str, list] = {}
        for trade_date, code, items_json in rows:
            items, error = _load_json(items_json, [])
            if error or not isinstance(items, list) or not items:
                continue
            last = items[-1]
            if not isinstance(last, dict):
                continue
            try:
                value = float(last.get("matched"))
            except (TypeError, ValueError):
                continue
            if value != value or not (value > 0):
                continue
            collected.setdefault(str(code), []).append((trade_date, value))
        for code, values in collected.items():
            values.sort(key=lambda pair: order.get(pair[0], 0))
            result[code] = {
                "dates": [pair[0] for pair in values],
                "last_matched_lots": [pair[1] for pair in values],
            }
        return result
    finally:
        conn.close()


# ─── auction 强度快照 ──────────────────────────────────────────────────────


def _snapshot_row(trade_date: str, code: str, analysis, error) -> dict:
    """把 analysis_json 摊平到行上，便于 `auction_state` 直接消费契约字段。"""
    row = {"trade_date": trade_date, "code": code}
    if isinstance(analysis, dict):
        row.update(analysis)
    else:
        row["error"] = "analysis_json 不是对象"
    if error:
        row["error"] = error
    return row


def list_auction_snapshots(trade_date: str, source: str = "v2",
                           with_meta: bool = False, *,
                           readonly: bool = False) -> list[dict]:
    """列出某日竞价强度快照，按 code 排序；逐行 JSON 损坏只标 ``error``，不中断整批。

    ``with_meta=True`` 时逐行追加 ``created_at`` 键（因子层血缘读取，方案 §15.2）；
    默认 ``False`` 返回形状保持不变。``readonly=True`` 走 ``_connect_readonly``
    （``mode=ro``、零 DDL、零 sidecar）；缺表等 ``sqlite3.Error`` 由调用方捕获。
    """
    if readonly:
        conn = _connect_readonly()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT trade_date, code, analysis_json, created_at FROM auction_snapshots "
                "WHERE trade_date = ? AND source = ? ORDER BY code",
                (trade_date, source),
            ).fetchall()
        finally:
            conn.close()
        return _auction_snapshot_rows(rows, with_meta)
    conn = _connect(create=False)
    if conn is None:
        return []
    try:
        _ensure_schema(conn, migrate=False)
        rows = conn.execute(
            "SELECT trade_date, code, analysis_json, created_at FROM auction_snapshots "
            "WHERE trade_date = ? AND source = ? ORDER BY code",
            (trade_date, source),
        ).fetchall()
    finally:
        conn.close()
    return _auction_snapshot_rows(rows, with_meta)


def _auction_snapshot_rows(rows, with_meta: bool) -> list[dict]:
    result = []
    for r in rows:
        analysis, error = _load_json(r[2], {})
        item = _snapshot_row(r[0], r[1], analysis, error)
        if with_meta:
            item["created_at"] = r[3]
        result.append(item)
    return result


def get_auction_snapshot(trade_date: str, code: str,
                         source: str = "v2") -> Optional[dict]:
    """读取单只竞价强度快照；无记录返回 None，损坏行返回带 ``error`` 的行。"""
    conn = _connect(create=False)
    if conn is None:
        return None
    try:
        _ensure_schema(conn, migrate=False)
        row = conn.execute(
            "SELECT trade_date, code, analysis_json FROM auction_snapshots "
            "WHERE trade_date = ? AND code = ? AND source = ?",
            (trade_date, code, source),
        ).fetchone()
        if not row:
            return None
        analysis, error = _load_json(row[2], {})
        return _snapshot_row(row[0], row[1], analysis, error)
    finally:
        conn.close()


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


# ─── 云端同步：导出 / 水位 / 恢复（M2） ────────────────────────────────────
#
# 这一组 API 只服务 assets_sync 的 Adapter：
#   - 导出与水位走只读事务（WAL 快照），绝不写库；
#   - 恢复走 BEGIN IMMEDIATE 单事务，逐来源独立（prep / auction 各自提交）；
#   - created_at 只作旁路传输：新插入行写入源端时间，已存在行保留本地时间；
#   - 所有函数支持可选 db_path（None → 调用期 ASSETS_DB_PATH），供
#     `assets pull --target-dir` 与测试隔离。
#
# 水位 token = "<max_created_at>|<row_count>|<total_bytes>"，对调用方是不透明字符串；
# 同时含行数与字节数，降低「同一秒内重采且内容等长」的漏检概率。


def _watermark_token(max_created, count, total_bytes) -> str:
    return f"{max_created or ''}|{int(count or 0)}|{int(total_bytes or 0)}"


def _row_list(conn: sqlite3.Connection, sql: str, params: tuple) -> list[tuple]:
    """在只读事务里取行，保证同一快照。"""
    conn.execute("BEGIN")
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.rollback()


def export_prep_assets(trade_date: str, *, db_path=None) -> list[dict]:
    """导出某日 prep 资产（含 created_at 与逐行解析错误标记）。"""
    conn = _connect(create=False, path=db_path)
    if conn is None:
        return []
    try:
        _ensure_schema(conn, migrate=False)
        rows = _row_list(
            conn,
            "SELECT trade_date, asset_type, payload_json, created_at "
            "FROM prep_assets WHERE trade_date = ? ORDER BY asset_type",
            (trade_date,),
        )
    finally:
        conn.close()
    result = []
    for trade_date_value, asset_type, payload_json, created_at in rows:
        payload, error = _load_json(payload_json, None)
        result.append({
            "trade_date": trade_date_value,
            "asset_type": asset_type,
            "payload": payload,
            "payload_error": error,
            "created_at": created_at,
        })
    return result


def export_auction_assets(trade_date: str, *, db_path=None) -> list[dict]:
    """导出某日竞价原始资产（items + quote + created_at，逐行错误隔离）。"""
    conn = _connect(create=False, path=db_path)
    if conn is None:
        return []
    try:
        _ensure_schema(conn, migrate=False)
        quote_expr = _auction_quote_expr(conn)
        rows = _row_list(
            conn,
            f"SELECT trade_date, code, name, items_json, {quote_expr}, created_at "
            "FROM auction_assets WHERE trade_date = ? ORDER BY code",
            (trade_date,),
        )
    finally:
        conn.close()
    result = []
    for trade_date_value, code, name, items_json, quote_json, created_at in rows:
        items, items_error = _load_json(items_json, [])
        quote, quote_error = _load_json(quote_json, None)
        result.append({
            "trade_date": trade_date_value,
            "code": code,
            "name": name,
            "items": items,
            "items_error": items_error,
            "quote": quote,
            "quote_error": quote_error,
            "created_at": created_at,
        })
    return result


def _list_dates(table: str, *, db_path=None) -> list[str]:
    conn = _connect(create=False, path=db_path)
    if conn is None:
        return []
    try:
        _ensure_schema(conn, migrate=False)
        rows = _row_list(
            conn,
            f"SELECT DISTINCT trade_date FROM {table} ORDER BY trade_date",
            (),
        )
    finally:
        conn.close()
    return [row[0] for row in rows]


def list_prep_asset_dates(*, db_path=None) -> list[str]:
    return _list_dates("prep_assets", db_path=db_path)


def list_auction_asset_dates(*, db_path=None) -> list[str]:
    return _list_dates("auction_assets", db_path=db_path)


def _list_snapshot_dates(table: str, *, source: str, date_from=None,
                         date_to=None, db_path=None) -> list[str]:
    """区间内某来源有快照的日期（升序去重）；只读、不建库、无 DDL。

    读路径 ``_ensure_schema(migrate=False)`` 不补列；库文件不存在时返回 ``[]``。
    """
    conn = _connect(create=False, path=db_path)
    if conn is None:
        return []
    try:
        _ensure_schema(conn, migrate=False)
        clauses = ["source = ?"]
        params: list = [source]
        if date_from is not None:
            clauses.append("trade_date >= ?")
            params.append(date_from)
        if date_to is not None:
            clauses.append("trade_date <= ?")
            params.append(date_to)
        rows = _row_list(
            conn,
            f"SELECT DISTINCT trade_date FROM {table} WHERE {' AND '.join(clauses)} "
            "ORDER BY trade_date",
            tuple(params),
        )
    finally:
        conn.close()
    return [row[0] for row in rows]


def list_prep_snapshot_dates(date_from=None, date_to=None, *, source: str = "v2",
                             db_path=None) -> list[str]:
    """``prep_snapshots`` 区间内快照日期（决策回测只读枚举）。"""
    return _list_snapshot_dates("prep_snapshots", source=source,
                                date_from=date_from, date_to=date_to,
                                db_path=db_path)


def list_auction_snapshot_dates(date_from=None, date_to=None, *,
                                source: str = "v2", db_path=None) -> list[str]:
    """``auction_snapshots`` 区间内快照日期（决策回测只读枚举）。"""
    return _list_snapshot_dates("auction_snapshots", source=source,
                                date_from=date_from, date_to=date_to,
                                db_path=db_path)


def _watermarks(table: str, size_expr: str, *, db_path=None) -> dict[str, str]:
    conn = _connect(create=False, path=db_path)
    if conn is None:
        return {}
    try:
        _ensure_schema(conn, migrate=False)
        rows = _row_list(
            conn,
            f"SELECT trade_date, MAX(created_at), COUNT(*), COALESCE(SUM({size_expr}), 0) "
            f"FROM {table} GROUP BY trade_date",
            (),
        )
    finally:
        conn.close()
    return {row[0]: _watermark_token(row[1], row[2], row[3]) for row in rows}


def prep_watermarks(*, db_path=None) -> dict[str, str]:
    return _watermarks("prep_assets", "LENGTH(payload_json)", db_path=db_path)


def auction_watermarks(*, db_path=None) -> dict[str, str]:
    return _watermarks(
        "auction_assets",
        "LENGTH(items_json) + LENGTH(COALESCE(quote_json, ''))",
        db_path=db_path,
    )


def _same_json(left_text, right_value) -> bool:
    """比较 DB 文本与解析值：按规范化 JSON 语义相等（不做文本逐字节断言）。"""
    if left_text is None:
        return right_value is None
    try:
        return _dumps(json.loads(left_text)) == _dumps(right_value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False


def restore_prep_assets(trade_date: str, rows: list[dict], *,
                        force: bool = False, db_path=None) -> dict:
    """恢复 prep 资产。

    - 新插入行写入源端 created_at（AC3）；
    - 已存在行：内容相同 skipped、不同未 force conflicts、force 只更新 payload_json，
      **保留本地 created_at**（本地权威）。
    """
    result = {"restored": 0, "skipped": 0, "conflicts": [], "failed": []}
    with _lock:
        conn = _connect(path=db_path)
        try:
            _ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            for row in rows:
                asset_type = row.get("asset_type")
                payload = row.get("payload")
                if not asset_type or payload is None:
                    result["failed"].append({
                        "key": f"prep:{asset_type}", "reason": "payload 缺失或损坏"})
                    continue
                new_text = _dumps(payload)
                existing = conn.execute(
                    "SELECT payload_json FROM prep_assets "
                    "WHERE trade_date = ? AND asset_type = ?",
                    (trade_date, asset_type),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO prep_assets(trade_date, asset_type, payload_json, created_at) "
                        "VALUES (?, ?, ?, COALESCE(NULLIF(?, ''), datetime('now','localtime')))",
                        (trade_date, asset_type, new_text, row.get("created_at") or ""),
                    )
                    result["restored"] += 1
                elif _same_json(existing[0], payload):
                    result["skipped"] += 1
                elif not force:
                    result["conflicts"].append({
                        "key": f"prep:{asset_type}", "reason": "content differs"})
                else:
                    conn.execute(
                        "UPDATE prep_assets SET payload_json = ? "
                        "WHERE trade_date = ? AND asset_type = ?",
                        (new_text, trade_date, asset_type),
                    )
                    result["restored"] += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return result


def restore_auction_assets(trade_date: str, rows: list[dict], *,
                           force: bool = False, db_path=None) -> dict:
    """恢复竞价原始资产（逐 code）。

    quote 合并语义与写入侧一致：``quote_json = COALESCE(远端, 本地)``，
    远端 quote 为 None 时即使 force 也不得抹掉本地不可再生行情。
    """
    result = {"restored": 0, "skipped": 0, "conflicts": [], "failed": []}
    with _lock:
        conn = _connect(path=db_path)
        try:
            _ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            for row in rows:
                code = row.get("code")
                items = row.get("items")
                if not code or items is None:
                    result["failed"].append({
                        "key": f"auction:{code}", "reason": "items 缺失或损坏"})
                    continue
                name = row.get("name") or ""
                quote = row.get("quote")
                quote_text = _dumps(quote) if quote is not None else None
                existing = conn.execute(
                    "SELECT name, items_json, quote_json FROM auction_assets "
                    "WHERE trade_date = ? AND code = ?",
                    (trade_date, code),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO auction_assets(trade_date, code, name, items_json, quote_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?, COALESCE(NULLIF(?, ''), datetime('now','localtime')))",
                        (trade_date, code, name, _dumps(items), quote_text,
                         row.get("created_at") or ""),
                    )
                    result["restored"] += 1
                    continue
                same = (
                    (existing[0] or "") == name
                    and _same_json(existing[1], items)
                    and _same_json(existing[2], quote)
                )
                if same:
                    result["skipped"] += 1
                elif not force:
                    result["conflicts"].append({
                        "key": f"auction:{code}", "reason": "content differs"})
                else:
                    conn.execute(
                        "UPDATE auction_assets SET name = ?, items_json = ?, "
                        "quote_json = COALESCE(?, quote_json) "
                        "WHERE trade_date = ? AND code = ?",
                        (name, _dumps(items), quote_text, trade_date, code),
                    )
                    result["restored"] += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return result
