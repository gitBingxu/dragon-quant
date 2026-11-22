"""canonical JSON 与内容指纹（纯函数，可被快照/仓储复用）。"""

from __future__ import annotations

import hashlib
import json


def canonical_json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False,
                      sort_keys=True)


def content_hash(payload) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
