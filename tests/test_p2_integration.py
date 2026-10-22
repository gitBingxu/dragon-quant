"""P2 决策接入集成：review-account 等价性、A/B 门禁、Sharpe 与行业暴露。

零网络、零真实 DB：全部临时 ``assets.db``；行情用最小 FakeData/FakeRows。
"""

import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from dragon_quant.models.types import KBar
from dragon_quant.factors import storage as fstore
from dragon_quant.factors.decision import default_snapshot, save_default_model_snapshot
from dragon_quant.factors.types import FactorValue
from dragon_quant.review_account.evaluation import (
    compare_strategies, industry_exposure, metrics, sharpe_ratio,
)
from dragon_quant.review_account.market import (
    at, bar_times, build_row, collect_candidates_ex, validate_bars,
)
from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.simulator import AccountSimulator

DAY = "2026-09-07"
CAND = {"code": "600001", "name": "样本", "rank": 1, "composite_score": 80,
        "is_true_dragon": True, "trade_date": "2026-09-04"}


def history(day=DAY, price=10):
    end = date.fromisoformat(day)
    days = [end - timedelta(days=n) for n in range(50, 0, -1)
            if (end - timedelta(days=n)).weekday() < 5]
    return [KBar(at(d.isoformat(), "15:00"), 1_000_000, price, price + .2,
                 price - .2, price, 0, 0, 12, 1_000_000_000) for d in days]


def bars(day=DAY, price=10, turnover=1.0):
    return [KBar(t, 10000, price, price + .05, price - .05, price, 0, 0,
                 turnover, 100000) for t in bar_times(day)]


def row(day=DAY, phase="open", price=10, timestamp=None, known=None):
    ts = timestamp or at(day, "09:30")
    result = build_row(history(day), day, price, known or [], ts,
                       execution_price=price)
    result["phase"] = phase
    return result


class FakeData:
    def __init__(self, days=None):
        self.days = days or ["2026-09-04", DAY, "2026-09-08"]

    def calendar(self, start, end, include_today=False):
        return [d for d in self.days if start <= d <= end]

    def daily(self, code, start, refresh=False):
        return history("2026-09-09")

    def intraday(self, code, day, until=None):
        return validate_bars(bars(day), day, until)

    def try_intraday(self, code, day, until=None):
        try:
            return self.intraday(code, day, until)
        except Exception:
            return None


class FlatData:
    """无行情、无候选：只验证 compare_strategies 的编排与门禁。"""

    def __init__(self, days):
        self.days = days

    def calendar(self, start, end, include_today=False):
        return [d for d in self.days if start <= d <= end]

    def daily(self, code, start, refresh=False):
        return []

    def intraday(self, code, day, until=None):
        return []

    def try_intraday(self, code, day, until=None):
        return None


class TempDbBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "assets.db")
        self._patch = patch("dragon_quant.storage.assets_db._connect",
                            side_effect=self._connect)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def _connect(self, *args, create=True, path=None, **kwargs):
        target = path or self._db_path
        if not create and not Path(target).exists():
            return None
        return sqlite3.connect(target)

    @staticmethod
    def _summary(result):
        def trade_fields(trade):
            payload = asdict(trade)
            # signal 是诊断元数据（priority_key / entry_priority 含因子注解），
            # 不是成交字段；决策是否改变由成交本身（code/price/qty/reason）体现。
            payload.pop("signal", None)
            return payload

        return {
            "total_return": result["total_return"],
            "final_equity": result["final_equity"],
            "max_drawdown": result["max_drawdown"],
            "trade_count": result["trade_count"],
            "trades": [trade_fields(trade) for trade in result["trades"]],
            "positions": [asdict(position) for position in result["positions"]],
            "account_positions": [
                {key: value for key, value in position.items()
                 if key != "entry_signal"}          # 诊断元数据，非成交字段
                for position in result["account_state"]["positions"]
            ],
        }


