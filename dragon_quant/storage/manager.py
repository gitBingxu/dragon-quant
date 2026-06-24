"""
StorageManager — 统一持久化数据管理

支持查看 / 清理 / 磁盘占用统计。
"""

import os
import shutil
import time
from pathlib import Path

from dragon_quant.storage.paths import (
    DATA_DIR, COOKIE_DIR, CACHE_DIR, LOG_DIR, RESULTS_DIR, SHARED_DIR,
)


def _dir_info(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False, "files": 0, "bytes": 0}

    files = list(path.iterdir())
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return {
        "path": str(path),
        "exists": True,
        "files": len(files),
        "bytes": total,
        "size": _fmt_size(total),
    }


def _fmt_size(b: int) -> str:
    if b < 1024:
        return f"{b}B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f}K"
    return f"{b / (1024 * 1024):.1f}M"


def _clear_dir(path: Path, days: int = None):
    if not path.exists():
        return 0

    removed = 0
    now = time.time()
    cutoff = now - days * 86400 if days else None

    for f in path.iterdir():
        if cutoff and f.stat().st_mtime > cutoff:
            continue
        try:
            if f.is_file():
                f.unlink()
                removed += 1
            elif f.is_dir():
                shutil.rmtree(f)
                removed += 1
        except OSError:
            pass
    return removed


class StorageManager:

    def status(self) -> dict:
        return {
            "data_dir": str(DATA_DIR),
            "cookies": _dir_info(COOKIE_DIR),
            "cache": _dir_info(CACHE_DIR),
            "logs": _dir_info(LOG_DIR),
            "results": _dir_info(RESULTS_DIR),
            "shared": _dir_info(SHARED_DIR),
        }

    def size(self) -> dict:
        total = 0
        parts = {}
        for key, d in [("cookies", COOKIE_DIR), ("cache", CACHE_DIR),
                       ("logs", LOG_DIR), ("results", RESULTS_DIR),
                       ("shared", SHARED_DIR)]:
            info = _dir_info(d)
            parts[key] = info["bytes"]
            total += info["bytes"]
        return {"total_bytes": total, "total": _fmt_size(total), "by_dir": parts}

    def clear_cache(self) -> int:
        return _clear_dir(CACHE_DIR)

    def clear_results(self, days: int = None) -> int:
        return _clear_dir(RESULTS_DIR, days=days)

    def clear_logs(self, days: int = None) -> int:
        removed = _clear_dir(LOG_DIR, days=days)
        try:
            from dragon_quant.storage import db
            if days:
                cutoff_ts = time.time() - days * 86400
                removed += db.delete_old_scan_logs(cutoff_ts, source="v1")
                removed += db.delete_old_scan_logs(cutoff_ts, source="v2")
            else:
                removed += db.delete_all_scan_logs(source="v1")
                removed += db.delete_all_scan_logs(source="v2")
        except Exception:
            pass
        return removed

    def clear_all(self) -> dict:
        return {
            "cache": self.clear_cache(),
            "results": self.clear_results(),
            "logs": self.clear_logs(),
            "shared": _clear_dir(SHARED_DIR),
        }


manager = StorageManager()
