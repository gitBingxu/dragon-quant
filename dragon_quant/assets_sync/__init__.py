"""数据资产云端同步。

模块边界：
  - codec      规范化 JSON / gzip / 哈希 / 分区键 / manifest schema
  - models     值对象与报告
  - backends   Strategy：S3Backend / LocalDirBackend
  - sources    Adapter：assets.db 的 prep / auction / 快照 / 因子 / 财务表
  - tables     TableSpec 注册表 + CompositeTableSource（M6）
  - manifest   分区 manifest 构建 / 解析 / 重建
  - state      state.json（schema 2）原子读写 + 进程锁
  - service    push / pull / status / verify（分区编排 + 行级合并）
  - cli_service  CLI 应用服务：backend/sources/paths 装配 + 动作分派 + 日期/错误协调
                 （命令层只消费它，不直接构造后端；M6）
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
