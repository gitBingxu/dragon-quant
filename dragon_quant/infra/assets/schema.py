"""assets.db schema 常量（唯一 DDL 事实源）。

- ``BASE_SCHEMA``：15 张表的 ``CREATE TABLE IF NOT EXISTS`` 与索引；
- ``TABLE_NAMES``：assets.db 物理表全集（4 张 prep/auction 资产 + dragons 镜像 +
  5 张 ``factor_*`` + 5 张 ``fin_*`` + 4 张持续采集 ``capture_*`` /
  ``market_*``）；
- ``_REQUIRED_COLUMNS`` / ``_WRITE_REQUIRED_COLUMNS`` / ``_COLUMN_MIGRATIONS``：
  wrong-DB 识别与表驱动列迁移。

DDL 放在本模块，使 ``infra.assets`` 不依赖 ``infra.factor``；连接、schema 守卫与
资产仓储在 :mod:`dragon_quant.infra.assets.db`。
"""

from __future__ import annotations

TABLE_NAMES = (
    "prep_assets",
    "prep_snapshots",
    "auction_assets",
    "auction_snapshots",
    "dragons",
    "factor_values",
    "factor_registry",
    "factor_model_snapshots",
    "factor_build_runs",
    "factor_sector_snapshots",
    "fin_observed_snapshots",
    "fin_report_files",
    "fin_report_records",
    "fin_field_calibrations",
    "fin_xdxr_records",
    "capture_plans",
    "capture_runs",
    "market_observations",
    "market_bar_versions",
)

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

-- ─── 持续采集：观察计划 / 采集运行 / 原始观测 / bar 版本（§5.4）───────────
-- 契约：原始市场资产归 assets.db；计划与观测**不可变**（修订=新行），
-- 账户金额/真实成交不写入本库。租约字段（lease_*）是本机协调状态，
-- **不进入同步语义列**，备份恢复后不得变回活跃锁。

-- 事前母集：每个 (trade_date, revision) 一行；revision 只追加，不回改。
CREATE TABLE IF NOT EXISTS capture_plans (
    plan_id          TEXT PRIMARY KEY,        -- cp_<YYYYMMDD>_r<N>
    trade_date       TEXT NOT NULL,           -- YYYY-MM-DD（计划所属交易日）
    revision         INTEGER NOT NULL,
    parent_plan_id   TEXT,                    -- 上一版计划（盘中修订）
    pool_json        TEXT NOT NULL,           -- 母集来源/读取日期/覆盖缺口
    members_json     TEXT NOT NULL,           -- 逐 code 首次获知/入池原因/跟踪截止
    task_config_json TEXT NOT NULL,           -- 时槽/主题/版本
    config_version   TEXT NOT NULL,
    content_hash     TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE(trade_date, revision)
);
CREATE INDEX IF NOT EXISTS idx_capture_plans_date
    ON capture_plans(trade_date, revision);

-- 一次（计划×阶段×时槽）运行：唯一防止重入；租约为短事务协调字段。
CREATE TABLE IF NOT EXISTS capture_runs (
    run_id            TEXT PRIMARY KEY,       -- <plan_id>:<phase>:<slot>
    plan_id           TEXT NOT NULL,
    trade_date        TEXT NOT NULL,
    phase             TEXT NOT NULL,          -- auction / intraday / postclose
    slot              TEXT NOT NULL,          -- 计划时槽标签（如 0926）
    status            TEXT NOT NULL,          -- planned/running/partial/complete/
                                              -- missed/failed/skipped
    started_at        TEXT,
    finished_at       TEXT,
    deadline_at       TEXT,
    lease_owner       TEXT,                   -- 本机协调字段（不同步）
    lease_token       INTEGER NOT NULL DEFAULT 0,   -- fencing token（不同步）
    lease_expires_at  TEXT,                   -- 不同步
    report_json       TEXT,                   -- 逐项成功/失败/缺失与覆盖
    created_at        TEXT NOT NULL,
    UNIQUE(plan_id, phase, slot)
);
CREATE INDEX IF NOT EXISTS idx_capture_runs_date
    ON capture_runs(trade_date, status);
CREATE INDEX IF NOT EXISTS idx_capture_runs_plan
    ON capture_runs(plan_id);

