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
import uuid
from dataclasses import fields, MISSING
from pathlib import Path
from typing import Any, Optional

from dragon_quant.models.types import KBar, StockInfo, Quote, SectorPerformance


def _today_end() -> int:
    """当日 23:59:59 的时间戳（秒）"""
    t = time.localtime()
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 23, 59, 59, 0, 0, 0)))


# ─── 按交易日磁盘缓存：key 前缀 → dataclass 类型注册表 ───
# 按前缀长度降序匹配，解决 `kline:1min:sector:` 与 `kline:1min:` 重叠。
_TYPE_REGISTRY: list[tuple[str, type, bool]] = sorted(
    [
        ("kline:1min:sector:", KBar, True),
        ("kline:5min:sector:", KBar, True),
        ("sector:components:", StockInfo, True),
        ("kline:1min:", KBar, True),
        ("kline:day:", KBar, True),
        ("quotes:batch", Quote, True),
        ("sector:ranking", SectorPerformance, True),
    ],
    key=lambda x: len(x[0]),
    reverse=True,
)


def _resolve_type(key: str) -> Optional[tuple[type, bool]]:
    """按 key 匹配目标 dataclass。返回 (cls, is_list) 或 None（不参与还原）。"""
    for prefix, cls, is_list in _TYPE_REGISTRY:
        if key == prefix or key.startswith(prefix):
            return cls, is_list
    return None


def _from_dict(cls: type, d: dict) -> Any:
    """把 dict 还原成 dataclass，缺失字段用默认值/零值，多余字段忽略。"""
    if not isinstance(d, dict):
        return d
    _ZERO = {int: 0, float: 0.0, str: "", bool: False}
    kwargs = {}
    for f in fields(cls):
        if f.name in d:
            val = d[f.name]
        elif f.default is not MISSING:
            val = f.default
        elif f.default_factory is not MISSING:  # type: ignore[misc]
            val = f.default_factory()  # type: ignore[misc]
        else:
            val = _ZERO.get(f.type if isinstance(f.type, type) else None, None)
        kwargs[f.name] = val
    obj = cls(**kwargs)
    # 轻量类型矫正
    if cls is KBar and not isinstance(obj.timestamp, int):
        try:
            obj.timestamp = int(obj.timestamp)
        except (TypeError, ValueError):
            pass
    if hasattr(obj, "code") and obj.code is not None and not isinstance(obj.code, str):
        obj.code = str(obj.code)
    return obj


def _deserialize(key: str, raw: Any) -> Any:
    """按 key 类型注册表把磁盘 raw（dict/list[dict]）还原成 dataclass。"""
    resolved = _resolve_type(key)
    if resolved is None:
        return raw
    cls, is_list = resolved
    if is_list:
        if not isinstance(raw, list):
            return raw
        return [_from_dict(cls, x) for x in raw if isinstance(x, dict)]
    return _from_dict(cls, raw) if isinstance(raw, dict) else raw



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

    # ─── 按交易日的 provider 响应缓存 ───

    @staticmethod
    def _safe_name(key: str) -> str:
        """key → 文件名安全形式（冒号转下划线，Windows 兼容）。"""
        return key.replace(":", "_")

    def _trade_date_path(self, key: str, trade_date: str,
                         namespace: str = "") -> Optional[Path]:
        if not self._cache_dir:
            return None
        d = self._cache_dir / "provider" / trade_date
        suffix = f"__{namespace}" if namespace else ""
        return d / f"{self._safe_name(key)}{suffix}.json"

    def set_for_trade_date(self, key: str, data: Any, trade_date: str,
                           namespace: str = ""):
        """写内存 + 原子写盘到交易日命名空间目录。"""
        # 内存：保持本轮 scorer 的 cache.get 行为不变
        with self._lock:
            self._mem[key] = CacheEntry(data, _today_end())
        p = self._trade_date_path(key, trade_date, namespace)
        if p is None:
            return
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            safe = _to_json_safe(data)
            tmp = p.with_name(f"{p.stem}.{uuid.uuid4().hex}.tmp")
            with open(tmp, "w") as f:
                json.dump({"data": safe, "ts": time.time()}, f, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception as e:
            print(f"  ⚠️ 交易日缓存写入失败 {key}: {e}", file=sys.stderr)

    def load_for_trade_date(self, key: str, trade_date: str,
                            namespace: str = "") -> Optional[Any]:
        """先查内存；未命中读交易日磁盘缓存并反序列化回 dataclass，回填内存。"""
        mem = self.get(key)
        if mem is not None:
            return mem
        p = self._trade_date_path(key, trade_date, namespace)
        if p is None or not p.exists():
            return None
        try:
            with open(p) as f:
                blob = json.load(f)
            restored = _deserialize(key, blob.get("data"))
        except Exception:
            return None
        if restored is None:
            return None
        self.set(key, restored, ttl=_today_end())
        return restored

    # ─── 统计 ───

    def stats(self) -> dict:
        return {"hit": self._hit, "miss": self._miss, "size": len(self._mem)}

    # ─── 助手 ───

    def cache_key(self, provider: str, endpoint: str, *args) -> str:
        parts = [provider, endpoint]
        parts.extend(str(a) for a in args)
        return ":".join(parts)
