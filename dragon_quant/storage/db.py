"""
SQLite 持久化层 — 扫描结果存储

管理 scans + scan_stocks 两张核心表。
每次扫描结束后自动写入，用户无感知。

线程安全：WAL 模式 + 每次操作独立连接。
"""

import sqlite3
import json
import threading
from pathlib import Path
from typing import Optional

from dragon_quant.storage.paths import DB_PATH

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id           TEXT PRIMARY KEY,
    scan_date    TEXT NOT NULL,
    elapsed_s    REAL,
    top_n        INTEGER,
    candidates_n INTEGER,
    workers      INTEGER,
    raw_output   TEXT,
    created_at   TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS scan_stocks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         TEXT NOT NULL REFERENCES scans(id),
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
    report_text     TEXT,
    UNIQUE(scan_id, code)
);

CREATE INDEX IF NOT EXISTS idx_scan_stocks_code ON scan_stocks(code);
CREATE INDEX IF NOT EXISTS idx_scan_stocks_scan ON scan_stocks(scan_id);

CREATE TABLE IF NOT EXISTS dragons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT NOT NULL,
    code            TEXT NOT NULL,
    name            TEXT,
    scan_id         TEXT,
    rank            INTEGER,
    composite_score REAL,
    board_count     INTEGER,
    open_px         REAL,
    close_px        REAL,
    high_px         REAL,
    low_px          REAL,
    pct             REAL,
    turnover_rate   REAL,
    amount          REAL,
    market_cap      REAL,
    concepts_json   TEXT,
    report_text     TEXT,
    version         TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    buy_date        TEXT,
    buy_price       REAL,
    max_return_5d   REAL,
    max_drawdown_5d REAL,
    review_status   TEXT DEFAULT 'pending',
    UNIQUE(trade_date, code)
);

CREATE INDEX IF NOT EXISTS idx_dragons_date ON dragons(trade_date);
CREATE INDEX IF NOT EXISTS idx_dragons_code ON dragons(code);
CREATE INDEX IF NOT EXISTS idx_dragons_review ON dragons(review_status, trade_date);

CREATE TABLE IF NOT EXISTS scan_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id     TEXT NOT NULL,
    ts          REAL,
    category    TEXT,
    level       TEXT,
    message     TEXT,
    code        TEXT,
    data_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_scan_logs_scan ON scan_logs(scan_id);
CREATE INDEX IF NOT EXISTS idx_scan_logs_category ON scan_logs(category);
CREATE INDEX IF NOT EXISTS idx_scan_logs_level ON scan_logs(level);
CREATE INDEX IF NOT EXISTS idx_scan_logs_code ON scan_logs(code);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)
    # 尝试执行升级，如果列已存在会忽略错误
    try:
        conn.execute("ALTER TABLE scan_stocks ADD COLUMN report_text TEXT;")
    except sqlite3.OperationalError:
        pass
    _migrate_dragons(conn)


def _migrate_dragons(conn: sqlite3.Connection):
    """为 dragons 表新增 review 相关列（幂等）。"""
    COLUMNS = [
        "buy_date TEXT",
        "buy_price REAL",
        "max_return_5d REAL",
        "max_drawdown_5d REAL",
        "review_status TEXT DEFAULT 'pending'",
        "version TEXT DEFAULT ''",
    ]
    for col in COLUMNS:
        try:
            conn.execute(f"ALTER TABLE dragons ADD COLUMN {col};")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("ALTER TABLE scans ADD COLUMN raw_output TEXT;")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def init_db():
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
        finally:
            conn.close()


