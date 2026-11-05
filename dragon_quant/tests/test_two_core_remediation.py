"""两核心修复（M1–M6）的定向回归：成熟度、BS 窗口/故障、信号语义、
跨段归因、研究入口与请求隔离。"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dragon_quant.bs_eval import aggregate
from dragon_quant.bs_eval.constants import (
    POST_EXIT_WINDOW_DAYS,
    STATUS_CANNOT_EVALUATE,
    STATUS_NO_SIGNAL,
)
from dragon_quant.infra.factor import signal_adapter
from dragon_quant.infra.storage import schema as schema_mod
from dragon_quant.review import strict_metrics
from dragon_quant.review_account.evaluation import entry_reason_stats, metrics_from_rows
from dragon_quant.review_account.models import Position, StrategyConfig
from dragon_quant.review_account.service import restore_incremental_seed
from dragon_quant.scan.storage.dragons import summarize_review_rows
from web_ui.backend.presenters import scan_quality

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "web_ui" / "frontend" / "src"


def _bar(day: str, open_px=10.0, high=None, low=None, close=None,
         volume=1000.0) -> dict:
    close = open_px if close is None else close
    high = max(open_px, close) + 0.2 if high is None else high
    low = min(open_px, close) - 0.2 if low is None else low
    return {"date": day, "open": open_px, "high": high, "low": low,
            "close": close, "volume": volume}


class TestReviewMaturity(unittest.TestCase):
    def test_maturity_cutoff_requires_full_window(self):
        days = [f"2026-09-{d:02d}" for d in range(1, 12)]  # 11 days
        calendar = set(days)
        # 需要 6 个交易日后才可能完整：上界 = 倒数第 7 个交易日。
        self.assertEqual(strict_metrics.maturity_cutoff(calendar), days[4])
        self.assertIsNone(strict_metrics.maturity_cutoff(set(days[:6])))

    def test_partial_never_enters_formal_summary(self):
        rows = [{
            "review_status": "partial",
            "strict_reviewed": True,
            "strict_review_status": "partial",
            "strict_review_reason": "partial_window",
            "strict_max_return_5d": 9.0,
            "strict_max_drawdown_5d": -1.0,
            "max_return_5d": 9.0,
            "max_drawdown_5d": -1.0,
        }, {
            # 旧数据：status=completed 但 reason=partial_window，同样排除。
            "review_status": "completed",
            "strict_reviewed": True,
            "strict_review_status": "completed",
            "strict_review_reason": "partial_window",
            "strict_max_return_5d": 8.0,
            "strict_max_drawdown_5d": -1.0,
            "max_return_5d": 8.0,
            "max_drawdown_5d": -1.0,
        }]
        summary = summarize_review_rows(rows)
        self.assertEqual(summary["partial"], 2)
        self.assertEqual(summary["win_rate_strict_n"], 0)
        self.assertIsNone(summary["win_rate_strict"])
        self.assertEqual(summary["win_rate_loose_n"], 0)

    def test_tradable_open_excludes_one_word_board_without_deferral(self):
        calendar = {"2026-09-01", "2026-09-02", "2026-09-03"}
        klines = [
            _bar("2026-09-01", 10.0, 11.0, 10.0, 10.5),
            _bar("2026-09-02", 11.0, 11.0, 11.0, 11.0),   # 一字板
            _bar("2026-09-03", 11.5, 12.0, 11.0, 11.8),
        ]
        result = strict_metrics.evaluate_tradable_open(
            klines, calendar, "2026-09-01")
        self.assertEqual(result["status"], "cannot_evaluate")
        self.assertEqual(result["reason"], "one_word_board_unfillable")
        self.assertEqual(result["entry_date"], "2026-09-02")
        self.assertEqual(result["horizons"], {})

    def test_tradable_open_net_return_and_partial(self):
        calendar = {"2026-09-01", "2026-09-02", "2026-09-03"}
        klines = [
            _bar("2026-09-01", 10.0, 11.0, 10.0, 10.5),
            _bar("2026-09-02", 10.5, 11.2, 10.2, 11.0),
            _bar("2026-09-03", 11.0, 11.5, 10.8, 11.5),
        ]
        result = strict_metrics.evaluate_tradable_open(
            klines, calendar, "2026-09-01", hold_days=(1, 3), cost_bps=13.0)
        self.assertEqual(result["status"], "partial")
        one = result["horizons"]["1"]
        self.assertEqual(one["status"], "completed")
        # 买入后第 1 个交易日收盘 = 2026-09-03 的 11.5。
        self.assertAlmostEqual(one["net_return_pct"],
                               round((11.5 / 10.5 - 1) * 100 - 0.13, 2))
        self.assertEqual(result["horizons"]["3"]["status"], "partial")


class TestSignalSemantics(unittest.TestCase):
    def test_prep_groups_ignore_day_kind(self):
        self.assertEqual(signal_adapter.prep_group(
            {"eligibility": "eligible", "position_pct": 20}), "recommended")
        self.assertEqual(signal_adapter.prep_group(
            {"eligibility": "eligible", "position_pct": 0}),
            "not_recommended")
        self.assertIsNone(signal_adapter.prep_expected_direction(
            "not_recommended"))
        self.assertEqual(signal_adapter.prep_group({"position_pct": None}),
                         "data_insufficient")

    def test_auction_neutral_not_bullish_and_fail_closed(self):
        neutral = signal_adapter.auction_signal({
            "level": "中", "direction": "neutral", "strength": 60,
            "unverified": False, "data_quality": {"status": "ok"},
            "is_weak": False})
        self.assertEqual(neutral["direction"], "neutral")
        self.assertIsNone(signal_adapter.auction_expected_direction("neutral"))
        missing = signal_adapter.auction_signal({"level": "强", "strength": 80})
        self.assertEqual(missing["direction"], "unknown")
        self.assertFalse(missing["data_available"])
        self.assertEqual(missing["reason"], "unverified_missing")


class TestBsWindowsAndFaults(unittest.TestCase):
    def test_sell_axes_can_both_hold(self):
        row = {"sell_max_return_5d": 10.0, "sell_max_drawdown_5d": -4.0,
               "sell_window_partial": 0}
        axes = aggregate.sell_axes(row)
        self.assertTrue(axes["missed_upside"])
        self.assertTrue(axes["avoided_downside"])
        # 旧互斥 verdict 仍兼容，但只作为辅助标签。
        self.assertEqual(aggregate.sell_verdict(row), "flew")

    def test_sell_window_partial_has_no_axes(self):
        axes = aggregate.sell_axes({"sell_window_partial": 1})
        self.assertIsNone(axes["missed_upside"])
        self.assertFalse(axes["comparable"])

    def test_data_coverage_error_is_retryable_not_no_signal(self):
        from dragon_quant.bs_eval.evaluator import BsEvaluator
        from dragon_quant.review_account.market import DataCoverageError
        from dragon_quant.tests.test_bs_eval_evaluator import (
            CALENDAR, FakeData, row_for_day,
        )
        from dragon_quant.bs_eval.main import _engine_config

        data = FakeData()
        evaluator = BsEvaluator(_engine_config(), data, CALENDAR, CALENDAR[-1])
        def flaky(*args, **kwargs):
            raise DataCoverageError("synthetic coverage fault")

        with patch("dragon_quant.bs_eval.evaluator.historical_events", flaky):
            result = evaluator.evaluate(row_for_day())
        self.assertEqual(result["eval_status"], STATUS_CANNOT_EVALUATE)
        self.assertEqual(result["eval_note"], "data_coverage_error")
        self.assertEqual(result["is_terminal"], 0)
        self.assertGreater(result["fault_days"], 0)
        self.assertNotEqual(result["eval_status"], STATUS_NO_SIGNAL)
        # 真实停牌（unavailable）仍不走 fault 分支，由既有测试覆盖。

    def test_post_exit_window_is_after_sell_day(self):
        from dragon_quant.review_account.indicators import post_exit_stats
        from dragon_quant.infra.models import KBar
        from dragon_quant.review_account.market import at

        bars = [
            KBar(at("2026-09-01", "15:00"), 1e6, 10, 10, 9, 10, 0, 0, 1, 1e8),
            KBar(at("2026-09-02", "15:00"), 1e6, 10, 11, 9.5, 10.5, 0, 0, 1, 1e8),
            KBar(at("2026-09-03", "15:00"), 1e6, 10.5, 10.6, 9.9, 10.1, 0, 0, 1, 1e8),
        ]
        stats = post_exit_stats(bars, "2026-09-01", 10.0,
                                window=POST_EXIT_WINDOW_DAYS)
        # 窗口不含卖出当日（09-01 的低点 9 不计入），全窗最低 9.5 → -5%。
        self.assertAlmostEqual(stats["post_exit_max_return"], 10.0)
        self.assertAlmostEqual(stats["post_exit_max_drawdown"], -5.0)
        self.assertEqual(stats["post_exit_window_days"], 2)


class TestCrossSegmentAttribution(unittest.TestCase):
    def _seed(self):
        position = Position(
            code="600001", name="A", qty=500, entry_date="2026-08-01",
            entry_price=10.0, cost=5005.0, entry_reason_code="buy_open_turn_strong",
            entry_reason_text="", mark=12.0, realized_pnl=200.0,
            initial_qty=1000, initial_cost=10010.0)
        snapshot = {
            "trade_date": "2026-09-01", "cash": 1000.0,
            "total_equity": 7000.0, "market_value": 6000.0,
            "positions": [position.__dict__],
        }
        run = {"id": 2, "date_to": "2026-09-01",
               "run_meta": {"realized_pnl_total": 1234.0}}
        return position, snapshot, run

    def test_restore_freezes_period_start_and_realized(self):
        _, snapshot, run = self._seed()
        seed = restore_incremental_seed(run, [snapshot], [])
        restored = seed["state"].positions[0]
        self.assertTrue(restored.inherited)
        self.assertEqual(restored.period_start_qty, 500)
        self.assertEqual(restored.period_start_mark, 12.0)
        self.assertEqual(restored.period_start_cost, 5005.0)
        self.assertEqual(seed["start_realized_pnl"], 1234.0)
        self.assertEqual(seed["attribution_quality"], "precise")

    def test_contribution_uses_period_pnl_and_start_equity(self):
        rows = [{
            "status": "closed", "realized_return": 20.0,
            "entry_price": 10.0, "qty": 500,
            "entry_reason_code": "buy_open_turn_strong",
            "period_pnl": 1000.0,
        }]
        ledger = {
            "quality": "precise", "unknown_rows": 0,
            "reconciliation": {"diff": 0.0, "within_tolerance": True},
            "rows": [{"entry_reason_code": "buy_open_turn_strong",
                      "period_pnl": 1000.0, "realized_part": 1000.0,
                      "unrealized_part": 0.0, "start_value": 0.0,
                      "buy_cost": 5000.0, "status": "closed"}],
            "totals": {"start_equity": 200_000.0, "end_equity": 201_000.0,
                       "ledger_period_pnl": 1000.0,
                       "realized_part": 1000.0, "unrealized_part": 0.0},
        }
        stats = entry_reason_stats(rows, 100_000.0, start_equity=200_000.0,
                                   ledger=ledger)
        total = stats["rows"][-1]
        self.assertAlmostEqual(total["contribution"], 0.5)  # 1000 / 200000
        self.assertEqual(stats["contribution_basis"], "precise")

    def test_legacy_rows_marked_approximation_and_metrics_none_safe(self):
        rows = [{
            "status": "closed", "realized_return": 10.0,
            "entry_price": 10.0, "qty": 100,
            "entry_reason_code": "buy_open_turn_strong",
        }]
        ledger = {"quality": "unknown", "unknown_rows": 1,
                  "reconciliation": {"diff": None, "within_tolerance": False},
                  "rows": [{"entry_reason_code": "buy_open_turn_strong",
                            "period_pnl": None, "realized_part": None,
                            "unrealized_part": None, "status": "open"}],
                  "totals": {"start_equity": 10_000.0, "end_equity": None,
                             "ledger_period_pnl": None}}
        stats = entry_reason_stats(rows, 10_000.0, start_equity=10_000.0,
                                   ledger=ledger)
        self.assertEqual(stats["contribution_basis"], "unknown")
        summary = metrics_from_rows(
            {"id": 1, "total_return": 0.0, "max_drawdown": 0.0,
             "trade_count": 1, "initial_cash": 10_000.0,
             "start_equity": 10_000.0,
             "run_meta": {"attribution_quality": "legacy_approximation"}},
            [{"total_equity": 10_000.0, "positions": []}],
            [{"side": "BUY", "realized_pnl": 0.0, "fee": 0.0,
              "amount": 0.0, "qty": 0, "code": "600001"}],
            rows)
        self.assertIsNone(summary["net_profit_ratio"])
        self.assertEqual(summary["contribution_basis"], "legacy_approximation")
        self.assertEqual(summary["period_pnl_amount"], 0.0)


class TestScanQualityPresenter(unittest.TestCase):
    def test_canonical_dedupe_and_groups(self):
        rows = [
            {"code": "600001", "scan_id": "s1", "scan_top_n": 5, "rank": 3,
             "composite_score": 80, "is_true_dragon": True,
             "scan_created_at": "2026-09-01 15:30"},
            {"code": "600001", "scan_id": "s2", "scan_top_n": 10, "rank": 1,
             "composite_score": 90, "is_true_dragon": True,
             "scan_created_at": "2026-09-01 16:00"},
            {"code": "600002", "scan_id": "s1", "scan_top_n": 5, "rank": 2,
             "composite_score": 70, "is_true_dragon": False,
             "scan_created_at": "2026-09-01 15:30"},
        ]
        canonical, raw = scan_quality.canonical_candidates(rows)
        self.assertEqual(raw, 3)
        best = {row["code"]: row for row in canonical}
        self.assertEqual(best["600001"]["scan_id"], "s2")
        self.assertEqual(len(canonical), 2)

    def test_quality_groups_and_one_word_exclusion(self):
        calendar = ["2026-09-01", "2026-09-02", "2026-09-03",
                    "2026-09-04", "2026-09-05", "2026-09-08"]
        bars = {
            "600001": [_bar("2026-09-02", 10.0, 10.5, 9.8, 10.4),
                       _bar("2026-09-03", 10.4, 10.8, 10.2, 10.6),
                       _bar("2026-09-04", 10.6, 11.0, 10.4, 10.9),
                       _bar("2026-09-05", 10.9, 11.2, 10.7, 11.1),
                       _bar("2026-09-08", 11.1, 11.4, 10.9, 11.3)],
            "600002": [_bar("2026-09-02", 10.0, 10.0, 10.0, 10.0)],
        }
        contributions = {"2026-09-01": [
            {"code": "600001", "rank": 1, "composite_score": 85,
             "is_true_dragon": True, "scan_top_n": 5},
            {"code": "600002", "rank": 2, "composite_score": 65,
             "is_true_dragon": False, "scan_top_n": 5},
        ]}
        payload = scan_quality.build_scan_quality(
            date_from="2026-09-01", date_to="2026-09-01",
            contributions_by_date=contributions, calendar=calendar, bars=bars)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["coverage"]["raw_contributions"], 2)
        self.assertEqual(
            payload["coverage"]["excluded"]["one_word_board_unfillable"], 1)
        self.assertEqual(payload["groups"]["by_gate"]["passed"]["valid"], 1)
        self.assertIn("rejected", payload["groups"]["by_gate"])


class TestSchemaIdempotency(unittest.TestCase):
    def test_ensure_schema_twice_and_experiment_table_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "dragon.db")
            try:
                schema_mod._ensure_schema(conn)
                schema_mod._ensure_schema(conn)
                cols = {r[1] for r in conn.execute(
                    "PRAGMA table_info(review_account_runs)")}
                self.assertTrue({"base_run_id", "start_equity",
                                 "start_positions_json", "start_realized_pnl",
                                 "run_meta_json"} <= cols)
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertIn("review_account_experiments", tables)
            finally:
                conn.close()


class TestFrontendRequestIsolation(unittest.TestCase):
    def test_account_page_binds_response_to_run_and_splits_aux_failures(self):
        text = (FRONTEND / "pages" / "account" / "AccountPage.tsx").read_text(
            encoding="utf-8")
        self.assertIn("requestSeq", text)
        self.assertIn("detailRunId", text)
        self.assertIn("Promise.allSettled", text)
        self.assertIn("detailMatchesRun", text)

    def test_nav_has_two_core_and_legacy_links(self):
        text = (FRONTEND / "AppShell.tsx").read_text(encoding="utf-8")
        for token in ('label: "龙头识别"', 'label: "账户验证"', 'to: "/prep"',
                      'to: "/auction"', 'to: "/bs"', 'to: "/factors"',
                      'to: "/research"'):
            self.assertIn(token, text)

    def test_research_and_experiment_entry_files_exist(self):
        self.assertTrue((FRONTEND / "pages" / "research" / "ResearchPage.tsx").exists())
        self.assertTrue((FRONTEND / "pages" / "account" / "ExperimentPanel.tsx").exists())


if __name__ == "__main__":
    unittest.main()


class TestResearchRoutes(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient
        from web_ui.backend import deps
        from web_ui.backend.app import create_app
        from web_ui.backend.settings import DashboardSettings

        bars = {"600001": [
            _bar("2026-09-02", 10.0, 10.5, 9.8, 10.4),
            _bar("2026-09-03", 10.4, 10.8, 10.2, 10.6),
            _bar("2026-09-04", 10.6, 11.0, 10.4, 10.9),
            _bar("2026-09-05", 10.9, 11.2, 10.7, 11.1),
            _bar("2026-09-08", 11.1, 11.4, 10.9, 11.3),
        ]}

        class Scans:
            def list_scan_dates(self, date_from=None, date_to=None):
                return ["2026-09-01"]

            def list_scan_stock_contributions_by_date(self, day):
                return [{"code": "600001", "rank": 1, "composite_score": 85,
                         "is_true_dragon": True, "scan_top_n": 5}]

        class Account:
            def get_review_account_run(self, run_id):
                return None

            def query_review_account_experiments(self, run_id, limit=5):
                return [{"id": 9, "base_run_id": run_id,
                         "experiment_version": "account_experiment_v1",
                         "status": "evaluated", "request": {},
                         "report": {"variants": {}}}]

        app = create_app(DashboardSettings(dist_dir=Path(tempfile.mkdtemp())))
        app.dependency_overrides[deps.scan_repository] = lambda: Scans()
        app.dependency_overrides[deps.account_repository] = lambda: Account()
        app.dependency_overrides[deps.local_bars_loader] = lambda: (
            lambda codes, date_from, date_to: {
                "rows": bars, "truncated": False,
                "bar_dates": ["2026-09-01", "2026-09-02", "2026-09-03",
                              "2026-09-04", "2026-09-05", "2026-09-08"],
                "codes": list(bars)})
        app.dependency_overrides[deps.bar_calendar_loader] = lambda: (
            lambda date_from=None, date_to=None: [
                "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
                "2026-09-05", "2026-09-08"])
        app.dependency_overrides[deps.dragon_repository] = lambda: None
        return TestClient(app, base_url="http://localhost"), app

    def test_scan_quality_is_read_only(self):
        client, app = self._client()
        try:
            body = client.get(
                "/api/research/scan-quality?date_from=2026-09-01&date_to=2026-09-01"
            ).json()
            self.assertEqual(body["status"], "ok")
            self.assertEqual(body["coverage"]["raw_contributions"], 1)
            self.assertEqual(body["evaluation_version"], "scan_quality_v1")
        finally:
            app.dependency_overrides.clear()

    def test_experiments_require_confirm_and_get_does_not_recompute(self):
        client, app = self._client()
        try:
            rejected = client.post(
                "/api/account/experiments", json={"run_id": 1})
            self.assertEqual(rejected.status_code, 400)
            listed = client.get("/api/account/experiments?run_id=1").json()
            self.assertEqual(listed["data"][0]["id"], 9)
        finally:
            app.dependency_overrides.clear()


class TestExperimentHttpSuccess(unittest.TestCase):
    def _client_and_state(self):
        from datetime import date, timedelta
        from fastapi.testclient import TestClient
        from web_ui.backend import deps
        from web_ui.backend.app import create_app
        from web_ui.backend.settings import DashboardSettings

        start = date(2026, 8, 3)
        end = date(2026, 9, 25)
        days = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                days.append(current.isoformat())
            current += timedelta(days=1)

        bars_rows = []
        for index, day in enumerate(days):
            base = 10.0 + index * 0.02
            bars_rows.append({
                "date": day, "open": base, "high": base + 0.15,
                "low": base - 0.15, "close": base + 0.05,
                "volume": 1_000_000.0, "amount": 1_000_000_000.0,
                "turnover": 8.0, "pct": 0.5,
            })

        def dragon_loader(target, top_n=None, source="v2"):
            if target < "2026-08-20" or target > "2026-09-25":
                return []
            return [{
                "trade_date": target, "code": "600001", "name": "样本",
                "rank": 1, "composite_score": 85.0, "board_count": 2,
                "is_true_dragon": True, "amount": 1_000_000_000.0,
                "turnover_rate": 10.0, "close_px": 11.0,
            }]

        def snapshot_reader(day, source="v2", calendar=None):
            dates = [d for d in days if d < day]
            if not dates:
                return None, None, "calendar"
            snapshot_date = dates[-1]
            return ({
                "trade_date": snapshot_date, "version": "0.8",
                "sentiment": {"position_pct": 1.0},
                "review_candidates": [{
                    "code": "600001", "name": "样本",
                    "eligibility": "eligible", "position_pct": 20,
                    "review_score": 85.0, "review_grade": "A",
                }],
            }, snapshot_date, None)

        run = {
            "id": 7, "source": "v2", "display_name": "fixture",
            "strategy_name": "dragon_pullback_daily",
            "strategy_params": {"source": "v2", "initial_cash": 100000.0,
                                "strategy_name": "dragon_pullback_daily"},
            "date_from": "2026-09-01", "date_to": "2026-09-20",
            "initial_cash": 100000.0, "base_equity": None,
            "start_equity": 100000.0, "start_positions": [],
            "start_realized_pnl": 0.0, "run_meta": {}, "base_run_id": None,
            "final_equity": 100000.0, "total_return": 0.0,
            "max_drawdown": 0.0, "trade_count": 0, "win_rate": None,
            "created_at": "2026-09-21 10:00:00",
        }
        saved: dict = {}

        class Account:
            def get_review_account_run(self, run_id):
                return run if run_id == 7 else None

            def query_review_account_snapshots(self, run_id):
                return []

            def query_review_account_closed_positions(self, run_id):
                return []

            def save_review_account_experiment(self, base_run_id, **kwargs):
                saved["base_run_id"] = base_run_id
                saved["report"] = kwargs["report"]
                saved["status"] = kwargs["status"]
                saved["request"] = kwargs["request"]
                return 42

            def query_review_account_experiments(self, run_id, limit=5):
                return [{"id": 42, "base_run_id": run_id,
                         "experiment_version": "account_experiment_v3",
                         "status": saved.get("status"),
                         "request": saved.get("request"),
                         "report": saved.get("report")}]

        class Factor:
            def get_model_snapshot(self, name, version=None):
                return None

        class Assets:
            def list_auction_snapshots(self, day, source="v2"):
                return []

        app = create_app(DashboardSettings(dist_dir=Path(tempfile.mkdtemp())))
        account = Account()
        app.dependency_overrides[deps.account_repository] = lambda: account
        app.dependency_overrides[deps.assets_repository] = lambda: Assets()
        app.dependency_overrides[deps.factor_storage] = lambda: Factor()
        app.dependency_overrides[deps.candidate_loader_factory] = lambda: (
            lambda source: dragon_loader)
        app.dependency_overrides[deps.prep_snapshot_reader] = lambda: (
            snapshot_reader)
        app.dependency_overrides[deps.local_bars_loader] = lambda: (
            lambda codes, date_from, date_to: {
                "rows": {code: bars_rows for code in codes},
                "truncated": False,
                "bar_dates": days,
                "codes": list(codes),
            })
        app.dependency_overrides[deps.bar_calendar_loader] = lambda: (
            lambda date_from=None, date_to=None: [
                d for d in days
                if (date_from is None or d >= date_from)
                and (date_to is None or d <= date_to)])
        saved["run"] = run
        saved["account"] = account
        return TestClient(app, base_url="http://localhost"), app, saved

    def test_post_runs_real_simulator_and_get_reads_saved_report(self):
        client, app, saved = self._client_and_state()
        try:
            response = client.post(
                "/api/account/experiments",
                json={"run_id": 7, "confirm": True,
                      "sell_rules": ["trailing"]},
            )
            self.assertEqual(response.status_code, 201, response.text)
            report = response.json()["data"]
            self.assertIn(report["status"],
                          {"evaluated", "insufficient_evidence"})
            self.assertTrue(report["frozen"]["content_sha256"])
            self.assertIn("baseline", report["variants"])
            self.assertIn("train", report["variants"]["baseline"])
            self.assertIn("validation", report["variants"]["baseline"])
            holdout = [name for name, item in report["variants"].items()
                       if "test" in item]
            self.assertTrue(holdout)
            self.assertTrue(set(holdout) <= {"baseline", report["selected"]})
            self.assertEqual(saved["base_run_id"], 7)
            self.assertIn("sell_rule_trailing_off", report["variants"])
            listed = client.get("/api/account/experiments?run_id=7").json()
            self.assertEqual(listed["data"][0]["id"], 42)
            self.assertEqual(
                listed["data"][0]["report"]["experiment_version"],
                "account_experiment_v3")
        finally:
            app.dependency_overrides.clear()


class TestFrozenExperimentInputs(unittest.TestCase):
    def test_prep_ablation_actually_changes_08_pool(self):
        from dataclasses import replace
        from dragon_quant.review_account.market import collect_candidates_ex

        snapshot = {
            "version": "0.8",
            "review_candidates": [
                {"code": "600001", "eligibility": "eligible", "position_pct": 20},
                {"code": "600002", "eligibility": "not_qualified",
                 "position_pct": 0},
            ],
            "sentiment": {"position_pct": 1.0},
        }
        calendar = ["2026-09-01", "2026-09-02"]
        cfg = StrategyConfig(source="v2")
        loader = lambda date, top_n=None, source="v2": []
        reader = lambda day: (snapshot, "2026-09-01", None)
        baseline, _ = collect_candidates_ex(
            calendar, "2026-09-02", cfg, loader, snapshot_loader=reader)
        ablated, _ = collect_candidates_ex(
            calendar, "2026-09-02",
            replace(cfg, prep_verdict_filter_enabled=False), loader,
            snapshot_loader=reader)
        self.assertEqual({row["code"] for row in baseline}, {"600001"})
        self.assertEqual({row["code"] for row in ablated},
                         {"600001", "600002"})

    def test_freeze_fingerprint_changes_with_content_and_versions(self):
        from dragon_quant.review_account import experiments

        cfg = StrategyConfig(source="v2")
        days = ["2026-09-01", "2026-09-02"]
        snapshot = {"version": "0.8", "review_candidates": [
            {"code": "600001", "eligibility": "eligible", "position_pct": 20}],
            "sentiment": {"position_pct": 1.0}}
        base = dict(
            cfg=cfg, calendar=days, date_from="2026-09-01",
            date_to="2026-09-02",
            snapshot_reader=lambda day: (snapshot, "2026-09-01", None),
            dragon_loader=lambda date, top_n=None, source="v2": [],
            auction_reader=lambda day: [],
        )
        bars = {"600001": [{"date": "2026-09-02", "open": 10.0,
                            "high": 10.5, "low": 9.5, "close": 10.2}]}
        first = experiments.freeze_upstream(**base, bars=bars)
        second = experiments.freeze_upstream(**base, bars=bars)
        self.assertEqual(first["content_sha256"], second["content_sha256"])
        changed = experiments.freeze_upstream(
            **{**base, "snapshot_reader": lambda day: (
                {**snapshot, "version": "0.7"}, "2026-09-01", None)},
            bars=bars)
        self.assertNotEqual(first["content_sha256"],
                            changed["content_sha256"])
        self.assertEqual(first["versions"], ["0.8"])

    def test_no_change_and_incremental_seed_guard(self):
        from dragon_quant.review_account import experiments

        cfg = StrategyConfig(source="v2")
        variants = experiments.build_variants(cfg)
        frozen = {"calendar": ["2026-09-01"], "content_sha256": "x",
                  "versions": ["0.8"], "codes": [], "snapshots": {},
                  "snapshot_meta": {}, "sentiments": {}, "dragons": {},
                  "auctions": {}, "bars": {}, "model_snapshot": None}
        report = experiments.run_experiments(
            run={"id": 1, "date_from": "2026-09-01",
                 "date_to": "2026-09-01", "base_run_id": 5},
            cfg=cfg, data=object(), frozen=frozen, seed=None)
        self.assertEqual(report["status"], "insufficient_data")
        self.assertEqual(report["reason"], "incremental_seed_missing")
        self.assertIn("drop_auction_ranking", variants)


class TestPeriodLedger(unittest.TestCase):
    def _run(self, **overrides):
        return {
            "id": 1, "initial_cash": 100000.0, "start_equity": 12000.0,
            "start_positions": [{
                "code": "600001", "qty": 1000, "cost": 10005.0,
                "entry_price": 10.0, "entry_date": "2026-08-01",
                "entry_reason_code": "buy_open_turn_strong",
                "mark": 12.0, "realized_pnl": 0.0,
            }],
            "run_meta": {"attribution_quality": "precise"},
            **overrides,
        }

    def test_inherited_unrealized_uses_period_start_mark(self):
        from dragon_quant.review_account.evaluation import period_ledger

        snapshots = [{
            "trade_date": "2026-09-01", "total_equity": 12100.0,
            "positions": [{
                "code": "600001", "qty": 1000, "cost": 10005.0,
                "entry_price": 10.0, "entry_date": "2026-08-01",
                "entry_reason_code": "buy_open_turn_strong",
                "mark": 12.1,
            }],
        }]
        ledger = period_ledger(self._run(), snapshots, [])
        row = ledger["rows"][0]
        self.assertAlmostEqual(row["period_pnl"], 100.0, places=6)
        self.assertAlmostEqual(row["unrealized_part"], 100.0, places=6)
        self.assertAlmostEqual(row["realized_part"], 0.0, places=6)
        self.assertAlmostEqual(ledger["reconciliation"]["diff"], 0.0,
                               places=6)

    def test_half_sale_reconciles_realized_and_unrealized(self):
        from dragon_quant.review_account.evaluation import period_ledger

        snapshots = [{
            "trade_date": "2026-09-02", "total_equity": 12295.0,
            "positions": [{
                "code": "600001", "qty": 500, "cost": 5002.5,
                "entry_price": 10.0, "entry_date": "2026-08-01",
                "entry_reason_code": "buy_open_turn_strong",
                "mark": 12.1,
            }],
        }]
        trades = [{"side": "SELL", "code": "600001", "qty": 500,
                   "amount": 6250.0, "fee": 5.0, "realized_pnl": 1245.0,
                   "reason_code": "trailing"}]
        ledger = period_ledger(self._run(), snapshots, trades)
        row = ledger["rows"][0]
        self.assertAlmostEqual(row["realized_part"], 245.0, places=6)
        self.assertAlmostEqual(row["unrealized_part"], 50.0, places=6)
        self.assertAlmostEqual(row["period_pnl"], 295.0, places=6)
        self.assertTrue(ledger["reconciliation"]["within_tolerance"])

    def test_old_start_mark_is_unknown_not_fake_precise(self):
        from dragon_quant.review_account.evaluation import period_ledger

        run = self._run(start_positions=[{
            "code": "600001", "qty": 1000, "cost": 10005.0,
            "entry_price": 10.0, "entry_date": "2026-08-01",
            "entry_reason_code": "buy_open_turn_strong", "mark": None,
        }], run_meta={"attribution_quality": "legacy_approximation"})
        snapshots = [{"trade_date": "2026-09-01", "total_equity": 12100.0,
                      "positions": [{"code": "600001", "qty": 1000,
                                     "cost": 10005.0, "entry_price": 10.0,
                                     "mark": 12.1}]}]
        ledger = period_ledger(run, snapshots, [])
        self.assertEqual(ledger["quality"], "unknown")
        self.assertIsNone(ledger["rows"][0]["period_pnl"])


class TestOldSchemaMigration(unittest.TestCase):
    def test_old_tables_gain_columns_and_keep_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "dragon.db")
            try:
                conn.executescript("""