class TestReviewAccountEquivalence(TempDbBase):
    def _run(self, cfg, loader):
        with patch("dragon_quant.review_account.simulator.db.get_dragons_by_date",
                   side_effect=lambda *args, **kwargs: [dict(loader)]):
            return AccountSimulator(cfg, data=FakeData()).run(DAY, DAY)

    def test_switch_off_equals_baseline_even_with_residual_annotation(self):
        baseline = self._run(StrategyConfig(), CAND)
        residual = {**CAND, "entry_priority": {"factor": {
            "mode": "on", "score": 100.0, "missing": False}}}
        off = self._run(StrategyConfig(factor_ranking_enabled=False), residual)
        self.assertTrue(baseline["trades"], "fixture 必须产生至少一笔买入")
        self.assertEqual(self._summary(off), self._summary(baseline))

    def test_empty_factor_values_degrade_and_equal_baseline(self):
        baseline = self._run(StrategyConfig(), CAND)
        save_default_model_snapshot()          # 快照存在，但没有任何 factor_values
        on = self._run(StrategyConfig(factor_ranking_enabled=True), CAND)
        self.assertEqual(self._summary(on), self._summary(baseline))
        # 同条件下的注解必须是整日降级（覆盖率 0 < 0.6）
        with patch("dragon_quant.review_account.simulator.db.get_dragons_by_date",
                   side_effect=lambda *args, **kwargs: [dict(CAND)]):
            pool, ctx = collect_candidates_ex(
                FakeData().calendar("2026-07-01", DAY), DAY,
                StrategyConfig(factor_ranking_enabled=True),
                lambda *args, **kwargs: [dict(CAND)])
        self.assertEqual(ctx["factor_mode"], "degraded")
        self.assertEqual(pool[0]["entry_priority"]["factor"]["mode"], "degraded")
        self.assertTrue(ctx["factor_degraded"])

    def test_switch_off_reads_no_factor_storage(self):
        def boom(*args, **kwargs):
            raise AssertionError("factor_loader must not be called when off")

        pool, ctx = collect_candidates_ex(
            FakeData().calendar("2026-07-01", DAY), DAY, StrategyConfig(),
            lambda *args, **kwargs: [dict(CAND)], factor_loader=boom)
        self.assertEqual(ctx["factor_mode"], "off")
        self.assertIsNone(ctx["factor_coverage"])
        self.assertNotIn("factor", pool[0]["entry_priority"])


class TestCompareStrategiesGate(TempDbBase):
    def _days(self, count=40):
        start = date(2026, 1, 1)
        return [(start + timedelta(days=index)).isoformat()
                for index in range(count)]

    def _seed_all_days(self, days):
        snapshot = default_snapshot(names=["five_dim_drive"])
        fstore.save_model_snapshot(
            snapshot["model_name"], snapshot["model_version"],
            params=snapshot["params"],
            factor_versions=snapshot["factor_versions"])
        version = snapshot["factor_versions"]["five_dim_drive"]
        for day in days:
            fstore.replace_factor_day(day, "five_dim_drive", version, [
                FactorValue(trade_date=day, code="600001",
                            factor_name="five_dim_drive", factor_version=version,
                            value=1.0, available_at=f"{day}T15:05:00",
                            source="scan")])

    def _run(self, days):
        with patch("dragon_quant.review_account.simulator.db.get_dragons_by_date",
                   return_value=[]):
            return compare_strategies(
                days[0], days[-1], FlatData(days), StrategyConfig(),
                variant_grid={"factor_on": {"factor_ranking_enabled": True}})

    def test_low_build_completion_returns_insufficient_data(self):
        days = self._days()
        report = self._run(days)
        self.assertEqual(report["status"], "insufficient_data")
        self.assertIn("factor_build_completion", report["reason"])
        self.assertEqual(report["factor_build_completion"], 0.0)
        self.assertEqual(report["variants"], {})       # 门禁在跑回测之前
        self.assertEqual(report["trial_count"], 5)

    def test_full_completion_runs_factor_variant_with_sharpe(self):
        days = self._days()
        self._seed_all_days(days)
        report = self._run(days)
        self.assertEqual(report["factor_build_completion"], 1.0)
        self.assertEqual(report["trial_count"], 5)
        self.assertIn("sharpe", report["variants"]["factor_on"]["train"])
        self.assertIn("sharpe", report["variants"]["baseline"]["train"])


