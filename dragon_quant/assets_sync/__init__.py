"""数据资产云端同步。

模块边界：
  - codec      规范化 JSON / gzip / 哈希
  - models     值对象与报告
  - backends   Strategy：S3Backend / LocalDirBackend
  - sources    Adapter：assets.db 的 prep_assets / auction_assets（M2）
  - manifest   每日 manifest 构建 / 解析 / 重建（M3）
  - state      state.json 原子读写 + 进程锁（M3）
  - service    push / pull / status / verify（M3）
"""

from dragon_quant.assets_sync.errors import (
    AmbiguousError,
    AssetsSyncError,
    ConfigError,
    IntegrityError,
    LockBusyError,
    NotFoundError,
    StorageError,
)
from dragon_quant.assets_sync.models import (
    AssetShard,
    Manifest,
    ManifestEntry,
    ObjectMeta,
    RestoreReport,
    ShardBatch,
    SyncReport,
)

__all__ = [
    "AmbiguousError",
    "AssetShard",
    "AssetsSyncError",
    "ConfigError",
    "IntegrityError",
    "LockBusyError",
    "Manifest",
    "ManifestEntry",
    "NotFoundError",
    "ObjectMeta",
    "RestoreReport",
    "ShardBatch",
    "StorageError",
    "SyncReport",
]
