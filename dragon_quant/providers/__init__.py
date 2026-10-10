"""
Provider 工厂 — 按需创建适配器实例
"""

from dragon_quant.providers.base import StockProvider
from dragon_quant.providers.tdx import TdxProvider
from dragon_quant.providers.tencent import TencentProvider
from dragon_quant.providers.ths import THSProvider
from dragon_quant.providers.xueqiu import XueqiuProvider

__all__ = [
    "StockProvider",
    "THSProvider",
    "TdxProvider",
    "TencentProvider",
    "XueqiuProvider",
    "create_providers",
]


def create_providers(logger=None) -> dict[str, StockProvider]:
    """创建所有数据源适配器"""
    providers = {
        "tdx": TdxProvider(),
        "ths": THSProvider(),
        "xueqiu": XueqiuProvider(),
        "tencent": TencentProvider(),
    }
    if logger:
        for p in providers.values():
            p.set_logger(logger)
    return providers
