"""Command layer: lazy registry, ``handle(args, services)`` and dispatch.

These tests pin the command seam:

* ``dragon_quant.commands.COMMANDS`` is a lazy registry that covers every
  top-level command exactly once;
* every command exposes ``handle(args, services)``, obtains ``Services`` from
  ``app.build_services`` (providers only for data commands) and calls the
  orchestrator / strategy helper through its single public implementation;
* ``dispatch`` is the only entry point shared by ``cli.main`` and tests;
* the live argparse tree still matches the M1 stage-1 51-node snapshot;
* the pure CLI helpers in ``commands/_shared.py`` stay the single
  implementation re-exported by ``cli``;
* the retired command compatibility shim is gone and unreferenced.

No provider is constructed for real, no network is touched and no real database
is opened (``dragon_quant/tests/conftest.py`` redirects ``DQ_DATA_DIR``).
"""

from __future__ import annotations

import argparse
import ast
import errno
import inspect
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, call, patch

from dragon_quant import cli

ROOT = Path(__file__).resolve().parents[2]
COVERAGE_MAP = ROOT / "dragon_quant" / "tests" / "e2e" / "coverage-map.json"
COMMANDS_DIR = ROOT / "dragon_quant" / "commands"

TOP_LEVEL_COMMANDS = (
    "scan",
    "logs",
    "data",
    "blacklist",
    "review",
    "review-account",
    "dragons",
    "prep",
    "auction",
    "assets",
    "factor",
    "buy",
    "sell",
    "vpa",
    "storage",
)

# Every top-level command runs through its own handler module.
MIGRATED_COMMANDS = TOP_LEVEL_COMMANDS

# One module per migrated command; buy/sell share ``commands/buy_sell.py`` and
# expose one handler each instead of a single ``handle``.
MODULE_NAMES = {
    "review-account": "review_account",
    "buy": "buy_sell",
    "sell": "buy_sell",
}
HANDLER_ATTRIBUTES = {
    "buy": "handle_buy",
    "sell": "handle_sell",
}

# Commands whose providers are decided per invocation.
CONDITIONAL_PROVIDER_COMMANDS = ("review-account", "prep", "factor")
PROVIDER_ALWAYS = ("vpa", "buy", "sell")
PROVIDER_NEVER = ("scan", "logs", "blacklist", "dragons", "storage", "assets")


def _args_for(name: str) -> SimpleNamespace:
    """Minimal argparse namespace carrying the flags a provider predicate reads."""
    if name == "review":
        return SimpleNamespace(ui_only=False)
    if name == "data":
        return SimpleNamespace(data_action="kline")
    if name == "review-account":
        return SimpleNamespace(ui_only=False, date_from="20260101", date_to="20260102")
    if name == "prep":
        return SimpleNamespace(recompute=False)
    if name == "auction":
        return SimpleNamespace(recompute=None)
    if name == "factor":
        return SimpleNamespace(factor_action="build", fin_action=None)
    return SimpleNamespace()


