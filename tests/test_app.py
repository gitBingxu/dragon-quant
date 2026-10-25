"""M2A Composition Root contracts: runtime providers/clock and app services.

These tests pin the small assembly seam introduced by M2A. They must not
construct real providers, touch the network, or read the real databases: the
default provider factory is always patched here.
"""

import tempfile
import unittest
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from dragon_quant.app import (
    RuntimeConfig,
    build_services,
    ensure_storage,
)
from dragon_quant.runtime.clock import Clock, FixedClock, SystemClock
from dragon_quant.runtime.providers import ProviderBundle, build_provider_bundle
from dragon_quant.storage import paths

_PROVIDER_NAMES = ("tdx", "ths", "xueqiu", "tencent")


def _fake_mapping() -> dict[str, MagicMock]:
    return {name: MagicMock(name=name) for name in _PROVIDER_NAMES}


class TestProviderBundle(unittest.TestCase):
    """ProviderBundle is the single provider assembly unit in runtime."""

    def test_build_provider_bundle_delegates_to_create_providers(self):
        fakes = _fake_mapping()
        with patch("dragon_quant.runtime.providers.create_providers",
                   return_value=fakes) as factory:
            bundle = build_provider_bundle()

        factory.assert_called_once_with(logger=None)
        self.assertIs(bundle.tdx, fakes["tdx"])
        self.assertIs(bundle.ths, fakes["ths"])
        self.assertIs(bundle.xueqiu, fakes["xueqiu"])
        self.assertIs(bundle.tencent, fakes["tencent"])
        self.assertEqual(bundle.as_dict(), fakes)

    def test_build_provider_bundle_forwards_logger(self):
        logger = MagicMock()
        with patch("dragon_quant.runtime.providers.create_providers",
                   return_value=_fake_mapping()) as factory:
            build_provider_bundle(logger=logger)

        factory.assert_called_once_with(logger=logger)

    def test_bundle_is_immutable(self):
        bundle = ProviderBundle(**_fake_mapping())
        with self.assertRaises(FrozenInstanceError):
            bundle.tdx = None  # type: ignore[misc]


class TestClock(unittest.TestCase):
    """Minimal clock contract: passive in M2A, no production call sites."""

    def test_fixed_clock_reports_supplied_moment(self):
        moment = datetime(2026, 5, 19, 15, 30, tzinfo=timezone.utc)
        clock = FixedClock(moment)

        self.assertEqual(clock.now(), moment)
        self.assertEqual(clock.today(), date(2026, 5, 19))
        self.assertEqual(clock.stamp(), "20260519_153000")
        self.assertEqual(clock.monotonic(), 0.0)

    def test_system_clock_returns_wall_clock_values(self):
        clock = SystemClock()

        self.assertIsInstance(clock.now(), datetime)
        self.assertIsInstance(clock.today(), date)
        self.assertIsInstance(clock.stamp(), str)
        self.assertIsInstance(clock.monotonic(), float)

    def test_both_implementations_satisfy_the_protocol(self):
        self.assertIsInstance(SystemClock(), Clock)
        self.assertIsInstance(FixedClock(datetime(2026, 5, 19)), Clock)

    def test_module_seam_defaults_to_system_clock_and_round_trips(self):
        """M9A: production call sites read this module-level seam."""
        from dragon_quant.runtime import clock as runtime_clock

        self.assertIsInstance(runtime_clock.get_clock(), SystemClock)
        moment = datetime(2026, 5, 19, 15, 30, tzinfo=timezone.utc)
        self.addCleanup(runtime_clock.reset_clock)

        previous = runtime_clock.set_clock(FixedClock(moment))

        self.assertIsInstance(previous, SystemClock)
        self.assertIsInstance(runtime_clock.get_clock(), FixedClock)
        self.assertEqual(runtime_clock.now(), moment)
        self.assertEqual(runtime_clock.today(), date(2026, 5, 19))
        self.assertEqual(runtime_clock.stamp(), "20260519_153000")
        self.assertEqual(runtime_clock.monotonic(), 0.0)

        runtime_clock.reset_clock()
        self.assertIsInstance(runtime_clock.get_clock(), SystemClock)

    def test_set_clock_rejects_incomplete_duck_types(self):
        from dragon_quant.runtime import clock as runtime_clock

        self.addCleanup(runtime_clock.reset_clock)
        with self.assertRaises(TypeError):
            runtime_clock.set_clock(object())


