"""对象存储后端（Strategy）。"""

from dragon_quant.infra.assets.sync.backends.base import ObjectStorage
from dragon_quant.infra.assets.sync.backends.local import LocalDirBackend
from dragon_quant.infra.assets.sync.backends.s3 import S3Backend

__all__ = ["LocalDirBackend", "ObjectStorage", "S3Backend"]
