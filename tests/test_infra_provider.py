"""Task 4: ``dragon_quant.infra.provider`` is the canonical provider layer.

The provider implementations, the data facade, the rate limiter and the single
``ProviderBundle`` live under ``infra/provider``.  Legacy paths only keep
migration shims; the canonical cookie library must not own a ``__main__``
guard (Cookie CLI is the formal ``data cookie-status|cookie-set|cookie-fetch``
command, not an independent module entry).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = REPO_ROOT / "dragon_quant" / "infra" / "provider"


def test_provider_facade_exports_the_public_contract():
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

    providers = create_providers()
    assert set(providers) == {"tdx", "ths", "xueqiu", "tencent"}
    assert isinstance(providers["tdx"], TdxProvider)
    assert isinstance(providers["ths"], THSProvider)
    assert isinstance(providers["xueqiu"], XueqiuProvider)
    assert isinstance(providers["tencent"], TencentProvider)
    assert all(isinstance(provider, StockProvider) for provider in providers.values())

    bundle = build_provider_bundle()
    assert isinstance(bundle, ProviderBundle)
    assert set(bundle.as_dict()) == {"tdx", "ths", "xueqiu", "tencent"}
    assert isinstance(bundle.tdx, TdxProvider)
    assert isinstance(bundle.ths, THSProvider)
    assert isinstance(bundle.xueqiu, XueqiuProvider)
    assert isinstance(bundle.tencent, TencentProvider)
    assert ProviderBundle.__module__ == "dragon_quant.infra.provider.bundle"


def test_provider_bundle_is_defined_once():
    sites: list[str] = []
    for path in sorted((REPO_ROOT / "dragon_quant").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.ClassDef) and node.name == "ProviderBundle"
            for node in tree.body
        ):
            sites.append(path.relative_to(REPO_ROOT).as_posix())
    assert sites == ["dragon_quant/infra/provider/bundle.py"], sites


def test_canonical_cookie_has_no_main_guard():
    source = (PROVIDER_DIR / "cookie.py").read_text(encoding="utf-8")
    assert "__main__" not in source


def test_data_and_rate_limit_are_moved():
    from dragon_quant.infra.provider.data import batch_get_quotes, get_kline
    from dragon_quant.infra.provider.rate_limit import RateLimiter

    assert callable(get_kline)
    assert callable(batch_get_quotes)
    assert RateLimiter.__module__ == "dragon_quant.infra.provider.rate_limit"
