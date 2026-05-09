"""
Provider 工厂 — 按需创建适配器实例
"""

from dragon_quant.providers.base import StockProvider
from dragon_quant.providers.eastmoney import EastMoneyProvider
from dragon_quant.providers.xueqiu import XueqiuProvider
from dragon_quant.providers.tencent import TencentProvider

__all__ = ["StockProvider", "EastMoneyProvider", "XueqiuProvider", "TencentProvider", "create_providers"]


def create_providers() -> dict[str, StockProvider]:
    """创建所有数据源适配器"""
    return {
        "eastmoney": EastMoneyProvider(),
        "xueqiu": XueqiuProvider(),
        "tencent": TencentProvider(),
    }
