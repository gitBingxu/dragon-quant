"""端到端集成测试：buy/sell 与账户级 review 的编排层（跨模块 + 持久化）。

单测覆盖了引擎内层（SignalEngine/TradingEngine/策略/成交）的每一步，但
「CLI 入口 → 编排回放 → 信号入库 → 输出」这条完整链路此前没有任何测试，
导致 buy 命令去重这类 bug 只有在实盘使用后才被发现。本文件补齐这条链路的
集成测试，复用 test_review_account.FakeData 与 db._connect 的临时库 patch。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dragon_quant.review_account.live_trade.trader import LiveTrader
from dragon_quant.infra.models import KBar
from dragon_quant.review_account.evaluation import TOTAL_ROW_CODE, compare_strategies, entry_reason_stats
from dragon_quant.review_account.market import bar_times, validate_bars
from dragon_quant.review_account.models import ClosedPosition, StrategyConfig
from dragon_quant.review_account.service import run_review_account
from dragon_quant.review_account.simulator import AccountSimulator
from dragon_quant.infra.storage.connection import init_db
from dragon_quant.review_account import storage as review_storage
from dragon_quant.review_account.live_trade import storage as signal_storage
from dragon_quant.tests.test_review_account import CAND, DAY, FakeData


class IntegrationBase(unittest.TestCase):
    """用临时 SQLite 隔离持久化，令领域模块经 _base 转发走临时库。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        self._conn = sqlite3.connect(self._db_path)
        self._patch = patch("dragon_quant.infra.storage.connection._connect",
                            side_effect=lambda: sqlite3.connect(self._db_path))
        self._patch.start()
        init_db()

    def tearDown(self):
        self._patch.stop()
        self._conn.close()
        self._tmpdir.cleanup()


class _DropData(FakeData):
    """当日5分钟K低点跌破首日止损线，触发卖出信号。"""

    def intraday(self, code, day, until=None):
        bars = [KBar(t, 10000, 10.0, 10.0, 9.5, 10.0, 0, 0, 1.0, 100000)
                for t in bar_times(day)]
        return validate_bars(bars, day, until)


class TestLiveTradeIntegration(IntegrationBase):
    def test_buy_end_to_end_records_signal(self):
        with patch("dragon_quant.infra.assets.dragons_mirror.get_dragons_by_date", return_value=[CAND]):
            result = LiveTrader(StrategyConfig(), source="v2", data=FakeData()).buy(DAY, "10:00")
        self.assertEqual(result["action"], "buy")
        self.assertEqual([b["code"] for b in result["buys"]], [CAND["code"]])
        self.assertEqual([s["code"] for s in signal_storage.list_open_signals(source="v2")], [CAND["code"]])

    def test_sell_end_to_end_closes_signal(self):
        signal_storage.insert_signal(CAND["code"], CAND["name"], "2026-09-04", 10.0, "buy", "买", {}, "v2")
        result = LiveTrader(StrategyConfig(), source="v2", data=_DropData()).sell(DAY, "10:00")
        self.assertEqual(result["action"], "sell")
        self.assertEqual([s["code"] for s in result["sells"]], [CAND["code"]])
        self.assertEqual(signal_storage.list_open_signals(source="v2"), [])


