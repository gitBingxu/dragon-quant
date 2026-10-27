"""Composition Root — the small assembly seam for feature entrypoints.

Only assembly primitives live here: runtime config, the ``Services`` bundle,
``build_services`` and ``ensure_storage``.  Feature objects (``MarketData``,
``AccountSimulator``/``Simulator``, ``LiveTrader``, assets backends, strategy
config) are constructed by their own feature factories, never here.

Dependency direction: ``app`` may import runtime contracts, storage and
providers, but never ``cli``/``commands``; feature modules must not import
``app``.  ``ProviderBundle`` is defined in :mod:`dragon_quant.infra.provider.bundle`
and is only imported/used here.

Provider construction stays lazy: ``build_services()`` never calls the provider
factory.  A command that needs market data passes ``providers=`` explicitly
(e.g. ``build_services(providers=build_provider_bundle())``); read-only
commands keep ``providers=None``.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from dragon_quant.infra.clock import Clock, get_clock
from dragon_quant.infra.provider.bundle import ProviderBundle
from dragon_quant.infra.storage import paths as _paths

# Keep the historical default; source routing remains a per-feature argument.
DEFAULT_SOURCE = "v2"


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime-wide configuration resolved at assembly time."""

    data_dir: Path
    source: str = DEFAULT_SOURCE
    verbose: bool = True


@dataclass(frozen=True)
class Services:
    """Dependency bundle handed to feature entrypoints (all optional at the edge)."""

    config: RuntimeConfig
    clock: Clock
    providers: Optional[ProviderBundle] = None


def _resolve_data_dir(env: Optional[Mapping[str, str]]) -> Path:
    """Resolve the reported data directory without changing storage semantics.

    ``storage.paths`` resolves ``DQ_DATA_DIR`` once at import time and every
    storage module reads those constants.  This helper only mirrors that value
    for ``RuntimeConfig``; it never redirects a database path.
    """
    if env is not None:
        override = env.get("DQ_DATA_DIR")
        if override:
            return Path(override)
    return _paths.DATA_DIR


def build_services(*, providers: Optional[ProviderBundle] = None,
                   clock: Optional[Clock] = None,
                   env: Optional[Mapping[str, str]] = None) -> Services:
    """Assemble ``Services`` without constructing providers or touching the network.

    ``clock`` defaults to the **active** ``runtime.clock`` instance
    (``get_clock()``) so there is exactly one clock source per process: an
    explicit argument wins, otherwise whatever ``set_clock`` installed (the
    E2E ``FixedClock`` included) is bound.  This function never instantiates a
    second clock.
    """
    return Services(
        config=RuntimeConfig(data_dir=_resolve_data_dir(env)),
        clock=get_clock() if clock is None else clock,
        providers=providers,
    )


def ensure_storage(services: Services) -> None:
    """Idempotently ensure the established data directories and dragon.db schema.

    The storage location is always the one resolved by :mod:`dragon_quant.infra.storage.paths`
    (``DQ_DATA_DIR``/platform default).  ``services.config.data_dir`` is
    informational and deliberately does not become a second storage root.
    """
    from dragon_quant.infra.storage import connection

    for directory in (
        _paths.COOKIE_DIR,
        _paths.CACHE_DIR,
        _paths.LOG_DIR,
        _paths.RESULTS_DIR,
        _paths.SHARED_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    connection.init_db()
