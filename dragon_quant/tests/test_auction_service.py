"""M4 auction feature service contract tests.

These tests pin the extracted ``dragon_quant.review_account.auction.main`` seam only:

* payload shape / key order for collect, empty-pool and recompute;
* explicit ``services`` win, missing providers fall back to the runtime factory;
* prep-pool calendar behavior (failure -> visible legacy fallback);
* recompute constructs no provider and never touches the network;
* collection vs snapshot-write failures keep their distinct exit codes/warnings.
"""

from __future__ import annotations

import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from dragon_quant.review_account.auction.main import (
    AuctionOutcome, run_auction, run_auction_recompute,
)
from dragon_quant.infra.models import Quote

DAY = "2026-10-20"
PREV = "2026-10-19"
ITEMS = [{"time": "09:25:00", "price": 10.0, "matched": 100, "unmatched": 0}]

COLLECT_KEYS = [
    "ok", "mode", "trade_date", "source", "pool_source", "fallback_reason",
    "prep_snapshot_date", "prep_unavailable_reason", "recommended_count",
    "days_ignored", "lookback_dates", "candidate_codes", "saved_count", "saved",
    "empty", "failed", "quote", "quote_missing", "asset_read_error",
    "scored_count", "snapshot_saved_count", "snapshot_ok", "snapshot_skipped",
    "snapshot_error", "snapshots", "strategy_context",
]
EMPTY_KEYS = [
    "ok", "mode", "trade_date", "source", "pool_source", "fallback_reason",
    "prep_snapshot_date", "prep_unavailable_reason", "pool_empty",
    "candidate_codes", "recommended_count", "ignore_reason_count",
    "ignore_reason_samples", "prep_degraded", "days_ignored", "message",
    "strategy_context",
]
RECOMPUTE_KEYS = [
    "ok", "mode", "trade_date", "source", "asset_count", "usable_count",
    "skipped_empty", "quote_missing", "scored_count", "snapshot_saved_count",
    "snapshot_ok", "snapshot_skipped", "snapshot_error", "failed", "snapshots",
    "strategy_context",
]
SNAPSHOT_KEYS = [
    "ok", "mode", "trade_date", "source", "from_cache", "pool_source",
    "fallback_reason", "prep_snapshot_date", "prep_unavailable_reason",
    "recommended_count", "days_ignored", "lookback_dates", "candidate_codes",
    "saved_count", "saved", "empty", "failed", "quote", "quote_missing",
    "asset_read_error", "scored_count", "snapshot_saved_count", "snapshot_ok",
    "snapshot_skipped", "snapshot_error", "snapshots", "strategy_context",
]


def _quote(code: str) -> Quote:
    return Quote(
        code=code, name="x", price=10.0, prev_close=10.0, open_px=10.0,
        high=10.0, low=10.0, pct=0.0, chg=0.0, turnover_rate=0.0,
        amplitude=0.0, volume=0.0, amount=0.0, market_cap=100e8,
        float_market_cap=50e8, volume_ratio=0.0, pe=0.0,
        limit_up=11.0, limit_down=9.0, avg_price=10.0,
    )


def _services(tdx=None):
    return SimpleNamespace(providers=SimpleNamespace(tdx=tdx or object()))


def _snapshot(codes=(("600001", "甲"), ("600002", "乙"))):
    return {"candidates": [
        {"code": code, "name": name, "composite_score": 80,
         "is_true_dragon": True, "verdict": "strong"}
        for code, name in codes
    ]}


def _asset(code: str, *, name=None, items=None, quote="default"):
    from dragon_quant.review_account.auction.serialize import quote_to_payload

    return {
        "trade_date": DAY,
        "code": code,
        "name": name or code,
        "items": list(ITEMS) if items is None else items,
        "quote": quote_to_payload(_quote(code)) if quote == "default" else quote,
    }