-- 原始观测：同语义键 + 同内容 = 幂等去重；内容不同 = 追加 revision。
CREATE TABLE IF NOT EXISTS market_observations (
    obs_id            TEXT PRIMARY KEY,       -- <semantic_key>#r<N>
    trade_date        TEXT NOT NULL,
    market            TEXT NOT NULL,          -- sh/sz/bj/sector/market
    code              TEXT NOT NULL DEFAULT '',
    obs_type          TEXT NOT NULL,          -- auction_path/auction_final/quote/...
    source            TEXT NOT NULL,          -- tdx / ths / imported:...
    source_event_time TEXT,                   -- 源事件时间（可空）
    received_at       TEXT NOT NULL,          -- 系统首次收到该内容的时间
    first_received_at TEXT NOT NULL,          -- 同一语义观测的最早收到时间
    available_at      TEXT NOT NULL,          -- 可用于决策的时点（PIT）
    valid_until       TEXT,
    units_json        TEXT NOT NULL,          -- 单位（未核实显式标 unverified）
    quality           TEXT NOT NULL,          -- semantics_verified / unverified / ...
    schema_version    TEXT NOT NULL,
    revision          INTEGER NOT NULL,
    content_hash      TEXT NOT NULL,
    semantic_key      TEXT NOT NULL,
    payload_json      TEXT NOT NULL,
    run_id            TEXT,
    calibration_json  TEXT,
    created_at        TEXT NOT NULL,
    UNIQUE(semantic_key, revision),
    UNIQUE(semantic_key, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_market_obs_date
    ON market_observations(trade_date, obs_type);
CREATE INDEX IF NOT EXISTS idx_market_obs_code
    ON market_observations(code, trade_date);

-- 不复权 bar 版本：身份 = (market, code, period, adjustment, interval_end)。
-- 不同 market / source / 复权口径不得共用 revision；语义验证状态随行留证。
CREATE TABLE IF NOT EXISTS market_bar_versions (
    bar_id         TEXT PRIMARY KEY,          -- <market>|<code>|<period>|<adj>|<end>#r<N>
    trade_date     TEXT NOT NULL,
    market         TEXT NOT NULL,
    code           TEXT NOT NULL,
    period         TEXT NOT NULL,             -- 1min / 5min / daily
    interval_start TEXT NOT NULL,
    interval_end   TEXT NOT NULL,
    open           REAL NOT NULL,
    high           REAL NOT NULL,
    low            REAL NOT NULL,
    close          REAL NOT NULL,
    volume         REAL,
    amount         REAL,
    adjustment     TEXT NOT NULL DEFAULT 'none',   -- 原始不复权
    source         TEXT NOT NULL,
    observed_at    TEXT NOT NULL,
    available_at   TEXT NOT NULL,
    revision       INTEGER NOT NULL,
    content_hash   TEXT NOT NULL,
    completeness   TEXT NOT NULL,             -- final / unverified / partial
    time_semantics TEXT NOT NULL DEFAULT 'unverified',  -- verified / unverified
    unit_semantics TEXT NOT NULL DEFAULT 'unverified',  -- verified / unverified
    derived        INTEGER NOT NULL DEFAULT 0,
    schema_version TEXT NOT NULL,
    run_id         TEXT,
    calibration_json TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE(market, code, period, adjustment, interval_end, revision),
    UNIQUE(market, code, period, adjustment, interval_end, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_market_bars_code
    ON market_bar_versions(code, period, trade_date);
CREATE INDEX IF NOT EXISTS idx_market_bars_date
    ON market_bar_versions(trade_date, period);
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
    # 持续采集：只登记核心列（wrong-DB 识别）；租约列只属于 capture_runs。
    "capture_plans": ("plan_id", "trade_date", "revision", "members_json",
                      "task_config_json", "content_hash"),
    "capture_runs": ("run_id", "plan_id", "trade_date", "phase", "slot", "status"),
    "market_observations": ("obs_id", "trade_date", "obs_type", "source",
                            "received_at", "revision", "content_hash",
                            "semantic_key", "payload_json", "calibration_json"),
    "market_bar_versions": ("bar_id", "trade_date", "code", "period",
                            "interval_end", "open", "high", "low", "close",
                            "revision", "content_hash", "completeness"),
}

# 写路径探针：迁移后必须包含可空新增列，补列失败立即以 AssetsDbSchemaError 暴露。
_WRITE_REQUIRED_COLUMNS = {
    **_REQUIRED_COLUMNS,
    "auction_assets": ("trade_date", "code", "items_json", "quote_json"),
    "market_bar_versions": ("time_semantics", "unit_semantics",
                            "calibration_json"),
}

# 表驱动列迁移：新增可空列 = 注册一行；只在写路径执行。
# 注意：SQLite 无法 ALTER 既有 UNIQUE 约束；bar 身份从 (code, period, end)
# 扩到 (market, code, period, adjustment, end) 只对**新写入**生效，旧行保留其
# 当时身份。同步/恢复按 bar_id 逐行，不受影响。
_COLUMN_MIGRATIONS = {
    "auction_assets": (("quote_json", "TEXT"),),
    "market_observations": (("calibration_json", "TEXT"),),
    "market_bar_versions": (
        ("time_semantics", "TEXT NOT NULL DEFAULT 'unverified'"),
        ("unit_semantics", "TEXT NOT NULL DEFAULT 'unverified'"),
        ("calibration_json", "TEXT"),
    ),
}
