"""infra.provider — canonical data-source adapters, data facade and rate limiter.

Public imports should go through :mod:`dragon_quant.infra.provider.main`; this
package root re-exports the same contract for convenience.
"""

from __future__ import annotations

from dragon_quant.infra.provider.main import (
    ProviderBundle,
    StockProvider,
    TdxProvider,
    TencentProvider,
    THSProvider,
    XueqiuProvider,
    build_provider_bundle,
    create_providers,
)

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