def _cache_row(code: str, **overrides) -> dict:
    """``list_auction_snapshots`` 摊平后的行形状（analysis_json + code/trade_date）。"""
    row = {
        "trade_date": DAY, "code": code, "version": "2",
        "is_weak": False, "strength": 60.0, "direction": "neutral",
        "level": "中", "dims": {}, "reject_reason": None,
        "unverified": False, "rank": 1, "rank_reason": None,
        "validation_status": "baseline_unvalidated",
        "config_version": "auction_v2_baseline",
        "data_quality": {"status": "ok", "completeness": 1.0,
                         "reason_codes": [], "reasons": []},
        "risk_events": [], "raw_factors": {}, "evidence": {},
        "reference_prices": {}, "windows": {}, "actions": [],
    }
    row.update(overrides)
    return row


class _CollectHarness:
    """Patch every I/O seam of ``run_auction`` and record the calls."""

    def __init__(self, test, **opts):
        self.test = test
        self.opts = opts
        self.calls = {
            "calendar": [], "auction": [], "quotes": [],
            "saved": [], "saved_rows": [], "replaced": None, "factory": 0,
            "snapshot_reads": 0, "cache_reads": 0,
        }

    def __enter__(self):
        self.stack = ExitStack()
        opt, calls = self.opts, self.calls
        snapshot = opt.get("snapshot", _snapshot())
        dragons = opt.get("dragons", [])
        legacy_dates = opt.get("legacy_dates")
        if legacy_dates is None:
            legacy_dates = [PREV] if dragons else []
        calendar = opt.get("calendar", [PREV, DAY])

        if opt.get("patch_factory"):
            bundle_tdx = opt.get("factory_tdx", object())

            def _factory():
                calls["factory"] += 1
                return SimpleNamespace(tdx=bundle_tdx)
            self._patch("dragon_quant.infra.provider.bundle.build_provider_bundle",
                        side_effect=_factory)

        def _calendar(tdx, days=120):
            calls["calendar"].append(tdx)
            if opt.get("calendar_error") is not None:
                raise opt["calendar_error"]
            return list(calendar)
        self._patch("dragon_quant.infra.market_calendar.tdx_trade_dates",
                    side_effect=_calendar)

        def _prep_snapshot(date, source="v2"):
            calls["snapshot_reads"] += 1
            if opt.get("snapshot_error") is not None:
                raise opt["snapshot_error"]
            return snapshot
        self._patch("dragon_quant.infra.assets.db.get_prep_snapshot",
                    side_effect=_prep_snapshot)

        self._patch("dragon_quant.infra.assets.dragons_mirror.list_dragon_trade_dates",
                    return_value=legacy_dates)
        self._patch("dragon_quant.infra.assets.dragons_mirror.get_dragons",
                    return_value=dragons)
        self._patch("dragon_quant.infra.assets.dragons_mirror.stale_dates",
                    return_value={})

        def _auction(code, services=None):
            calls["auction"].append(code)
            if opt.get("auction_error") is not None:
                raise opt["auction_error"]
            return list(ITEMS) if opt.get("auction_items", ITEMS) else []
        self._patch("dragon_quant.infra.provider.data.get_auction", side_effect=_auction)

        quotes = opt.get("quotes")
        if quotes is None:
            quotes = [_quote(item["code"])
                      for item in snapshot.get("candidates", [])] \
                if snapshot else []

        def _batch(codes, source="tdx", services=None):
            calls["quotes"].append(source)
            if opt.get("batch_error") is not None:
                raise opt["batch_error"]
            return list(quotes)
        self._patch("dragon_quant.infra.provider.data.batch_get_quotes", side_effect=_batch)

        def _save(trade_date, code, name, items, quote=None):
            calls["saved"].append(code)
            calls["saved_rows"].append({"code": code, "name": name})
            if opt.get("save_error") is not None:
                raise opt["save_error"]
        self._patch("dragon_quant.infra.assets.db.save_auction_asset",
                    side_effect=_save)

        persisted = opt.get("persisted")

        def _list(date):
            if opt.get("list_error") is not None:
                raise opt["list_error"]
            if persisted is not None:
                return list(persisted)
            return [_asset(row["code"], name=row["name"])
                    for row in calls["saved_rows"]]
        self._patch("dragon_quant.infra.assets.db.list_auction_assets",
                    side_effect=_list)
        self._patch("dragon_quant.infra.assets.db.list_auction_history",
                    return_value={})

        def _list_cached(date, source="v2", with_meta=False):
            calls["cache_reads"] += 1
            if opt.get("cache_error") is not None:
                raise opt["cache_error"]
            return list(opt.get("cache_rows") or [])
        self._patch("dragon_quant.infra.assets.db.list_auction_snapshots",
                    side_effect=_list_cached)

        def _replace(trade_date, payloads, source="v2"):
            calls["replaced"] = dict(payloads)
            if opt.get("replace_error") is not None:
                raise opt["replace_error"]
            return len(payloads)
        self._patch("dragon_quant.infra.assets.db.replace_auction_snapshots",
                    side_effect=_replace)
        return self

    def _patch(self, target, **kwargs):
        self.stack.enter_context(patch(target, **kwargs))

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)


