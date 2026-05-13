"""
日志查询模块 — 面向 Agent 的日志读写/清除 API

用法（Python API）:
  from dragon_quant.logging.query import tail_logs, query_logs, clear_logs, list_logs

  # 查看最近 20 条日志
  lines = tail_logs(20)

  # 按条件查询
  errors = query_logs(level="error")
  drive_scores = query_logs(category="scorer:drive", code="600172")

  # 清除 7 天前的日志
  result = clear_logs(days=7)

  # 列出所有日志文件
  files = list_logs()

CLI 用法:
  python -m dragon_quant logs tail [-n 20]
  python -m dragon_quant logs query [--date 20260513] [--category scorer:drive] [--level error] [--code 600172] [--tail 50]
  python -m dragon_quant logs clear [--days 7]
  python -m dragon_quant logs list
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from dragon_quant.storage.paths import LOG_DIR


def _get_log_files(date: Optional[str] = None) -> list[Path]:
    """获取日志文件列表，按时间倒序"""
    if not LOG_DIR.exists():
        return []

    pattern = re.compile(r"^scan_(\d{8}_\d{6})\.jsonl$")
    files = []
    for f in sorted(LOG_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        m = pattern.match(f.name)
        if not m:
            continue
        if date and not m.group(1).startswith(date):
            continue
        files.append(f)
    return files


def tail_logs(lines: int = 20, date: Optional[str] = None) -> list[dict]:
    """读取最新日志文件最后 N 行

    Args:
        lines: 返回行数
        date: 指定日期（YYYYMMDD），默认最新
    """
    files = _get_log_files(date=date)
    if not files:
        return []

    entries = []
    # 倒序读文件取 tail
    with open(files[0]) as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    result = entries[-lines:]
    # 注入文件信息
    for e in result:
        e["_file"] = files[0].name
    return result


def query_logs(date: Optional[str] = None,
               category: Optional[str] = None,
               level: Optional[str] = None,
               code: Optional[str] = None,
               tail: int = 200) -> list[dict]:
    """按条件查询日志条目

    Args:
        date: 日期过滤（YYYYMMDD），默认最新文件
        category: 日志类别过滤，如 "phase", "api", "scorer:drive"
        level: 级别过滤，如 "info", "warn", "error"
        code: 股票代码过滤
        tail: 最多返回条数
    """
    files = _get_log_files(date=date)
    if not files:
        return []

    results = []
    # 从最新文件读
    with open(files[0]) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if category and entry.get("category") != category and \
               not (entry.get("category", "").startswith(category)):
                continue
            if level and entry.get("level") != level:
                continue
            if code and entry.get("code") != code:
                continue

            entry["_file"] = files[0].name
            results.append(entry)
            if len(results) >= tail:
                break

    return results


def clear_logs(days: int = 7) -> dict:
    """清除 N 天前的日志文件

    Args:
        days: 保留最近 N 天的日志，默认 7
    Returns:
        {"cleared": count, "kept": count, "files_removed": [...]}
    """
    if not LOG_DIR.exists():
        return {"cleared": 0, "kept": 0, "files_removed": []}

    cutoff = time.time() - days * 86400
    cleared = 0
    kept = 0
    removed = []

    for f in sorted(LOG_DIR.iterdir()):
        if not f.name.endswith(".jsonl"):
            continue
        if f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                cleared += 1
                removed.append(f.name)
            except OSError:
                pass
        else:
            kept += 1

    return {"cleared": cleared, "kept": kept, "files_removed": removed}


def list_logs() -> list[dict]:
    """列出所有日志文件及元数据"""
    files = _get_log_files()
    result = []
    for f in files:
        stat = f.stat()
        result.append({
            "name": f.name,
            "path": str(f),
            "size_bytes": stat.st_size,
            "size": _fmt_size(stat.st_size),
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            "lines": sum(1 for _ in open(f)),
        })
    return result


def log_summary(date: Optional[str] = None) -> dict:
    """获取最新扫描的摘要信息（api_stats、errors、phases）

    Args:
        date: 日期过滤，默认最新
    Returns:
        {
            "file": str,
            "total_entries": int,
            "phases": {name: message, ...},
            "api_stats": dict,
            "error_count": int,
            "scorer_count": int,
        }
    """
    entries = tail_logs(lines=9999, date=date)
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
        "file": entries[0].get("_file", ""),
        "total_entries": len(entries),
        "phases": phases,
        "api_stats": api_stats,
        "error_count": error_count,
        "scorer_count": scorer_count,
    }


def _fmt_size(b: int) -> str:
    if b < 1024:
        return f"{b}B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f}K"
    return f"{b / (1024 * 1024):.1f}M"
