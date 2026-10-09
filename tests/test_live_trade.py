"""tests for dragon_quant.live_trade — 纯信号 buy/sell 命令（复用 review_account 策略与成交）。"""
import copy
import datetime
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from dragon_quant.live_trade.service import _make_config, _print_buy, _print_sell, run_buy, run_sell
from dragon_quant.live_trade.signal_engine import SignalEngine
from dragon_quant.live_trade.trader import LiveTrader
from dragon_quant.review_account.data import historical_events
from dragon_quant.review_account.engine import TradingEngine
from dragon_quant.review_account.market import SHANGHAI, at
from dragon_quant.review_account.models import AccountState, MarketEvent, StrategyConfig
from dragon_quant.storage import db
from tests.test_review_account import CAND, DAY, FakeData, position, row


class TestSignalEngine(unittest.TestCase):
    """SignalEngine 与 TradingEngine 共享策略/成交，但去掉账户/数量/仓位限制，buy-all。"""

    def test_buy_all_not_just_top_one(self):
        cfg = StrategyConfig()
        engine = SignalEngine(cfg)
        cands = [
            {**CAND, "code": "600001", "composite_score": 95, "rank": 1},
            {**CAND, "code": "600002", "composite_score": 60, "rank": 2},
        ]
        rows = {"600001": row(), "600002": row()}
        result = engine.step(MarketEvent(at(DAY, "09:30"), "open", rows, cands))
        # 两只都触发买点 → 都进入 pending（不择优只取第一只）
        self.assertEqual({o["stock_code"] for o in result["pending"]}, {"600001", "600002"})
        # 下一事件全部撮合
        fill_rows = {"600001": row(timestamp=at(DAY, "09:30") + 1),
                     "600002": row(timestamp=at(DAY, "09:30") + 1)}
        result2 = engine.step(MarketEvent(at(DAY, "09:30") + 1, "fill", fill_rows, cands))
        self.assertEqual({b["code"] for b in result2["buys"]}, {"600001", "600002"})

    def test_buy_entry_price_parity_with_backtest(self):
        cfg = StrategyConfig()
        sig = SignalEngine(cfg)
        acc = TradingEngine(cfg, AccountState(100000))
        sig_prices, acc_prices = [], []
        for event in historical_events(DAY, [CAND], {CAND["code"]}, FakeData()):
            event.allow_buy = True
            sr = sig.step(copy.deepcopy(event))
            ar = acc.step(copy.deepcopy(event))
            sig_prices += [b["entry_price"] for b in sr["buys"]]
            acc_prices += [t.price for t in ar["trades"]]
        self.assertEqual(len(sig_prices), 1)
        self.assertEqual(sig_prices, acc_prices)

    def test_sell_exit_price_parity_with_backtest(self):
        cfg = StrategyConfig()
        p = position()  # entry 2026-09-04, price 10
        sig = SignalEngine(cfg, positions=[copy.deepcopy(p)])
        acc = TradingEngine(cfg, AccountState(100000, [copy.deepcopy(p)]))
        # 首日止损：hold_days=1，bar_low 跌破 -3.5%（9.65）
        r = row(phase="bar", timestamp=at(DAY, "09:35"))
        r["bar_low"], r["bar_close"], r["bar_high"] = 9.6, 9.6, 9.7
        e1 = MarketEvent(at(DAY, "09:35"), "bar", {p.code: r})
        e1.allow_buy = False
        s1 = sig.step(copy.deepcopy(e1))
        a1 = acc.step(copy.deepcopy(e1))
        self.assertEqual([o["side"] for o in s1["pending"]], ["SELL"])
        self.assertEqual([o["side"] for o in a1["pending"]], ["SELL"])
        # 下一事件按 execution_price 撮合
        r2 = row(phase="fill", price=9.6, timestamp=at(DAY, "09:35") + 1000)
        e2 = MarketEvent(r2["observed_at"], "fill", {p.code: r2})
        e2.allow_buy = False
        s2 = sig.step(copy.deepcopy(e2))
        a2 = acc.step(copy.deepcopy(e2))
        self.assertEqual(s2["sells"][0]["exit_price"], a2["trades"][0].price)
        self.assertEqual(s2["sells"][0]["reason_code"], a2["trades"][0].reason_code)

    def test_held_codes_block_rebuy(self):
        cfg = StrategyConfig()
        engine = SignalEngine(cfg, held_codes={CAND["code"]})
        result = engine.step(MarketEvent(at(DAY, "09:30"), "open", {CAND["code"]: row()}, [CAND]))
        self.assertEqual(result["pending"], [])
        self.assertEqual(result["details"][0]["reason_code"], "already_holding")

    def test_already_processed_guard(self):
        cfg = StrategyConfig()
        engine = SignalEngine(cfg)
        event = MarketEvent(at(DAY, "09:30"), "open", {CAND["code"]: row()}, [CAND])
        engine.step(event)
        self.assertEqual(engine.step(event)["reason_code"], "already_processed")


class TestPrintBuy(unittest.TestCase):
    """buy 命令输出按 code 去重：未触发候选只列一次。"""

    def test_dedupes_rejected_candidates_by_code(self):
        details = [
            {"code": "600001", "name": "a", "passed": False, "reason_text": "r1"},
            {"code": "600002", "name": "b", "passed": False, "reason_text": "r2"},
            {"code": "600001", "name": "a", "passed": False, "reason_text": "r3"},
        ]
        result = {"buys": [], "details": details, "reason_text": "候选池暂无触发买点的标的"}
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_buy(DAY, result)
        out = buf.getvalue()
        self.assertIn("候选未触发买点（2 只）", out)
        self.assertEqual(out.count("（600001）"), 1)
        self.assertEqual(out.count("（600002）"), 1)


