"""dragon.db 全部 DDL 与幂等 schema 迁移（唯一 schema 规则源）。

连接 / 锁 / ``_schema_ready`` 守卫在 :mod:`dragon_quant.infra.storage.connection`；
本模块只负责「在给定连接上建表、补列、灌种子」，不含连接语义。
"""

from __future__ import annotations

import sqlite3

from dragon_quant.infra.storage.identifiers import (
    VALID_SOURCES,
    _normalize_source,
    _tables,
)

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS vpa_analysis (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT NOT NULL,
    code          TEXT NOT NULL,
    name          TEXT,
    source        TEXT,
    health_score  REAL,
    signal        TEXT,
    summary       TEXT,
    factors_json  TEXT,
    version       TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(trade_date, code)
);

CREATE INDEX IF NOT EXISTS idx_vpa_date ON vpa_analysis(trade_date);
CREATE INDEX IF NOT EXISTS idx_vpa_code ON vpa_analysis(code);

CREATE TABLE IF NOT EXISTS sector_blacklist (
    name        TEXT PRIMARY KEY,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS review_account_runs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    source               TEXT DEFAULT 'v2',
    display_name         TEXT,
    strategy_name        TEXT NOT NULL,
    strategy_params_json TEXT,
    date_from            TEXT NOT NULL,
    date_to              TEXT NOT NULL,
    initial_cash         REAL,
    base_equity          REAL,
    final_equity         REAL,
    total_return         REAL,
    max_drawdown         REAL,
    trade_count          INTEGER,
    win_rate             REAL,
    created_at           TEXT DEFAULT (datetime('now','localtime')),
    base_run_id          INTEGER,
    start_equity         REAL,
    start_positions_json TEXT,
    start_realized_pnl   REAL,
    start_cash           REAL,
    start_peak_equity    REAL,
    start_consecutive_losses INTEGER,
    start_marks_json     TEXT,
    run_meta_json        TEXT
);

CREATE INDEX IF NOT EXISTS idx_review_account_runs_created ON review_account_runs(created_at);

CREATE TABLE IF NOT EXISTS review_account_snapshots (
    id                           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                       INTEGER NOT NULL REFERENCES review_account_runs(id) ON DELETE CASCADE,
    trade_date                   TEXT NOT NULL,
    cash                         REAL,
    market_value                 REAL,
    total_equity                 REAL,
    daily_return                 REAL,
    cumulative_return            REAL,
    drawdown                     REAL,
    position_code                TEXT,
    position_name                TEXT,
    position_qty                 INTEGER,
    position_cost                REAL,
    position_market_price        REAL,
    position_unrealized_return   REAL
);

CREATE INDEX IF NOT EXISTS idx_review_account_snapshots_run ON review_account_snapshots(run_id, trade_date);

CREATE TABLE IF NOT EXISTS review_account_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES review_account_runs(id) ON DELETE CASCADE,
    trade_date      TEXT NOT NULL,
    code            TEXT NOT NULL,
    name            TEXT,
    side            TEXT NOT NULL,
    price           REAL,
    qty             INTEGER,
    amount          REAL,
    fee             REAL,
    realized_pnl    REAL,
    cash_after      REAL,
    position_after  INTEGER,
    reason_code     TEXT,
    reason_text     TEXT,
    signal_json     TEXT
);

CREATE INDEX IF NOT EXISTS idx_review_account_trades_run ON review_account_trades(run_id, trade_date);

CREATE TABLE IF NOT EXISTS review_account_positions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id               INTEGER NOT NULL REFERENCES review_account_runs(id) ON DELETE CASCADE,
    code                 TEXT NOT NULL,
    name                 TEXT,
    entry_date           TEXT,
    entry_price          REAL,
    qty                  INTEGER,
    entry_reason_code    TEXT,
    entry_signal_json    TEXT,
    exit_date            TEXT,
    exit_price           REAL,
    exit_reason_code     TEXT,
    exit_signal_json     TEXT,
    realized_return      REAL,
    hold_days            INTEGER,
    status               TEXT,
    post_exit_max_return    REAL,
    post_exit_max_drawdown  REAL,
    post_exit_window_days   INTEGER,
    initial_cost            REAL,
    realized_pnl            REAL,
    period_pnl              REAL,
    period_start_value      REAL,
    inherited               INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_review_account_positions_run ON review_account_positions(run_id, entry_date);

