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

REPO_ROOT = Path(__file__).resolve().parents[2]
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
    package = REPO_ROOT / "dragon_quant"
    for path in sorted(package.rglob("*.py")):
        if path.relative_to(package).parts[:1] == ("tests",):
            # ``dragon_quant/tests`` 是 source-only 测试树，不属于生产包扫描面。
            continue
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


def test_data_facade_defaults_to_tdx_and_rejects_unknown_source():
    """data 门面：个股 K 线默认走 TDX（无 Cookie），未知 source 显式报错不静默落雪球。"""
    from unittest.mock import Mock, patch

    import pytest

    import dragon_quant.infra.provider.data as data_mod

    tdx, ths, xq, tx = Mock(), Mock(), Mock(), Mock()
    providers = {"tdx": tdx, "ths": ths, "xueqiu": xq, "tencent": tx}
    with patch.object(data_mod, "create_providers", return_value=providers), \
         patch.object(data_mod, "_providers", None):
        data_mod.get_kline("600172", days=5)
        data_mod.get_minute_kline("600172")
    tdx.get_kline.assert_called_once_with("600172", days=5)
    tdx.get_minute_kline.assert_called_once_with("600172")
    xq.get_kline.assert_not_called()
    xq.get_minute_kline.assert_not_called()

    with patch.object(data_mod, "create_providers", return_value=providers), \
         patch.object(data_mod, "_providers", None):
        with pytest.raises(ValueError, match="未知数据源"):
            data_mod.get_kline("600172", source="nope")
        with pytest.raises(ValueError, match="未知数据源"):
            data_mod.get_minute_kline("600172", source="nope")
        with pytest.raises(ValueError, match="未知数据源"):
            data_mod.get_quote("600172", source="nope")
        with pytest.raises(ValueError, match="未知数据源"):
            data_mod.batch_get_quotes(["600172"], source="nope")
