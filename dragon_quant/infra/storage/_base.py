"""db 基础设施的转发包装（canonical 版本）。

领域模块（blacklist / scans / dragons / review_account / signals / logs）需要
``_connect`` / ``_ensure_schema`` / ``_normalize_source`` / ``_tables`` / ``_lock``。
若在领域模块 import 时直接绑定函数引用，那么测试对连接缝的 patch 将无法传递。

因此这里统一提供「运行时动态解析」的转发包装：包装函数每次调用都经
``_connection.<name>`` 重新查找，从而让 patch 对领域模块同样生效。``_lock`` 是同一
把全局锁，直接绑定即可（锁对象从不被 patch）。
"""

from __future__ import annotations

from dragon_quant.infra.storage import connection as _connection
from dragon_quant.infra.storage import identifiers as _identifiers


def _connect(*args, **kwargs):
    return _connection._connect(*args, **kwargs)


def _ensure_schema(*args, **kwargs):
    return _connection._ensure_schema(*args, **kwargs)


def _normalize_source(*args, **kwargs):
    return _identifiers._normalize_source(*args, **kwargs)


def _tables(*args, **kwargs):
    return _identifiers._tables(*args, **kwargs)


_lock = _connection._lock
