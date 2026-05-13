"""
DataCache — 内存 + 本地双重缓存

流程：调用方 → DataCache(先查缓存) → RateLimiter(并发控制) → Provider(HTTP)

缓存策略：
  - 板块行情/个股K线 → 当天有效（过期=次日00:00）
  - 涨停榜 → 当天有效
  - 写入本地 JSON 做持久化（subprocess 共享）
"""

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional


def _today_end() -> int:
    """当日 23:59:59 的时间戳（秒）"""
    t = time.localtime()
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 23, 59, 59, 0, 0, 0)))


def _to_json_safe(obj):
    """递归转换 dataclass / bytes 为 JSON 安全类型"""
    if hasattr(obj, '__dataclass_fields__'):
        return {
            f.name: _to_json_safe(getattr(obj, f.name))
            for f in obj.__dataclass_fields__.values()
        }
    if isinstance(obj, list):
        return [_to_json_safe(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, bytes):
        return obj.decode('utf-8', errors='replace')
    return obj


class CacheEntry:
    __slots__ = ("data", "ttl")

    def __init__(self, data: Any, ttl: int):
        self.data = data
        self.ttl = ttl

    @property
    def expired(self) -> bool:
        return time.time() > self.ttl


class DataCache:
    """内存缓存 + 可选的本地持久化"""

    def __init__(self, cache_dir: Optional[Path] = None):
        self._mem: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()

        if cache_dir is None:
            from dragon_quant.storage.paths import CACHE_DIR
            cache_dir = CACHE_DIR

        self._cache_dir = cache_dir
        self._hit = 0
        self._miss = 0

    # ─── 内存操作 ───

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._mem.get(key)
        if entry is None:
            self._miss += 1
            return None
        if entry.expired:
            with self._lock:
                del self._mem[key]
            self._miss += 1
            return None
        self._hit += 1
        return entry.data

    def set(self, key: str, data: Any, ttl: Optional[int] = None):
        if ttl is None:
            ttl = _today_end()
        with self._lock:
            self._mem[key] = CacheEntry(data, ttl)
        # 持久化
        if self._cache_dir:
            self._persist(key, data)

    def invalidate(self, key: str):
        with self._lock:
            self._mem.pop(key, None)
        if self._cache_dir:
            p = self._cache_dir / f"{key}.json"
            if p.exists():
                p.unlink()

    def clear_expired(self):
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._mem.items() if v.expired]
            for k in expired:
                del self._mem[k]

    def snapshot(self) -> dict[str, Any]:
        """导出全部未过期数据快照（用于共享缓存）"""
        now = time.time()
        result = {}
        with self._lock:
            for k, v in self._mem.items():
                if v.ttl > now:
                    result[k] = v.data
        return result

    def load_snapshot(self, data: dict[str, Any], ttl: Optional[int] = None):
        """从外部加载数据到缓存"""
        if ttl is None:
            ttl = _today_end()
        with self._lock:
            for k, v in data.items():
                self._mem[k] = CacheEntry(v, ttl)

    # ─── 本地持久化 ───

    def _persist(self, key: str, data: Any):
        if not self._cache_dir:
            return
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            safe = _to_json_safe(data)
            with open(self._cache_dir / f"{key}.json", "w") as f:
                json.dump({"data": safe, "ts": time.time()}, f, ensure_ascii=False)
        except Exception as e:
            print(f"  ⚠️ 缓存持久化失败 {key}: {e}", file=sys.stderr)

    def load_persisted(self, key: str, max_age: int = 86400) -> Optional[Any]:
        """从本地文件恢复缓存"""
        if not self._cache_dir:
            return None
        p = self._cache_dir / f"{key}.json"
        if not p.exists():
            return None
        try:
            with open(p) as f:
                blob = json.load(f)
            age = time.time() - blob.get("ts", 0)
            if age > max_age:
                p.unlink()
                return None
            data = blob["data"]
            self.set(key, data, ttl=int(time.time()) + max_age - int(age))
            return data
        except Exception:
            return None

    # ─── 统计 ───

    def stats(self) -> dict:
        return {"hit": self._hit, "miss": self._miss, "size": len(self._mem)}

    # ─── 助手 ───

    def cache_key(self, provider: str, endpoint: str, *args) -> str:
        parts = [provider, endpoint]
        parts.extend(str(a) for a in args)
        return ":".join(parts)
