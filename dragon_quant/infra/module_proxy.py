"""名称代理模块（``review`` / ``prep`` 包门面的历史属性面）。

任务背景：这两个包的 ``__init__`` 对外暴露「旧模块属性」——读取按名字惰性转发到
各自的 ``main`` 门面，写入转发到 ``main``（因此
``patch.object(prep, "TdxProvider", ...)`` 仍真实改变运行期行为）。这是批准设计
（方案 §4「包 ``__init__`` 策略」）保留的兼容面，**不是**迁移期旧路径 shim。

本模块只使用 stdlib，不 import 任何业务模块；它是原 ``infra.compat`` 中
``NameProxyModule`` / ``install_name_proxy`` 的唯一存活职责（``alias_module`` 随
旧路径一起删除）。
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import AbstractSet, Mapping

_MAPPING_KEY = "_dq_proxy_mapping"
_LOCAL_SUBMODULES_KEY = "_dq_local_submodules"
_ORIGINALS_KEY = "_dq_proxy_originals"
_MISSING = object()


class NameProxyModule(ModuleType):
    """按名字转发到 owner 模块的惰性代理模块。

    私有状态只存放在模块 ``__dict__``，不会与被代理名字冲突；映射值是可
    ``importlib.import_module`` 的 owner **模块名**。
    """

    def __getattr__(self, name: str) -> object:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        mapping = self.__dict__.get(_MAPPING_KEY) or {}
        owner_name = mapping.get(name)
        if owner_name is None:
            raise AttributeError(
                f"module {self.__name__!r} has no attribute {name!r}")
        try:
            owner = importlib.import_module(owner_name)
        except ImportError as exc:
            raise AttributeError(
                f"module {self.__name__!r} cannot import owner "
                f"{owner_name!r} for attribute {name!r}") from exc
        try:
            return getattr(owner, name)
        except AttributeError as exc:
            raise AttributeError(
                f"owner module {owner_name!r} has no attribute {name!r}") from exc

    def __setattr__(self, name: str, value: object) -> None:
        mapping = self.__dict__.get(_MAPPING_KEY) or {}
        local = self.__dict__.get(_LOCAL_SUBMODULES_KEY) or frozenset()
        owner_name = mapping.get(name)
        if owner_name is not None and name not in local:
            owner = importlib.import_module(owner_name)
            originals = self.__dict__.setdefault(_ORIGINALS_KEY, {})
            # 记住首次覆盖前的 owner 值，供 unittest.mock.patch 退出时恢复。
            if name not in originals:
                originals[name] = getattr(owner, name, _MISSING)
            setattr(owner, name, value)
            return
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        """让 ``unittest.mock.patch`` 的退出语义对代理模块生效。

        ``patch`` 不知道映射名的原值不在代理 ``__dict__`` 里，退出时会对目标
        ``delattr``。这里改为恢复 ``__setattr__`` 记下的 owner 原值，而不是把
        owner 的真实属性删掉。
        """
        mapping = self.__dict__.get(_MAPPING_KEY) or {}
        local = self.__dict__.get(_LOCAL_SUBMODULES_KEY) or frozenset()
        owner_name = mapping.get(name)
        if owner_name is not None and name not in local:
            originals = self.__dict__.get(_ORIGINALS_KEY) or {}
            if name not in originals:
                raise AttributeError(name)
            original = originals.pop(name)
            owner = importlib.import_module(owner_name)
            if original is _MISSING:
                delattr(owner, name)
            else:
                setattr(owner, name, original)
            return
        super().__delattr__(name)


def install_name_proxy(
    module: ModuleType,
    mapping: Mapping[str, str],
    *,
    local_submodules: AbstractSet[str] = frozenset(),
) -> None:
    """就地安装名称代理状态；``module`` 应是 :class:`NameProxyModule` 或其子类。"""

    module.__dict__[_MAPPING_KEY] = dict(mapping)
    module.__dict__[_LOCAL_SUBMODULES_KEY] = frozenset(local_submodules)
    module.__dict__[_ORIGINALS_KEY] = {}
