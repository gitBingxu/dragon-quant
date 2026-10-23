"""测试存储隔离 helper。

``storage.dragons.save_dragons`` 在主写 dragon.db 之后会 fan-out 双写
assets.db 镜像。只 patch ``db._connect`` 的测试挡不住第二条连接，镜像写会落到
真实 assets.db（2026-10-23 的脏数据事故）。需要写 dragons 的用例一律用
``patch_storage_dbs`` 一次覆盖两个连接点。
"""

import sqlite3
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


def patch_storage_dbs(dragon_path, assets_path=None) -> ExitStack:
    """同时 patch dragon.db 与 assets.db 的 ``_connect``，返回已进入的 ExitStack。

    ``assets_path`` 缺省为 ``<dragon_path>.assets``（两库必须不同文件）。
    """
    dragon_path = str(dragon_path)
    assets_path = str(assets_path or (dragon_path + ".assets"))
    if Path(dragon_path).resolve() == Path(assets_path).resolve():
        raise ValueError("dragon.db 与 assets.db 必须指向不同文件，否则镜像写不可观测")

    def _dragon(*args, **kwargs):
        return sqlite3.connect(dragon_path)

    def _assets(create=True, path=None):
        target = str(path) if path is not None else assets_path
        if not create and not Path(target).exists():
            return None
        return sqlite3.connect(target)

    stack = ExitStack()
    stack.enter_context(
        patch("dragon_quant.storage.db._connect", side_effect=_dragon))
    stack.enter_context(
        patch("dragon_quant.storage.assets_db._connect", side_effect=_assets))
    return stack
