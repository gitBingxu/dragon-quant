"""infra — 基础设施叶子层（唯一连接 / schema / 路径 / 缓存 / provider / 资产 / 因子）。

迁移期本包已承载 ``clock`` / ``models`` / ``market_calendar`` 三个共享叶子与
:mod:`dragon_quant.infra.compat` 薄 shim；``__init__.py`` 保持**零 eager import**，
导入本包不加载任何子模块、不创建数据目录（B1）。
"""

from __future__ import annotations

__all__: list[str] = []