CREATE TABLE dragon_review_strict (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL, code TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'v2', buy_date TEXT, buy_price REAL,
    max_return_5d REAL, max_drawdown_5d REAL, max_return_hold_days INTEGER,
    review_status TEXT NOT NULL, reason TEXT, review_version TEXT,
    computed_at TEXT, UNIQUE(trade_date, code, source)
);
CREATE TABLE review_account_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT DEFAULT 'v2',
    display_name TEXT, strategy_name TEXT NOT NULL,
    strategy_params_json TEXT, date_from TEXT NOT NULL, date_to TEXT NOT NULL,
    initial_cash REAL, base_equity REAL, final_equity REAL,
    total_return REAL, max_drawdown REAL, trade_count INTEGER,
    win_rate REAL, created_at TEXT
);
INSERT INTO dragon_review_strict(trade_date, code, review_status)
VALUES ('2026-08-01', '600001', 'completed');
INSERT INTO review_account_runs(strategy_name, date_from, date_to)
VALUES ('dragon_pullback_daily', '2026-08-01', '2026-08-31');
""")
                schema_mod._ensure_schema(conn)
                schema_mod._ensure_schema(conn)
                strict_cols = {r[1] for r in conn.execute(
                    "PRAGMA table_info(dragon_review_strict)")}
                run_cols = {r[1] for r in conn.execute(
                    "PRAGMA table_info(review_account_runs)")}
                self.assertIn("tradable_open_json", strict_cols)
                self.assertTrue({"base_run_id", "start_equity",
                                 "start_positions_json", "start_realized_pnl",
                                 "run_meta_json"} <= run_cols)
                self.assertEqual(conn.execute(
                    "SELECT review_status FROM dragon_review_strict"
                ).fetchone()[0], "completed")
                self.assertEqual(conn.execute(
                    "SELECT strategy_name FROM review_account_runs"
                ).fetchone()[0], "dragon_pullback_daily")
            finally:
                conn.close()


class TestScanQualityR5(unittest.TestCase):
    def test_canonical_prefers_latest_on_ties(self):
        rows = [
            {"code": "600001", "rank": 1, "composite_score": 80,
             "scan_id": "old", "scan_created_at": "2026-09-01 15:00"},
            {"code": "600001", "rank": 1, "composite_score": 80,
             "scan_id": "new", "scan_created_at": "2026-09-01 16:00"},
        ]
        canonical, raw = scan_quality.canonical_candidates(rows)
        self.assertEqual(raw, 2)
        self.assertEqual(canonical[0]["scan_id"], "new")

    def test_immature_window_has_no_opportunity_rate(self):
        contributions = {"2026-09-01": [{
            "code": "600001", "rank": 1, "composite_score": 80,
            "is_true_dragon": True, "scan_top_n": 5}]}
        bars = {"600001": [_bar("2026-09-01", 10.0, 10.5, 9.8, 10.2),
                           _bar("2026-09-02", 10.2, 12.0, 10.0, 11.8)]}
        payload = scan_quality.build_scan_quality(
            date_from="2026-09-01", date_to="2026-09-01",
            contributions_by_date=contributions,
            calendar=["2026-09-01", "2026-09-02"], bars=bars)
        self.assertEqual(payload["status"], "insufficient_data")
        self.assertEqual(payload["coverage"]["excluded"]["immature"], 1)
        self.assertIsNone(
            payload["baseline"]["missed_opportunity_rate"])
        self.assertEqual(payload["coverage"]["by_horizon"]["3"]["immature"], 1)


class TestPeriodLedgerReconciliationMore(unittest.TestCase):
    def test_full_run_reconciles_fees_and_zero_open(self):
        from dragon_quant.review_account.evaluation import (
            metrics_from_rows, period_ledger,
        )

        run = {"id": 1, "initial_cash": 100000.0, "start_equity": 100000.0,
               "start_positions": [], "run_meta": {}}
        snapshots = [
            {"trade_date": "2026-09-01", "total_equity": 100000.0,
             "positions": []},
            {"trade_date": "2026-09-03", "total_equity": 101155.0,
             "positions": []},
        ]
        trades = [
            {"side": "BUY", "code": "600001", "qty": 500, "amount": 50000.0,
             "fee": 15.0, "realized_pnl": -15.0},
            {"side": "SELL", "code": "600001", "qty": 500, "amount": 51200.0,
             "fee": 30.0, "realized_pnl": 1155.0},
        ]
        ledger = period_ledger(run, snapshots, trades)
        self.assertAlmostEqual(ledger["totals"]["ledger_period_pnl"], 1155.0)
        self.assertTrue(ledger["reconciliation"]["within_tolerance"])
        summary = metrics_from_rows(run, snapshots, trades, [])
        self.assertEqual(summary["open_count"], 0)
        self.assertAlmostEqual(summary["period_pnl_amount"], 1155.0)

    def test_three_segment_chain_with_half_sale_reconciles(self):
        from dragon_quant.review_account.evaluation import period_ledger

        # 第三段：期初继承 500 股 @12；本段卖 250 @12.5，期末 250 @12.2。
        run = {
            "id": 3, "start_equity": 6000.0, "base_equity": 6000.0,
            "start_positions": [{
                "code": "600001", "qty": 500, "cost": 5002.5,
                "entry_price": 10.0, "entry_date": "2026-08-01",
                "entry_reason_code": "buy_open_turn_strong",
                "mark": 12.0, "realized_pnl": 0.0,
            }],
            "run_meta": {"attribution_quality": "precise"},
        }
        snapshots = [{
            "trade_date": "2026-09-10", "total_equity": 6175.0,
            "positions": [{
                "code": "600001", "qty": 250, "cost": 2501.25,
                "entry_price": 10.0, "entry_date": "2026-08-01",
                "entry_reason_code": "buy_open_turn_strong",
                "mark": 12.2,
            }],
        }]
        trades = [{"side": "SELL", "code": "600001", "qty": 250,
                   "amount": 3125.0, "fee": 0.0, "realized_pnl": 0.0}]
        ledger = period_ledger(run, snapshots, trades)
        row = ledger["rows"][0]
        self.assertAlmostEqual(row["period_pnl"], 175.0)
        self.assertAlmostEqual(row["realized_part"], 125.0)
        self.assertAlmostEqual(row["unrealized_part"], 50.0)
        self.assertTrue(ledger["reconciliation"]["within_tolerance"])

    def test_open_count_comes_from_end_snapshot(self):
        from dragon_quant.review_account.evaluation import metrics_from_rows

        run = {"id": 1, "initial_cash": 100000.0, "start_equity": 100000.0,
               "start_positions": [], "run_meta": {}}
        snapshots = [{
            "trade_date": "2026-09-03", "total_equity": 100000.0,
            "market_value": 5000.0,
            "positions": [{"code": "600001", "qty": 500, "cost": 5000.0,
                           "mark": 10.0}],
        }]
        trades = [{"side": "BUY", "code": "600001", "qty": 500,
                   "amount": 5000.0, "fee": 0.0, "realized_pnl": 0.0}]
        summary = metrics_from_rows(run, snapshots, trades, [])
        self.assertEqual(summary["open_count"], 1)
        self.assertAlmostEqual(summary["unrealized_amount"], 0.0)


class TestTradableOpenPersistence(unittest.TestCase):
    def test_strict_tradable_open_roundtrips_through_query(self):
        from dragon_quant.scan.storage import dragons as store

        store.save_dragons("2026-08-20", [{
            "code": "600001", "name": "A", "rank": 1,
            "composite_score": 80.0, "is_true_dragon": True,
        }], version="1.4.0")
        store.save_dual_review(
            "2026-08-20", "600001",
            loose={"status": "completed", "buy_date": "2026-08-21",
                   "buy_price": 10.0, "max_return_5d": 5.0,
                   "max_drawdown_5d": -1.0, "max_return_hold_days": 2},
            strict={"review_status": "completed",
                    "buy_date": "2026-08-21", "buy_price": 10.0,
                    "max_return_5d": 5.0, "max_drawdown_5d": -1.0,
                    "max_return_hold_days": 2,
                    "tradable_open": {
                        "caliber": "tradable_open_v1",
                        "status": "completed",
                        "entry_date": "2026-08-21",
                        "entry_price": 10.0,
                        "horizons": {"1": {"status": "completed",
                                           "net_return_pct": 1.0}},
                    }},
        )
        rows = store.query_dragons({})
        self.assertEqual(rows[0]["strict_tradable_open"]["caliber"],
                         "tradable_open_v1")
        self.assertEqual(
            rows[0]["strict_tradable_open"]["horizons"]["1"]
            ["net_return_pct"], 1.0)


class TestAccountSummaryHttpReconciliation(unittest.TestCase):
    def test_incremental_chain_summary_reconciles_and_open_count(self):
        import json as _json
        from dragon_quant.review_account import storage as account_store
        from dragon_quant.tests.test_web_backend import make_client

        run_id = account_store.create_review_account_run(
            source="v2", strategy_name="dragon_pullback_daily",
            strategy_params_json=_json.dumps({"source": "v2"}),
            date_from="2026-09-10", date_to="2026-09-10",
            initial_cash=6000.0, final_equity=6175.0, total_return=2.92,
            max_drawdown=0.0, trade_count=1, win_rate=None,
            base_run_id=99, start_equity=6000.0,
            start_positions_json=_json.dumps([{
                "code": "600001", "qty": 500, "cost": 5002.5,
                "entry_price": 10.0, "entry_date": "2026-08-01",
                "entry_reason_code": "buy_open_turn_strong",
                "mark": 12.0, "realized_pnl": 0.0,
            }]),
            start_realized_pnl=0.0,
            run_meta_json=_json.dumps({"attribution_quality": "precise"}),
        )
        account_store.save_review_account_results(
            run_id,
            snapshots=[{
                "trade_date": "2026-09-10", "cash": 3125.0,
                "market_value": 3050.0, "total_equity": 6175.0,
                "positions_json": _json.dumps([{
                    "code": "600001", "qty": 250, "cost": 2501.25,
                    "entry_price": 10.0, "entry_date": "2026-08-01",
                    "entry_reason_code": "buy_open_turn_strong",
                    "mark": 12.2,
                }]),
            }],
            trades=[{
                "trade_date": "2026-09-10", "code": "600001",
                "name": "龙头A", "side": "SELL", "price": 12.5, "qty": 250,
                "amount": 3125.0, "fee": 0.0, "realized_pnl": 0.0,
                "cash_after": 3125.0, "position_after": 250,
                "reason_code": "trailing", "reason_text": "",
                "signal_json": "{}",
            }],
            positions=[],
        )
        client = make_client()
        body = client.get(f"/api/account/summary?run_id={run_id}").json()
        data = body["data"]
        self.assertEqual(data["open_count"], 1)
        self.assertAlmostEqual(data["period_pnl_amount"], 175.0)
        self.assertAlmostEqual(data["realized_amount"], 125.0)
        self.assertAlmostEqual(data["unrealized_amount"], 50.0)
        self.assertTrue(data["reconciliation"]["within_tolerance"])


class TestExperimentInputClassification(TestExperimentHttpSuccess):
    def test_empty_calendar_is_422(self):
        from web_ui.backend import deps

        client, app, _ = self._client_and_state()
        try:
            app.dependency_overrides[deps.bar_calendar_loader] = lambda: (
                lambda date_from=None, date_to=None: [])
            response = client.post(
                "/api/account/experiments",
                json={"run_id": 7, "confirm": True})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()["error"],
                             "local_calendar_missing")
        finally:
            app.dependency_overrides.clear()

    def test_truncated_bars_are_422_not_partial_stats(self):
        from web_ui.backend import deps

        client, app, _ = self._client_and_state()
        try:
            app.dependency_overrides[deps.local_bars_loader] = lambda: (
                lambda codes, date_from, date_to: {
                    "rows": {}, "truncated": True,
                    "bar_dates": [], "codes": []})
            response = client.post(
                "/api/account/experiments",
                json={"run_id": 7, "confirm": True})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()["error"], "experiment_inputs_unavailable")
        finally:
            app.dependency_overrides.clear()

    def test_incremental_run_without_seed_is_400(self):
        from web_ui.backend import deps

        client, app, _ = self._client_and_state()
        account = app.dependency_overrides[deps.account_repository]()
        run = account.get_review_account_run(7)
        run.update({"base_run_id": 3, "start_positions": [],
                    "start_equity": None, "base_equity": None,
                    "start_cash": None})
        try:
            response = client.post(
                "/api/account/experiments",
                json={"run_id": 7, "confirm": True})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()["error"],
                             "incremental_seed_unavailable")
        finally:
            app.dependency_overrides.clear()


class TestPrepAblationReachesBuyGate(unittest.TestCase):
    def test_pool_and_buy_gate_both_use_research_bypass(self):
        from dataclasses import replace
        from dragon_quant.review_account.market import collect_candidates_ex
        from dragon_quant.review_account.models import StrategyConfig
        from dragon_quant.review_account.strategy.buy import evaluate_buy
        from dragon_quant.review_account.strategy.common import BuyRejected
        from dragon_quant.tests.test_review_account import (
            CAND, row,
        )

        # 1) 独立 scan 合格、仅 prep 不推荐：生产路径必须拒绝。
        ineligible = {**CAND, "review_pool": True, "review_eligible": False,
                      "composite_score": 99.0, "is_true_dragon": True}
        r = row()
        with self.assertRaises(BuyRejected) as ctx:
            evaluate_buy(ineligible, r, StrategyConfig(), r["prev_row"])
        self.assertEqual(ctx.exception.code, "review_not_eligible")

        # 2) 研究消融开启后，同一候选进入共享形态判断并触发买点。
        research_cfg = replace(StrategyConfig(), prep_verdict_filter_enabled=False,
                               research_prep_bypass=True)
        signal = evaluate_buy(ineligible, r, research_cfg, r["prev_row"])
        self.assertIsNotNone(signal)

        # 3) 池消融与决策路径一致：0.8 快照 B 只在 bypass 变体进入池。
        snapshot = {
            "version": "0.8",
            "sentiment": {"position_pct": 1.0},
            "review_candidates": [
                {"code": CAND["code"], "eligibility": "not_qualified",
                 "position_pct": 0, "review_score": 99.0},
            ],
        }
        calendar = ["2026-09-01", "2026-09-02"]
        reader = lambda day: (snapshot, "2026-09-01", None)
        baseline, _ = collect_candidates_ex(
            calendar, "2026-09-02", StrategyConfig(),
            lambda date, top_n=None, source="v2": [],
            snapshot_loader=reader)
        ablated, _ = collect_candidates_ex(
            calendar, "2026-09-02", research_cfg,
            lambda date, top_n=None, source="v2": [],
            snapshot_loader=reader)
        self.assertEqual(baseline, [])
        self.assertEqual(len(ablated), 1)
        buy = evaluate_buy(ablated[0], r, research_cfg, r["prev_row"])
        self.assertIsNotNone(buy)


class TestIncrementalExperimentSeedNoLookahead(unittest.TestCase):
    def test_seed_from_run_uses_start_fields_not_end_snapshot(self):
        from dragon_quant.review_account.experiments import seed_from_run

        run = {
            "id": 9, "base_run_id": 3,
            "start_equity": 11000.0,
            "start_cash": 9000.0,
            "start_peak_equity": 12000.0,
            "start_consecutive_losses": 2,
            "start_realized_pnl": 500.0,
            "start_positions": [{
                "code": "600001", "name": "A", "qty": 100,
                "entry_date": "2026-08-01", "entry_price": 10.0,
                "cost": 1000.0, "entry_reason_code": "buy_open_turn_strong",
                "entry_reason_text": "", "mark": 20.0,
                "period_start_qty": 999, "period_start_mark": 1.0,
                "period_start_cost": 999.0,
                "period_start_realized_pnl": 999.0,
            }],
            # 期末快照刻意不同，且不应被消费。
            "date_to": "2026-09-30",
        }
        seed = seed_from_run(run)
        self.assertIsNotNone(seed)
        state = seed["state"]
        self.assertEqual(state.cash, 9000.0)
        self.assertEqual(len(state.positions), 1)
        self.assertEqual(state.positions[0].qty, 100)
        self.assertEqual(state.positions[0].mark, 20.0)
        self.assertEqual(state.peak_equity, 12000.0)
        self.assertEqual(state.consecutive_losses, 2)
        self.assertEqual(seed["base_equity"], 11000.0)
        self.assertEqual(seed["attribution_quality"], "precise")
        # 期初字段覆盖为当前 run 的 period start，不沿用更早一层。
        self.assertEqual(state.positions[0].period_start_qty, 100)
        self.assertEqual(state.positions[0].period_start_mark, 20.0)
        self.assertEqual(state.positions[0].period_start_cost, 1000.0)

    def test_missing_start_fields_are_not_reproducible(self):
        from dragon_quant.review_account.experiments import seed_from_run

        with self.assertRaises(ValueError):
            seed_from_run({
                "base_run_id": 3, "start_equity": None,
                "start_positions": [{"code": "600001", "qty": 100,
                                     "entry_price": 10.0, "cost": 1000.0,
                                     "mark": None}],
            })

    def test_http_does_not_read_end_snapshot_for_seed(self):
        client, app, saved = TestExperimentHttpSuccess()._client_and_state()
        run = saved["run"]
        run.update({
            "base_run_id": 3, "start_equity": 11000.0,
            "start_cash": 9000.0, "start_peak_equity": 12000.0,
            "start_consecutive_losses": 2, "start_realized_pnl": 500.0,
            "start_positions": [{
                "code": "600001", "name": "样本", "qty": 100,
                "entry_date": "2026-08-01", "entry_price": 10.0,
                "cost": 1000.0, "entry_reason_code": "buy_open_turn_strong",
                "entry_reason_text": "", "mark": 20.0,
            }],
            "start_marks": {"600001": 20.0},
        })

        def _forbidden(*args, **kwargs):
            raise AssertionError("experiment seed must not read end snapshots")

        saved["account"].query_review_account_snapshots = _forbidden
        try:
            response = client.post(
                "/api/account/experiments",
                json={"run_id": 7, "confirm": True})
            self.assertEqual(response.status_code, 201, response.text)
            report = response.json()["data"]
            self.assertTrue(report["frozen"]["seed_inherited"])
            self.assertEqual(report["frozen"]["attribution_quality"],
                             "precise")
        finally:
            app.dependency_overrides.clear()


class TestFactorFreeze(unittest.TestCase):
    def _model(self):
        return {
            "model_name": "equal_rank_v1", "model_version": "v1",
            "params": {"weights": {"f1": 1.0}, "lower_better": [],
                       "neutral": [], "min_cohort": 2},
            "factor_versions": {"f1": "1"},
        }

    def test_frozen_factor_values_and_model_are_injected(self):
        from dataclasses import replace
        from dragon_quant.review_account import experiments

        cfg = replace(StrategyConfig(source="v2"), factor_ranking_enabled=True)
        days = ["2026-09-01", "2026-09-02"]
        calls = []

        def reader(dates, codes, names, versions):
            calls.append((tuple(dates), tuple(codes), tuple(names)))
            return {day: {code: {"f1": 1.0} for code in codes}
                    for day in dates}

        frozen = experiments.freeze_upstream(
            cfg=cfg, calendar=days, date_from="2026-09-01",
            date_to="2026-09-02",
            snapshot_reader=lambda day: ({"version": "0.8"}, "2026-09-01", None),
            dragon_loader=lambda date, top_n=None, source="v2": [],
            auction_reader=lambda day: [],
            bars_getter=lambda codes: {code: [] for code in codes},
            model_snapshot=self._model(),
            factor_reader=reader,
            extra_codes=["600009"],
        )
        self.assertEqual(frozen["factor_completion"], 1.0)
        self.assertIn("600009", frozen["codes"])
        self.assertEqual(calls[0][2], ("f1",))
        gated, reason, model = experiments._factor_gate(cfg, days, frozen)
        self.assertIsNone(reason)
        self.assertEqual(model.version, "v1")
        self.assertEqual(gated.factor_model_version, "v1")
        loader = experiments._frozen_factor_loader(frozen)
        self.assertEqual(loader("2026-09-01", ("f1",), ("600009",), {"f1": "1"}),
                         {"600009": {"f1": 1.0}})

    def test_missing_factor_values_are_insufficient(self):
        from dataclasses import replace
        from dragon_quant.review_account import experiments

        cfg = replace(StrategyConfig(source="v2"), factor_ranking_enabled=True)
        frozen = experiments.freeze_upstream(
            cfg=cfg, calendar=["2026-09-01"], date_from="2026-09-01",
            date_to="2026-09-01",
            snapshot_reader=lambda day: ({"version": "0.8"}, "2026-09-01", None),
            dragon_loader=lambda date, top_n=None, source="v2": [],
            auction_reader=lambda day: [], bars_getter=lambda codes: {},
            model_snapshot=self._model(),
            factor_reader=lambda *args: {},
        )
        gated, reason, model = experiments._factor_gate(
            cfg, ["2026-09-01"], frozen)
        self.assertIsNone(gated)
        self.assertIn("factor_build_completion", reason)
        self.assertIsNone(model)

    def test_http_factor_variant_uses_frozen_store(self):
        client, app, saved = TestExperimentHttpSuccess()._client_and_state()
        from web_ui.backend import deps

        class Factor:
            def __init__(self):
                self.reads = 0

            def get_model_snapshot(self, name, version=None):
                return {
                    "model_name": "equal_rank_v1", "model_version": "v1",
                    "params": {"weights": {"f1": 1.0}, "lower_better": [],
                               "neutral": [], "min_cohort": 2},
                    "factor_versions": {"f1": "1"},
                }

            def get_day_values(self, day, *, names=None, codes=None,
                               versions=None, db_path=None):
                self.reads += 1
                return {"values": {code: {"f1": 1.0} for code in codes}}

        factor = Factor()
        app.dependency_overrides[deps.factor_storage] = lambda: factor
        try:
            response = client.post(
                "/api/account/experiments",
                json={"run_id": 7, "confirm": True})
            self.assertEqual(response.status_code, 201, response.text)
            report = response.json()["data"]
            self.assertEqual(report["frozen"]["factor_completion"], 1.0)
            self.assertGreater(factor.reads, 0)
            variant = report["variants"]["factor_model_on"]
            self.assertNotEqual(variant.get("status"), "insufficient_data")
        finally:
            app.dependency_overrides.clear()


class TestLifecycleVsPeriodStats(unittest.TestCase):
    def test_lifecycle_columns_stay_closed_only(self):
        from dragon_quant.review_account.evaluation import entry_reason_stats

        positions = [
            {"code": "600001", "status": "closed",
             "entry_reason_code": "buy_open_turn_strong",
             "realized_return": 10.0},
            {"code": "600002", "status": "closed",
             "entry_reason_code": "buy_open_ma5_pullback",
             "realized_return": -5.0},
        ]
        ledger = {
            "quality": "precise",
            "unknown_rows": 0,
            "reconciliation": {"diff": 0.0, "within_tolerance": True},
            "rows": [
                {"entry_reason_code": "buy_open_turn_strong",
                 "period_pnl": 100.0, "realized_part": 100.0,
                 "unrealized_part": 0.0, "start_value": 1000.0,
                 "buy_cost": 0.0, "status": "closed"},
                {"entry_reason_code": "buy_open_ma5_pullback",
                 "period_pnl": -50.0, "realized_part": -50.0,
                 "unrealized_part": 0.0, "start_value": 1000.0,
                 "buy_cost": 0.0, "status": "closed"},
                # 未平仓浮亏：不得进入“盈利笔数/胜率”。
                {"entry_reason_code": "buy_divergence_first_break",
                 "period_pnl": -30.0, "realized_part": 0.0,
                 "unrealized_part": -30.0, "start_value": 500.0,
                 "buy_cost": 0.0, "status": "open"},
            ],
            "totals": {"start_equity": 10000.0, "end_equity": 10020.0,
                       "ledger_period_pnl": 20.0, "realized_part": 50.0,
                       "unrealized_part": -30.0, "fees": 0.0,
                       "equity_change": 20.0},
        }
        stats = entry_reason_stats(positions, 10000.0, start_equity=10000.0,
                                   ledger=ledger)
        rows = {row["reason_code"]: row for row in stats["rows"]}
        total = rows["__total__"]
        self.assertEqual(total["trades"], 2)
        self.assertEqual(total["wins"], 1)
        self.assertEqual(total["win_rate"], 50.0)
        self.assertEqual(total["period_pnl"], 20.0)
        self.assertEqual(rows["buy_divergence_first_break"]["trades"], 0)
        self.assertIsNone(rows["buy_divergence_first_break"]["win_rate"])
        self.assertEqual(rows["buy_divergence_first_break"]["period_pnl"], -30.0)
        self.assertTrue(rows["buy_divergence_first_break"]["has_open"])
        self.assertAlmostEqual(total["contribution"], 0.2)
        self.assertTrue(stats["period_complete"])

    def test_unknown_ledger_does_not_fake_precise_totals(self):
        from dragon_quant.review_account.evaluation import entry_reason_stats

        positions = [{"code": "600001", "status": "closed",
                      "entry_reason_code": "buy_open_turn_strong",
                      "realized_return": 10.0}]
        ledger = {"quality": "unknown", "unknown_rows": 1,
                  "reconciliation": {"diff": None,
                                     "within_tolerance": False},
                  "rows": [{"entry_reason_code": "buy_open_turn_strong",
                            "period_pnl": None, "realized_part": None,
                            "unrealized_part": None, "status": "open"}],
                  "totals": {"start_equity": 10000.0, "end_equity": 10100.0,
                             "ledger_period_pnl": None, "realized_part": None,
                             "unrealized_part": None}}
        stats = entry_reason_stats(positions, 10000.0, start_equity=10000.0,
                                   ledger=ledger)
        total = stats["rows"][-1]
        self.assertEqual(total["trades"], 1)
        self.assertIsNone(total["period_pnl"])
        self.assertIsNone(total["contribution"])
        self.assertEqual(stats["contribution_basis"], "unknown")
        self.assertFalse(stats["period_complete"])

    def test_fifo_allocates_same_code_to_different_buy_reasons(self):
        from dragon_quant.review_account.evaluation import period_ledger

        run = {"start_equity": 0.0, "start_positions": [],
               "run_meta": {"attribution_quality": "precise"}}
        trades = [
            {"side": "BUY", "code": "600001", "qty": 100,
             "amount": 1000.0, "fee": 0.0, "realized_pnl": 0.0,
             "trade_date": "2026-09-01",
             "reason_code": "buy_open_turn_strong"},
            {"side": "SELL", "code": "600001", "qty": 100,
             "amount": 1200.0, "fee": 0.0, "realized_pnl": 200.0,
             "trade_date": "2026-09-02",
             "reason_code": "trailing_take_profit"},
            {"side": "BUY", "code": "600001", "qty": 100,
             "amount": 1300.0, "fee": 0.0, "realized_pnl": 0.0,
             "trade_date": "2026-09-03",
             "reason_code": "buy_open_ma5_pullback"},
            {"side": "SELL", "code": "600001", "qty": 100,
             "amount": 1200.0, "fee": 0.0, "realized_pnl": -100.0,
             "trade_date": "2026-09-04",
             "reason_code": "stop_loss"},
        ]
        snapshots = [{"trade_date": "2026-09-04", "total_equity": 100.0,
                      "positions": []}]
        ledger = period_ledger(run, snapshots, trades)
        rows = {(row["entry_reason_code"]): row for row in ledger["rows"]}
        self.assertAlmostEqual(rows["buy_open_turn_strong"]["period_pnl"], 200.0)
        self.assertAlmostEqual(rows["buy_open_ma5_pullback"]["period_pnl"], -100.0)
        self.assertTrue(ledger["reconciliation"]["within_tolerance"])


class TestDecisionReviewSharesSignalAdapter(unittest.TestCase):
    def test_prep_groups_and_directions_match_shared_adapter(self):
        from dragon_quant.infra.factor import decision_review as dr
        from dragon_quant.infra.factor import signal_adapter

        candidates = [
            {"code": "600001", "eligibility": "eligible", "position_pct": 20},
            {"code": "600002", "eligibility": "not_qualified",
             "position_pct": 0},
            {"code": "600003", "eligibility": "eligible",
             "position_pct": None},
        ]
        adapter = dr.PrepSnapshotAdapter.__new__(dr.PrepSnapshotAdapter)
        day = adapter.map_day("2026-09-01", {
            "version": "0.8", "review_candidates": candidates,
        })
        groups = {obs.code: obs.group for obs in day.observations}
        self.assertEqual(groups["600001"], "recommended")
        self.assertEqual(groups["600002"], "not_recommended")
        self.assertEqual(groups["600003"], "data_insufficient")
        for obs in day.observations:
            self.assertEqual(obs.group, signal_adapter.prep_group(
                next(c for c in candidates if c["code"] == obs.code)))
        self.assertIsNone(signal_adapter.prep_expected_direction(
            "not_recommended"))
        specs = {spec.key: spec.expected_direction
                 for spec in dr.SOURCE_GROUP_SPECS["prep"]}
        self.assertIsNone(specs["not_recommended"])

    def test_auction_direction_matches_shared_adapter(self):
        from dragon_quant.infra.factor import decision_review as dr
        from dragon_quant.infra.factor import signal_adapter

        rows = [
            {"code": "600001", "version": "2", "level": "强",
             "direction": "bull", "strength": 80, "is_weak": False,
             "unverified": False, "data_quality": {"status": "ok"}},
            {"code": "600002", "version": "2", "level": "中",
             "direction": "neutral", "strength": 60, "is_weak": False,
             "unverified": False, "data_quality": {"status": "ok"}},
            {"code": "600003", "version": "2", "level": "弱",
             "direction": "bear", "strength": 20, "is_weak": True,
             "unverified": False, "data_quality": {"status": "ok"}},
            {"code": "600004", "version": "2", "level": "强",
             "direction": "bull", "strength": 80, "is_weak": False,
             "data_quality": {"status": "ok"}},
        ]
        adapter = dr.AuctionSnapshotAdapter.__new__(dr.AuctionSnapshotAdapter)
        day = adapter.map_day("2026-09-01", rows)
        by_code = {obs.code: obs for obs in day.observations}
        for row in rows:
            expected = signal_adapter.auction_signal(row)
            self.assertEqual(by_code[row["code"]].direction_group,
                             expected["direction"])
        self.assertEqual(by_code["600002"].direction_group, "neutral")
        self.assertEqual(by_code["600004"].direction_group, "unknown")
        self.assertTrue(by_code["600003"].veto)
        self.assertEqual(signal_adapter.auction_expected_direction("neutral"),
                         None)


class TestLegacyIncrementalNotReproducible(unittest.TestCase):
    def test_seed_rejects_old_run_without_start_positions(self):
        from dragon_quant.review_account.experiments import seed_from_run

        with self.assertRaises(ValueError):
            seed_from_run({
                "id": 1, "base_run_id": 9, "base_equity": 20000.0,
                "start_equity": 20000.0, "start_positions": [],
                "start_state_recorded": False,
            })

    def test_ledger_marks_old_run_unknown(self):
        from dragon_quant.review_account.evaluation import period_ledger

        ledger = period_ledger(
            {"id": 1, "base_run_id": 9, "base_equity": 20000.0,
             "start_equity": 20000.0, "start_positions": [],
             "start_state_recorded": False, "run_meta": {}},
            [{"trade_date": "2026-09-01", "total_equity": 20000.0,
              "positions": []}],
            [])
        self.assertEqual(ledger["quality"], "unknown")
        self.assertIsNone(ledger["totals"]["ledger_period_pnl"])


class TestResearchFlagNotExposedToProduction(unittest.TestCase):
    def test_account_create_rejects_research_bypass(self):
        from web_ui.backend import compat

        with self.assertRaises(ValueError):
            compat.create_account_run(
                {"date_from": "2026-09-01", "date_to": "2026-09-02",
                 "strategy_params": {"research_prep_bypass": True}},
                default_source="v2", run_account=lambda **kwargs: {},
                account=None)
