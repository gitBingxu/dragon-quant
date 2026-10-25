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
from pathlib import Path
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

-- ─── dragons 全量镜像（dragon.db.dragons_v2 → assets.db）─────────────────────
-- 列 schema 与 dragons_v2 **完全一致**（含 id / source），同一行可直接对拷；
-- 权威仍是 dragon.db，镜像 best-effort 写入、可经 `assets backfill-dragons` 自愈。
-- id 为本库自增（与源库不保证相等），created_at 为本库写入时间，两者均不参与
-- 一致性判定、不进入 assets_sync 语义列（见 assets_sync/tables.py）。
CREATE TABLE IF NOT EXISTS dragons (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date            TEXT NOT NULL,
    code                  TEXT NOT NULL,
    name                  TEXT,
    scan_id               TEXT,
    rank                  INTEGER,
    composite_score       REAL,
    board_count           INTEGER,
    open_px               REAL,
    close_px              REAL,
    high_px               REAL,
    low_px                REAL,
    pct                   REAL,
    turnover_rate         REAL,
    amount                REAL,
    market_cap            REAL,
    concepts_json         TEXT,
    report_text           TEXT,
    is_true_dragon        INTEGER,
    version               TEXT DEFAULT '',
    created_at            TEXT DEFAULT (datetime('now','localtime')),
    buy_date              TEXT,
    buy_price             REAL,
    max_return_5d         REAL,
    max_drawdown_5d       REAL,
    max_return_hold_days  INTEGER,
    review_status         TEXT DEFAULT 'pending',
    source                TEXT DEFAULT 'v2',
    UNIQUE(trade_date, code)
);

CREATE INDEX IF NOT EXISTS idx_assets_dragons_date   ON dragons(trade_date);
CREATE INDEX IF NOT EXISTS idx_assets_dragons_code   ON dragons(code);
CREATE INDEX IF NOT EXISTS idx_assets_dragons_review ON dragons(review_status, trade_date);

-- ─── 因子层（factors/，P0）──────────────────────────────────────────────
-- 权威源：一个交易日的每只标的每个因子一行；缺失 = 无行（绝不写 0 / 中性分）。
CREATE TABLE IF NOT EXISTS factor_values (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date       TEXT NOT NULL,
    code             TEXT NOT NULL,
    factor_name      TEXT NOT NULL,
    factor_version   TEXT NOT NULL,
    value            REAL NOT NULL,
    available_at     TEXT NOT NULL,
    source           TEXT NOT NULL,
    source_scan_id   TEXT,                      -- 血缘：canonical scan id（source=scan 时）
    source_meta_json TEXT,                      -- 所有 source 都写 frame.meta 诊断血缘
                                                -- （覆盖率/样本数/跳过计数；fin 额外含
                                                --  period/file_md5/pit_basis）
    computed_at      TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(trade_date, code, factor_name, factor_version)
);

CREATE INDEX IF NOT EXISTS idx_factor_values_date_name
    ON factor_values(trade_date, factor_name, factor_version);
CREATE INDEX IF NOT EXISTS idx_factor_values_code_date
    ON factor_values(code, trade_date);

-- 元数据快照：同一 (name, version) 语义不可变；版本升级 = 新行。
CREATE TABLE IF NOT EXISTS factor_registry (
    factor_name     TEXT NOT NULL,
    factor_version  TEXT NOT NULL,
    label           TEXT,
    frequency       TEXT NOT NULL,
    source          TEXT NOT NULL,
    depends_on_json TEXT,
    lookback        INTEGER DEFAULT 0,
    direction       TEXT NOT NULL,
    unit            TEXT,
    available_phase TEXT NOT NULL,
    pit_rule        TEXT NOT NULL,
    scope_json      TEXT,
    params_json     TEXT,
    default_enabled INTEGER DEFAULT 0,
    valid_from      TEXT,
    deprecated_at   TEXT,
    owner           TEXT,
    description     TEXT,
    spec_hash       TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY(factor_name, factor_version)
);

-- 模型快照：可复现一次决策所需的全部确定性输入（P2 写入方）。
CREATE TABLE IF NOT EXISTS factor_model_snapshots (
    model_name           TEXT NOT NULL,
    model_version        TEXT NOT NULL,
    params_json          TEXT NOT NULL,
    factor_versions_json TEXT NOT NULL,
    created_at           TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY(model_name, model_version)
);

-- build 审计：单因子失败不中断，逐因子状态在此留档。
CREATE TABLE IF NOT EXISTS factor_build_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    report_json TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_factor_build_runs_date ON factor_build_runs(trade_date);

-- 主板块快照：从 scans.raw_output 派生（不是「行业归属」）。
-- category 可能是 gn/dq —— 只有 category='hy' 才算真实行业。
CREATE TABLE IF NOT EXISTS factor_sector_snapshots (
    trade_date    TEXT NOT NULL,
    code          TEXT NOT NULL,
    industry_code TEXT,
    industry_name TEXT,
    category      TEXT NOT NULL,
    source        TEXT,
    scan_id       TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(trade_date, code, category)
);
CREATE INDEX IF NOT EXISTS idx_factor_sector_date
    ON factor_sector_snapshots(trade_date, category);

-- ─── 财务原始链路（factors/finance.py，P0.5）──────────────────────────────
-- 日度观测：get_finance_info 的最新单期命名快照（不可再生，same_day_only）。
CREATE TABLE IF NOT EXISTS fin_observed_snapshots (
    trade_date   TEXT NOT NULL,              -- 观测日 YYYY-MM-DD（= 采集当日）
    code         TEXT NOT NULL,
    updated_date INTEGER,                    -- TDX 财务更新日期 YYYYMMDD（可空）
    period       TEXT,                       -- 与账本值级哈希匹配得出；匹配不到为 NULL
    values_json  TEXT NOT NULL,              -- 命名语义字段（本方案需要的子集）
    values_hash  TEXT NOT NULL,              -- 语义字段规范化后的 sha256
    captured_at  TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(trade_date, code)
);