CREATE TABLE IF NOT EXISTS review_account_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES review_account_runs(id) ON DELETE CASCADE,
    event_date      TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    code            TEXT,
    name            TEXT,
    title           TEXT,
    detail          TEXT,
    reason_code     TEXT,
    cash            REAL,
    total_equity    REAL,
    signal_json     TEXT
);

CREATE INDEX IF NOT EXISTS idx_review_account_events_run ON review_account_events(run_id, event_date, id);

CREATE TABLE IF NOT EXISTS review_account_experiments (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    base_run_id          INTEGER NOT NULL,
    created_at           TEXT DEFAULT (datetime('now','localtime')),
    experiment_version   TEXT NOT NULL,
    status               TEXT,
    request_json         TEXT,
    report_json          TEXT
);

CREATE INDEX IF NOT EXISTS idx_review_account_experiments_run
    ON review_account_experiments(base_run_id, id);

CREATE TABLE IF NOT EXISTS buy_sell_signals (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    code               TEXT NOT NULL,
    name               TEXT,
    source             TEXT NOT NULL DEFAULT 'v2',
    entry_date         TEXT NOT NULL,
    entry_price        REAL,
    entry_reason_code  TEXT,
    entry_reason_text  TEXT,
    entry_signal_json  TEXT,
    highest_return     REAL DEFAULT 0,
    highest_price      REAL DEFAULT 0,
    took_profit_half   INTEGER DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'open',
    exit_date          TEXT,
    exit_price         REAL,
    exit_reason_code   TEXT,
    exit_reason_text   TEXT,
    exit_signal_json   TEXT,
    hold_days          INTEGER,
    created_at         TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at         TEXT DEFAULT (datetime('now', 'localtime')),
    qty                INTEGER,
    half_exit_date     TEXT,
    half_exit_price    REAL,
    half_exit_qty      INTEGER,
    half_exit_reason_code TEXT,
    half_exit_reason_text TEXT,
    half_exit_signal_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_buy_sell_open ON buy_sell_signals(code) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_buy_sell_open_by_date ON buy_sell_signals(status, entry_date, code);

CREATE TABLE IF NOT EXISTS dragon_review_strict (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date            TEXT NOT NULL,
    code                  TEXT NOT NULL,
    source                TEXT NOT NULL DEFAULT 'v2',
    buy_date              TEXT,
    buy_price             REAL,
    max_return_5d         REAL,
    max_drawdown_5d       REAL,
    max_return_hold_days  INTEGER,
    review_status         TEXT NOT NULL,
    reason                TEXT,
    review_version        TEXT,
    tradable_open_json    TEXT,
    computed_at           TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(trade_date, code, source)
);

CREATE INDEX IF NOT EXISTS idx_dragon_review_strict_status
    ON dragon_review_strict(source, review_status, trade_date);
CREATE INDEX IF NOT EXISTS idx_dragon_review_strict_code
    ON dragon_review_strict(code);

CREATE TABLE IF NOT EXISTS bs_runs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at            TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at           TEXT,
    range_from            TEXT,
    range_to              TEXT,
    limit_n               INTEGER,
    force                 INTEGER NOT NULL DEFAULT 0,
    evaluator_version     TEXT NOT NULL,
    strategy_name         TEXT NOT NULL,
    strategy_params_json  TEXT,
    strategy_hash         TEXT NOT NULL,
    rows_scanned          INTEGER NOT NULL DEFAULT 0,
    rows_skipped_terminal INTEGER NOT NULL DEFAULT 0,
    rows_evaluated        INTEGER NOT NULL DEFAULT 0,
    rows_no_signal        INTEGER NOT NULL DEFAULT 0,
    rows_bought           INTEGER NOT NULL DEFAULT 0,
    rows_sold             INTEGER NOT NULL DEFAULT 0,
    rows_partial          INTEGER NOT NULL DEFAULT 0,
    rows_error            INTEGER NOT NULL DEFAULT 0,
    note                  TEXT
);

