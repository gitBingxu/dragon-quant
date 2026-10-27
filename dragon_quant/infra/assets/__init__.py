"""infra.assets — 数据资产独立库（assets.db）与云端同步。

``__init__.py`` 保持**零 eager import**：导入本包不加载任何子模块、不创建数据目录
或数据库（B1）。公共门面在 :mod:`dragon_quant.infra.assets.main`；DDL 在
:mod:`dragon_quant.infra.assets.schema`，连接与资产仓储在
:mod:`dragon_quant.infra.assets.db`，dragons 镜像在
:mod:`dragon_quant.infra.assets.dragons_mirror`，云端同步在
:mod:`dragon_quant.infra.assets.sync`。
"""

from __future__ import annotations

__all__: list[str] = []
