"""
Provider 工厂 — 按需创建适配器实例
"""

from dragon_quant.providers.base import StockProvider
from dragon_quant.providers.eastmoney import EastMoneyProvider
from dragon_quant.providers.xueqiu import XueqiuProvider
from dragon_quant.providers.tencent import TencentProvider
from dragon_quant.providers.ths import THSProvider

__all__ = ["StockProvider", "EastMoneyProvider", "XueqiuProvider", "TencentProvider", "THSProvider", "create_providers"]


def create_providers(logger=None) -> dict[str, StockProvider]:
    """创建所有数据源适配器"""
    providers = {
        "eastmoney": EastMoneyProvider(),
        "ths": THSProvider(),
        "xueqiu": XueqiuProvider(),
        "tencent": TencentProvider(),
    }
    if logger:
        for p in providers.values():
            p.set_logger(logger)
    return providers
