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

CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         TEXT NOT NULL REFERENCES scans(id),
    review_date     TEXT NOT NULL,
    trading_days    INTEGER DEFAULT 5,
    benchmark_return REAL,
    avg_return      REAL,
    win_rate        REAL,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS review_stocks (
    review_id       INTEGER REFERENCES reviews(id),
    scan_id         TEXT NOT NULL,
    code            TEXT NOT NULL,
    name            TEXT,
    scan_score      REAL,
    entry_date      TEXT,
    entry_price     REAL,
    exit_date       TEXT,
    exit_price      REAL,
    return_pct      REAL,
    excess_return   REAL,
    entry_type      TEXT DEFAULT 'daily',
    note            TEXT,
    UNIQUE(scan_id, code)
);

CREATE INDEX IF NOT EXISTS idx_scan_stocks_code ON scan_stocks(code);
CREATE INDEX IF NOT EXISTS idx_scan_stocks_scan ON scan_stocks(scan_id);
CREATE INDEX IF NOT EXISTS idx_review_stocks_code ON review_stocks(code);
CREATE INDEX IF NOT EXISTS idx_review_stocks_scan ON review_stocks(scan_id);

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
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(trade_date, code)
);

CREATE INDEX IF NOT EXISTS idx_dragons_date ON dragons(trade_date);
CREATE INDEX IF NOT EXISTS idx_dragons_code ON dragons(code);

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


def save_dragons(trade_date: str, scan_id: str, dragons: list[dict]):
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
                ))
            
            conn.executemany(
                "INSERT OR REPLACE INTO dragons("
                "trade_date, code, name, scan_id, rank, composite_score, board_count, "
                "open_px, close_px, high_px, low_px, pct, turnover_rate, amount, market_cap, "
                "concepts_json, report_text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            "concepts_json, report_text "
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
            }
            for r in rows
        ]
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


def save_review(scan_id: str, review_date: str, trading_days: int,
                benchmark_return: float, avg_return: float, win_rate: float,
                details: list[dict]) -> int:
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)

            # 清理旧的 review_stocks 和 reviews，防止产生孤儿数据
            conn.execute("DELETE FROM review_stocks WHERE scan_id = ?", (scan_id,))
            conn.execute("DELETE FROM reviews WHERE scan_id = ?", (scan_id,))

            cursor = conn.execute(
                "INSERT INTO reviews(scan_id, review_date, trading_days, benchmark_return, avg_return, win_rate) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (scan_id, review_date, trading_days, benchmark_return, avg_return, win_rate),
            )
            review_id = cursor.lastrowid

            rows = []
            for d in details:
                rows.append((
                    review_id, scan_id,
                    d.get("code", ""), d.get("name", ""),
                    d.get("scan_score"), d.get("entry_date"),
                    d.get("entry_price"), d.get("exit_date"),
                    d.get("exit_price"), d.get("return_pct"),
                    d.get("excess_return"), d.get("entry_type", "daily"),
                    d.get("note", ""),
                ))

            conn.executemany(
                "INSERT INTO review_stocks(review_id, scan_id, code, name, scan_score, "
                "entry_date, entry_price, exit_date, exit_price, return_pct, "
                "excess_return, entry_type, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

            conn.commit()
            return review_id
        finally:
            conn.close()


def get_review(scan_id: str) -> Optional[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT id, scan_id, review_date, trading_days, benchmark_return, "
            "avg_return, win_rate, created_at FROM reviews WHERE scan_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (scan_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "scan_id": row[1], "review_date": row[2],
            "trading_days": row[3], "benchmark_return": row[4],
            "avg_return": row[5], "win_rate": row[6], "created_at": row[7],
        }
    finally:
        conn.close()


def get_review_stocks(scan_id: str) -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT code, name, scan_score, entry_date, entry_price, exit_date, "
            "exit_price, return_pct, excess_return, entry_type, note "
            "FROM review_stocks WHERE scan_id = ? ORDER BY return_pct DESC",
            (scan_id,),
        ).fetchall()
        return [
            {
                "code": r[0], "name": r[1], "scan_score": r[2],
                "entry_date": r[3], "entry_price": r[4],
                "exit_date": r[5], "exit_price": r[6],
                "return_pct": r[7], "excess_return": r[8],
                "entry_type": r[9], "note": r[10],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_stock_review_history(code: str, limit: int = 20) -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT rs.scan_id, rs.scan_score, rs.entry_date, rs.return_pct, "
            "rs.excess_return, r.review_date "
            "FROM review_stocks rs "
            "JOIN reviews r ON rs.review_id = r.id "
            "WHERE rs.code = ? ORDER BY r.review_date DESC LIMIT ?",
            (code, limit),
        ).fetchall()
        return [
            {
                "scan_id": r[0], "scan_score": r[1], "entry_date": r[2],
                "return_pct": r[3], "excess_return": r[4], "review_date": r[5],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_review_stats() -> dict:
    conn = _connect()
    try:
        _ensure_schema(conn)
        total = conn.execute("SELECT COUNT(*) FROM review_stocks").fetchone()[0]
        if total == 0:
            return {"total": 0, "win_rate": 0, "avg_return": 0,
                    "avg_excess": 0, "best_return": 0, "worst_return": 0}

        win = conn.execute(
            "SELECT COUNT(*) FROM review_stocks WHERE return_pct > 0"
        ).fetchone()[0]

        agg = conn.execute(
            "SELECT AVG(return_pct), AVG(excess_return), MAX(return_pct), MIN(return_pct) "
            "FROM review_stocks"
        ).fetchone()

        return {
            "total": total, "win_rate": round(win / total, 4),
            "avg_return": round(agg[0] or 0, 2),
            "avg_excess": round(agg[1] or 0, 2),
            "best_return": round(agg[2] or 0, 2),
            "worst_return": round(agg[3] or 0, 2),
        }
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