CREATE TABLE IF NOT EXISTS bs_signals (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                    INTEGER REFERENCES bs_runs(id) ON DELETE SET NULL,
    trade_date                TEXT NOT NULL,
    code                      TEXT NOT NULL,
    name                      TEXT,
    source                    TEXT NOT NULL DEFAULT 'v2',
    strategy_hash             TEXT NOT NULL,
    evaluator_version         TEXT NOT NULL,
    eval_status               TEXT NOT NULL,
    eval_note                 TEXT,
    is_terminal               INTEGER NOT NULL DEFAULT 0,
    data_quality              TEXT,
    replay_days               INTEGER NOT NULL DEFAULT 0,
    fallback_days             INTEGER NOT NULL DEFAULT 0,
    suspended_days            INTEGER NOT NULL DEFAULT 0,
    fault_days                INTEGER NOT NULL DEFAULT 0,
    buy_date                  TEXT,
    buy_rule_code             TEXT,
    buy_rule_text             TEXT,
    buy_loose_price           REAL,
    buy_strict_price          REAL,
    buy_data_quality          TEXT,
    buy_loose_max_return_5d   REAL,
    buy_loose_max_drawdown_5d REAL,
    buy_strict_max_return_5d  REAL,
    buy_strict_max_drawdown_5d REAL,
    buy_max_return_hold_days  INTEGER,
    buy_window_partial        INTEGER NOT NULL DEFAULT 0,
    sell_date                 TEXT,
    sell_rule_code            TEXT,
    sell_rule_text            TEXT,
    sell_price                REAL,
    sell_hold_days            INTEGER,
    sell_data_quality         TEXT,
    sell_half_date            TEXT,
    sell_half_price           REAL,
    sell_half_qty             INTEGER,
    sell_half_rule_code       TEXT,
    sell_half_max_return_5d   REAL,
    sell_half_max_drawdown_5d REAL,
    sell_half_window_days     INTEGER,
    sell_max_return_5d        REAL,
    sell_max_drawdown_5d      REAL,
    sell_max_return_hold_days INTEGER,
    sell_post_exit_window_days INTEGER,
    sell_window_partial       INTEGER NOT NULL DEFAULT 0,
    content_hash              TEXT,
    last_attempt_at           TEXT,
    created_at                TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at                TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(trade_date, code)
);

CREATE INDEX IF NOT EXISTS idx_bs_signals_trade_date ON bs_signals(trade_date);
CREATE INDEX IF NOT EXISTS idx_bs_signals_code ON bs_signals(code);
CREATE INDEX IF NOT EXISTS idx_bs_signals_buy_date ON bs_signals(buy_date);
CREATE INDEX IF NOT EXISTS idx_bs_signals_sell_date ON bs_signals(sell_date);
CREATE INDEX IF NOT EXISTS idx_bs_signals_status ON bs_signals(eval_status, is_terminal);
CREATE INDEX IF NOT EXISTS idx_bs_signals_attempt ON bs_signals(is_terminal, last_attempt_at);
CREATE INDEX IF NOT EXISTS idx_bs_signals_run ON bs_signals(run_id);
CREATE INDEX IF NOT EXISTS idx_bs_runs_created ON bs_runs(started_at);
"""

# 行业板块黑名单默认种子（行业板块为真实行业分类，默认无需屏蔽；
# 如需屏蔽特定行业，用 `blacklist add` 维护）。
_BLACKLIST_SEED: list[str] = []


def _ensure_schema(conn: sqlite3.Connection):
    conn.executescript(BASE_SCHEMA)
    _ensure_review_account_columns(conn)
    _ensure_signal_columns(conn)
    _ensure_bs_columns(conn)
    _ensure_strict_columns(conn)
    for source in sorted(VALID_SOURCES):
        _create_versioned_tables(conn, source)
        _ensure_dragon_columns(conn, source)
    _seed_blacklist(conn)
    conn.commit()


def _ensure_signal_columns(conn: sqlite3.Connection):
    """对已存在的 buy_sell_signals 幂等补列（qty 落库冻结 + 半仓腿记账）。

    并发首启撞 duplicate column 时复查后视为成功（沿用 assets_db 迁移先例）。
    """
    wanted = (
        ("qty", "INTEGER"),
        ("half_exit_date", "TEXT"),
        ("half_exit_price", "REAL"),
        ("half_exit_qty", "INTEGER"),
        ("half_exit_reason_code", "TEXT"),
        ("half_exit_reason_text", "TEXT"),
        ("half_exit_signal_json", "TEXT"),
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(buy_sell_signals)")}
    for name, decl in wanted:
        if name in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE buy_sell_signals ADD COLUMN {name} {decl}")
        except sqlite3.OperationalError:
            existing = {r[1] for r in conn.execute("PRAGMA table_info(buy_sell_signals)")}
            if name not in existing:
                raise


def _ensure_strict_columns(conn: sqlite3.Connection):
    """对已存在的 ``dragon_review_strict`` 幂等补列（旧库升级保留数据）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(dragon_review_strict)")}
    if "tradable_open_json" not in cols:
        conn.execute("ALTER TABLE dragon_review_strict ADD COLUMN tradable_open_json TEXT")


