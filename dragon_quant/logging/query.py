"""
日志查询模块 — 面向 Agent 的日志读写/清除 API

所有数据存储在 SQLite scan_logs 表中，不再依赖 JSONL 文件。

用法（Python API）:
  from dragon_quant.logging.query import tail_logs, query_logs, clear_logs, list_logs

  # 查看最近 20 条日志
  lines = tail_logs(20)

  # 按条件查询
  errors = query_logs(level="error")
  drive_scores = query_logs(category="scorer:drive", code="600172")

  # 清除 N 天前的日志
  result = clear_logs(days=7)

  # 列出所有扫描的日志概要
  folders = list_logs()

CLI 用法:
  python -m dragon_quant logs tail [-n 20]
  python -m dragon_quant logs query [--date 20260513] [--category scorer:drive] [--level error] [--code 600172] [--tail 50]
  python -m dragon_quant logs clear [--days 7]
  python -m dragon_quant logs list
"""

import json
import time
from typing import Optional

from dragon_quant.storage import db as store


def tail_logs(lines: int = 20, date: Optional[str] = None, source: str = "v1") -> list[dict]:
    """读取最新日志最后 N 条

    Args:
        lines: 返回条数
        date: 指定日期（YYYYMMDD），默认最新
    """
    scan_id = None
    if date:
        scan_id = _find_latest_scan_for_date(date, source=source)

    entries = store.get_scan_logs(scan_id=scan_id, tail=lines, source=source)
    return entries


def query_logs(date: Optional[str] = None,
               category: Optional[str] = None,
               level: Optional[str] = None,
               code: Optional[str] = None,
               tail: int = 200,
               source: str = "v1") -> list[dict]:
    """按条件查询日志条目

    Args:
        date: 日期过滤（YYYYMMDD），默认最新
        category: 日志类别过滤，如 "phase", "api", "scorer:drive"
        level: 级别过滤，如 "info", "warn", "error"
        code: 股票代码过滤
        tail: 最多返回条数
    """
    scan_id = None
    if date:
        scan_id = _find_latest_scan_for_date(date, source=source)

    entries = store.get_scan_logs(
        scan_id=scan_id, category=category, level=level, code=code, tail=tail, source=source,
    )
    return entries


def clear_logs(days: int = 7, source: str = "v1") -> dict:
    """清除 N 天前的日志

    Args:
        days: 保留最近 N 天的日志，默认 7
    Returns:
        {"cleared": count, "kept": count, "files_removed": [...]}
    """
    cutoff_ts = time.time() - days * 86400
    cleared = store.delete_old_scan_logs(cutoff_ts, source=source)
    return {"cleared": cleared, "kept": -1, "files_removed": []}


def list_logs(source: str = "v1") -> list[dict]:
    """列出所有扫描的日志概要"""
    folders = store.list_scan_log_folders(source=source)
    return folders


def log_summary(date: Optional[str] = None, source: str = "v1") -> dict:
    """获取最新扫描的摘要信息（api_stats、errors、phases）

    Args:
        date: 日期过滤，默认最新
    Returns:
        {
            "scan_id": str,
            "total_entries": int,
            "phases": {name: message, ...},
            "api_stats": dict,
            "error_count": int,
            "scorer_count": int,
        }
    """
    scan_id = None
    if date:
        scan_id = _find_latest_scan_for_date(date, source=source)

    return store.log_summary(scan_id=scan_id, source=source)


def _find_latest_scan_for_date(date: str, source: str = "v1") -> Optional[str]:
    """按日期前缀查找最新的 scan_id"""
    folders = store.list_scan_log_folders(source=source)
    new_prefix = f"{source}_{date}"
    for f in folders:
        scan_id = f["scan_id"]
        if scan_id.startswith(new_prefix) or scan_id.startswith(date):
            return f["scan_id"]
    return None