class TestCollectContract(unittest.TestCase):

    def test_prep_pool_payload_shape_and_key_order(self):
        with _CollectHarness(self) as harness:
            outcome = run_auction(services=_services(), today=DAY)

        self.assertIsInstance(outcome, AuctionOutcome)
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(outcome.mode, "collect")
        self.assertEqual(list(outcome.payload), COLLECT_KEYS)
        self.assertEqual(outcome.payload["pool_source"], "prep")
        self.assertIsNone(outcome.payload["fallback_reason"])
        self.assertEqual(outcome.payload["prep_snapshot_date"], PREV)
        self.assertEqual(outcome.payload["recommended_count"], 2)
        self.assertEqual(outcome.payload["candidate_codes"], 2)
        self.assertEqual(outcome.payload["saved_count"], 2)
        self.assertEqual(outcome.payload["scored_count"], 2)
        self.assertEqual(harness.calls["auction"], ["600001", "600002"])
        self.assertEqual(harness.calls["quotes"], ["tdx"])
        self.assertEqual(set(harness.calls["replaced"]), {"600001", "600002"})
        self.assertEqual(outcome.render_names, {"600001": "甲", "600002": "乙"})

    def test_missing_services_uses_the_runtime_provider_factory(self):
        tdx = object()
        with _CollectHarness(self, patch_factory=True, factory_tdx=tdx) as harness:
            outcome = run_auction(pool="prep", services=None, today=DAY)

        self.assertEqual(harness.calls["factory"], 1)
        self.assertEqual(harness.calls["calendar"], [tdx])
        # The prep pool must survive: the factory-provided TDX provider is valid.
        self.assertEqual(outcome.payload["pool_source"], "prep")
        self.assertIsNone(outcome.payload["fallback_reason"])

    def test_explicit_services_skip_the_factory(self):
        with _CollectHarness(self, patch_factory=True) as harness:
            outcome = run_auction(services=_services(), today=DAY)

        self.assertEqual(harness.calls["factory"], 0)
        self.assertEqual(outcome.payload["pool_source"], "prep")

    def test_calendar_failure_falls_back_to_legacy_with_a_warning(self):
        dragons = [{"code": "L", "name": "龙"}]
        with _CollectHarness(
                self, snapshot=_snapshot(), dragons=dragons,
                calendar_error=RuntimeError("tdx down")) as harness:
            outcome = run_auction(services=_services(), today=DAY)

        self.assertEqual(outcome.payload["pool_source"], "legacy")
        self.assertEqual(outcome.payload["fallback_reason"], "calendar")
        self.assertTrue(any("交易日历不可用" in line for line in outcome.warnings))
        self.assertEqual(harness.calls["auction"], ["L"])

    def test_empty_pool_returns_the_16_key_contract_without_quotes(self):
        with _CollectHarness(
                self, snapshot=None, dragons=[], legacy_dates=[]) as harness:
            outcome = run_auction(services=_services(), today=DAY)

        self.assertEqual(outcome.mode, "empty")
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(list(outcome.payload), EMPTY_KEYS)
        self.assertTrue(outcome.payload["pool_empty"])
        self.assertEqual(outcome.payload["candidate_codes"], 0)
        self.assertEqual(outcome.payload["message"],
                         "prep 与 legacy 均无可用候选，本次不采集竞价数据")
        self.assertEqual(harness.calls["auction"], [])
        self.assertEqual(harness.calls["quotes"], [])

    def test_days_is_ignored_only_when_the_prep_pool_succeeds(self):
        with _CollectHarness(self):
            outcome = run_auction(services=_services(), days=3, today=DAY)

        self.assertTrue(outcome.payload["days_ignored"])
        self.assertEqual(outcome.warnings,
                         ["⚠️ --days 仅在 legacy 路径生效，本次已忽略"])

    def test_total_collection_failure_exits_one_and_writes_nothing(self):
        with _CollectHarness(
                self, auction_error=RuntimeError("模拟盘口故障")) as harness:
            outcome = run_auction(services=_services(), today=DAY)

        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(outcome.mode, "collect")
        self.assertFalse(outcome.payload["ok"])
        self.assertEqual(outcome.payload["saved_count"], 0)
        self.assertEqual(outcome.payload["snapshot_skipped"], "no_payloads")
        self.assertFalse(outcome.payload["snapshot_ok"])
        self.assertEqual(
            [(item["code"], item["stage"]) for item in outcome.payload["failed"]],
            [("600001", "auction"), ("600002", "auction")])
        self.assertEqual(harness.calls["saved"], [])
        self.assertIsNone(harness.calls["replaced"])

    def test_snapshot_write_failure_keeps_exit_zero_and_warns(self):
        with _CollectHarness(
                self, replace_error=RuntimeError("e2e snapshot write blocked")) as harness:
            outcome = run_auction(services=_services(), today=DAY)

        self.assertEqual(outcome.exit_code, 0)
        self.assertFalse(outcome.payload["ok"])
        self.assertEqual(outcome.payload["saved_count"], 2)
        self.assertEqual(outcome.payload["snapshot_saved_count"], 0)
        self.assertFalse(outcome.payload["snapshot_ok"])
        self.assertIn("e2e snapshot write blocked", outcome.payload["snapshot_error"])
        self.assertTrue(any("快照未产出" in line for line in outcome.warnings))
        self.assertEqual(harness.calls["saved"], ["600001", "600002"])