def _ensure_bs_columns(conn: sqlite3.Connection):
    """对已存在的 bs_signals 幂等补列（bs_eval/2 的故障与独立窗口字段）。"""
    wanted = (
        ("fault_days", "INTEGER NOT NULL DEFAULT 0"),
        ("sell_half_max_return_5d", "REAL"),
        ("sell_half_max_drawdown_5d", "REAL"),
        ("sell_half_window_days", "INTEGER"),
        ("sell_post_exit_window_days", "INTEGER"),
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bs_signals)")}
    for name, decl in wanted:
        if name in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE bs_signals ADD COLUMN {name} {decl}")
        except sqlite3.OperationalError:
            existing = {r[1] for r in conn.execute("PRAGMA table_info(bs_signals)")}
            if name not in existing:
                raise


def _ensure_dragon_columns(conn: sqlite3.Connection, source: str):
    """对已存在的 dragons 分表幂等补列（CREATE IF NOT EXISTS 不会给旧表加列）。"""
    t = _tables(source)
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t['dragons']})")}
    if "is_true_dragon" not in cols:
        conn.execute(f"ALTER TABLE {t['dragons']} ADD COLUMN is_true_dragon INTEGER")


def _ensure_review_account_columns(conn: sqlite3.Connection):
    """对已存在的账户级 review 表幂等补列。"""
    run_cols = {r[1] for r in conn.execute("PRAGMA table_info(review_account_runs)")}
    if "display_name" not in run_cols:
        conn.execute("ALTER TABLE review_account_runs ADD COLUMN display_name TEXT")
    # 增量回测的收益率/指数曲线起点（基准最后收盘权益）；全量 run 保持 NULL。
    if "base_equity" not in run_cols:
        conn.execute("ALTER TABLE review_account_runs ADD COLUMN base_equity REAL")
    # 运行追溯（M3）：链式基准、期初状态/权益/已实现盈亏与运行质量元数据；
    # 旧 run 保持 NULL（unknown），不推断历史业务值。
    for name, decl in (
        ("base_run_id", "INTEGER"),
        ("start_equity", "REAL"),
        ("start_positions_json", "TEXT"),
        ("start_realized_pnl", "REAL"),
        ("start_cash", "REAL"),
        ("start_peak_equity", "REAL"),
        ("start_consecutive_losses", "INTEGER"),
        ("start_marks_json", "TEXT"),
        ("run_meta_json", "TEXT"),
    ):
        if name in run_cols:
            continue
        try:
            conn.execute(f"ALTER TABLE review_account_runs ADD COLUMN {name} {decl}")
        except sqlite3.OperationalError:
            existing = {r[1] for r in conn.execute(
                "PRAGMA table_info(review_account_runs)")}
            if name not in existing:
                raise
    snapshot_cols = {r[1] for r in conn.execute("PRAGMA table_info(review_account_snapshots)")}
    if "positions_json" not in snapshot_cols:
        conn.execute("ALTER TABLE review_account_snapshots ADD COLUMN positions_json TEXT DEFAULT '[]'")

    cols = {r[1] for r in conn.execute("PRAGMA table_info(review_account_trades)")}
    if "realized_pnl" not in cols:
        conn.execute("ALTER TABLE review_account_trades ADD COLUMN realized_pnl REAL")

    # 清仓后窗口指标：不带 DEFAULT，旧行保持 NULL（与「旧 run 不回溯」语义一致）。
    pos_cols = {r[1] for r in conn.execute("PRAGMA table_info(review_account_positions)")}
    for name, decl in (("post_exit_max_return", "REAL"),
                       ("post_exit_max_drawdown", "REAL"),
                       ("post_exit_window_days", "INTEGER"),
                       ("initial_cost", "REAL"),
                       ("realized_pnl", "REAL"),
                       ("period_pnl", "REAL"),
                       ("period_start_value", "REAL"),
                       ("inherited", "INTEGER DEFAULT 0")):
        if name in pos_cols:
            continue
        try:
            conn.execute(f"ALTER TABLE review_account_positions ADD COLUMN {name} {decl}")
        except sqlite3.OperationalError:
            existing = {r[1] for r in conn.execute("PRAGMA table_info(review_account_positions)")}
            if name not in existing:
                raise


