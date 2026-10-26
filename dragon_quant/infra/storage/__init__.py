"""infra.storage — dragon.db 基础设施（唯一连接 / schema / 路径 / 缓存 / manager）。

``__init__.py`` 保持**零 eager import**：导入本包不加载任何子模块、不创建数据
目录或数据库（B1）。公共门面在 :mod:`dragon_quant.infra.storage.main`。
"""

from __future__ import annotations

__all__: list[str] = []
