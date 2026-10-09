"""
SQLite 持久化层 — 统一门面（facade）。

本模块只保留数据库基础设施（连接 / schema / 物理分表 / 锁 / 初始化）。
具体业务按领域拆分为独立模块，并在文件末尾重新导出，对外 API 不变：

    blacklist.py       板块黑名单
    scans.py           扫描记录 / 评分明细（scans / scan_stocks）
    dragons.py         龙头物化 / 回测 / VPA / Review 查询
    review_account.py  账户级回测持久化
    signals.py         实盘辅助买卖信号（buy/sell）
    logs.py            结构化扫描日志

调用方式保持不变：`from dragon_quant.storage import db` 后继续 `db.xxx(...)`。
线程安全：WAL 模式 + 每次操作独立连接。
"""

import sqlite3
import threading

from dragon_quant.storage.paths import DB_PATH

_lock = threading.Lock()

VALID_SOURCES = {"v1", "v2"}


def _normalize_source(source: str = "v2") -> str:
    """规范化扫描/回测体系来源，只允许 v1 / v2。"""
    s = (source or "v2").lower().strip()
    if s not in VALID_SOURCES:
        raise ValueError(f"invalid source: {source!r}, expected one of {sorted(VALID_SOURCES)}")
    return s


def _tables(source: str = "v2") -> dict[str, str]:
    """返回指定体系的物理表名。表名只来自白名单 source，安全用于 SQL 拼接。"""
    s = _normalize_source(source)
    return {
        "scans": f"scans_{s}",
        "scan_stocks": f"scan_stocks_{s}",
        "scan_logs": f"scan_logs_{s}",
        "dragons": f"dragons_{s}",
    }

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
    final_equity         REAL,
    total_return         REAL,
    max_drawdown         REAL,
    trade_count          INTEGER,
    win_rate             REAL,
    created_at           TEXT DEFAULT (datetime('now','localtime'))
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
    status               TEXT
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
    updated_at         TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_buy_sell_open ON buy_sell_signals(code) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_buy_sell_open_by_date ON buy_sell_signals(status, entry_date, code);
"""

# 行业板块黑名单默认种子（行业板块为真实行业分类，默认无需屏蔽；
# 如需屏蔽特定行业，用 `blacklist add` 维护）。
_BLACKLIST_SEED: list[str] = []


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection):
    conn.executescript(BASE_SCHEMA)
    _ensure_review_account_columns(conn)
    for source in sorted(VALID_SOURCES):
        _create_versioned_tables(conn, source)
        _ensure_dragon_columns(conn, source)
    _seed_blacklist(conn)
    conn.commit()


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
    snapshot_cols = {r[1] for r in conn.execute("PRAGMA table_info(review_account_snapshots)")}
    if "positions_json" not in snapshot_cols:
        conn.execute("ALTER TABLE review_account_snapshots ADD COLUMN positions_json TEXT DEFAULT '[]'")

    cols = {r[1] for r in conn.execute("PRAGMA table_info(review_account_trades)")}
    if "realized_pnl" not in cols:
        conn.execute("ALTER TABLE review_account_trades ADD COLUMN realized_pnl REAL")


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


def init_db():
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
        finally:
            conn.close()



# ─── 领域模块门面（实现已拆分到各子模块，此处统一再导出） ───

from dragon_quant.storage.blacklist import (  # noqa: E402
    add_sector_blacklist,
    get_sector_blacklist,
    remove_sector_blacklist,
)
from dragon_quant.storage.scans import (  # noqa: E402
    delete_scans_by_date_topn,
    get_latest_scan_by_date,
    get_scan,
    get_scan_stocks,
    get_scans_by_date,
    has_scan,
    list_scan_stock_contributions_by_date,
    list_scans,
    save_scan,
)
from dragon_quant.storage.dragons import (  # noqa: E402
    delete_pending_dragons_not_in,
    get_dragon_meta,
    get_dragons,
    get_dragons_by_date,
    get_last_entry,
    get_last_entry_with_rank,
    get_pending_dragons,
    get_review_summary,
    list_dragon_trade_dates,
    query_dragons,
    rebuild_dragons_for_date,
    save_dragons,
    update_dragon_review,
    upsert_vpa,
)
from dragon_quant.storage.review_account import (  # noqa: E402
    create_review_account_run,
    delete_review_account_run,
    get_review_account_run,
    query_review_account_events,
    query_review_account_positions,
    query_review_account_runs,
    query_review_account_snapshots,
    query_review_account_trades,
    save_review_account_results,
)
from dragon_quant.storage.signals import (  # noqa: E402
    close_signal,
    insert_signal,
    list_open_signals,
    update_signal_peaks,
)
from dragon_quant.storage.logs import (  # noqa: E402
    count_scan_logs,
    delete_all_scan_logs,
    delete_old_scan_logs,
    get_scan_logs,
    list_scan_log_folders,
    log_summary,
    save_scan_logs,
)


init_db()
