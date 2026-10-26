"""Lazy CLI command registry.

``COMMANDS`` describes every top-level command exactly once.  Each entry
resolves to ``dragon_quant.commands.<name>:handle`` and is imported only when
dispatched; the command modules are the only handler implementation.

``dispatch(name, args)`` is the single entry point used by ``cli.main``:
handlers receive ``(args, services)`` where ``Services`` comes from
``app.build_services``.  A command's provider policy is a per-invocation
predicate (``providers_needed(args)``), so local branches such as
``review --ui-only``, ``data cookie-status|cookie-set|cookie-fetch``,
``prep --recompute``, ``auction --recompute`` and ``factor list`` stay offline;
``scan`` keeps the feature's historical construction (see :data:`COMMANDS`).
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable

Handler = Callable[..., None]
ProviderPolicy = Callable[[Any], bool]


def _always_providers(args: Any) -> bool:
    return True


@dataclass(frozen=True)
class LazyCommand:
    """A command: its handler module loads on first dispatch."""

    module: str
    attribute: str = "handle"
    needs_providers: ProviderPolicy = _always_providers

    def load(self) -> Handler:
        return getattr(import_module(self.module), self.attribute)

    def providers_needed(self, args: Any) -> bool:
        """Resolve the provider policy for this invocation."""
        return bool(self.needs_providers(args))


def _review_providers(args: Any) -> bool:
    """``--ui-only`` only serves the saved results; ``--dry-run`` never reads K 线."""
    return not getattr(args, "ui_only", False) and not getattr(args, "dry_run", False)


#: ``data`` actions that read market data (everything else stays fully offline).
_DATA_ONLINE_ACTIONS = frozenset({
    "sector", "components", "kline", "minute", "quote", "batch-quote"})


def _data_providers(args: Any) -> bool:
    """Only real queries construct providers; cookie/help branches stay offline."""
    return getattr(args, "data_action", None) in _DATA_ONLINE_ACTIONS


def _review_account_providers(args: Any) -> bool:
    """Only a real backtest reads market data; ``--ui-only``/missing dates do not."""
    return (
        not getattr(args, "ui_only", False)
        and bool(getattr(args, "date_from", None))
        and bool(getattr(args, "date_to", None))
    )


def _prep_providers(args: Any) -> bool:
    """``--recompute`` is purely local (stored assets only)."""
    return not getattr(args, "recompute", False)


def _auction_providers(args: Any) -> bool:
    """``--recompute`` rebuilds snapshots from stored assets without any network."""
    return getattr(args, "recompute", None) is None


def _factor_providers(args: Any) -> bool:
    """``build``/``eval``/``fin sync|calibrate`` may touch TDX; the rest is local."""
    action = getattr(args, "factor_action", None)
    if action == "fin":
        return getattr(args, "fin_action", None) in ("sync", "calibrate")
    return action in ("build", "eval")


def _never_providers(args: Any) -> bool:
    return False


COMMANDS: dict[str, LazyCommand] = {
    # scan keeps the feature's historical provider construction: passing a
    # pre-built bundle changes its api_stats accounting (the second
    # ``_get_trade_date`` call would run on logger-attached injected providers),
    # which the M1 golden pins.  Services are still assembled by the composition
    # root; providers stay None for this command.
    "scan": LazyCommand("dragon_quant.commands.scan",
                        needs_providers=_never_providers),
    "review": LazyCommand("dragon_quant.commands.review",
                          needs_providers=_review_providers),
    "logs": LazyCommand("dragon_quant.commands.logs",
                        needs_providers=_never_providers),
    "data": LazyCommand("dragon_quant.commands.data",
                        needs_providers=_data_providers),
    "blacklist": LazyCommand("dragon_quant.commands.blacklist",
                             needs_providers=_never_providers),
    "dragons": LazyCommand("dragon_quant.commands.dragons",
                           needs_providers=_never_providers),
    "storage": LazyCommand("dragon_quant.commands.storage",
                           needs_providers=_never_providers),
    "vpa": LazyCommand("dragon_quant.commands.vpa"),
    "review-account": LazyCommand(
        "dragon_quant.commands.review_account",
        needs_providers=_review_account_providers),
    "prep": LazyCommand(
        "dragon_quant.commands.prep", needs_providers=_prep_providers),
    "auction": LazyCommand(
        "dragon_quant.commands.auction", needs_providers=_auction_providers),
    "assets": LazyCommand(
        "dragon_quant.commands.assets", needs_providers=_never_providers),
    "factor": LazyCommand(
        "dragon_quant.commands.factor", needs_providers=_factor_providers),
    "buy": LazyCommand(
        "dragon_quant.commands.buy_sell", attribute="handle_buy"),
    "sell": LazyCommand(
        "dragon_quant.commands.buy_sell", attribute="handle_sell"),
}


def _build_services(needs_providers: bool):
    """Assemble ``Services`` for one dispatch; provider construction stays opt-in."""
    from dragon_quant.app import build_services

    if not needs_providers:
        return build_services()
    from dragon_quant.infra.provider.bundle import build_provider_bundle

    return build_services(providers=build_provider_bundle())


def dispatch(name: str | None, args: Any) -> bool:
    """Run the handler registered for ``name``; ``False`` when nothing matches."""
    entry = COMMANDS.get(name) if name else None
    if entry is None:
        return False
    entry.load()(args, _build_services(entry.providers_needed(args)))
    return True