class TestRecomputeContract(unittest.TestCase):

    def test_recompute_is_offline_and_never_constructs_providers(self):
        assets = [_asset("600001", name="甲"), _asset("600002", name="乙")]
        with ExitStack() as stack:
            factory = stack.enter_context(
                patch("dragon_quant.infra.provider.bundle.build_provider_bundle"))
            stack.enter_context(patch(
                "dragon_quant.infra.assets.db.list_auction_assets",
                return_value=assets))
            stack.enter_context(patch(
                "dragon_quant.infra.assets.db.list_auction_history",
                return_value={}))
            replace = stack.enter_context(patch(
                "dragon_quant.infra.assets.db.replace_auction_snapshots",
                return_value=2))
            get_auction = stack.enter_context(patch(
                "dragon_quant.infra.provider.data.get_auction"))
            batch = stack.enter_context(patch(
                "dragon_quant.infra.provider.data.batch_get_quotes"))

            outcome = run_auction_recompute("20261020", services=None, today=DAY)

        factory.assert_not_called()
        get_auction.assert_not_called()
        batch.assert_not_called()
        replace.assert_called_once()
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(outcome.mode, "recompute")
        self.assertEqual(list(outcome.payload), RECOMPUTE_KEYS)
        self.assertTrue(outcome.payload["ok"])
        self.assertEqual(outcome.payload["asset_count"], 2)
        self.assertEqual(outcome.payload["scored_count"], 2)
        self.assertEqual(outcome.render_names, {"600001": "甲", "600002": "乙"})

    def test_recompute_error_paths_keep_the_error_channel(self):
        cases = [
            ({"raw": "2026-13-40", "today": DAY, "assets": []}, "无效日期"),
            ({"raw": "20261021", "today": DAY, "assets": []}, "未来日期不可重算"),
            ({"raw": "20261020", "today": DAY, "assets": []}, "无竞价原始资产"),
        ]
        for options, expected in cases:
            with self.subTest(expected=expected):
                with ExitStack() as stack:
                    factory = stack.enter_context(
                        patch("dragon_quant.infra.provider.bundle.build_provider_bundle"))
                    stack.enter_context(patch(
                        "dragon_quant.infra.assets.db.list_auction_assets",
                        return_value=options["assets"]))
                    outcome = run_auction_recompute(
                        options["raw"], services=None, today=options["today"])

                factory.assert_not_called()
                self.assertEqual(outcome.exit_code, 1)
                self.assertEqual(outcome.mode, "error")
                self.assertFalse(outcome.payload["ok"])
                self.assertIn(expected, outcome.payload["error"])

    def test_recompute_snapshot_write_failure_is_an_error(self):
        with ExitStack() as stack:
            stack.enter_context(patch(
                "dragon_quant.infra.assets.db.list_auction_assets",
                return_value=[_asset("600001")]))
            stack.enter_context(patch(
                "dragon_quant.infra.assets.db.list_auction_history",
                return_value={}))
            stack.enter_context(patch(
                "dragon_quant.infra.assets.db.replace_auction_snapshots",
                side_effect=RuntimeError("db locked")))
            outcome = run_auction_recompute("20261020", today=DAY)

        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(outcome.mode, "error")
        self.assertIn("快照写入失败", outcome.payload["error"])
        self.assertIn("db locked", outcome.payload["error"])


