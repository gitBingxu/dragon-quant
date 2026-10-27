"""``scan.cache`` 公共门面 — 扫描专属缓存。

- 通用内存 + 本地持久化实现仍只有 ``infra.storage.cache.DataCache``；
- ``ScanCache`` 只增加 scan 专属键与批量行情便捷读取，键构造统一委托
  :mod:`dragon_quant.scan.sectors`，避免出现第二套键；
- ``create_scan_cache`` 是编排器唯一装配入口（``cache_dir`` 缺省沿用
  ``CACHE_DIR``，构造期不建目录，B1）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from dragon_quant.infra.storage.cache import DataCache
from dragon_quant.scan.sectors import (
    components_key as _components_key,
    sector_1min_key as _sector_1min_key,
    sector_5min_key as _sector_5min_key,
    sector_ranking_key as _sector_ranking_key,
    stock_1min_key as _stock_1min_key,
    stock_5min_key as _stock_5min_key,
)

__all__ = ["ScanCache", "create_scan_cache"]


class ScanCache(DataCache):
    """通用 ``DataCache`` + 扫描专属键构造与批量行情读取。"""

    quotes_key = "quotes:batch"

    # ─── 板块键（委托 scan.sectors 唯一实现）─────────────────────────────

    def sector_ranking_key(self, source: str, category: str) -> str:
        return _sector_ranking_key(source, category)

    def sector_components_key(self, key: str) -> str:
        return _components_key(key)

    def sector_1min_key(self, key: str) -> str:
        return _sector_1min_key(key)

    def sector_5min_key(self, key: str) -> str:
        return _sector_5min_key(key)

    # ─── 个股键（委托 scan.sectors 唯一实现）─────────────────────────────

    def stock_1min_key(self, code: str) -> str:
        return _stock_1min_key(code)

    def stock_5min_key(self, code: str) -> str:
        return _stock_5min_key(code)

    # ─── 批量行情 ─────────────────────────────────────────────────────────

    def quotes(self) -> list:
        """读取本轮批量行情（未加载时返回空列表）。"""
        return self.get(self.quotes_key) or []


def create_scan_cache(cache_dir: Optional[Path] = None) -> ScanCache:
    return ScanCache(cache_dir=cache_dir)
