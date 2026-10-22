"""数据资产同步错误类型（CLI 退出码映射见 cli_commands._cmd_assets）。"""


class AssetsSyncError(Exception):
    """所有资产同步错误的基类。"""


class ConfigError(AssetsSyncError):
    """配置/凭据缺失或非法 → 退出码 2。"""


class StorageError(AssetsSyncError):
    """对象存储请求失败（网络/5xx/鉴权等）。"""


class NotFoundError(StorageError):
    """对象不存在（404）。"""


class IntegrityError(AssetsSyncError):
    """哈希/大小校验失败。"""


class AmbiguousError(AssetsSyncError):
    """manifest 重建出现同日多候选，需要显式 --pick。"""


class LockBusyError(AssetsSyncError):
    """进程锁被占用（退出码 1，区别于配置/网络错误的 2）。"""
