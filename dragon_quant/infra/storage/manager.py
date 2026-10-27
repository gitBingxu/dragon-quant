"""
StorageManager — 统一持久化数据管理

支持查看 / 清理 / 磁盘占用统计。
"""

import shutil
from pathlib import Path
from typing import Optional

from dragon_quant.infra import clock
from dragon_quant.infra.storage.paths import (
    DATA_DIR, COOKIE_DIR, CACHE_DIR, LOG_DIR, RESULTS_DIR, SHARED_DIR,
    DB_PATH, ASSETS_DB_PATH, LEGACY_PREP_DB_PATH,
)


def _db_info(path: Path) -> dict:
    """单个 .db 文件的存在性与体积（clear --all 不清理任何 .db，仅供观察）。"""
    if not path.exists():
        return {"path": str(path), "exists": False, "bytes": 0}
    size = path.stat().st_size
    return {"path": str(path), "exists": True, "bytes": size, "size": _fmt_size(size)}


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


def _clear_dir(path: Path, days: Optional[int] = None):
    if not path.exists():
        return 0

    removed = 0
    now = clock.now().timestamp()
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
            "dragon_db": _db_info(DB_PATH),
            "assets_db": _db_info(ASSETS_DB_PATH),
            "legacy_prep_db": _db_info(LEGACY_PREP_DB_PATH),
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

    def clear_results(self, days: Optional[int] = None) -> int:
        return _clear_dir(RESULTS_DIR, days=days)

    def clear_logs(self, days: Optional[int] = None) -> int:
        """清理日志**文件**；scan_logs 数据行由 ``scan.storage.logs.clear_scan_logs`` 负责。"""
        return _clear_dir(LOG_DIR, days=days)

    def clear_all(self) -> dict:
        protected = [str(p) for p in (DB_PATH, ASSETS_DB_PATH, LEGACY_PREP_DB_PATH)
                     if p.exists()]
        return {
            "cache": self.clear_cache(),
            "results": self.clear_results(),
            "logs": self.clear_logs(),
            "shared": _clear_dir(SHARED_DIR),
            # 独立字段，绝不混入「清理计数」：数据库文件必须保留。
            "protected_databases": protected,
        }


manager = StorageManager()
