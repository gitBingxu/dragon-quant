"""M2B optional injection contracts: review-account / live-trade / data / VPA.

These tests pin the M2B seam only.  A supplied ``Services`` bundle must reach the
feature's real construction path (``AccountSimulator``/``MarketData``,
``LiveTrader``, the data facade, ``vpa.analyze``); an omitted ``services`` must
keep the historical construction and monkeypatch behavior untouched.

No real provider is constructed and no network/real database is touched.
"""

import ast
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dragon_quant.app import build_services
from dragon_quant.models.types import KBar
from dragon_quant.runtime.providers import ProviderBundle

ROOT = Path(__file__).resolve().parent.parent
PROVIDER_NAMES = ("tdx", "ths", "xueqiu", "tencent")
DAY = "2026-09-07"


def fake_bundle() -> ProviderBundle:
    return ProviderBundle(**{name: MagicMock(name=name) for name in PROVIDER_NAMES})


def services_with(bundle: ProviderBundle | None) -> object:
    return build_services(providers=bundle)


def vpa_bars(n: int = 30) -> list[KBar]:
    return [
        KBar(i * 86400000, 1_000_000.0, 10.0, 10.1, 9.9, 10.0, 0.0, 0.0, 2.0, 10_000_000.0)
        for i in range(n)
    ]


class TestDataFacadeInjection(unittest.TestCase):
    """The data facade uses the supplied bundle and never the lazy registry."""

    def setUp(self):
        self.bundle = fake_bundle()
        self.services = services_with(self.bundle)

    def _call_without_default_factory(self, fn, *args, **kwargs):
        with patch("dragon_quant.data.create_providers") as factory:
            result = fn(*args, services=self.services, **kwargs)
        factory.assert_not_called()
        return result

    def test_sector_calls_use_bundle_ths(self):
        import dragon_quant.data as data

        self._call_without_default_factory(data.get_sector_ranking, asc=True)
        self.bundle.ths.get_sector_ranking.assert_called_once_with(asc=True)

        self._call_without_default_factory(
            data.get_sector_components, "881167", page=2, all_pages=True, page_size=10)
        self.bundle.ths.get_sector_components.assert_called_once_with(
            "881167", page=2, all_pages=True, page_size=10)

        self._call_without_default_factory(data.get_sector_5min_kline, "881167", bars=20)
        self.bundle.ths.get_sector_5min_kline.assert_called_once_with("881167", bars=20)

    def test_individual_stock_calls_use_bundle_by_source(self):
        import dragon_quant.data as data

        self._call_without_default_factory(data.get_kline, "600001", days=30)
        self.bundle.xueqiu.get_kline.assert_called_once_with("600001", days=30)

        self._call_without_default_factory(data.get_kline, "600001", source="tencent", days=5)
        self.bundle.tencent.get_kline.assert_called_once_with("600001", days=5)

        self._call_without_default_factory(data.get_minute_kline, "600001")
        self.bundle.xueqiu.get_minute_kline.assert_called_once_with("600001")

        self._call_without_default_factory(data.get_quote, "600001")
        self.bundle.tencent.get_quote.assert_called_once_with("600001")

        self._call_without_default_factory(data.batch_get_quotes, ["600001", "600002"])
        self.bundle.tencent.batch_get_quotes.assert_called_once_with(["600001", "600002"])

    def test_auction_uses_bundle_tdx(self):
        import dragon_quant.data as data

        self._call_without_default_factory(data.get_auction, "600001")
        self.bundle.tdx.get_auction.assert_called_once_with("600001")

    def test_omitted_services_keeps_lazy_default_factory(self):
        import dragon_quant.data as data

        mapping = {name: MagicMock(name=name) for name in PROVIDER_NAMES}
        with patch("dragon_quant.data.create_providers", return_value=mapping) as factory, \
                patch.object(data, "_providers", None):
            data.get_kline("600001")

        factory.assert_called_once_with()
        mapping["xueqiu"].get_kline.assert_called_once_with("600001", days=20)

    def test_services_without_providers_falls_back_to_default_factory(self):
        import dragon_quant.data as data

        mapping = {name: MagicMock(name=name) for name in PROVIDER_NAMES}
        with patch("dragon_quant.data.create_providers", return_value=mapping) as factory, \
                patch.object(data, "_providers", None):
            data.get_quote("600001", services=build_services())

        factory.assert_called_once_with()
        mapping["tencent"].get_quote.assert_called_once_with("600001")


