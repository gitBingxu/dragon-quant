"""dragon-quant Strategy SDK —— 用户 workspace 策略的唯一稳定导入入口。

用户策略只需：

    from dragon_quant.strategy_sdk import StrategyContext, StrategyIntent, FactorValue

可选地复用内建因子类型：

    from dragon_quant.strategy_sdk import FactorSpec, FactorContext, FactorFrame

本包只提供协议与纯数据类型；不 import Web / commands / workspace 装载器，
也不 import ``review_account`` 实现，避免用户代码与内部实现强耦合。
"""

from __future__ import annotations

from dragon_quant.strategy_sdk.types import (
    EXIT_FRACTIONS,
    FACTOR_STATUS_MISSING,
    FACTOR_STATUS_OK,
    FACTOR_STATUS_UNAVAILABLE,
    INTENT_ACTIONS,
    SDK_VERSION,
    FactorValue,
    Strategy,
    StrategyContext,
    StrategyError,
    StrategyIntent,
    context_from_payload,
    context_to_payload,
    freeze,
    intent_to_payload,
    validate_intent,
)
from dragon_quant.infra.factor.types import (
    FactorContext,
    FactorFrame,
    FactorSpec,
)

__all__ = [
    "EXIT_FRACTIONS",
    "FACTOR_STATUS_MISSING",
    "FACTOR_STATUS_OK",
    "FACTOR_STATUS_UNAVAILABLE",
    "INTENT_ACTIONS",
    "SDK_VERSION",
    "FactorContext",
    "FactorFrame",
    "FactorSpec",
    "FactorValue",
    "Strategy",
    "StrategyContext",
    "StrategyError",
    "StrategyIntent",
    "validate_intent",
    "context_from_payload",
    "context_to_payload",
    "freeze",
    "intent_to_payload",
    "resources_dir",
    "read_resource",
    "iter_resources",
]


def resources_dir():
    """返回 SDK 资源目录路径（``importlib.resources`` 兼容 wheel 安装）。"""
    from importlib import resources

    return resources.files("dragon_quant.strategy_sdk.resources")


def read_resource(*parts: str) -> str:
    """读取打包资源文本（README / 模板）；缺失时抛 ``FileNotFoundError``。"""
    node = resources_dir()
    for part in parts:
        node = node.joinpath(part)
    return node.read_text(encoding="utf-8")


def iter_resources(subdir: str = "templates") -> list[str]:
    """列出资源子目录中的文件名（稳定的排序，供 ``workspace init`` 复制模板）。"""
    node = resources_dir().joinpath(subdir)
    if not node.is_dir():
        return []
    return sorted(child.name for child in node.iterdir() if child.is_file())
