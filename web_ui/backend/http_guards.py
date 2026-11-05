"""HTTP 触发写操作的共享守卫（纯函数，无 I/O）。

本机 dashboard 只允许同源页面触发写操作：``Origin`` 缺省（非浏览器 / curl）
放行，显式跨站 Origin 一律拒绝。
"""

from __future__ import annotations

from urllib.parse import urlsplit

_ALLOWED_ORIGIN_HOSTS = {"localhost", "127.0.0.1", "::1"}


def origin_allowed(origin: str) -> bool:
    """``Origin`` 头是否指向本机（``localhost`` / ``127.0.0.1`` / ``::1``）。"""
    try:
        host = urlsplit(origin).hostname
    except ValueError:
        return False
    return bool(host) and host.lower() in _ALLOWED_ORIGIN_HOSTS


__all__ = ["origin_allowed"]