class TestSnapshotCacheContract(unittest.TestCase):
    """快照命中：零 provider/零采集/零评分/零业务写入。"""

    def test_cache_hit_returns_snapshots_without_any_collection(self):
        rows = [_cache_row("600001"),
                _cache_row("600002", rank=2, strength=55.0)]
        with _CollectHarness(self, patch_factory=True,
                             cache_rows=rows) as harness:
            outcome = run_auction(pool="legacy", days=3,
                                  services=_services(), today=DAY)

        self.assertEqual(outcome.mode, "snapshot")
        self.assertEqual(outcome.exit_code, 0)
        payload = outcome.payload
        self.assertEqual(list(payload), SNAPSHOT_KEYS)
        self.assertTrue(payload["from_cache"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["pool_source"], "snapshot")
        self.assertIsNone(payload["fallback_reason"])
        self.assertIsNone(payload["prep_snapshot_date"])
        self.assertEqual(payload["candidate_codes"], 2)
        self.assertEqual(payload["saved_count"], 0)
        self.assertEqual(payload["saved"], [])
        self.assertEqual(payload["scored_count"], 2)
        self.assertEqual(payload["snapshot_saved_count"], 0)
        self.assertTrue(payload["snapshot_ok"])
        self.assertEqual(payload["snapshot_skipped"], "cache_hit")
        self.assertIsNone(payload["snapshot_error"])
        self.assertEqual(payload["failed"], [])
        self.assertEqual([item["code"] for item in payload["snapshots"]],
                         ["600001", "600002"])
        self.assertFalse(payload["days_ignored"])
        self.assertEqual(outcome.render_names, {})

        self.assertEqual(harness.calls["cache_reads"], 1)
        self.assertEqual(harness.calls["factory"], 0)
        self.assertEqual(harness.calls["calendar"], [])
        self.assertEqual(harness.calls["auction"], [])
        self.assertEqual(harness.calls["quotes"], [])
        self.assertEqual(harness.calls["saved"], [])
        self.assertIsNone(harness.calls["replaced"])

    def test_partial_corruption_returns_valid_rows_and_reports_bad_ones(self):
        rows = [_cache_row("600001"),
                {"trade_date": DAY, "code": "600002",
                 "error": "JSONDecodeError: bad"}]
        with _CollectHarness(self, patch_factory=True,
                             cache_rows=rows) as harness:
            outcome = run_auction(services=_services(), today=DAY)

        payload = outcome.payload
        self.assertEqual(outcome.mode, "snapshot")
        self.assertEqual(outcome.exit_code, 0)
        self.assertFalse(payload["ok"])
        self.assertEqual([item["code"] for item in payload["snapshots"]],
                         ["600001"])
        self.assertEqual([(item["code"], item["stage"])
                          for item in payload["failed"]],
                         [("600002", "snapshot")])
        self.assertEqual(harness.calls["auction"], [])
        self.assertEqual(harness.calls["saved"], [])
        positions = {item["code"]: item
                     for item in payload["strategy_context"]["positions"]}
        self.assertEqual(positions["600002"]["status"], "failed")
        self.assertEqual(positions["600002"]["unknown_reason"],
                         "snapshot_corrupt")

    def test_all_corrupt_rows_fall_back_to_full_collection(self):
        rows = [{"trade_date": DAY, "code": "600001",
                 "error": "JSONDecodeError: bad"}]
        with _CollectHarness(self, cache_rows=rows) as harness:
            outcome = run_auction(services=_services(), today=DAY)

        self.assertEqual(outcome.mode, "collect")
        self.assertEqual(harness.calls["cache_reads"], 1)
        self.assertEqual(harness.calls["auction"], ["600001", "600002"])

    def test_empty_shell_rows_fall_back_without_a_warning(self):
        rows = [{"trade_date": DAY, "code": "600001"}]
        with _CollectHarness(self, cache_rows=rows) as harness:
            outcome = run_auction(services=_services(), today=DAY)

        self.assertEqual(outcome.mode, "collect")
        self.assertEqual(outcome.warnings, [])
        self.assertEqual(harness.calls["auction"], ["600001", "600002"])

    def test_cache_read_error_falls_back_with_a_warning(self):
        with _CollectHarness(self, cache_error=RuntimeError("db locked")) as harness:
            outcome = run_auction(services=_services(), today=DAY)

        self.assertEqual(outcome.mode, "collect")
        self.assertEqual(harness.calls["auction"], ["600001", "600002"])
        self.assertTrue(any("竞价快照读取失败" in line
                            for line in outcome.warnings))

    def test_force_skips_the_cache_and_collects(self):
        rows = [_cache_row("600001")]
        with _CollectHarness(self, cache_rows=rows) as harness:
            outcome = run_auction(services=_services(), today=DAY, force=True)

        self.assertEqual(outcome.mode, "collect")
        self.assertEqual(harness.calls["cache_reads"], 0)
        self.assertEqual(harness.calls["auction"], ["600001", "600002"])

    def test_projection_failure_isolates_only_the_bad_row(self):
        from dragon_quant.review_account.auction import scoring

        rows = [_cache_row("600001"), _cache_row("600002")]

        def _summary(payload):
            if payload["code"] == "600002":
                raise RuntimeError("boom")
            return scoring.snapshot_summary(payload)

        with _CollectHarness(self, patch_factory=True, cache_rows=rows):
            with patch("dragon_quant.review_account.auction.collect.snapshot_summary",
                       side_effect=_summary):
                outcome = run_auction(services=_services(), today=DAY)

        self.assertEqual(outcome.mode, "snapshot")
        self.assertEqual([item["code"] for item in outcome.payload["snapshots"]],
                         ["600001"])
        self.assertEqual([(item["code"], item["stage"])
                          for item in outcome.payload["failed"]],
                         [("600002", "snapshot")])
        self.assertIn("snapshot_projection_failed",
                      outcome.payload["failed"][0]["error"])

    def test_all_projection_failures_fall_back_to_collection(self):
        rows = [_cache_row("600001")]
        with _CollectHarness(self, cache_rows=rows) as harness:
            with patch("dragon_quant.review_account.auction.collect.snapshot_summary",
                       side_effect=RuntimeError("boom")):
                outcome = run_auction(services=_services(), today=DAY)

        self.assertEqual(outcome.mode, "collect")
        self.assertEqual(harness.calls["auction"], ["600001", "600002"])


class TestPublicFacade(unittest.TestCase):

    def test_format_report_forwards_to_the_report_implementation(self):
        from dragon_quant.review_account.auction.main import format_report

        with patch("dragon_quant.review_account.auction.report.format_auction_report",
                   return_value=["line"]) as implementation:
            self.assertEqual(format_report([], trade_date=DAY), ["line"])

        implementation.assert_called_once_with([], trade_date=DAY)

    def test_package_exposes_the_public_use_case_facade(self):
        import dragon_quant.review_account.auction as auction

        self.assertTrue(callable(auction.run_auction))
        self.assertTrue(callable(auction.run_auction_recompute))
        self.assertEqual(auction.AuctionOutcome.__module__,
                         "dragon_quant.review_account.auction.types")


if __name__ == "__main__":
    unittest.main()
