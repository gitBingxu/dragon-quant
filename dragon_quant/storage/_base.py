"""db 基础设施的转发包装。

领域模块（blacklist / scans / dragons / review_account / signals / logs）需要
`_connect` / `_ensure_schema` / `_normalize_source` / `_tables` / `_lock`。若在
领域模块 import 时用 `_connect = db._connect` 绑定原函数引用，那么测试对
`db._connect` 的 patch（以及 db 模块的任何后续替换）将无法传递到领域模块。

因此这里统一提供「运行时动态解析」的转发包装：包装函数每次调用都经 `_db.<name>`
重新查找，从而让 patch 对领域模块同样生效。`_lock` 是同一把全局锁，直接绑定即可
（锁对象从不被 patch）。
"""

from dragon_quant.storage import db as _db


def _connect(*args, **kwargs):
    return _db._connect(*args, **kwargs)


def _ensure_schema(*args, **kwargs):
    return _db._ensure_schema(*args, **kwargs)


def _normalize_source(*args, **kwargs):
    return _db._normalize_source(*args, **kwargs)


def _tables(*args, **kwargs):
    return _db._tables(*args, **kwargs)


_lock = _db._lock
