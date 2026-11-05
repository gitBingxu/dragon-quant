"""快照读取小工具（prep / auction 投影共用，无 I/O）。

``store.load`` 契约可能是 ``(payload, meta)`` 或直接 ``payload``；这里统一拆包，
避免各 presenter 复制第二份实现。
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = ["split_load"]


def split_load(loaded: Any) -> tuple[dict, dict]:
    """兼容 ``(payload, meta)`` 与直接 payload，返回两个普通 dict。"""

    if isinstance(loaded, tuple) and len(loaded) == 2:
        payload, meta = loaded
        return (payload if isinstance(payload, Mapping) else {}), (
            meta if isinstance(meta, Mapping) else {})
    return (loaded if isinstance(loaded, Mapping) else {}), {}
