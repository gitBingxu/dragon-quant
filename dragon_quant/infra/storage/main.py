"""infra.storage 公共门面（唯一批准出口，**无** ``transaction``）。

- ``init_db`` — dragon.db 幂等建目录 + schema；
- ``DataCache`` — 通用内存 + 本地持久化缓存（scan 专属 ScanCache 不在此层）；
- ``StorageManager`` — 数据目录查询 / 清理 / 体积统计。

领域仓储（scans / dragons / logs / ...）不在此处导出，由各自 feature/storage
模块拥有。
"""

from __future__ import annotations

from dragon_quant.infra.storage.cache import DataCache
from dragon_quant.infra.storage.connection import init_db
from dragon_quant.infra.storage.manager import StorageManager

__all__ = ["init_db", "DataCache", "StorageManager"]
