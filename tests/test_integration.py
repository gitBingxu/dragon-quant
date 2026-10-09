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
from unittest.mock import patch

from dragon_quant.live_trade.trader import LiveTrader
from dragon_quant.models.types import KBar
from dragon_quant.review_account.evaluation import compare_strategies
from dragon_quant.review_account.market import bar_times, validate_bars
from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.service import run_review_account
from dragon_quant.storage import db
from tests.test_review_account import CAND, DAY, FakeData


class IntegrationBase(unittest.TestCase):
    """用临时 SQLite 隔离持久化，令领域模块经 _base 转发走临时库。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        self._conn = sqlite3.connect(self._db_path)
        self._patch = patch("dragon_quant.storage.db._connect",
                            side_effect=lambda: sqlite3.connect(self._db_path))
        self._patch.start()
        db.init_db()

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
        with patch("dragon_quant.storage.db.get_dragons_by_date", return_value=[CAND]):
            result = LiveTrader(StrategyConfig(), source="v2", data=FakeData()).buy(DAY, "10:00")
        self.assertEqual(result["action"], "buy")
        self.assertEqual([b["code"] for b in result["buys"]], [CAND["code"]])
        self.assertEqual([s["code"] for s in db.list_open_signals(source="v2")], [CAND["code"]])

    def test_sell_end_to_end_closes_signal(self):
        db.insert_signal(CAND["code"], CAND["name"], "2026-09-04", 10.0, "buy", "买", {}, "v2")
        result = LiveTrader(StrategyConfig(), source="v2", data=_DropData()).sell(DAY, "10:00")
        self.assertEqual(result["action"], "sell")
        self.assertEqual([s["code"] for s in result["sells"]], [CAND["code"]])
        self.assertEqual(db.list_open_signals(source="v2"), [])


class TestReviewAccountIntegration(IntegrationBase):
    def test_run_review_account_persists_run(self):
        fake = {"initial_cash": 100000.0, "final_equity": 100000.0, "total_return": 0.0,
                "max_drawdown": 0.0, "trade_count": 0, "win_rate": None,
                "snapshots": [], "trades": [], "positions": [], "events": []}
        with patch("dragon_quant.review_account.service.AccountSimulator") as Sim:
            Sim.return_value.run.return_value = fake
            result = run_review_account("2026-09-07", "2026-09-08", source="v2", verbose=False)
        self.assertIsNotNone(result["run_id"])
        runs = db.query_review_account_runs(source="v2")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["date_from"], "2026-09-07")
        self.assertEqual(runs[0]["date_to"], "2026-09-08")


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
