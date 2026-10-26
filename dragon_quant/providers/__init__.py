"""Provider 数据的旧路径例外（唯一保留）。

``dragon_quant.providers`` 不是 provider 公共门面：provider 实现与公共导入入口在
:mod:`dragon_quant.infra.provider`（``main`` 门面）。本包只保留
:mod:`dragon_quant.providers.cookie` 这个既有的 ``python -m`` 独立入口；不要从本包
导入 provider 类或 Cookie 库函数。
"""

from __future__ import annotations

__all__: list[str] = []