def _create_versioned_tables(conn: sqlite3.Connection, source: str):
    """创建指定扫描体系的物理分表（幂等）。"""
    s = _normalize_source(source)
    t = _tables(s)
    conn.executescript(f"""
CREATE TABLE IF NOT EXISTS {t['scans']} (
    id           TEXT PRIMARY KEY,
    scan_date    TEXT NOT NULL,
    elapsed_s    REAL,
    top_n        INTEGER,
    candidates_n INTEGER,
    workers      INTEGER,
    raw_output   TEXT,
    source       TEXT DEFAULT '{s}',
    created_at   TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS {t['scan_stocks']} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         TEXT NOT NULL REFERENCES {t['scans']}(id),
    code            TEXT NOT NULL,
    name            TEXT,
    rank            INTEGER,
    composite_score REAL,
    board_count     INTEGER,
    concepts_json   TEXT,
    dim_drive       REAL,
    dim_anti_drop   REAL,
    dim_leadership  REAL,
    dim_absorption  REAL,
    dim_liquidity   REAL,
    is_true_dragon  INTEGER,
    reject_reason   TEXT,
    report_text     TEXT,
    open_px         REAL,
    close_px        REAL,
    high_px         REAL,
    low_px          REAL,
    pct             REAL,
    turnover_rate   REAL,
    amount          REAL,
    market_cap      REAL,
    source          TEXT DEFAULT '{s}',
    UNIQUE(scan_id, code)
);

CREATE INDEX IF NOT EXISTS idx_{t['scan_stocks']}_code ON {t['scan_stocks']}(code);
CREATE INDEX IF NOT EXISTS idx_{t['scan_stocks']}_scan ON {t['scan_stocks']}(scan_id);

CREATE TABLE IF NOT EXISTS {t['dragons']} (
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
    source                TEXT DEFAULT '{s}',
    UNIQUE(trade_date, code)
);

CREATE INDEX IF NOT EXISTS idx_{t['dragons']}_date ON {t['dragons']}(trade_date);
CREATE INDEX IF NOT EXISTS idx_{t['dragons']}_code ON {t['dragons']}(code);
CREATE INDEX IF NOT EXISTS idx_{t['dragons']}_review ON {t['dragons']}(review_status, trade_date);

CREATE TABLE IF NOT EXISTS {t['scan_logs']} (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id     TEXT NOT NULL,
    ts          REAL,
    category    TEXT,
    level       TEXT,
    message     TEXT,
    code        TEXT,
    data_json   TEXT,
    source      TEXT DEFAULT '{s}'
);
CREATE INDEX IF NOT EXISTS idx_{t['scan_logs']}_scan ON {t['scan_logs']}(scan_id);
CREATE INDEX IF NOT EXISTS idx_{t['scan_logs']}_category ON {t['scan_logs']}(category);
CREATE INDEX IF NOT EXISTS idx_{t['scan_logs']}_level ON {t['scan_logs']}(level);
CREATE INDEX IF NOT EXISTS idx_{t['scan_logs']}_code ON {t['scan_logs']}(code);
""")


def _seed_blacklist(conn: sqlite3.Connection):
    """首次建表时灌入默认板块黑名单种子（幂等，已存在则跳过）。"""
    for name in _BLACKLIST_SEED:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO sector_blacklist(name) VALUES (?)", (name,))
        except sqlite3.OperationalError:
            pass
