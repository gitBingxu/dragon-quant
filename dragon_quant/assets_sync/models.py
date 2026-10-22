"""数据资产同步的数据模型（不可变值对象）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AssetShard:
    """一个可独立上传/下载的数据资产分片。"""

    trade_date: str          # YYYY-MM-DD
    asset_type: str
    source: str              # prep / auction（assets.db）
    content: bytes           # 规范化 JSON（未压缩）
    sha256: str
    md5: str
    schema: int = 1
    # 采集时间只作旁路传输，绝不进入 sha256 / manifest_id。
    created_at: str = ""
    created_at_map: dict = field(default_factory=dict)   # auction: code -> created_at

    @property
    def key_token(self) -> str:
        return f"{self.source}_{self.asset_type}"


@dataclass(frozen=True)
class ManifestEntry:
    """manifest 中单个资产的元数据。"""

    source: str
    asset_type: str
    sha256: str
    md5: str
    size: int
    blob: str
    blob_sha256: str
    blob_size: int
    schema: int = 1
    created_at: str = ""
    created_at_map: dict = field(default_factory=dict)

    def identity(self) -> dict:
        """参与 manifest_id 计算的逻辑身份字段（不含采集时间）。"""
        return {
            "source": self.source,
            "asset_type": self.asset_type,
            "sha256": self.sha256,
            "md5": self.md5,
            "size": self.size,
            "schema": self.schema,
        }


@dataclass(frozen=True)
class Manifest:
    """某个交易日的资产清单（每日一份，重采覆盖）。"""

    trade_date: str
    manifest_id: str
    assets: dict[str, ManifestEntry] = field(default_factory=dict)
    schema: int = 1
    generated_at: str = ""
    generator_version: str = ""
    manifest_source: str = "push"   # push / rebuilt

    def identity_map(self) -> dict:
        return {key: entry.identity() for key, entry in self.assets.items()}


@dataclass(frozen=True)
class ObjectMeta:
    """对象存储的 HEAD/LIST 元数据。"""

    key: str
    size: int = 0
    etag: str | None = None
    last_modified: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class SyncReport:
    pushed: int = 0
    skipped: int = 0
    shrunk: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    bytes_uploaded: int = 0
    blobs_uploaded: int = 0
    manifests_uploaded: int = 0
    dates: list = field(default_factory=list)
    dry_run: bool = False
    metadata_supported: bool = True
    local_empty_remote_present: list = field(default_factory=list)


@dataclass
class RestoreReport:
    restored: int = 0
    skipped: int = 0
    conflicts: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    manifest_source: str = "remote"   # remote / rebuilt
    degraded: bool = False
    dates: list = field(default_factory=list)
    bytes_downloaded: int = 0
    unsupported: list = field(default_factory=list)


@dataclass(frozen=True)
class ShardBatch:
    """单日读取结果：成功分片 + 逐分片失败（源级异常直接抛）。"""

    shards: list
    failed: list = field(default_factory=list)