-- 回填期文件元数据：身份 = (报告期, md5)；md5 变即新行，旧行保留供审计。
CREATE TABLE IF NOT EXISTS fin_report_files (
    report_period TEXT NOT NULL,             -- YYYY-MM-DD（报告期末）
    filename      TEXT NOT NULL,             -- tdxfin/gpcwYYYYMMDD.zip
    md5           TEXT NOT NULL,
    filesize      INTEGER,
    first_seen_at TEXT NOT NULL,
    status        TEXT NOT NULL,             -- ok / parse_error / layout_unverified
    records_count INTEGER DEFAULT 0,
    raw_blob      BLOB,                      -- layout_unverified 时原始 float 数组（gzip）
    error         TEXT,
    PRIMARY KEY(report_period, md5)
);

-- 报告期账本：追加式 vintage。修订 = 新行，绝不覆盖旧行。
CREATE TABLE IF NOT EXISTS fin_report_records (
    report_period TEXT NOT NULL,
    code          TEXT NOT NULL,
    file_md5      TEXT NOT NULL,             -- vintage 身份
    layout_key    TEXT NOT NULL,             -- gpcw_<num_fields>
    values_json   TEXT NOT NULL,             -- 映射后的语义字段
    available_at  TEXT NOT NULL,             -- YYYY-MM-DDTHH:MM:SS（§6.4）
    pit_basis     TEXT NOT NULL,             -- observed / statutory_backfilled / revised
    first_seen_at TEXT,
    retracted_at  TEXT,                      -- 新版文件删除该 code 时的撤销时刻
    PRIMARY KEY(report_period, code, file_md5)
);
CREATE INDEX IF NOT EXISTS idx_fin_records_code_period
    ON fin_report_records(code, report_period, available_at);

-- 字段映射校准：按布局一行的最新结果（fail-closed 的唯一开关）。
CREATE TABLE IF NOT EXISTS fin_field_calibrations (
    layout_key      TEXT PRIMARY KEY,        -- gpcw_<num_fields>
    num_fields      INTEGER NOT NULL,
    mapping_json    TEXT NOT NULL,
    confidence_json TEXT NOT NULL,
    status          TEXT NOT NULL,           -- verified / unverified
    sample_n        INTEGER NOT NULL,
    calibrated_at   TEXT DEFAULT (datetime('now','localtime'))
);

-- 除权除息（旁路资产，本期不进因子）。
CREATE TABLE IF NOT EXISTS fin_xdxr_records (
    code         TEXT NOT NULL,
    ex_date      TEXT NOT NULL,              -- YYYY-MM-DD
    category     INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(code, ex_date, category)
);
"""

# 形状探针（读路径 / wrong-DB 识别）：只校验 NOT NULL 核心列，旧库缺 quote_json 也能读。
_REQUIRED_COLUMNS = {
    "prep_assets": ("trade_date", "asset_type", "payload_json"),
    "prep_snapshots": ("trade_date", "source", "result_json"),
    "auction_assets": ("trade_date", "code", "items_json"),
    "auction_snapshots": ("trade_date", "code", "source", "analysis_json"),
    # dragons 镜像：只登记核心列，用于 wrong-DB 识别（写路径全量断言自动继承）。
    "dragons": ("trade_date", "code", "name", "composite_score", "review_status"),
    # 因子层（P0）：只登记核心列，用于 wrong-DB 识别。
    "factor_values": ("trade_date", "code", "factor_name", "factor_version",
                      "value", "available_at", "source"),
    "factor_registry": ("factor_name", "factor_version", "frequency", "source",
                        "direction", "available_phase", "pit_rule", "spec_hash"),
    "factor_model_snapshots": ("model_name", "model_version", "params_json",
                               "factor_versions_json"),
    "factor_build_runs": ("trade_date", "report_json"),
    "factor_sector_snapshots": ("trade_date", "code", "category"),
    # 财务原始链路（P0.5）
    "fin_observed_snapshots": ("trade_date", "code", "values_json", "values_hash"),
    "fin_report_files": ("report_period", "filename", "md5", "status"),
    "fin_report_records": ("report_period", "code", "file_md5", "layout_key",
                           "values_json", "available_at", "pit_basis"),
    "fin_field_calibrations": ("layout_key", "num_fields", "mapping_json", "status"),
    "fin_xdxr_records": ("code", "ex_date", "category", "payload_json"),
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
            from dragon_quant.storage.paths import ensure_data_dirs

            ensure_data_dirs()
        else:
            # 显式路径（--target-dir / 测试隔离）：只建目标父目录，不碰主数据根。
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
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
                           with_meta: bool = False) -> list[dict]:
    """列出某日竞价强度快照，按 code 排序；逐行 JSON 损坏只标 ``error``，不中断整批。

    ``with_meta=True`` 时逐行追加 ``created_at`` 键（因子层血缘读取，方案 §15.2）；
    默认 ``False`` 返回形状保持不变。
    """
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
        result = []
        for r in rows:
            analysis, error = _load_json(r[2], {})
            item = _snapshot_row(r[0], r[1], analysis, error)
            if with_meta:
                item["created_at"] = r[3]
            result.append(item)
        return result
    finally:
        conn.close()


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
