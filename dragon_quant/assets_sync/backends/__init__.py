"""对象存储后端（Strategy）。"""

from dragon_quant.assets_sync.backends.base import ObjectStorage
from dragon_quant.assets_sync.backends.local import LocalDirBackend
from dragon_quant.assets_sync.backends.s3 import S3Backend

__all__ = ["LocalDirBackend", "ObjectStorage", "S3Backend"]
