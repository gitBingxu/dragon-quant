"""文件系统对象存储后端：单测与本地回放。

键映射为 `<root>/<key>`；元数据写入 `<root>/.meta/<key>.json`。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dragon_quant.infra.assets.sync.errors import NotFoundError
from dragon_quant.infra.assets.sync.models import ObjectMeta


class LocalDirBackend:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta_root = self.root / ".meta"

    def _path(self, key: str) -> Path:
        if key == ".meta" or key.startswith(".meta/"):
            raise ValueError(f"非法 key: {key}")
        target = (self.root / key).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError(f"非法 key: {key}")
        return target

    def _meta_path(self, key: str) -> Path:
        if key == ".meta" or key.startswith(".meta/"):
            raise ValueError(f"非法 key: {key}")
        target = (self.meta_root / f"{key}.json").resolve()
        if target != self.meta_root and self.meta_root not in target.parents:
            raise ValueError(f"非法 key: {key}")
        return target

    def put(self, key: str, data: bytes, metadata: dict[str, str] | None = None,
            content_md5: str | None = None) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        meta_path = self._meta_path(key)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise NotFoundError(key)
        return path.read_bytes()

    def head(self, key: str) -> ObjectMeta | None:
        path = self._path(key)
        if not path.exists():
            return None
        metadata = {}
        meta_path = self._meta_path(key)
        if meta_path.exists():
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                metadata = {}
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        return ObjectMeta(key=key, size=path.stat().st_size, etag=None,
                          last_modified=mtime, metadata=metadata)

    def list(self, prefix: str) -> list[ObjectMeta]:
        results = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            if self.meta_root in path.parents:
                continue
            key = path.relative_to(self.root).as_posix()
            if not key.startswith(prefix):
                continue
            meta = self.head(key)
            if meta is not None:
                results.append(meta)
        return results

    def delete(self, key: str) -> None:
        for path in (self._path(key), self._meta_path(key)):
            if path.exists():
                os.unlink(path)
