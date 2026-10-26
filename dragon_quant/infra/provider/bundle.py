"""Provider bundle — the single named-provider assembly unit.

``ProviderBundle`` is defined here and nowhere else.  The default factory
delegates to :func:`dragon_quant.infra.provider.main.create_providers` so
provider construction and logger attachment keep their current behavior.
Construction is explicit: nothing in this module builds a bundle at import
time.
"""

from dataclasses import dataclass

from dragon_quant.infra.provider.base import StockProvider


@dataclass(frozen=True)
class ProviderBundle:
    """The four data providers needed by a feature command."""

    tdx: StockProvider
    ths: StockProvider
    xueqiu: StockProvider
    tencent: StockProvider

    def as_dict(self) -> dict[str, StockProvider]:
        """Mapping view compatible with the historical ``create_providers()`` result."""
        return {
            "tdx": self.tdx,
            "ths": self.ths,
            "xueqiu": self.xueqiu,
            "tencent": self.tencent,
        }


def build_provider_bundle(logger=None) -> ProviderBundle:
    """Create the default bundle by delegating to ``create_providers``."""
    from dragon_quant.infra.provider.main import create_providers

    providers = create_providers(logger=logger)
    return ProviderBundle(
        tdx=providers["tdx"],
        ths=providers["ths"],
        xueqiu=providers["xueqiu"],
        tencent=providers["tencent"],
    )
