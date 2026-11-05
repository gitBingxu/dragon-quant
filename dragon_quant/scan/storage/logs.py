"""结构化扫描日志（scan_logs）持久化与查询。"""

import json
from typing import Optional

from dragon_quant.infra import clock
from dragon_quant.infra.storage import connection as _connection
from dragon_quant.infra.storage._base import _connect, _ensure_schema, _lock, _normalize_source, _tables


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


def get_scan_logs_page(scan_id: str, level: Optional[str] = None,
                       offset: int = 0, limit: int = 200,
                       source: str = "v2") -> dict:
    """日志正文分页（``ORDER BY ts DESC``），**不返回 ``data_json``**。

    返回 ``{"total": 过滤后总条数, "entries": [{ts, category, level, message, code}]}``；
    无日志返回 ``{"total": 0, "entries": []}``，不是错误（方案 §4.1）。
    """
    total = count_scan_logs_filtered(scan_id, level=level, source=source)
    if total == 0:
        return {"total": 0, "entries": []}
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        conditions = ["scan_id = ?"]
        params: list = [scan_id]
        if level:
            conditions.append("level = ?")
            params.append(level)
        params.extend([max(1, int(limit)), max(0, int(offset))])
        rows = conn.execute(
            "SELECT ts, category, level, message, code "
            f"FROM {t['scan_logs']} WHERE {' AND '.join(conditions)} "
            "ORDER BY ts DESC, rowid DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return {
            "total": total,
            "entries": [
                {
                    "ts": row[0],
                    "category": row[1] or "",
                    "level": row[2] or "",
                    "message": row[3] or "",
                    "code": row[4] or "",
                }
                for row in rows
            ],
        }
    finally:
        conn.close()


def count_scan_logs_filtered(scan_id: str, level: Optional[str] = None,
                             source: str = "v2") -> int:
    """按级别过滤后的日志条数（分页总数；不解析 ``data_json``）。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        conditions = ["scan_id = ?"]
        params: list = [scan_id]
        if level:
            conditions.append("level = ?")
            params.append(level)
        return conn.execute(
            f"SELECT COUNT(*) FROM {t['scan_logs']} WHERE {' AND '.join(conditions)}",
            params,
        ).fetchone()[0]
    finally:
        conn.close()


def summarize_scan_logs(scan_ids: list[str], source: str = "v2") -> dict[str, dict]:
    """批量聚合 ``scan_logs``：``{scan_id: {log_entries, error_n, first_ts, last_ts}}``。

    列表页状态推导的唯一来源：无日志的 scan 不出现在结果里（调用方按 0/null 处理），
    不逐行调用 :func:`log_summary`（方案 §5.6，避免 N+1）。
    """
    ids = [str(scan_id) for scan_id in (scan_ids or []) if scan_id]
    if not ids:
        return {}
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            "SELECT scan_id, COUNT(*), "
            "SUM(CASE WHEN level = 'error' THEN 1 ELSE 0 END), "
            "MIN(ts), MAX(ts) "
            f"FROM {t['scan_logs']} WHERE scan_id IN ({placeholders}) "
            "GROUP BY scan_id",
            ids,
        ).fetchall()
        return {
            row[0]: {
                "log_entries": int(row[1] or 0),
                "error_n": int(row[2] or 0),
                "first_ts": row[3],
                "last_ts": row[4],
            }
            for row in rows
        }
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


def clear_scan_logs(days: Optional[int] = None) -> int:
    """清除 dragon.db 中的扫描日志行（v1 + v2），返回删除行数。

    ``storage clear --logs/--all`` 的数据库侧职责归日志仓储本模块所有；
    ``infra.storage`` 只负责文件系统清理，不得反向依赖 feature。
    库文件不存在时直接返回 0（B1：不为「清理日志」凭空建空库）；单次删除失败
    不中断（与既有 ``StorageManager.clear_logs`` 的静默语义一致）。
    """
    if not _connection.DB_PATH.exists():
        return 0
    removed = 0
    try:
        if days:
            cutoff_ts = clock.now().timestamp() - days * 86400
            removed += delete_old_scan_logs(cutoff_ts, source="v1")
            removed += delete_old_scan_logs(cutoff_ts, source="v2")
        else:
            removed += delete_all_scan_logs(source="v1")
            removed += delete_all_scan_logs(source="v2")
    except Exception:
        pass
    return removed