class TestPortfolioMetrics(unittest.TestCase):
    def test_sharpe_edge_cases(self):
        self.assertIsNone(sharpe_ratio([]))
        self.assertIsNone(sharpe_ratio([100.0]))
        self.assertIsNone(sharpe_ratio([100.0, 110.0, 121.0]))   # std=0
        self.assertIsNone(sharpe_ratio([100.0, float("nan"), 90.0]))
        value = sharpe_ratio([100.0, 105.0, 90.0])
        self.assertIsNotNone(value)
        self.assertTrue(value < 0)
        self.assertGreater(sharpe_ratio([100.0, 101.0, 102.5]), 0)

    def test_metrics_only_adds_sharpe_key(self):
        result = (AccountSimulator(StrategyConfig(), data=FlatData(
            ["2026-01-05", "2026-01-06"])).run("2026-01-05", "2026-01-06"))
        payload = metrics(result)
        self.assertIn("sharpe", payload)
        self.assertIsNone(payload["sharpe"])
        for key in ("total_return", "max_drawdown", "trade_count",
                    "closed_count", "win_rate", "fees", "turnover"):
            self.assertIn(key, payload)

    def test_industry_exposure_uses_entry_date_snapshot(self):
        positions = [
            {"code": "A", "entry_date": "2026-10-19", "market_value": 100.0},
            {"code": "B", "entry_date": "2026-10-19", "market_value": 300.0},
            {"code": "C", "entry_date": "2026-10-20", "market_value": 100.0},
            {"code": "D", "entry_date": "2026-10-20", "market_value": 0.0},
        ]
        maps = {
            "2026-10-19": {
                "A": {"name": "银行", "category": "hy"},
                "B": {"name": "券商", "category": "gn"},       # 概念 → unattributed
            },
            "2026-10-20": {
                "C": {"name": "银行", "category": "hy"},
            },
        }
        calls = []

        def loader(entry_date):
            calls.append(entry_date)
            return maps[entry_date]

        payload = industry_exposure(positions, sector_loader=loader)
        self.assertEqual(calls, ["2026-10-19", "2026-10-20"])
        self.assertEqual(payload["basis"], "entry_date_snapshot")
        self.assertEqual(payload["by_industry"],
                         [{"name": "银行", "weight": 0.4, "count": 2}])
        self.assertAlmostEqual(payload["coverage"], 0.4)
        self.assertAlmostEqual(payload["unattributed_ratio"], 0.6)
        self.assertAlmostEqual(payload["hhi"], 0.16)
        self.assertEqual(payload["unattributed_reasons"],
                         {"non_hy_primary": 1, "no_snapshot": 1})

    def test_industry_exposure_empty_pool_does_not_fake_zero_coverage(self):
        payload = industry_exposure([], sector_map={})
        self.assertIsNone(payload["coverage"])
        self.assertIsNone(payload["unattributed_ratio"])
        self.assertIsNone(payload["hhi"])
        self.assertEqual(payload["by_industry"], [])


class TestFactorConfigValidation(unittest.TestCase):
    def test_defaults_off(self):
        cfg = StrategyConfig()
        self.assertFalse(cfg.factor_ranking_enabled)
        self.assertEqual(cfg.factor_model_name, "equal_rank_v1")
        self.assertEqual(cfg.factor_model_version, "")
        self.assertEqual(cfg.factor_min_coverage, 0.6)
        self.assertFalse(StrategyConfig.from_dict({}).factor_ranking_enabled)

    def test_invalid_values_rejected(self):
        with self.assertRaises(ValueError):
            StrategyConfig.from_dict({"factor_min_coverage": 0})
        with self.assertRaises(ValueError):
            StrategyConfig.from_dict({"factor_min_coverage": 1.5})
        with self.assertRaises(ValueError):
            StrategyConfig.from_dict({"factor_model_name": ""})
        with self.assertRaises(ValueError):
            StrategyConfig.from_dict({"factor_model_version": 1})
        with self.assertRaises(ValueError):
            StrategyConfig.from_dict({"factor_ranking_enabled": "yes"})
        cfg = StrategyConfig.from_dict({"factor_ranking_enabled": True,
                                        "factor_min_coverage": 0.9})
        self.assertTrue(cfg.factor_ranking_enabled)
        self.assertEqual(cfg.factor_min_coverage, 0.9)


if __name__ == "__main__":
    unittest.main()