class TestVpaInjection(unittest.TestCase):
    """vpa.analyze resolves the K-line through the supplied bundle."""

    def test_analyze_uses_injected_provider(self):
        from dragon_quant.vpa.engine import analyze

        bundle = fake_bundle()
        bundle.xueqiu.get_kline.return_value = vpa_bars()

        with patch("dragon_quant.data.create_providers") as factory:
            report = analyze("600001", days=30, services=services_with(bundle))

        factory.assert_not_called()
        bundle.xueqiu.get_kline.assert_called_once_with("600001", days=30)
        self.assertFalse(report.fallback)
        self.assertTrue(report.factors)

    def test_omitted_services_keeps_patchable_get_kline_signature(self):
        import dragon_quant.data as data
        from dragon_quant.vpa.engine import analyze

        original = data.get_kline
        data.get_kline = lambda code, source="xueqiu", days=20: []
        try:
            report = analyze("600001")
        finally:
            data.get_kline = original

        self.assertTrue(report.fallback)
        self.assertEqual(report.reason, "insufficient_data")


class TestReviewAccountInjection(unittest.TestCase):
    """run_review_account forwards the bundle to the real MarketData seam."""

    RESULT = {
        "date_from": "2026-09-01", "date_to": "2026-09-02",
        "initial_cash": 100_000.0, "final_equity": 100_000.0,
        "total_return": 0.0, "max_drawdown": 0.0, "trade_count": 0,
        "win_rate": None, "snapshots": [], "trades": [], "positions": [], "events": [],
    }

    def _run(self, services=None):
        from dragon_quant.review_account.service import run_review_account
        from dragon_quant.review_account.simulator import AccountSimulator

        captured = {}

        def fake_run(sim, date_from, date_to):
            captured["data"] = sim.data
            return dict(self.RESULT)

        with patch.object(AccountSimulator, "run", fake_run), \
                patch("dragon_quant.review_account.service.annotate_post_exit"), \
                patch("dragon_quant.review_account.service.db.create_review_account_run",
                      return_value=1), \
                patch("dragon_quant.review_account.service.db.save_review_account_results"):
            run_review_account("2026-09-01", "2026-09-02", verbose=False, services=services)
        return captured["data"]

    def test_supplied_bundle_reaches_market_data_provider(self):
        bundle = fake_bundle()
        data = self._run(services_with(bundle))
        self.assertIs(data.provider, bundle.xueqiu)

    def test_omitted_services_keeps_default_provider(self):
        from dragon_quant.providers.xueqiu import XueqiuProvider

        data = self._run()
        self.assertIsInstance(data.provider, XueqiuProvider)


class TestLiveTradeInjection(unittest.TestCase):
    """run_buy / run_sell hand the bundle to LiveTrader as real MarketData."""

    def test_run_buy_uses_injected_provider(self):
        from dragon_quant.live_trade.service import run_buy

        bundle = fake_bundle()
        with patch("dragon_quant.live_trade.service.LiveTrader") as trader_cls:
            trader_cls.return_value.buy.return_value = {"buys": []}
            run_buy(DAY, as_of="10:00", verbose=False, services=services_with(bundle))

        (cfg,), kwargs = trader_cls.call_args
        self.assertEqual(kwargs["source"], "v2")
        self.assertIs(kwargs["data"].provider, bundle.xueqiu)
        self.assertEqual(cfg.source, "v2")

    def test_run_sell_uses_injected_provider(self):
        from dragon_quant.live_trade.service import run_sell

        bundle = fake_bundle()
        with patch("dragon_quant.live_trade.service.LiveTrader") as trader_cls:
            trader_cls.return_value.sell.return_value = {"sells": []}
            run_sell(DAY, as_of="10:00", verbose=False, services=services_with(bundle))

        _, kwargs = trader_cls.call_args
        self.assertIs(kwargs["data"].provider, bundle.xueqiu)

    def test_omitted_services_keeps_livetrader_default_construction(self):
        from dragon_quant.live_trade.service import run_buy

        with patch("dragon_quant.live_trade.service.LiveTrader") as trader_cls:
            trader_cls.return_value.buy.return_value = {"buys": []}
            run_buy(DAY, as_of="10:00", verbose=False)

        _, kwargs = trader_cls.call_args
        self.assertNotIn("data", kwargs)
        self.assertEqual(kwargs["source"], "v2")


class TestFeatureImportDirection(unittest.TestCase):
    """Feature modules must not import the Composition Root."""

    FILES = (
        ROOT / "dragon_quant" / "review_account" / "service.py",
        ROOT / "dragon_quant" / "review_account" / "data.py",
        ROOT / "dragon_quant" / "live_trade" / "service.py",
        ROOT / "dragon_quant" / "data.py",
        ROOT / "dragon_quant" / "vpa" / "engine.py",
    )

    def test_no_feature_module_imports_app(self):
        for path in self.FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    modules = [node.module]
                for module in modules:
                    self.assertFalse(
                        module == "dragon_quant.app" or module.startswith("dragon_quant.app."),
                        f"{path.name} must not import {module}",
                    )


if __name__ == "__main__":
    unittest.main()
