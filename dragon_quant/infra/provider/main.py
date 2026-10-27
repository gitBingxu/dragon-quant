"""Provider public facade — the single import site for data-source adapters.

``StockProvider`` is the only provider ABC, ``ProviderBundle`` is defined once
in :mod:`dragon_quant.infra.provider.bundle`, and ``create_providers`` is the
historical four-provider factory (same keys, same logger attachment).
"""

from __future__ import annotations

from dragon_quant.infra.provider.base import StockProvider
from dragon_quant.infra.provider.bundle import (
    ProviderBundle,
    build_provider_bundle,
)
from dragon_quant.infra.provider.tdx import TdxProvider
from dragon_quant.infra.provider.tencent import TencentProvider
from dragon_quant.infra.provider.ths import THSProvider
from dragon_quant.infra.provider.xueqiu import XueqiuProvider

__all__ = [
    "StockProvider",
    "THSProvider",
    "TdxProvider",
    "TencentProvider",
    "XueqiuProvider",
    "ProviderBundle",
    "build_provider_bundle",
    "create_providers",
]


def create_providers(logger=None) -> dict[str, StockProvider]:
    """Create all data-source adapters (historical four-key mapping)."""
    providers = {
        "tdx": TdxProvider(),
        "ths": THSProvider(),
        "xueqiu": XueqiuProvider(),
        "tencent": TencentProvider(),
    }
    if logger:
        for provider in providers.values():
            provider.set_logger(logger)
    return providers