class TestPrintSell(unittest.TestCase):
    """sell 命令输出格式化（卖出 + 继续持有）。"""

    def test_formats_sell_and_held(self):
        result = {
            "sells": [{"code": "600001", "name": "样本", "exit_price": 9.5,
                       "hold_days": 1, "reason_text": "首日止损"}],
            "held": [{"code": "600002", "name": "另一只", "highest_return": 8.5}],
        }
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_sell(DAY, result)
        out = buf.getvalue()
        self.assertIn("卖出 样本（600001）@9.50", out)
        self.assertIn("持有 1 天", out)
        self.assertIn("继续持有 另一只（600002）", out)
        self.assertIn("+8.5%", out)


class TestLiveTraderRun(unittest.TestCase):
    """LiveTrader._run 的日期/时段校验（编排层入口，此前零覆盖）。"""

    def _fake_now(self, hour, minute):
        fake = MagicMock()
        fake.strptime = datetime.datetime.strptime
        fake.now.return_value = datetime.datetime(2026, 10, 9, hour, minute, tzinfo=SHANGHAI)
        return fake

    @staticmethod
    def _trader():
        return LiveTrader(StrategyConfig(), source="v2",
                          data=FakeData(days=["2026-09-04", "2026-09-07",
                                              "2026-09-08", "2026-10-09"]))

    def test_historical_requires_at(self):
        with self.assertRaises(ValueError):
            self._trader().buy(DAY)

    def test_rejects_non_trade_day(self):
        with self.assertRaises(ValueError):
            self._trader().buy("2026-09-06", as_of="10:00")

    def test_before_open_rejected(self):
        with patch("dragon_quant.live_trade.trader.datetime", self._fake_now(9, 0)), \
                self.assertRaises(ValueError):
            self._trader().buy("2026-10-09")

    def test_noon_break_rejected(self):
        with patch("dragon_quant.live_trade.trader.datetime", self._fake_now(12, 0)), \
                self.assertRaises(ValueError):
            self._trader().buy("2026-10-09")

    def test_after_close_rejected(self):
        with patch("dragon_quant.live_trade.trader.datetime", self._fake_now(15, 30)), \
                self.assertRaises(ValueError):
            self._trader().buy("2026-10-09")


class TestServiceRun(unittest.TestCase):
    """service 层 run_buy/run_sell/_make_config 的装配与打印接线。"""

    def test_make_config_merges_strategy_params(self):
        cfg = _make_config("v2", {"min_score": 60})
        self.assertEqual(cfg.source, "v2")
        self.assertEqual(cfg.min_score, 60)
        self.assertEqual(_make_config("v2", None).min_score, 50.0)

    def test_run_buy_wires_trader_and_print(self):
        result = {"buys": [], "details": [], "reason_text": "候选池暂无触发买点的标的"}
        with patch("dragon_quant.live_trade.service.LiveTrader") as Trader:
            Trader.return_value.buy.return_value = result
            buf = io.StringIO()
            with redirect_stdout(buf):
                out = run_buy(DAY, source="v2", as_of="10:00")
        self.assertIs(out, result)
        self.assertIn("买入建议", buf.getvalue())
        Trader.return_value.buy.assert_called_once_with(DAY, "10:00")

    def test_run_sell_wires_trader_and_print(self):
        result = {"sells": [], "held": [], "reason_text": "持仓暂无卖出信号"}
        with patch("dragon_quant.live_trade.service.LiveTrader") as Trader:
            Trader.return_value.sell.return_value = result
            buf = io.StringIO()
            with redirect_stdout(buf):
                out = run_sell(DAY, source="v2", as_of="10:00")
        self.assertIs(out, result)
        self.assertIn("卖出建议", buf.getvalue())
        Trader.return_value.sell.assert_called_once_with(DAY, "10:00")


class TestSignalStorage(unittest.TestCase):
    """buy_sell_signals 表的幂等写入 / before_date 过滤 / 平仓 / 峰值写回。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        self._conn = sqlite3.connect(self._db_path)
        with patch("dragon_quant.storage.db._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            db.init_db()

    def tearDown(self):
        self._conn.close()
        self._tmpdir.cleanup()

    def _connect(self):
        return patch("dragon_quant.storage.db._connect",
                     side_effect=lambda: sqlite3.connect(self._db_path))

    def test_signal_roundtrip_idempotent_and_before_date(self):
        code = "600001"
        with self._connect():
            i1 = db.insert_signal(code, "样本", "2026-09-07", 10.0,
                                  "buy_open_ma5_pullback", "开盘承接", {"a": 1})
            i2 = db.insert_signal(code, "样本", "2026-09-07", 10.0,
                                  "buy_open_ma5_pullback", "开盘承接", {"a": 1})
            self.assertEqual(i1, i2)  # 幂等：同一 code 未平仓不重复记录
            self.assertEqual([s["code"] for s in db.list_open_signals(source="v2")], [code])
            # before_date：只取 entry_date < 目标日的持仓
            self.assertEqual([s["code"] for s in db.list_open_signals(before_date="2026-09-08", source="v2")], [code])
            self.assertEqual(db.list_open_signals(before_date="2026-09-07", source="v2"), [])
            # 峰值写回
            self.assertTrue(db.update_signal_peaks(code, 8.5, 10.85))
            row_sig = db.list_open_signals(source="v2")[0]
            self.assertAlmostEqual(row_sig["highest_return"], 8.5)
            self.assertAlmostEqual(row_sig["highest_price"], 10.85)
            # 平仓后不再出现在未平仓列表
            self.assertTrue(db.close_signal(code, "2026-09-08", 9.5, "hard_stop_loss", "止损", {}, 1))
            self.assertEqual(db.list_open_signals(source="v2"), [])


if __name__ == "__main__":
    unittest.main()