class TestBuildServices(unittest.TestCase):
    """build_services assembles config + clock; providers stay lazy."""

    def test_defaults_do_not_construct_providers(self):
        with patch("dragon_quant.runtime.providers.create_providers") as runtime_factory, \
                patch("dragon_quant.providers.create_providers") as legacy_factory:
            services = build_services()

        runtime_factory.assert_not_called()
        legacy_factory.assert_not_called()
        self.assertEqual(services.config, RuntimeConfig(data_dir=paths.DATA_DIR))
        self.assertEqual(services.config.source, "v2")
        self.assertIs(services.config.verbose, True)
        self.assertIsInstance(services.clock, SystemClock)
        self.assertIsNone(services.providers)

    def test_env_data_dir_override_is_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            services = build_services(env={"DQ_DATA_DIR": raw})

        self.assertEqual(services.config.data_dir, Path(raw))

    def test_env_without_override_uses_established_paths(self):
        self.assertEqual(build_services(env={}).config.data_dir, paths.DATA_DIR)

    def test_default_clock_binds_the_active_runtime_seam(self):
        """No second clock source: services.clock is the active runtime instance."""
        from dragon_quant.runtime import clock as runtime_clock

        self.assertIs(build_services().clock, runtime_clock.get_clock())

        moment = datetime(2026, 5, 19, 15, 30)
        previous = runtime_clock.set_clock(FixedClock(moment))
        try:
            frozen = build_services()
            self.assertIs(frozen.clock, runtime_clock.get_clock())
            self.assertIsInstance(frozen.clock, FixedClock)
            self.assertEqual(frozen.clock.now(), moment)
        finally:
            runtime_clock.set_clock(previous)

        restored = build_services()
        self.assertIsInstance(restored.clock, SystemClock)
        self.assertIs(restored.clock, runtime_clock.get_clock())

    def test_supplied_bundle_and_clock_are_stored_verbatim(self):
        bundle = ProviderBundle(**_fake_mapping())
        clock = FixedClock(datetime(2026, 5, 19))

        services = build_services(providers=bundle, clock=clock)

        self.assertIs(services.providers, bundle)
        self.assertIs(services.clock, clock)

    def test_services_is_frozen(self):
        services = build_services()
        with self.assertRaises(FrozenInstanceError):
            services.providers = None  # type: ignore[misc]


class TestEnsureStorage(unittest.TestCase):
    """ensure_storage follows the established storage path semantics."""

    def test_creates_directories_and_initializes_schema_idempotently(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "established"
            dirs = {name: root / name
                    for name in ("cookies", "cache", "logs", "results", "shared")}
            other = Path(raw) / "other"
            # config.data_dir deliberately points elsewhere: storage must not
            # follow it, or a command could bypass DQ_DATA_DIR semantics.
            services = build_services(env={"DQ_DATA_DIR": str(other)})

            with patch("dragon_quant.app._paths.COOKIE_DIR", dirs["cookies"]), \
                    patch("dragon_quant.app._paths.CACHE_DIR", dirs["cache"]), \
                    patch("dragon_quant.app._paths.LOG_DIR", dirs["logs"]), \
                    patch("dragon_quant.app._paths.RESULTS_DIR", dirs["results"]), \
                    patch("dragon_quant.app._paths.SHARED_DIR", dirs["shared"]), \
                    patch("dragon_quant.storage.db.init_db") as init_db:
                ensure_storage(services)
                ensure_storage(services)

            self.assertEqual(init_db.call_count, 2)
            init_db.assert_called_with()
            for path in dirs.values():
                self.assertTrue(path.is_dir(), f"missing directory {path}")
            self.assertFalse(other.exists())


class TestFeatureServicesContract(unittest.TestCase):
    """M2 final contract: every provider-dependent feature entry accepts optional services."""

    def test_feature_entrypoints_accept_optional_services(self):
        import inspect

        from dragon_quant import data, orchestrator, review
        from dragon_quant.factors import finance
        from dragon_quant.live_trade import service as live_service
        from dragon_quant.review_account import prep, service as account_service
        from dragon_quant.vpa import engine as vpa_engine

        entrypoints = {
            "orchestrator.scan": orchestrator.scan,
            "review.run_review": review.run_review,
            "review_account.run_review_account": account_service.run_review_account,
            "live_trade.run_buy": live_service.run_buy,
            "live_trade.run_sell": live_service.run_sell,
            "vpa.analyze": vpa_engine.analyze,
            "prep.run_prep": prep.run_prep,
            "finance.sync_snapshots": finance.sync_snapshots,
            "finance.sync_backfill": finance.sync_backfill,
            "finance.calibrate_field_map": finance.calibrate_field_map,
            "finance.load_fin_source": finance.load_fin_source,
            "finance.load_trade_calendar": finance.load_trade_calendar,
            "data.get_sector_ranking": data.get_sector_ranking,
            "data.get_sector_components": data.get_sector_components,
            "data.get_sector_5min_kline": data.get_sector_5min_kline,
            "data.get_kline": data.get_kline,
            "data.get_minute_kline": data.get_minute_kline,
            "data.get_quote": data.get_quote,
            "data.batch_get_quotes": data.batch_get_quotes,
            "data.get_auction": data.get_auction,
        }

        for name, function in entrypoints.items():
            with self.subTest(entrypoint=name):
                parameters = inspect.signature(function).parameters
                self.assertIn("services", parameters, name)
                self.assertIsNone(parameters["services"].default, name)


if __name__ == "__main__":
    unittest.main()
