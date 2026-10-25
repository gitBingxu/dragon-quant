"""统一持久化层的公共边界说明。

storage 采用**文档化子模块边界**，不在包根做 eager facade 重导出：这样
``import dragon_quant.storage.paths`` 不会触发包 ``__init__`` 加载 ``db`` /
``assets_db`` 等重模块，也不会产生任何目录、数据库或 schema 的导入期副作用。
调用方按需 import 下列子模块；``__all__`` 只是公共名字清单，本模块**不 import
它们**，因此不会引入初始化动作或新的依赖环。
"""

__all__ = [
    "db",
    "assets_db",
    "dragons_mirror",
    "finance",
    "manager",
    "paths",
    "blacklist",
    "signals",
    "scans",
    "dragons",
    "logs",
    "review_account",
]