def save_scan(scan_id: str, scan_date: str, elapsed_s: float,
              top_n: int, candidates_n: int, workers: int,
              stocks: list[dict], raw_output: str = None):
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)

            conn.execute(
                "INSERT OR REPLACE INTO scans(id, scan_date, elapsed_s, top_n, candidates_n, workers, raw_output) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (scan_id, scan_date, elapsed_s, top_n, candidates_n, workers, raw_output),
            )

            conn.execute("DELETE FROM scan_stocks WHERE scan_id = ?", (scan_id,))

            rows = []
            for i, s in enumerate(stocks):
                dims = s.get("dimensions", {})
                concepts = s.get("concepts", [])
                rows.append((
                    scan_id,
                    s.get("code", ""),
                    s.get("name", ""),
                    i + 1,
                    s.get("composite_score", 0),
                    s.get("board_count", 0),
                    json.dumps(concepts, ensure_ascii=False),
                    dims.get("drive", {}).get("score"),
                    dims.get("anti_drop", {}).get("score"),
                    dims.get("leadership", {}).get("score"),
                    dims.get("absorption", {}).get("score"),
                    s.get("report_text", ""),
                ))

            conn.executemany(
                "INSERT INTO scan_stocks(scan_id, code, name, rank, composite_score, "
                "board_count, concepts_json, dim_drive, dim_anti_drop, dim_leadership, dim_absorption, report_text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

            conn.commit()
        finally:
            conn.close()


def list_scans(limit: int = 50) -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, scan_date, elapsed_s, top_n, candidates_n, workers, created_at "
            "FROM scans ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r[0], "scan_date": r[1], "elapsed_s": r[2],
                "top_n": r[3], "candidates_n": r[4], "workers": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_latest_scan_by_date(scan_date: str, top_n: int) -> Optional[dict]:
    """按日期和 top_n 获取最新的一次扫描记录"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT id, scan_date, elapsed_s, top_n, candidates_n, workers, created_at, raw_output "
            "FROM scans WHERE scan_date = ? AND top_n = ? ORDER BY created_at DESC LIMIT 1",
            (scan_date, top_n)
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "scan_date": row[1], "elapsed_s": row[2],
            "top_n": row[3], "candidates_n": row[4], "workers": row[5],
            "created_at": row[6], "raw_output": row[7],
        }
    finally:
        conn.close()

def get_scan(scan_id: str) -> Optional[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT id, scan_date, elapsed_s, top_n, candidates_n, workers, created_at, raw_output "
            "FROM scans WHERE id = ?",
            (scan_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "scan_date": row[1], "elapsed_s": row[2],
            "top_n": row[3], "candidates_n": row[4], "workers": row[5],
            "created_at": row[6], "raw_output": row[7],
        }
    finally:
        conn.close()


def save_dragons(trade_date: str, scan_id: str, dragons: list[dict], version: str = ""):
    """保存或更新 top_n 到 dragons 表中"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            
            rows = []
            for i, s in enumerate(dragons):
                concepts = s.get("concepts", [])
                rows.append((
                    trade_date,
                    s.get("code", ""),
                    s.get("name", ""),
                    scan_id,
                    i + 1,  # rank
                    s.get("composite_score", 0),
                    s.get("board_count", 0),
                    s.get("open_px", 0),
                    s.get("close_px", 0),
                    s.get("high_px", 0),
                    s.get("low_px", 0),
                    s.get("pct", 0),
                    s.get("turnover_rate", 0),
                    s.get("amount", 0),
                    s.get("market_cap", 0),
                    json.dumps(concepts, ensure_ascii=False),
                    s.get("report_text", ""),
                    version,
                ))
            
            conn.executemany(
                "INSERT OR REPLACE INTO dragons("
                "trade_date, code, name, scan_id, rank, composite_score, board_count, "
                "open_px, close_px, high_px, low_px, pct, turnover_rate, amount, market_cap, "
                "concepts_json, report_text, version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()

def get_dragons(trade_date: str) -> list[dict]:
    """获取某日的 dragons 数据"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT code, name, scan_id, rank, composite_score, board_count, "
            "open_px, close_px, high_px, low_px, pct, turnover_rate, amount, market_cap, "
            "concepts_json, report_text, version "
            "FROM dragons WHERE trade_date = ? ORDER BY composite_score DESC",
            (trade_date,),
        ).fetchall()
        
        return [
            {
                "code": r[0], "name": r[1], "scan_id": r[2], "rank": r[3],
                "composite_score": r[4], "board_count": r[5],
                "open_px": r[6], "close_px": r[7], "high_px": r[8], "low_px": r[9],
                "pct": r[10], "turnover_rate": r[11], "amount": r[12], "market_cap": r[13],
                "concepts": json.loads(r[14]) if r[14] else [],
                "report_text": r[15] or "",
                "version": r[16] or "",
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_last_entry(code: str) -> Optional[str]:
    """返回该 code 最近一次入选的 trade_date，无记录则返回 None。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT trade_date FROM dragons WHERE code = ? "
            "ORDER BY trade_date DESC LIMIT 1",
            (code,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_pending_dragons(trade_date: Optional[str] = None,
                        top_n: Optional[int] = None) -> list[dict]:
    """获取待 review 的 dragons 记录（review_status = 'pending'）。

    可按 trade_date 和 top_n 过滤。
    """
    conn = _connect()
    try:
        _ensure_schema(conn)
        sql = (
            "SELECT trade_date, code, name, scan_id, rank, composite_score, "
            "board_count, open_px, close_px, high_px, low_px, pct, "
            "turnover_rate, amount, market_cap, concepts_json, report_text, "
            "buy_date, buy_price, max_return_5d, max_drawdown_5d, review_status, version "
            "FROM dragons WHERE review_status = 'pending'"
        )
        params: list = []
        if trade_date:
            sql += " AND trade_date = ?"
            params.append(trade_date)
        if top_n:
            sql += " ORDER BY composite_score DESC LIMIT ?"
            params.append(top_n)
        else:
            sql += " ORDER BY trade_date DESC, composite_score DESC"

        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "trade_date": r[0], "code": r[1], "name": r[2],
                "scan_id": r[3], "rank": r[4], "composite_score": r[5],
                "board_count": r[6], "open_px": r[7], "close_px": r[8],
                "high_px": r[9], "low_px": r[10], "pct": r[11],
                "turnover_rate": r[12], "amount": r[13], "market_cap": r[14],
                "concepts": json.loads(r[15]) if r[15] else [],
                "report_text": r[16] or "",
                "buy_date": r[17], "buy_price": r[18],
                "max_return_5d": r[19], "max_drawdown_5d": r[20],
                "review_status": r[21],
                "version": r[22] or "",
            }
            for r in rows
        ]
    finally:
        conn.close()


def update_dragon_review(trade_date: str, code: str,
                         buy_date: Optional[str] = None,
                         buy_price: Optional[float] = None,
                         max_return_5d: Optional[float] = None,
                         max_drawdown_5d: Optional[float] = None,
                         review_status: str = "completed"):
    """更新单条 dragon 的 review 字段。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute(
                "UPDATE dragons SET "
                "buy_date = COALESCE(?, buy_date), "
                "buy_price = COALESCE(?, buy_price), "
                "max_return_5d = COALESCE(?, max_return_5d), "
                "max_drawdown_5d = COALESCE(?, max_drawdown_5d), "
                "review_status = ? "
                "WHERE trade_date = ? AND code = ?",
                (buy_date, buy_price, max_return_5d, max_drawdown_5d,
                 review_status, trade_date, code),
            )
            conn.commit()
        finally:
            conn.close()


def get_scan_stocks(scan_id: str) -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT code, name, rank, composite_score, board_count, concepts_json, "
            "dim_drive, dim_anti_drop, dim_leadership, dim_absorption, report_text "
            "FROM scan_stocks WHERE scan_id = ? ORDER BY rank",
            (scan_id,),
        ).fetchall()
        return [
            {
                "code": r[0], "name": r[1], "rank": r[2],
                "composite_score": r[3], "board_count": r[4],
                "concepts": json.loads(r[5]) if r[5] else [],
                "dim_drive": r[6], "dim_anti_drop": r[7],
                "dim_leadership": r[8], "dim_absorption": r[9],
                "report_text": r[10] or "",
            }
            for r in rows
        ]
    finally:
        conn.close()


def has_scan(scan_id: str) -> bool:
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute("SELECT 1 FROM scans WHERE id = ?", (scan_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def save_scan_logs(scan_id: str, entries: list[dict]):
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute("DELETE FROM scan_logs WHERE scan_id = ?", (scan_id,))

            rows = []
            for e in entries:
                rows.append((
                    scan_id,
                    e.get("ts", 0),
                    e.get("category", ""),
                    e.get("level", ""),
                    e.get("message", ""),
                    e.get("code", ""),
                    json.dumps(e.get("data", {}), ensure_ascii=False),
                ))

            conn.executemany(
                "INSERT INTO scan_logs(scan_id, ts, category, level, message, code, data_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()


def get_scan_logs(scan_id: Optional[str] = None,
                  category: Optional[str] = None,
                  level: Optional[str] = None,
                  code: Optional[str] = None,
                  tail: int = 200) -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        conditions = []
        params = []

        if scan_id:
            conditions.append("scan_id = ?")
            params.append(scan_id)
        if category:
            conditions.append("(category = ? OR category LIKE ?)")
            params.append(category)
            params.append(category + ":%")
        if level:
            conditions.append("level = ?")
            params.append(level)
        if code:
            conditions.append("code = ?")
            params.append(code)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.append(tail)
        rows = conn.execute(
            f"SELECT scan_id, ts, category, level, message, code, data_json "
            f"FROM scan_logs {where} ORDER BY ts DESC LIMIT ?",
            params,
        ).fetchall()

        return [
            {
                "scan_id": r[0], "ts": r[1], "category": r[2],
                "level": r[3], "message": r[4], "code": r[5],
                "data": json.loads(r[6]) if r[6] else {},
            }
            for r in rows
        ]
    finally:
        conn.close()


def list_scan_log_folders() -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT scan_id, COUNT(*) as cnt, MIN(ts) as first_ts, MAX(ts) as last_ts "
            "FROM scan_logs GROUP BY scan_id ORDER BY scan_id DESC"
        ).fetchall()
        return [
            {
                "scan_id": r[0], "entries": r[1],
                "first_ts": r[2], "last_ts": r[3],
            }
            for r in rows
        ]
    finally:
        conn.close()


def log_summary(scan_id: Optional[str] = None) -> dict:
    entries = get_scan_logs(scan_id=scan_id, tail=99999)
    if not entries:
        return {"error": "无日志"}

    phases = {}
    api_stats = {"total": 0, "ok": 0, "error": 0, "total_ms": 0, "by_provider": {}}
    error_count = 0
    scorer_count = 0

    for e in entries:
        cat = e.get("category", "")
        data = e.get("data", {})

        if cat.startswith("phase:"):
            phases[cat.replace("phase:", "")] = e.get("message", "")
        elif cat.startswith("api:"):
            api_stats["total"] += 1
            if data.get("ok"):
                api_stats["ok"] += 1
            else:
                api_stats["error"] += 1
            elapsed = data.get("elapsed_ms", 0)
            api_stats["total_ms"] += elapsed
            provider = cat.split(":")[1] if ":" in cat else "unknown"
            api_stats["by_provider"].setdefault(provider, {"count": 0, "total_ms": 0})
            api_stats["by_provider"][provider]["count"] += 1
            api_stats["by_provider"][provider]["total_ms"] += elapsed
        elif cat.startswith("scorer:"):
            scorer_count += 1

        if e.get("level") == "error":
            error_count += 1

    return {
        "scan_id": scan_id or entries[0].get("scan_id", ""),
        "total_entries": len(entries),
        "phases": phases,
        "api_stats": api_stats,
        "error_count": error_count,
        "scorer_count": scorer_count,
    }


def count_scan_logs(scan_id: str) -> int:
    conn = _connect()
    try:
        _ensure_schema(conn)
        return conn.execute(
            "SELECT COUNT(*) FROM scan_logs WHERE scan_id = ?", (scan_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def delete_old_scan_logs(cutoff_ts: float) -> int:
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            cur = conn.execute(
                "DELETE FROM scan_logs WHERE ts < ?", (cutoff_ts,)
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def delete_all_scan_logs() -> int:
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            cur = conn.execute("DELETE FROM scan_logs")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


init_db()