def _import_roots(path: Path, *, top_level_only: bool) -> set[str]:
    """Absolute module names touched by ``path`` (``from X import y`` → X and X.y)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    nodes = tree.body if top_level_only else ast.walk(tree)
    for node in nodes:
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            base = node.module or ""
            if base:
                roots.add(base)
            roots.update(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
                if alias.name != "*"
            )
    return roots


def _fake_bundle():
    from dragon_quant.infra.provider.bundle import ProviderBundle

    return ProviderBundle(**{
        name: MagicMock(name=name) for name in ("tdx", "ths", "xueqiu", "tencent")
    })


class TestCommandRegistry(unittest.TestCase):

    def test_registry_covers_every_top_level_command_exactly_once(self):
        from dragon_quant import commands

        self.assertEqual(set(commands.COMMANDS), set(TOP_LEVEL_COMMANDS))
        self.assertEqual(len(commands.COMMANDS), len(TOP_LEVEL_COMMANDS))

    def test_migrated_entries_are_lazy_handle_references(self):
        from dragon_quant import commands

        for name in MIGRATED_COMMANDS:
            entry = commands.COMMANDS[name]
            module = MODULE_NAMES.get(name, name)
            attribute = HANDLER_ATTRIBUTES.get(name, "handle")
            self.assertIsInstance(entry, commands.LazyCommand, name)
            self.assertEqual(entry.module, f"dragon_quant.commands.{module}")
            self.assertEqual(entry.attribute, attribute)
            self.assertIs(entry.load(), getattr(
                __import__(entry.module, fromlist=[attribute]), attribute))

    def test_every_top_level_command_has_its_own_handler_module(self):
        from dragon_quant import commands

        self.assertEqual(len(MIGRATED_COMMANDS), 15)
        for name in MIGRATED_COMMANDS:
            with self.subTest(command=name):
                entry = commands.COMMANDS[name]
                self.assertIsInstance(entry, commands.LazyCommand, name)
                self.assertTrue(entry.module.startswith("dragon_quant.commands."))
                module = __import__(entry.module, fromlist=[entry.attribute])
                self.assertTrue(callable(getattr(module, entry.attribute)))

    def test_provider_policy_is_resolved_per_invocation(self):
        from dragon_quant import commands

        for name in PROVIDER_NEVER:
            with self.subTest(command=name):
                self.assertFalse(
                    commands.COMMANDS[name].providers_needed(_args_for(name)), name)
        for name in PROVIDER_ALWAYS:
            with self.subTest(command=name):
                self.assertTrue(
                    commands.COMMANDS[name].providers_needed(_args_for(name)), name)

        review = commands.COMMANDS["review"]
        self.assertTrue(review.providers_needed(SimpleNamespace(ui_only=False)))
        self.assertFalse(review.providers_needed(SimpleNamespace(ui_only=True)))

        data = commands.COMMANDS["data"]
        for action in ("cookie-status", "cookie-set", "cookie-fetch", None):
            with self.subTest(data_action=action):
                self.assertFalse(data.providers_needed(
                    SimpleNamespace(data_action=action)))
        for action in ("sector", "components", "kline", "minute", "quote",
                       "batch-quote"):
            with self.subTest(data_action=action):
                self.assertTrue(data.providers_needed(
                    SimpleNamespace(data_action=action)))

        account = commands.COMMANDS["review-account"]
        self.assertTrue(account.providers_needed(_args_for("review-account")))
        self.assertFalse(account.providers_needed(
            SimpleNamespace(ui_only=True, date_from=None, date_to=None)))
        self.assertFalse(account.providers_needed(
            SimpleNamespace(ui_only=False, date_from=None, date_to=None)))

        prep = commands.COMMANDS["prep"]
        self.assertTrue(prep.providers_needed(SimpleNamespace(recompute=False)))
        self.assertFalse(prep.providers_needed(SimpleNamespace(recompute=True)))

        auction = commands.COMMANDS["auction"]
        self.assertTrue(auction.providers_needed(SimpleNamespace(recompute=None)))
        self.assertFalse(auction.providers_needed(
            SimpleNamespace(recompute="20261020")))

        factor = commands.COMMANDS["factor"]
        for action in ("build", "eval"):
            with self.subTest(factor_action=action):
                self.assertTrue(factor.providers_needed(
                    SimpleNamespace(factor_action=action, fin_action=None)))
        for action in ("list", "query", "status", "export"):
            with self.subTest(factor_action=action):
                self.assertFalse(factor.providers_needed(
                    SimpleNamespace(factor_action=action, fin_action=None)))
        self.assertTrue(factor.providers_needed(
            SimpleNamespace(factor_action="fin", fin_action="sync")))
        self.assertTrue(factor.providers_needed(
            SimpleNamespace(factor_action="fin", fin_action="calibrate")))
        self.assertFalse(factor.providers_needed(
            SimpleNamespace(factor_action="fin", fin_action="status")))
        self.assertFalse(factor.providers_needed(
            SimpleNamespace(factor_action=None, fin_action=None)))

    def test_registry_import_does_not_load_command_modules(self):
        code = (
            "import sys\n"
            "import dragon_quant.commands\n"
            "loaded = sorted(name for name in sys.modules\n"
            "                if name.startswith('dragon_quant.commands.'))\n"
            "print(','.join(loaded))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_command_modules_never_import_cli_statically(self):
        modules = sorted(COMMANDS_DIR.glob("*.py"))
        self.assertTrue(modules)
        for path in modules:
            offenders = {
                name for name in _import_roots(path, top_level_only=False)
                if name == "dragon_quant.cli" or name.startswith("dragon_quant.cli.")
            }
            self.assertFalse(offenders, f"{path.name} imports cli: {sorted(offenders)}")

    def test_cli_imports_the_registry_but_no_handler_module(self):
        imports = _import_roots(ROOT / "dragon_quant" / "cli.py", top_level_only=False)
        self.assertIn("dragon_quant.commands", imports)
        allowed = {"dispatch", "_shared"}   # package symbols/helpers, not handlers
        offenders = sorted(
            name for name in imports
            if name.startswith("dragon_quant.commands.")
            and name.split(".")[2] not in allowed
        )
        self.assertFalse(offenders, f"cli.py statically loads handlers: {offenders}")

    def test_the_legacy_command_shim_is_deleted_and_unreferenced(self):
        """M11B removed the forwarding shim; the command modules are the handlers."""
        shim = ROOT / "dragon_quant" / ("cli_" + "commands.py")
        self.assertFalse(shim.exists(), f"{shim} must be deleted")

        # 拼接模块名，避免本测试文件自身命中下面的源码扫描。
        needle = "dragon_quant." + "cli_" + "commands"
        package = ROOT / "dragon_quant"
        offenders = sorted(
            path.relative_to(ROOT).as_posix()
            for path in package.rglob("*.py")
            if path.relative_to(package).parts[:1] != ("tests",)
            and needle in path.read_text(encoding="utf-8")
        )
        self.assertEqual(offenders, [], "production code still references the shim")


class TestDispatchAndServices(unittest.TestCase):

    def test_no_data_command_receives_services_without_providers(self):
        from dragon_quant import commands
        from dragon_quant.commands import logs as logs_command

        args = SimpleNamespace()
        services = object()
        with patch("dragon_quant.infra.provider.bundle.build_provider_bundle") as bundle, \
                patch("dragon_quant.app.build_services", return_value=services) as build, \
                patch.object(logs_command, "handle") as handler:
            self.assertTrue(commands.dispatch("logs", args))

        build.assert_called_once_with()
        bundle.assert_not_called()
        handler.assert_called_once_with(args, services)

    def test_data_commands_receive_the_default_provider_bundle(self):
        from dragon_quant import commands

        for name in PROVIDER_ALWAYS + ("review", "data", "review-account",
                                       "prep", "factor", "auction"):
            with self.subTest(command=name):
                entry = commands.COMMANDS[name]
                module = __import__(entry.module, fromlist=[entry.attribute])
                args = _args_for(name)
                services = object()
                bundle = _fake_bundle()
                with patch("dragon_quant.infra.provider.bundle.build_provider_bundle",
                           return_value=bundle) as factory, \
                        patch("dragon_quant.app.build_services",
                              return_value=services) as build, \
                        patch.object(module, entry.attribute) as handler:
                    self.assertTrue(commands.dispatch(name, args))

                factory.assert_called_once_with()
                build.assert_called_once_with(providers=bundle)
                handler.assert_called_once_with(args, services)

    def test_auction_recompute_and_assets_stay_fully_offline(self):
        from dragon_quant import commands

        for name, args in (
            ("auction", SimpleNamespace(recompute="20261020")),
            ("assets", SimpleNamespace(assets_action="status")),
        ):
            with self.subTest(command=name):
                entry = commands.COMMANDS[name]
                module = __import__(entry.module, fromlist=[entry.attribute])
                services = object()
                with patch("dragon_quant.infra.provider.bundle.build_provider_bundle") as factory, \
                        patch("dragon_quant.app.build_services",
                              return_value=services) as build, \
                        patch.object(module, entry.attribute) as handler:
                    self.assertTrue(commands.dispatch(name, args))

                factory.assert_not_called()
                build.assert_called_once_with()
                handler.assert_called_once_with(args, services)

    def test_offline_actions_of_online_commands_skip_provider_construction(self):
        """M3C: ``review --ui-only`` and ``data cookie-*`` never build providers."""
        from dragon_quant import commands

        for name, args in (
            ("review", SimpleNamespace(ui_only=True)),
            ("data", SimpleNamespace(data_action="cookie-status")),
            ("data", SimpleNamespace(data_action="cookie-set", cookie="x")),
            ("data", SimpleNamespace(data_action="cookie-fetch")),
            ("data", SimpleNamespace(data_action=None)),
        ):
            with self.subTest(command=name, action=getattr(args, "data_action", None)):
                entry = commands.COMMANDS[name]
                module = __import__(entry.module, fromlist=[entry.attribute])
                services = object()
                with patch("dragon_quant.infra.provider.bundle.build_provider_bundle") as factory, \
                        patch("dragon_quant.app.build_services",
                              return_value=services) as build, \
                        patch.object(module, entry.attribute) as handler:
                    self.assertTrue(commands.dispatch(name, args))

                factory.assert_not_called()
                build.assert_called_once_with()
                handler.assert_called_once_with(args, services)

    def test_registry_lookup_needs_no_prior_cli_import(self):
        # Dispatch only needs the registry package; no CLI import is required.
        code = (
            "from dragon_quant.commands import COMMANDS\n"
            "print(COMMANDS['auction'].load().__name__)\n"
            "print(COMMANDS['assets'].load().__name__)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["handle", "handle"])

    def test_unknown_or_missing_command_is_not_dispatched(self):
        from dragon_quant import commands

        self.assertFalse(commands.dispatch(None, SimpleNamespace()))
        self.assertFalse(commands.dispatch("not-a-command", SimpleNamespace()))

    def test_cli_main_dispatches_migrated_commands_through_the_registry(self):
        with patch("sys.argv", ["dragon-quant", "review", "--date", "20260519"]), \
                patch.object(cli, "warn_if_unavailable"), \
                patch("dragon_quant.review.main.run_review") as run:
            cli.main()

        run.assert_called_once()
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["trade_date"], "2026-05-19")
        self.assertIsNotNone(kwargs["services"].providers)

    def test_cli_main_sends_auction_and_assets_through_the_registry(self):
        with patch("sys.argv", ["dragon-quant", "auction", "--recompute", "20261020"]), \
                patch.object(cli, "warn_if_unavailable"), \
                patch("dragon_quant.commands.auction.handle") as auction, \
                patch("dragon_quant.infra.provider.bundle.build_provider_bundle") as factory:
            cli.main()

        auction.assert_called_once()
        self.assertIsNone(auction.call_args.args[1].providers)
        factory.assert_not_called()

        with patch("sys.argv", ["dragon-quant", "assets", "status"]), \
                patch.object(cli, "warn_if_unavailable"), \
                patch("dragon_quant.commands.assets.handle") as assets:
            cli.main()

        assets.assert_called_once()
        self.assertIsNone(assets.call_args.args[1].providers)

    def test_scan_dispatch_keeps_the_historical_provider_construction(self):
        # The M1 golden pins scan's api_stats: injecting a pre-built bundle adds
        # one logged TDX call (second ``_get_trade_date``), so scan must keep
        # ``providers=None`` and let the orchestrator construct its own set.
        with patch("sys.argv", ["dragon-quant", "scan"]), \
                patch.object(cli, "warn_if_unavailable"), \
                patch("dragon_quant.scan.orchestrator.main.scan") as orchestrate, \
                patch("dragon_quant.infra.provider.bundle.build_provider_bundle") as factory:
            cli.main()

        factory.assert_not_called()
        self.assertIsNone(orchestrate.call_args.kwargs["services"].providers)


    def test_every_command_dispatches_to_its_handler(self):
        """M11B: ``dispatch`` is the single entry for all 15 top-level commands."""
        from dragon_quant import commands

        for name in TOP_LEVEL_COMMANDS:
            with self.subTest(command=name):
                entry = commands.COMMANDS[name]
                module = __import__(entry.module, fromlist=[entry.attribute])
                args = _args_for(name)
                services = object()
                bundle = _fake_bundle()
                expects_providers = entry.providers_needed(args)
                with patch("dragon_quant.infra.provider.bundle.build_provider_bundle",
                           return_value=bundle) as factory, \
                        patch("dragon_quant.app.build_services",
                              return_value=services) as build, \
                        patch.object(module, entry.attribute) as handler:
                    self.assertTrue(commands.dispatch(name, args))

                if expects_providers:
                    factory.assert_called_once_with()
                    build.assert_called_once_with(providers=bundle)
                else:
                    factory.assert_not_called()
                    build.assert_called_once_with()
                handler.assert_called_once_with(args, services)

    def test_scan_dispatch_forwards_top_n_and_keeps_providers_none(self):
        from dragon_quant import commands

        args = SimpleNamespace(date=None, top=7, candidates=3, workers=2,
                               force=True, no_cache=True)
        with patch("dragon_quant.scan.orchestrator.main.scan") as orchestrate, \
                patch("dragon_quant.infra.provider.bundle.build_provider_bundle") as factory:
            self.assertTrue(commands.dispatch("scan", args))

        factory.assert_not_called()
        orchestrate.assert_called_once()
        self.assertEqual(orchestrate.call_args.kwargs["top_n"], 7)
        self.assertIsNone(orchestrate.call_args.kwargs["services"].providers)

    def test_buy_dispatch_reads_strategy_params_through_the_shared_helper(self):
        from dragon_quant import commands

        args = SimpleNamespace(date="20260907", source="v2", at="10:00",
                               config="params.json", force=False, json=False)
        with patch("dragon_quant.infra.provider.bundle.build_provider_bundle",
                   return_value=_fake_bundle()), \
                patch("dragon_quant.commands._shared.load_strategy_params",
                      return_value={"max_position_pct": 25}) as params, \
                patch("dragon_quant.review_account.live_trade.main.run_buy",
                      return_value={"buys": []}) as run:
            self.assertTrue(commands.dispatch("buy", args))

        params.assert_called_once_with("params.json")
        self.assertEqual(run.call_args.kwargs["strategy_params"],
                         {"max_position_pct": 25})

    def test_cli_main_without_command_still_prints_help(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant"]), \
                patch.object(cli, "warn_if_unavailable"), \
                patch("dragon_quant.commands.dispatch", return_value=False) as dispatch, \
                redirect_stdout(buf):
            cli.main()

        dispatch.assert_called_once_with(None, ANY)
        self.assertIn("Usage:", buf.getvalue())
        self.assertIn("Commands:", buf.getvalue())


class TestParserSurface(unittest.TestCase):
    """The live argparse tree must still equal the M1 51-node snapshot."""

    def test_live_parser_matches_the_stage_one_snapshot(self):
        snapshot = json.loads(COVERAGE_MAP.read_text(encoding="utf-8"))["parser_snapshot"]
        expected = {
            node["command"]: sorted(node["flags"]) for node in snapshot["nodes"]
        }
        self.assertEqual(snapshot["node_count"], len(expected))

        live = _walk_parser(_capture_parser())
        self.assertEqual(set(live), set(expected))
        self.assertEqual(live, expected)

    def test_review_port_default_is_sentinel_and_review_account_keeps_8765(self):
        parser = _capture_parser()
        sub = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )

        def port_action(name: str) -> argparse.Action:
            return next(
                action for action in sub.choices[name]._actions
                if "--port" in action.option_strings
            )

        self.assertIsNone(port_action("review").default)
        self.assertEqual(port_action("review-account").default, 8765)
        # An explicit --port still parses through unchanged.
        self.assertEqual(
            sub.choices["review"].parse_args(["--port", "9000"]).port, 9000)


def _capture_parser() -> argparse.ArgumentParser:
    captured: dict[str, argparse.ArgumentParser] = {}
    original = argparse.ArgumentParser.parse_args

    def _fake_parse_args(self, args=None, namespace=None):
        captured["parser"] = self
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = _fake_parse_args
    try:
        with patch("sys.argv", ["dragon-quant", "-h"]):
            with unittest.TestCase().assertRaises(SystemExit):
                cli.main()
    finally:
        argparse.ArgumentParser.parse_args = original
    return captured["parser"]


def _walk_parser(parser: argparse.ArgumentParser) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}

    def visit(node: argparse.ArgumentParser, path: list[str]) -> None:
        for action in node._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for name, sub in action.choices.items():
                command = " ".join(path + [name])
                rows[command] = sorted(
                    flag
                    for child in sub._actions
                    for flag in getattr(child, "option_strings", [])
                )
                visit(sub, path + [name])

    visit(parser, [])
    return rows


class TestSharedHelpers(unittest.TestCase):

    def test_cli_reexports_the_shared_command_helpers(self):
        from dragon_quant.commands import _shared

        self.assertIs(cli._normalize_cli_date, _shared.normalize_cli_date)
        self.assertIs(cli._resolve_trade_date, _shared.resolve_trade_date)
        self.assertIs(cli._kbar_to_dict, _shared.kbar_to_dict)
        self.assertIs(cli._to_dict, _shared.to_dict)

    def test_cli_no_longer_exposes_the_strategy_aliases(self):
        self.assertFalse(hasattr(cli, "orchestrate_scan"))
        self.assertFalse(hasattr(cli, "_strategy_params"))

    def test_command_helpers_are_reexports_not_copies(self):
        """Each helper has exactly one implementation; command modules re-export it."""
        from dragon_quant.review_account.auction import main as auction_service
        from dragon_quant.commands import _shared, assets, auction, prep

        self.assertIs(auction._score_auction_assets,
                      auction_service._score_auction_assets)
        self.assertIs(auction._trade_calendar, auction_service._trade_calendar)
        self.assertEqual(prep._prep_plan_hint.__module__,
                         "dragon_quant.commands.prep")
        self.assertEqual(assets._assets_human_summary.__module__,
                         "dragon_quant.commands.assets")
        self.assertEqual(assets._period_filter_hint.__module__,
                         "dragon_quant.commands.assets")
        self.assertIs(cli._resolve_trade_date, _shared.resolve_trade_date)

    def test_load_strategy_params_returns_none_without_a_path(self):
        from dragon_quant.commands import _shared

        self.assertIsNone(_shared.load_strategy_params(None))

    def test_load_strategy_params_reads_and_validates_json(self):
        from dragon_quant.commands import _shared

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "params.json"
            path.write_text('{"max_position_pct": 25}', encoding="utf-8")

            self.assertEqual(_shared.load_strategy_params(str(path)),
                             {"max_position_pct": 25})

            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "策略配置必须是 JSON 对象"):
                _shared.load_strategy_params(str(path))

            path.write_text('{"factor_min_coverage": 0}', encoding="utf-8")
            with self.assertRaises(ValueError):
                _shared.load_strategy_params(str(path))

    def test_date_helpers_keep_their_historical_formats(self):
        from dragon_quant.commands import _shared

        self.assertEqual(_shared.normalize_cli_date("20260519"), "2026-05-19")
        self.assertEqual(_shared.normalize_cli_date("2026/05/19"), "2026-05-19")
        self.assertEqual(_shared.normalize_cli_date("2026-05-19"), "2026-05-19")

    def test_kbar_to_dict_rounds_floats(self):
        from dragon_quant.commands import _shared

        kbar = SimpleNamespace(
            time=1, open=1.234567, close=2.0, high=3.0, low=0.5,
            volume=10, pct=1.234567,
        )
        self.assertEqual(_shared.kbar_to_dict(kbar), {
            "time": 1, "open": 1.2346, "close": 2.0, "high": 3.0,
            "low": 0.5, "volume": 10, "pct": 1.2346,
        })


class TestMigratedHandlers(unittest.TestCase):

    def test_auction_calendar_uses_the_injected_tdx_provider(self):
        """M3C: the T-1 calendar reads ``services.providers.tdx`` (no construction)."""
        from dragon_quant.commands import auction as auction_command

        tdx = object()
        services = SimpleNamespace(providers=SimpleNamespace(tdx=tdx))
        with patch("dragon_quant.infra.market_calendar.tdx_trade_dates",
                   return_value=["2026-10-19"]) as calendar:
            self.assertEqual(auction_command._trade_calendar(services),
                             ["2026-10-19"])

        calendar.assert_called_once_with(tdx)

    def test_auction_calendar_without_providers_raises(self):
        from dragon_quant.commands import auction as auction_command

        with self.assertRaises(RuntimeError):
            auction_command._trade_calendar(SimpleNamespace(providers=None))
        with self.assertRaises(RuntimeError):
            auction_command._trade_calendar(None)

    def test_every_migrated_handler_accepts_args_and_services(self):
        from dragon_quant import commands

        self.assertEqual(len(MIGRATED_COMMANDS), 15)
        for name in MIGRATED_COMMANDS:
            with self.subTest(command=name):
                handler = commands.COMMANDS[name].load()
                parameters = list(inspect.signature(handler).parameters)
                self.assertEqual(parameters[:2], ["args", "services"])

    def test_scan_handle_forwards_parameters_and_services(self):
        from dragon_quant.commands import scan as scan_command

        args = SimpleNamespace(
            date=None, top=7, candidates=3, workers=2, force=True, no_cache=True)
        services = object()
        with patch("dragon_quant.scan.orchestrator.main.scan") as orchestrate:
            scan_command.handle(args, services)

        orchestrate.assert_called_once_with(
            top_n=7, candidates_n=3, workers=2, verbose=True, force=True,
            scorers="v2", refresh_provider_cache=True, services=services)

    def test_scan_handle_calls_the_orchestrator_without_a_cli_seam(self):
        from dragon_quant.commands import scan as scan_command

        self.assertFalse(hasattr(scan_command, "_orchestrate_scan"))

        args = SimpleNamespace(
            date=None, top=25, candidates=5, workers=2, force=False, no_cache=False)
        services = object()
        with patch.dict(sys.modules, {"dragon_quant.cli": None}), \
                patch("dragon_quant.scan.orchestrator.main.scan") as direct:
            scan_command.handle(args, services)

        direct.assert_called_once()

    def test_review_handle_forwards_date_and_services(self):
        from dragon_quant.commands import review as review_command

        args = SimpleNamespace(
            date="20260519", top=None, force=False, source="v2",
            ui=False, ui_only=False)
        services = object()
        with patch("dragon_quant.review.main.run_review") as run:
            review_command.handle(args, services)

        run.assert_called_once_with(
            trade_date="2026-05-19", top_n=None, force=False, verbose=True,
            source="v2", services=services, recompute_all=False,
            dry_run=False, limit=None)

    def test_review_handle_ui_only_starts_the_server_without_backtesting(self):
        from dragon_quant.commands import review as review_command

        args = SimpleNamespace(
            date=None, top=None, force=False, source="v2", ui=False, ui_only=True,
            port=8765, no_browser=True)
        with patch("dragon_quant.review.main.run_review") as run, \
                patch("web_ui.server.start_server") as start:
            review_command.handle(args, object())

        run.assert_not_called()
        start.assert_called_once_with(port=8765, open_browser=False, default_source="v2")

    def test_review_ui_only_default_port_retries_ephemeral_on_address_in_use(self):
        from dragon_quant.commands import review as review_command

        args = SimpleNamespace(
            date=None, top=None, force=False, source="v2", ui=False, ui_only=True,
            port=None, no_browser=True)
        conflict = OSError(errno.EADDRINUSE, "Address already in use")
        stderr = io.StringIO()
        with patch("dragon_quant.review.main.run_review") as run, \
                patch("web_ui.server.start_server",
                      side_effect=[conflict, None]) as start, \
                redirect_stderr(stderr):
            review_command.handle(args, object())

        run.assert_not_called()
        self.assertEqual(start.call_args_list, [
            call(port=8765, open_browser=False, default_source="v2"),
            call(port=0, open_browser=False, default_source="v2"),
        ])
        message = stderr.getvalue()
        self.assertIn("8765", message)
        self.assertIn("占用", message)
        self.assertNotIn("Traceback", message)

    def test_review_ui_only_explicit_port_conflict_fails_without_retry(self):
        from dragon_quant.commands import review as review_command

        args = SimpleNamespace(
            date=None, top=None, force=False, source="v2", ui=False, ui_only=True,
            port=8799, no_browser=True)
        conflict = OSError(errno.EADDRINUSE, "Address already in use")
        stderr = io.StringIO()
        with patch("dragon_quant.review.main.run_review") as run, \
                patch("web_ui.server.start_server", side_effect=conflict) as start, \
                redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            review_command.handle(args, object())

        run.assert_not_called()
        self.assertNotEqual(raised.exception.code, 0)
        start.assert_called_once_with(port=8799, open_browser=False, default_source="v2")
        message = stderr.getvalue()
        self.assertIn("8799", message)
        self.assertIn("--port", message)
        self.assertNotIn("Traceback", message)

    def test_review_ui_only_non_address_in_use_error_is_not_swallowed(self):
        from dragon_quant.commands import review as review_command

        args = SimpleNamespace(
            date=None, top=None, force=False, source="v2", ui=False, ui_only=True,
            port=8799, no_browser=True)
        denied = OSError(errno.EACCES, "Permission denied")
        stderr = io.StringIO()
        with patch("dragon_quant.review.main.run_review") as run, \
                patch("web_ui.server.start_server", side_effect=denied) as start, \
                redirect_stderr(stderr), self.assertRaises(OSError):
            review_command.handle(args, object())

        run.assert_not_called()
        self.assertEqual(start.call_count, 1)
        self.assertEqual(start.call_args.kwargs["port"], 8799)
        self.assertNotIn("8765", stderr.getvalue())

    def test_review_is_addr_in_use_matches_posix_errno_and_windows_winerror(self):
        from dragon_quant.commands import review as review_command

        self.assertTrue(
            review_command._is_addr_in_use(OSError(errno.EADDRINUSE, "in use")))
        self.assertFalse(
            review_command._is_addr_in_use(OSError(errno.EACCES, "denied")))
        self.assertTrue(
            review_command._is_addr_in_use(SimpleNamespace(errno=None, winerror=10048)))
        self.assertFalse(
            review_command._is_addr_in_use(SimpleNamespace(errno=None, winerror=10013)))

    def test_logs_handle_tail_prints_json_lines_with_source(self):
        from dragon_quant.commands import logs as logs_command

        args = SimpleNamespace(logs_action="tail", lines=2, source="v2")
        buf = io.StringIO()
        with patch("dragon_quant.scan.logger.main.tail_logs",
                   return_value=[{"a": 1}, {"b": "中"}]) as tail, redirect_stdout(buf):
            logs_command.handle(args, object())

        tail.assert_called_once_with(lines=2, source="v2")
        self.assertEqual(buf.getvalue().splitlines(), ['{"a": 1}', '{"b": "中"}'])

    def test_data_handle_kline_forwards_services_and_serializes_bars(self):
        from dragon_quant.commands import data as data_command

        kbar = SimpleNamespace(
            time=1, open=1.0, close=2.0, high=3.0, low=0.5, volume=10, pct=1.5)
        args = SimpleNamespace(data_action="kline", code="600001", source="xueqiu", days=20)
        services = object()
        buf = io.StringIO()
        with patch("dragon_quant.infra.provider.data.get_kline", return_value=[kbar]) as get, \
                redirect_stdout(buf):
            data_command.handle(args, services)

        get.assert_called_once_with(
            "600001", source="xueqiu", days=20, services=services)
        self.assertEqual(json.loads(buf.getvalue()), {
            "time": 1, "open": 1.0, "close": 2.0, "high": 3.0,
            "low": 0.5, "volume": 10, "pct": 1.5,
        })

    def test_data_handle_batch_quote_skips_missing_entries(self):
        from dataclasses import dataclass

        @dataclass
        class _Quote:
            code: str
            name: str

        from dragon_quant.commands import data as data_command

        quote = _Quote("600001", "A")
        args = SimpleNamespace(data_action="batch-quote", codes="600001,600002",
                               source="tencent")
        services = object()
        buf = io.StringIO()
        with patch("dragon_quant.infra.provider.data.batch_get_quotes",
                   return_value=[quote, None]) as batch, redirect_stdout(buf):
            data_command.handle(args, services)

        batch.assert_called_once_with(
            ["600001", "600002"], source="tencent", services=services)
        self.assertEqual(json.loads(buf.getvalue()), {"code": "600001", "name": "A"})

    def test_blacklist_handle_list_and_add(self):
        from dragon_quant.commands import blacklist as blacklist_command

        services = object()
        buf = io.StringIO()
        with patch("dragon_quant.scan.storage.blacklist.get_sector_blacklist",
                   return_value=[]), redirect_stdout(buf):
            blacklist_command.handle(SimpleNamespace(blacklist_action=None), services)
        self.assertEqual(buf.getvalue().strip(), "黑名单为空")

        buf = io.StringIO()
        with patch("dragon_quant.scan.storage.blacklist.add_sector_blacklist") as add, \
                redirect_stdout(buf):
            blacklist_command.handle(
                SimpleNamespace(blacklist_action="add", name="次新股"), services)
        add.assert_called_once_with("次新股")
        self.assertEqual(buf.getvalue().strip(), "✅ 已加入黑名单: 次新股")

    def test_dragons_handle_without_data_exits_one(self):
        from dragon_quant.commands import dragons as dragons_command

        buf = io.StringIO()
        with patch("dragon_quant.scan.storage.dragons.list_dragon_trade_dates", return_value=[]), \
                redirect_stdout(buf):
            with self.assertRaises(SystemExit) as raised:
                dragons_command.handle(
                    SimpleNamespace(date=None, true_only=False), object())

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("error", json.loads(buf.getvalue()))

    def test_storage_handle_size_uses_the_shared_manager(self):
        from dragon_quant.commands import storage as storage_command

        class _Manager:
            def size(self):
                return {"total": "1.0K", "by_dir": {"cache": 1024}}

        args = SimpleNamespace(storage_action="size")
        buf = io.StringIO()
        with patch.object(storage_command, "StorageManager", return_value=_Manager()), \
                redirect_stdout(buf):
            storage_command.handle(args, object())

        self.assertEqual(buf.getvalue().splitlines(), ["总占用: 1.0K", "  cache: 1.0K"])

    def test_vpa_handle_passes_services_and_persists_healthy_reports(self):
        from dragon_quant.commands import vpa as vpa_command

        factor = SimpleNamespace(
            name="vol_amount", title="成交量/额", signal="bullish",
            score=80.0, note="note", evidence=[], details={})
        report = SimpleNamespace(
            code="600001", source="xueqiu", health_score=80.0,
            signal="bullish", summary="summary", factors=[factor], fallback=False)
        args = SimpleNamespace(code="600001", source="xueqiu", days=60, no_save=False)
        services = object()
        buf = io.StringIO()
        with patch("dragon_quant.vpa.main.analyze", return_value=report) as analyze, \
                patch("dragon_quant.vpa.main.render", return_value="REPORT"), \
                patch("dragon_quant.vpa.main.upsert_vpa") as upsert, \
                redirect_stdout(buf):
            vpa_command.handle(args, services)

        analyze.assert_called_once_with(
            "600001", source="xueqiu", days=60, services=services)
        self.assertEqual(buf.getvalue(), "REPORT\n")
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.kwargs["code"], "600001")
        factors_json = json.loads(upsert.call_args.kwargs["factors_json"])
        self.assertEqual(factors_json[0]["name"], "vol_amount")

    def test_vpa_handle_skips_persistence_for_fallback_or_no_save(self):
        from dragon_quant.commands import vpa as vpa_command

        fallback = SimpleNamespace(
            code="600001", source="xueqiu", health_score=0.0, signal="neutral",
            summary="", factors=[], fallback=True)
        for no_save, report in ((False, fallback), (True, fallback)):
            with self.subTest(no_save=no_save):
                args = SimpleNamespace(
                    code="600001", source="xueqiu", days=60, no_save=no_save)
                with patch("dragon_quant.vpa.main.analyze", return_value=report), \
                        patch("dragon_quant.vpa.main.render", return_value="R"), \
                        patch("dragon_quant.vpa.main.upsert_vpa") as upsert, \
                        redirect_stdout(io.StringIO()):
                    vpa_command.handle(args, object())
                upsert.assert_not_called()


class TestReviewAccountCommand(unittest.TestCase):
    """M3B: review-account keeps its exit codes, channels and patch target."""

    def test_handle_forwards_dates_config_and_services(self):
        from dragon_quant.commands import review_account as command

        args = SimpleNamespace(
            ui_only=False, ui=False, date_from="20260901", date_to="20260902",
            capital=200000.0, source="v2", strategy="dragon_pullback_daily",
            config="params.json")
        services = object()
        with patch("dragon_quant.review_account.main.run_review_account") as run, \
                patch("dragon_quant.commands._shared.load_strategy_params",
                      return_value={"max_position_pct": 25}) as params:
            command.handle(args, services)

        params.assert_called_once_with("params.json")

        run.assert_called_once_with(
            date_from="2026-09-01", date_to="2026-09-02", initial_cash=200000.0,
            source="v2", strategy_name="dragon_pullback_daily", verbose=True,
            strategy_params={"max_position_pct": 25}, services=services)

    def test_missing_dates_prints_and_returns_without_a_provider(self):
        from dragon_quant.commands import review_account as command

        args = SimpleNamespace(
            ui_only=False, ui=False, date_from=None, date_to=None, config=None)
        buf = io.StringIO()
        with patch("dragon_quant.review_account.main.run_review_account") as run, \
                redirect_stderr(buf):
            command.handle(args, object())

        run.assert_not_called()
        self.assertEqual(
            buf.getvalue(),
            "错误: review-account 需要 --from 和 --to，或使用 --ui-only\n")

    def test_ui_only_starts_the_account_server_without_backtesting(self):
        from dragon_quant.commands import review_account as command

        args = SimpleNamespace(
            ui_only=True, ui=False, date_from=None, date_to=None, port=8765,
            no_browser=False, source="v2")
        with patch("dragon_quant.review_account.main.run_review_account") as run, \
                patch("web_ui.server.start_server") as server:
            command.handle(args, None)

        run.assert_not_called()
        server.assert_called_once_with(
            port=8765, open_browser=True, default_source="v2",
            default_page="account")

    def test_ui_after_backtest_starts_the_account_server(self):
        from dragon_quant.commands import review_account as command

        args = SimpleNamespace(
            ui_only=False, ui=True, date_from="20260901", date_to="20260902",
            capital=100000.0, source="v2", strategy="s", config=None,
            port=8765, no_browser=True)
        with patch("dragon_quant.review_account.main.run_review_account"), \
                patch("web_ui.server.start_server") as server:
            command.handle(args, object())

        self.assertEqual(server.call_args.kwargs["default_page"], "account")


class TestBuySellCommands(unittest.TestCase):
    """M3B: buy/sell reuse the shared strategy config resolver and services."""

    def test_buy_forwards_replay_time_force_and_services(self):
        from dragon_quant.commands import buy_sell as command

        args = SimpleNamespace(
            date="20260907", source="v2", at="10:00", config=None,
            force=False, json=False)
        services = object()
        with patch("dragon_quant.review_account.live_trade.main.run_buy",
                   return_value={"buys": []}) as run, \
                patch("dragon_quant.commands._shared.load_strategy_params",
                      return_value=None) as params:
            command.handle_buy(args, services)

        params.assert_called_once_with(None)
        run.assert_called_once_with(
            "2026-09-07", source="v2", verbose=True, as_of="10:00",
            strategy_params=None, force=False, services=services)

    def test_buy_reads_strategy_params_through_the_shared_helper(self):
        from dragon_quant.commands import buy_sell as command

        args = SimpleNamespace(
            date=None, source="v2", at=None, config="params.json",
            force=True, json=False)
        with patch("dragon_quant.review_account.live_trade.main.run_buy",
                   return_value={"buys": []}) as run, \
                patch("dragon_quant.commands._shared.load_strategy_params",
                      return_value={"max_position_pct": 25}) as params:
            command.handle_buy(args, object())

        params.assert_called_once_with("params.json")
        self.assertEqual(run.call_args.kwargs["strategy_params"],
                         {"max_position_pct": 25})
        self.assertTrue(run.call_args.kwargs["force"])

    def test_buy_value_error_keeps_the_json_and_human_channels(self):
        from dragon_quant.commands import buy_sell as command

        args = SimpleNamespace(
            date="20260907", source="v2", at=None, config=None,
            force=False, json=True)
        buf = io.StringIO()
        with patch("dragon_quant.review_account.live_trade.main.run_buy",
                   side_effect=ValueError("当前午间休市")), redirect_stdout(buf):
            with self.assertRaises(SystemExit) as cm:
                command.handle_buy(args, None)

        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(json.loads(buf.getvalue()), {
            "ok": False, "mode": "buy", "trade_date": "2026-09-07",
            "error": "当前午间休市",
        })

        args.json = False
        buf = io.StringIO()
        with patch("dragon_quant.review_account.live_trade.main.run_buy",
                   side_effect=ValueError("当前午间休市")), redirect_stderr(buf):
            with self.assertRaises(SystemExit) as cm:
                command.handle_buy(args, None)

        self.assertEqual(cm.exception.code, 1)
        self.assertIn("⏸ 暂不执行买入：当前午间休市", buf.getvalue())

    def test_sell_forwards_services_and_reports_errors_on_stderr(self):
        from dragon_quant.commands import buy_sell as command

        args = SimpleNamespace(
            date="20260908", source="v2", at="14:55", config=None, force=False)
        services = object()
        with patch("dragon_quant.review_account.live_trade.main.run_sell",
                   return_value={"sells": []}) as run:
            command.handle_sell(args, services)

        run.assert_called_once_with(
            "2026-09-08", source="v2", verbose=True, as_of="14:55",
            strategy_params=None, force=False, services=services)

        buf = io.StringIO()
        with patch("dragon_quant.review_account.live_trade.main.run_sell",
                   side_effect=ValueError("当前已收盘")), redirect_stderr(buf):
            with self.assertRaises(SystemExit) as cm:
                command.handle_sell(args, None)

        self.assertEqual(cm.exception.code, 1)
        self.assertIn("⏸ 暂不执行卖出：当前已收盘", buf.getvalue())

    def test_sell_reads_strategy_params_through_the_shared_helper(self):
        from dragon_quant.commands import buy_sell as command

        args = SimpleNamespace(
            date="20260908", source="v2", at=None, config="params.json",
            force=False)
        with patch("dragon_quant.review_account.live_trade.main.run_sell",
                   return_value={"sells": []}) as run, \
                patch("dragon_quant.commands._shared.load_strategy_params",
                      return_value={"max_position_pct": 25}) as params:
            command.handle_sell(args, object())

        params.assert_called_once_with("params.json")
        self.assertEqual(run.call_args.kwargs["strategy_params"],
                         {"max_position_pct": 25})


class TestPrepCommand(unittest.TestCase):
    """M3B: prep forwards services, recompute and both error channels."""

    def test_handle_forwards_services_recompute_and_plan_hint(self):
        from dragon_quant.commands import prep as command

        args = SimpleNamespace(date="20260901", recompute=True, force=False, json=False)
        services = object()
        buf = io.StringIO()
        with patch("dragon_quant.review_account.prep.run_prep",
                   return_value={"ok": 1}) as run, \
                patch("dragon_quant.review_account.prep.main.format_advice_lines",
                      return_value=["ADVICE"]), \
                patch("dragon_quant.review_account.prep.main.format_candidate_lines",
                      return_value=["CANDIDATE"]), \
                redirect_stdout(buf):
            command.handle(args, services)

        self.assertEqual(run.call_args.args, ("2026-09-01",))
        self.assertEqual(run.call_args.kwargs["recompute"], True)
        self.assertEqual(run.call_args.kwargs["force"], False)
        self.assertIs(run.call_args.kwargs["services"], services)
        self.assertEqual(run.call_args.kwargs["on_plan"].__class__.__name__,
                         "function")
        self.assertEqual(buf.getvalue(), "ADVICE\n\nCANDIDATE\n")

    def test_prep_error_keeps_json_on_stdout_and_human_on_stderr(self):
        from dragon_quant.commands import prep as command
        from dragon_quant.review_account.prep import PrepError

        args = SimpleNamespace(date="20260901", recompute=False, force=False, json=True)
        buf = io.StringIO()
        with patch("dragon_quant.review_account.prep.run_prep",
                   side_effect=PrepError("boom")), redirect_stdout(buf):
            with self.assertRaises(SystemExit) as cm:
                command.handle(args, object())

        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(json.loads(buf.getvalue()), {"error": "boom"})

        args.json = False
        buf = io.StringIO()
        with patch("dragon_quant.review_account.prep.run_prep",
                   side_effect=PrepError("boom")), redirect_stderr(buf):
            with self.assertRaises(SystemExit) as cm:
                command.handle(args, object())

        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(buf.getvalue(), "❌ 复盘失败：boom\n")

    def test_prep_unexpected_error_is_reported_not_raised(self):
        from dragon_quant.commands import prep as command

        args = SimpleNamespace(date="20260901", recompute=False, force=False, json=True)
        buf = io.StringIO()
        with patch("dragon_quant.review_account.prep.run_prep",
                   side_effect=RuntimeError("kaboom")), redirect_stdout(buf):
            with self.assertRaises(SystemExit) as cm:
                command.handle(args, object())

        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(json.loads(buf.getvalue()),
                         {"error": "RuntimeError: kaboom"})


class TestFactorCommand(unittest.TestCase):
    """M3B: factor forwards services and keeps exit codes 0/1/2."""

    def test_build_forwards_services(self):
        from dragon_quant.commands import factor as command

        args = SimpleNamespace(
            factor_action="build", date="20260911", from_date=None, to_date=None,
            only=None, all_factors=False, json=False)
        services = object()
        payload = {"ok": True, "mode": "date", "reports": [], "dates": [],
                   "build_ids": [], "warnings": []}
        with patch("dragon_quant.infra.factor.service.build",
                   return_value=payload) as build, \
                patch("dragon_quant.commands.factor._print_factor_human"):
            command.handle(args, services)

        build.assert_called_once_with(
            date="20260911", date_from=None, date_to=None, only=None,
            all_factors=False, services=services)

    def test_factor_fin_forwards_services_to_sync_and_calibrate(self):
        from dragon_quant.commands import factor as command

        services = object()
        sync_args = SimpleNamespace(
            factor_action="fin", fin_action="sync", snapshot=True, backfill=False,
            date="20260911", from_period=None, to_period=None, force=False,
            host=None, json=False)
        with patch("dragon_quant.infra.factor.service.fin_sync",
                   return_value={"ok": True, "mode": "snapshot"}) as sync, \
                patch("dragon_quant.commands.factor._print_fin_human"):
            command.handle(sync_args, services)

        self.assertIs(sync.call_args.kwargs["services"], services)

        cal_args = SimpleNamespace(
            factor_action="fin", fin_action="calibrate", sample=20,
            period=None, host=None, json=False)
        with patch("dragon_quant.infra.factor.service.fin_calibrate",
                   return_value={"ok": True, "mode": "calibrate"}) as calibrate, \
                patch("dragon_quant.commands.factor._print_fin_human"):
            command.handle(cal_args, services)

        self.assertIs(calibrate.call_args.kwargs["services"], services)

    def test_status_and_query_never_need_services(self):
        from dragon_quant.commands import factor as command

        status_args = SimpleNamespace(factor_action="status", json=False)
        with patch("dragon_quant.infra.factor.service.status",
                   return_value={"ok": True}) as status, \
                patch("dragon_quant.commands.factor._print_factor_human"):
            command.handle(status_args, None)
        status.assert_called_once_with()

    def test_parameter_errors_keep_exit_2_and_the_error_channel(self):
        from dragon_quant.commands import factor as command

        args = SimpleNamespace(factor_action=None, json=False)
        buf = io.StringIO()
        with redirect_stderr(buf):
            with self.assertRaises(SystemExit) as cm:
                command.handle(args, None)

        self.assertEqual(cm.exception.code, 2)
        self.assertIn("❌ 因子操作失败：未知 factor 子命令", buf.getvalue())

        args = SimpleNamespace(
            factor_action="build", date="20260911", from_date=None, to_date=None,
            only=None, all_factors=False, json=True)
        buf = io.StringIO()
        with patch("dragon_quant.infra.factor.service.build",
                   side_effect=ValueError("bad args")), redirect_stdout(buf):
            with self.assertRaises(SystemExit) as cm:
                command.handle(args, object())

        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(json.loads(buf.getvalue()),
                         {"ok": False, "error": "bad args"})

    def test_runtime_failure_keeps_exit_1(self):
        from dragon_quant.commands import factor as command

        args = SimpleNamespace(
            factor_action="status", json=True)
        buf = io.StringIO()
        with patch("dragon_quant.infra.factor.service.status",
                   side_effect=RuntimeError("db gone")), redirect_stdout(buf):
            with self.assertRaises(SystemExit) as cm:
                command.handle(args, object())

        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(json.loads(buf.getvalue()),
                         {"ok": False, "error": "RuntimeError: db gone"})


if __name__ == "__main__":
    unittest.main()
