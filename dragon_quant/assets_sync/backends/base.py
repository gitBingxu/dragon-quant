"""对象存储协议（Strategy 接口）。"""

from __future__ import annotations

from typing import Protocol

from dragon_quant.assets_sync.models import ObjectMeta


class ObjectStorage(Protocol):
    """最小 S3 语义子集：PUT / GET / HEAD / LIST / DELETE。"""

    def put(
        self,
        key: str,
        data: bytes,
        metadata: dict[str, str] | None = None,
        content_md5: str | None = None,
    ) -> None: ...

    def get(self, key: str) -> bytes: ...

    def head(self, key: str) -> ObjectMeta | None: ...

    def list(self, prefix: str) -> list[ObjectMeta]: ...

    def delete(self, key: str) -> None: ...
