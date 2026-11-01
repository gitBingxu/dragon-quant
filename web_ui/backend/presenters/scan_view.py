"""``/scans`` 视图投影：运行状态三态、日志摘要与分页（方案 §5.6）。

状态由 ``scan_logs_v2`` 聚合推导（Strategy 表驱动），不新增运行历史表：
``log_entries == 0 → logs_missing``；``error_n > 0 → completed_with_errors``；
否则 ``completed``。未落库的崩溃没有行，页面不展示、不推断。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

PHASE_ORDER = ("A", "B", "C", "D", "F")
PHASE_LABELS = {
    "A": "A · 涨停池",
    "B": "B · 板块榜",
    "C": "C · 情绪/宽度",
    "D": "D · 龙头窗口",
    "F": "F · 评分",
}


def scan_status(log_entries: int, error_n: int | None) -> str:
    if not log_entries:
        return "logs_missing"
    return "completed_with_errors" if (error_n or 0) > 0 else "completed"


def build_scan_row(
    row: Mapping[str, Any],
    stock_stats: Mapping[str, Any] | None,
    log_stats: Mapping[str, Any] | None,
) -> dict:
    """扫描行 + 批量聚合（真龙数 / 日志条数 / 错误数 / 状态）。"""

    scan_id = str(row.get("id") or "")
    stock = (stock_stats or {}).get(scan_id) or {}
    logs = (log_stats or {}).get(scan_id) or {}
    log_entries = int(logs.get("log_entries") or 0)
    error_n = int(logs["error_n"]) if logs else None
    return {
        "id": scan_id,
        "scan_date": row.get("scan_date") or "",
        "elapsed_s": row.get("elapsed_s"),
        "top_n": row.get("top_n"),
        "candidates_n": row.get("candidates_n"),
        "workers": row.get("workers"),
        "created_at": row.get("created_at") or "",
        "source": row.get("source") or "v2",
        "true_dragon_n": stock.get("true_dragon_n"),
        "stock_n": stock.get("stock_n"),
        "log_entries": log_entries,
        "error_n": error_n,
        "status": scan_status(log_entries, error_n),
    }


def build_scan_rows(
    rows: list[dict],
    stock_stats: Mapping[str, Any] | None,
    log_stats: Mapping[str, Any] | None,
) -> list[dict]:
    return [build_scan_row(row, stock_stats, log_stats) for row in rows]


def format_log_time(ts: Any) -> str | None:
    """epoch 秒 → Asia/Shanghai ``HH:MM:SS``；非法返回 ``None``。"""

    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(ts), SHANGHAI).strftime("%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None


def build_log_summary(raw: Mapping[str, Any] | None) -> dict | None:
    """``log_summary()`` 原始返回 → API 摘要；无日志返回 ``None``（不是错误）。"""

    if not isinstance(raw, Mapping) or raw.get("error"):
        return None
    phases_raw = raw.get("phases") if isinstance(raw.get("phases"), Mapping) else {}
    phases = [
        {
            "key": key,
            "label": PHASE_LABELS.get(key, key),
            "message": phases_raw.get(key) or "",
        }
        for key in PHASE_ORDER if key in phases_raw
    ]
    for key in sorted(k for k in phases_raw if k not in PHASE_ORDER):
        phases.append({
            "key": key,
            "label": PHASE_LABELS.get(key, key),
            "message": phases_raw.get(key) or "",
        })
    api = raw.get("api_stats") if isinstance(raw.get("api_stats"), Mapping) else {}
    return {
        "total_entries": int(raw.get("total_entries") or 0),
        "phases": phases,
        "api_total": int(api.get("total") or 0),
        "api_ok": int(api.get("ok") or 0),
        "api_error": int(api.get("error") or 0),
        "api_by_provider": dict(api.get("by_provider") or {}),
        "scorer_count": int(raw.get("scorer_count") or 0),
        "error_count": int(raw.get("error_count") or 0),
        "truncated": False,
    }


def build_scan_logs(
    scan_id: str,
    *,
    page: Mapping[str, Any] | None,
    summary_raw: Mapping[str, Any] | None,
) -> dict:
    """日志分页响应：``available=false`` 表示该次扫描没有保存日志。"""

    total = int((page or {}).get("total") or 0)
    entries = [
        {
            "ts": entry.get("ts"),
            "time": format_log_time(entry.get("ts")),
            "category": entry.get("category") or "",
            "level": entry.get("level") or "",
            "message": entry.get("message") or "",
            "code": entry.get("code") or "",
        }
        for entry in ((page or {}).get("entries") or [])
    ]
    summary = build_log_summary(summary_raw)
    available = bool(summary is not None or total)
    if summary is not None:
        sql_total = int(summary_raw.get("total_entries") or 0)
        summary["truncated"] = sql_total > len(entries) and total == sql_total
    return {
        "scan_id": scan_id,
        "available": available,
        "total": total if available else 0,
        "entries": entries if available else [],
        "summary": summary,
    }


__all__ = [
    "PHASE_LABELS",
    "PHASE_ORDER",
    "build_log_summary",
    "build_scan_logs",
    "build_scan_row",
    "build_scan_rows",
    "format_log_time",
    "scan_status",
]