class TestReviewAccountIntegration(IntegrationBase):
    def test_run_review_account_persists_run(self):
        fake = {"initial_cash": 100000.0, "final_equity": 100000.0, "total_return": 0.0,
                "max_drawdown": 0.0, "trade_count": 0, "win_rate": None,
                "snapshots": [], "trades": [], "positions": [], "events": []}
        with patch("dragon_quant.review_account.service.AccountSimulator") as Sim:
            Sim.return_value.run.return_value = fake
            result = run_review_account("2026-09-07", "2026-09-08", source="v2", verbose=False)
        self.assertIsNotNone(result["run_id"])
        runs = review_storage.query_review_account_runs(source="v2")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["date_from"], "2026-09-07")
        self.assertEqual(runs[0]["date_to"], "2026-09-08")

    def test_service_annotates_post_exit_fields_before_persist(self):
        closed = ClosedPosition(
            "600001", "样本", "2026-09-04", 10.0, 1000, "buy_open_ma5_pullback", {},
            "2026-09-04", 10.0, "trailing_take_profit", {}, 10.0, 0,
        )
        fake = {"initial_cash": 100000.0, "final_equity": 101000.0, "total_return": 1.0,
                "max_drawdown": 0.0, "trade_count": 2, "win_rate": 100.0,
                "snapshots": [], "trades": [], "positions": [closed], "events": []}
        sim = MagicMock()
        sim.run.return_value = fake
        sim.data = FakeData()   # daily() 返回截至 2026-09-08 的日K
        with patch("dragon_quant.review_account.service.AccountSimulator", return_value=sim):
            result = run_review_account("2026-09-04", "2026-09-08", source="v2", verbose=False)
        rows = review_storage.query_review_account_positions(result["run_id"])
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["post_exit_max_return"], 2.0)
        self.assertAlmostEqual(rows[0]["post_exit_max_drawdown"], -2.0)
        self.assertEqual(rows[0]["post_exit_window_days"], 2)

    def test_entry_stats_win_rate_matches_simulator_result(self):
        with patch("dragon_quant.infra.assets.dragons_mirror.get_dragons_by_date", return_value=[CAND]):
            result = AccountSimulator(StrategyConfig(), data=_DropData()).run(
                "2026-09-04", "2026-09-08")
        stats = entry_reason_stats([p.__dict__ for p in result["positions"]],
                                   result["initial_cash"])
        # run() 保持纯 as-of：注解字段在 run 结束时仍为默认值，由 service 补算。
        for p in result["positions"]:
            self.assertIsNone(p.post_exit_max_return)
            self.assertIsNone(p.post_exit_max_drawdown)
            self.assertEqual(p.post_exit_window_days, 0)
        total = stats["rows"][-1]
        self.assertEqual(total["reason_code"], TOTAL_ROW_CODE)
        self.assertEqual(total["trades"], len(result["positions"]))
        self.assertAlmostEqual(total["win_rate"], result["win_rate"])
        # 注解不得改变交易结果（golden 值取自改动前的真实回测）
        self.assertEqual(result["trade_count"], 2)
        self.assertAlmostEqual(result["final_equity"], 99495.199)
        self.assertEqual(result["win_rate"], 0.0)

    @staticmethod
    def _web_client(account):
        from fastapi.testclient import TestClient

        from web_ui.backend import deps
        from web_ui.backend.app import create_app
        from web_ui.backend.settings import DashboardSettings

        app = create_app(DashboardSettings())
        app.dependency_overrides[deps.account_repository] = lambda: account
        return TestClient(app, base_url="http://127.0.0.1")

    def test_positions_api_includes_stats_and_nullable_old_columns(self):
        fake_db = MagicMock()
        fake_db.query_review_account_positions.return_value = [{
            "code": "600001", "name": "样本", "entry_date": "2026-09-04",
            "entry_price": 10.0, "qty": 1000,
            "entry_reason_code": "buy_open_ma5_pullback",
            "entry_signal": {}, "exit_date": "2026-09-08", "exit_price": 10.5,
            "exit_reason_code": "trailing_take_profit", "exit_signal": {},
            "realized_return": 5.0, "hold_days": 2, "status": "closed",
            "post_exit_max_return": None, "post_exit_max_drawdown": None,
            "post_exit_window_days": None,
        }]
        fake_db.get_review_account_run.return_value = {"initial_cash": 100000.0}
        client = self._web_client(fake_db)
        response = client.get("/api/account/positions?run_id=1")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["data"]), 1)
        self.assertIsNone(payload["data"][0]["post_exit_max_return"])
        stats = payload["stats"]
        self.assertEqual(stats["initial_cash"], 100000.0)
        self.assertEqual(stats["total_trades"], 1)
        self.assertEqual(len(stats["rows"]), 5)   # 四类 + 合计
        self.assertEqual(stats["rows"][0]["reason_code"], "buy_open_ma5_pullback")
        self.assertEqual(stats["rows"][0]["trades"], 1)
        self.assertAlmostEqual(stats["rows"][-1]["win_rate"], 100.0)

    def test_positions_api_handles_missing_run(self):
        fake_db = MagicMock()
        fake_db.query_review_account_positions.return_value = []
        fake_db.get_review_account_run.return_value = None
        client = self._web_client(fake_db)
        response = client.get("/api/account/positions?run_id=999")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["data"], [])
        self.assertEqual(payload["stats"]["initial_cash"], 0.0)
        self.assertEqual(payload["stats"]["total_trades"], 0)
        self.assertEqual(len(payload["stats"]["rows"]), 5)
        for row in payload["stats"]["rows"]:
            self.assertEqual(row["trades"], 0)
            self.assertIsNone(row["contribution"])


class TestEvaluationIntegration(IntegrationBase):
    def _days(self, n):
        return [f"2026-01-{d:02d}" for d in range(1, n + 1)]

    @staticmethod
    def _skeleton(closed=0):
        pos = [SimpleNamespace(status="closed", realized_return=5.0) for _ in range(closed)]
        trades = [SimpleNamespace(side="SELL", realized_pnl=100.0, fee=5.0, amount=10000.0)
                  for _ in range(closed)]
        return {"total_return": 10.0, "max_drawdown": -5.0, "trade_count": closed,
                "win_rate": 60.0, "positions": pos, "trades": trades,
                "account_state": {"positions": []}, "initial_cash": 100000.0}

    def test_insufficient_evidence_when_no_variant_qualifies(self):
        data = FakeData(days=self._days(31))
        with patch("dragon_quant.review_account.evaluation.AccountSimulator") as Sim:
            Sim.return_value.run.return_value = self._skeleton(closed=0)
            result = compare_strategies("2026-01-01", "2026-01-31", data)
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_evaluated_when_baseline_qualifies(self):
        data = FakeData(days=self._days(31))
        with patch("dragon_quant.review_account.evaluation.AccountSimulator") as Sim:
            Sim.return_value.run.return_value = self._skeleton(closed=10)
            result = compare_strategies("2026-01-01", "2026-01-31", data)
        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(result["selected"], "baseline")
        self.assertIn("test", result["variants"]["baseline"])
        self.assertIn("stress_test", result["variants"]["baseline"])
        self.assertFalse(result["passes_holdout"])


if __name__ == "__main__":
    unittest.main()
