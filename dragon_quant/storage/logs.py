"""结构化扫描日志（scan_logs）持久化与查询。"""

import json
from typing import Optional

from dragon_quant.storage._base import _connect, _ensure_schema, _lock, _normalize_source, _tables


def save_scan_logs(scan_id: str, entries: list[dict], source: str = "v2"):
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            source = _normalize_source(source)
            t = _tables(source)
            conn.execute(f"DELETE FROM {t['scan_logs']} WHERE scan_id = ?", (scan_id,))

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
                    source,
                ))

            conn.executemany(
                f"INSERT INTO {t['scan_logs']}(scan_id, ts, category, level, message, code, data_json, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()


def get_scan_logs(scan_id: Optional[str] = None,
                  category: Optional[str] = None,
                  level: Optional[str] = None,
                  code: Optional[str] = None,
                  tail: int = 200,
                  source: str = "v2") -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
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
            f"FROM {t['scan_logs']} {where} ORDER BY ts DESC LIMIT ?",
            params,
        ).fetchall()

        return [
            {
                "scan_id": r[0], "ts": r[1], "category": r[2],
                "level": r[3], "message": r[4], "code": r[5],
                "data": json.loads(r[6]) if r[6] else {},
                "source": source,
            }
            for r in rows
        ]
    finally:
        conn.close()


def list_scan_log_folders(source: str = "v2") -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        rows = conn.execute(
            "SELECT scan_id, COUNT(*) as cnt, MIN(ts) as first_ts, MAX(ts) as last_ts "
            f"FROM {t['scan_logs']} GROUP BY scan_id ORDER BY scan_id DESC"
        ).fetchall()
        return [
            {
                "scan_id": r[0], "entries": r[1],
                "first_ts": r[2], "last_ts": r[3],
                "source": source,
            }
            for r in rows
        ]
    finally:
        conn.close()


def log_summary(scan_id: Optional[str] = None, source: str = "v2") -> dict:
    source = _normalize_source(source)
    entries = get_scan_logs(scan_id=scan_id, tail=99999, source=source)
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
        "source": source,
    }


def count_scan_logs(scan_id: str, source: str = "v2") -> int:
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        return conn.execute(
            f"SELECT COUNT(*) FROM {t['scan_logs']} WHERE scan_id = ?", (scan_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def delete_old_scan_logs(cutoff_ts: float, source: str = "v2") -> int:
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            source = _normalize_source(source)
            t = _tables(source)
            cur = conn.execute(
                f"DELETE FROM {t['scan_logs']} WHERE ts < ?", (cutoff_ts,)
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def delete_all_scan_logs(source: str = "v2") -> int:
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            source = _normalize_source(source)
            t = _tables(source)
            cur = conn.execute(f"DELETE FROM {t['scan_logs']}")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

