"""扫描记录 / 评分明细（scans / scan_stocks）持久化。"""

import json
from typing import Optional

from dragon_quant.storage._base import _connect, _ensure_schema, _lock, _normalize_source, _tables


def save_scan(scan_id: str, scan_date: str, elapsed_s: float,
              top_n: int, candidates_n: int, workers: int,
              stocks: list[dict], raw_output: Optional[str] = None,
              source: str = "v2"):
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            source = _normalize_source(source)
            t = _tables(source)

            conn.execute(
                f"INSERT OR REPLACE INTO {t['scans']}("
                f"id, scan_date, elapsed_s, top_n, candidates_n, workers, raw_output, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (scan_id, scan_date, elapsed_s, top_n, candidates_n, workers, raw_output, source),
            )

            conn.execute(f"DELETE FROM {t['scan_stocks']} WHERE scan_id = ?", (scan_id,))

            rows = []
            for i, s in enumerate(stocks):
                dims = s.get("dimensions", {})
                concepts = s.get("concepts", [])
                rows.append((
                    scan_id,
                    s.get("code", ""),
                    s.get("name", ""),
                    s.get("rank", i + 1),
                    s.get("composite_score", 0),
                    s.get("board_count", 0),
                    json.dumps(concepts, ensure_ascii=False),
                    dims.get("drive", {}).get("score"),
                    dims.get("anti_drop", {}).get("score"),
                    dims.get("leadership", {}).get("score"),
                    dims.get("absorption", {}).get("score"),
                    dims.get("liquidity", {}).get("score"),
                    1 if s.get("is_true_dragon") else 0 if "is_true_dragon" in s else None,
                    s.get("reject_reason"),
                    s.get("report_text", ""),
                    s.get("open_px"),
                    s.get("close_px"),
                    s.get("high_px"),
                    s.get("low_px"),
                    s.get("pct"),
                    s.get("turnover_rate"),
                    s.get("amount"),
                    s.get("market_cap"),
                    source,
                ))

            conn.executemany(
                f"INSERT INTO {t['scan_stocks']}("
                "scan_id, code, name, rank, composite_score, "
                "board_count, concepts_json, dim_drive, dim_anti_drop, dim_leadership, dim_absorption, "
                "dim_liquidity, is_true_dragon, reject_reason, report_text, "
                "open_px, close_px, high_px, low_px, pct, turnover_rate, amount, market_cap, source"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

            conn.commit()
        finally:
            conn.close()


def list_scans(limit: int = 50, source: str = "v2") -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        rows = conn.execute(
            "SELECT id, scan_date, elapsed_s, top_n, candidates_n, workers, created_at "
            f"FROM {t['scans']} ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r[0], "scan_date": r[1], "elapsed_s": r[2],
                "top_n": r[3], "candidates_n": r[4], "workers": r[5],
                "created_at": r[6], "source": source,
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_latest_scan_by_date(scan_date: str, top_n: int, source: str = "v2") -> Optional[dict]:
    """按日期和 top_n 获取最新的一次扫描记录"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        row = conn.execute(
            "SELECT id, scan_date, elapsed_s, top_n, candidates_n, workers, created_at, raw_output "
            f"FROM {t['scans']} WHERE scan_date = ? AND top_n = ? ORDER BY created_at DESC LIMIT 1",
            (scan_date, top_n)
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "scan_date": row[1], "elapsed_s": row[2],
            "top_n": row[3], "candidates_n": row[4], "workers": row[5],
            "created_at": row[6], "raw_output": row[7], "source": source,
        }
    finally:
        conn.close()


def get_scan(scan_id: str, source: str = "v2") -> Optional[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        row = conn.execute(
            "SELECT id, scan_date, elapsed_s, top_n, candidates_n, workers, created_at, raw_output "
            f"FROM {t['scans']} WHERE id = ?",
            (scan_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "scan_date": row[1], "elapsed_s": row[2],
            "top_n": row[3], "candidates_n": row[4], "workers": row[5],
            "created_at": row[6], "raw_output": row[7], "source": source,
        }
    finally:
        conn.close()


def get_scans_by_date(scan_date: str, source: str = "v2") -> list[dict]:
    """返回某日期下所有 scan 记录（不同 top_n）。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        rows = conn.execute(
            "SELECT id, scan_date, elapsed_s, top_n, candidates_n, workers, created_at "
            f"FROM {t['scans']} WHERE scan_date = ? ORDER BY top_n",
            (scan_date,),
        ).fetchall()
        return [
            {
                "id": r[0], "scan_date": r[1], "elapsed_s": r[2],
                "top_n": r[3], "candidates_n": r[4], "workers": r[5],
                "created_at": r[6], "source": source,
            }
            for r in rows
        ]
    finally:
        conn.close()


def delete_scans_by_date_topn(scan_date: str, top_n: int, source: str = "v2") -> int:
    """删除指定日期 + top_n 的所有扫描 run（硬删除）。

    Returns:
        删除的 scans 数量。
    """
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            source = _normalize_source(source)
            t = _tables(source)
            rows = conn.execute(
                f"SELECT id FROM {t['scans']} WHERE scan_date = ? AND top_n = ?",
                (scan_date, top_n),
            ).fetchall()
            scan_ids = [r[0] for r in rows]
            if not scan_ids:
                return 0

            placeholders = ",".join(["?"] * len(scan_ids))
            conn.execute(f"DELETE FROM {t['scan_stocks']} WHERE scan_id IN ({placeholders})", scan_ids)
            conn.execute(f"DELETE FROM {t['scan_logs']} WHERE scan_id IN ({placeholders})", scan_ids)
            conn.execute(f"DELETE FROM {t['scans']} WHERE id IN ({placeholders})", scan_ids)
            conn.commit()
            return len(scan_ids)
        finally:
            conn.close()


def list_scan_stock_contributions_by_date(scan_date: str, source: str = "v2") -> list[dict]:
    """返回某日期下所有扫描 run 的贡献明细（scan_stocks join scans）。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        rows = conn.execute(
            "SELECT "
            "  s.id, s.top_n, s.created_at, "
            "  ss.code, ss.name, ss.rank, ss.composite_score, ss.board_count, "
            "  ss.concepts_json, ss.report_text, "
            "  ss.open_px, ss.close_px, ss.high_px, ss.low_px, ss.pct, "
            "  ss.turnover_rate, ss.amount, ss.market_cap, ss.is_true_dragon "
            f"FROM {t['scans']} s "
            f"JOIN {t['scan_stocks']} ss ON ss.scan_id = s.id "
            "WHERE s.scan_date = ?",
            (scan_date,),
        ).fetchall()

        result = []
        for r in rows:
            result.append({
                "scan_id": r[0],
                "scan_top_n": r[1],
                "scan_created_at": r[2],
                "code": r[3],
                "name": r[4],
                "rank": r[5],
                "composite_score": r[6],
                "board_count": r[7],
                "concepts": json.loads(r[8]) if r[8] else [],
                "report_text": r[9] or "",
                "open_px": r[10],
                "close_px": r[11],
                "high_px": r[12],
                "low_px": r[13],
                "pct": r[14],
                "turnover_rate": r[15],
                "amount": r[16],
                "market_cap": r[17],
                "is_true_dragon": bool(r[18]) if r[18] is not None else None,
                "source": source,
            })
        return result
    finally:
        conn.close()


def get_scan_stocks(scan_id: str, source: str = "v2") -> list[dict]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        rows = conn.execute(
            "SELECT code, name, rank, composite_score, board_count, concepts_json, "
            "dim_drive, dim_anti_drop, dim_leadership, dim_absorption, dim_liquidity, "
            "is_true_dragon, reject_reason, report_text "
            f"FROM {t['scan_stocks']} WHERE scan_id = ? ORDER BY rank",
            (scan_id,),
        ).fetchall()
        return [
            {
                "code": r[0], "name": r[1], "rank": r[2],
                "composite_score": r[3], "board_count": r[4],
                "concepts": json.loads(r[5]) if r[5] else [],
                "dim_drive": r[6], "dim_anti_drop": r[7],
                "dim_leadership": r[8], "dim_absorption": r[9],
                "dim_liquidity": r[10],
                "is_true_dragon": bool(r[11]) if r[11] is not None else None,
                "reject_reason": r[12],
                "report_text": r[13] or "",
                "source": source,
            }
            for r in rows
        ]
    finally:
        conn.close()


def has_scan(scan_id: str, source: str = "v2") -> bool:
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        row = conn.execute(f"SELECT 1 FROM {t['scans']} WHERE id = ?", (scan_id,)).fetchone()
        return row is not None
    finally:
        conn.close()

