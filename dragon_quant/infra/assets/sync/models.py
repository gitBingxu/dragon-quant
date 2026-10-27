"""数据资产同步的数据模型（不可变值对象）。

M6 起分片身份由 **分区键**（``partition``）承载，不再假设一定是交易日：
  - date 分区键是 ``YYYY-MM-DD``（与旧版完全一致）；
  - period / period_raw 分区键是 ``fin-…`` / ``finraw-…``；
  - static 分区键是 ``static-<domain>``。

``trade_date`` 保留为**只读属性**（仅 date 分区非空），既有调用方零改动。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_DATE_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_date_key(value: str) -> bool:
    return bool(value) and bool(_DATE_KEY_RE.match(value))


@dataclass(frozen=True, init=False)
class AssetShard:
    """一个可独立上传/下载的数据资产分片。"""

    partition: str           # 分区键：YYYY-MM-DD / fin-… / finraw-… / static-…
    asset_type: str
    source: str              # prep / auction / prepsnap / auctionsnap / factor / fin
    content: bytes           # 规范化 JSON（未压缩）
    sha256: str
    md5: str
    schema: int = 1
    # 采集时间只作旁路传输，绝不进入 sha256 / manifest_id。
    created_at: str = ""
    created_at_map: dict = field(default_factory=dict)   # auction: code -> created_at

    def __init__(self, partition=None, asset_type="", source="", content=b"",
                 sha256="", md5="", schema=1, created_at="", created_at_map=None,
                 trade_date=None):
        # ``trade_date=`` 是既有调用方的关键字兼容别名（必须显式给出 partition）。
        if partition is None:
            partition = trade_date or ""
        object.__setattr__(self, "partition", partition or "")
        object.__setattr__(self, "asset_type", asset_type)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "content", content or b"")
        object.__setattr__(self, "sha256", sha256)
        object.__setattr__(self, "md5", md5)
        object.__setattr__(self, "schema", schema)
        object.__setattr__(self, "created_at", created_at or "")
        object.__setattr__(self, "created_at_map", dict(created_at_map or {}))

    @property
    def trade_date(self) -> str:
        """只读兼容属性：仅 date 分区返回分区键，其余返回空串。"""
        return self.partition if _is_date_key(self.partition) else ""

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
    # 行级诊断：仅供 status drift 判定，**不进 manifest_id**。
    rows: int | None = None

    def identity(self) -> dict:
        """参与 manifest_id 计算的逻辑身份字段（不含采集时间 / 行数）。"""
        return {
            "source": self.source,
            "asset_type": self.asset_type,
            "sha256": self.sha256,
            "md5": self.md5,
            "size": self.size,
            "schema": self.schema,
        }


@dataclass(frozen=True, init=False)
class Manifest:
    """某个分区的资产清单（每分区一份，重采覆盖）。"""

    partition: str
    manifest_id: str
    assets: dict[str, ManifestEntry] = field(default_factory=dict)
    schema: int = 1
    generated_at: str = ""
    generator_version: str = ""
    manifest_source: str = "push"   # push / rebuilt

    def __init__(self, partition=None, manifest_id="", assets=None, schema=1,
                 generated_at="", generator_version="", manifest_source="push",
                 trade_date=None):
        if partition is None:
            partition = trade_date or ""
        object.__setattr__(self, "partition", partition or "")
        object.__setattr__(self, "manifest_id", manifest_id)
        object.__setattr__(self, "assets", dict(assets or {}))
        object.__setattr__(self, "schema", schema)
        object.__setattr__(self, "generated_at", generated_at or "")
        object.__setattr__(self, "generator_version", generator_version or "")
        object.__setattr__(self, "manifest_source", manifest_source or "push")

    @property
    def trade_date(self) -> str:
        """只读兼容属性：schema 1 date 分区返回分区键，其余为空串。"""
        return self.partition if _is_date_key(self.partition) else ""

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
    # 行级合并计数（M6；legacy 分片级恢复保持为 0/空）。
    rows_inserted: int = 0
    rows_skipped: int = 0
    rows_filled: int = 0
    deferred: list = field(default_factory=list)
    drift: list = field(default_factory=list)
    # dry-run 下行级冲突必须下载后才能判定：以 merge_pending 标注（不伪造冲突）。
    merge_pending: int = 0
    period_excluded: list = field(default_factory=list)


@dataclass(frozen=True)
class ShardBatch:
    """单分区读取结果：成功分片 + 逐分片失败（源级异常直接抛）。"""

    shards: list
    failed: list = field(default_factory=list)
