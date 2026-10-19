"""数据资产云端同步。

模块边界：
  - codec      规范化 JSON / gzip / 哈希
  - models     不可变值对象
  - backends   Strategy：S3Backend / LocalDirBackend
  - sources    Adapter：prep_assets / auction_snapshots（M2）
  - service    push / pull / status / verify（M3）
"""

from dragon_quant.assets_sync.errors import (
    AmbiguousError,
    AssetsSyncError,
    ConfigError,
    IntegrityError,
    NotFoundError,
    StorageError,
)
from dragon_quant.assets_sync.models import (
    AssetShard,
    Manifest,
    ManifestEntry,
    ObjectMeta,
    RestoreReport,
    SyncReport,
)

__all__ = [
    "AmbiguousError",
    "AssetShard",
    "AssetsSyncError",
    "ConfigError",
    "IntegrityError",
    "Manifest",
    "ManifestEntry",
    "NotFoundError",
    "ObjectMeta",
    "RestoreReport",
    "StorageError",
    "SyncReport",
]
