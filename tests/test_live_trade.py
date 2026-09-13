import copy
import json
import unittest
from dataclasses import asdict
from unittest.mock import MagicMock, patch

from dragon_quant.live_trade.service import _account_config
from dragon_quant.live_trade.trader import LiveTrader
from dragon_quant.review_account.data import historical_events
from dragon_quant.review_account.engine import TradingEngine
from dragon_quant.review_account.market import at
from dragon_quant.review_account.models import AccountState, MarketEvent, StrategyConfig
from dragon_quant.storage import db
from tests.test_review_account import CAND, DAY, FakeData, position, row


class TestSharedExecution(unittest.TestCase):
    def setUp(self):
        self.cfg = StrategyConfig()
        self.account = db.ensure_live_account("__unified_test__", 100000,
            strategy_params_json=json.dumps(self.cfg.to_json_dict()), reset=True)

    def tearDown(self):
        conn = db._connect()
        conn.execute("DELETE FROM live_account WHERE id=?", (self.account["id"],))
        conn.commit()
        conn.close()

    def test_live_and_backtest_exact_event_parity_with_restarts(self):
        state = AccountState(100000)
        engine = TradingEngine(self.cfg, state)
        for day in [DAY, "2026-09-08"]:
            for event in historical_events(day, [CAND], {CAND["code"]}, FakeData()):
                expected = engine.step(copy.deepcopy(event))
                trader = LiveTrader(db.get_live_account("__unified_test__"), self.cfg, data=FakeData())
                actual = trader.process_event(copy.deepcopy(event))
                self.assertEqual([asdict(t) for t in actual["trades"]], [asdict(t) for t in expected["trades"]])
                self.assertEqual(db.load_live_engine_state(self.account)[0], state.to_dict())
        self.assertEqual(len(db.list_live_trades(self.account["id"])), 1)

    def test_sell_parity_partial_then_full_and_atomic_ledger(self):
        p = position()
        db.add_live_position(self.account["id"], {**asdict(p), "entry_signal_json": "{}"})
        state = AccountState(100000, [copy.deepcopy(p)])
        engine = TradingEngine(self.cfg, state)
        r = row(phase="late", price=11, timestamp=at(DAY, "14:55"))
        r.update(limit_up=11, prev_close=10, bar_timestamp=at(DAY, "14:55"))
        events = [MarketEvent(at(DAY, "14:55"), "late", {p.code: r}, allow_buy=False)]
        r2 = row(phase="fill", price=10.99, timestamp=at(DAY, "14:55") + 1000)
        events.append(MarketEvent(r2["observed_at"], "fill", {p.code: r2}, allow_buy=False))
        r3 = row(day="2026-09-08", price=9.3)
        events.append(MarketEvent(r3["observed_at"], "open", {p.code: r3}, allow_buy=False))
        r4 = row(day="2026-09-08", price=9.3, timestamp=r3["observed_at"] + 1000)
        events.append(MarketEvent(r4["observed_at"], "fill", {p.code: r4}, allow_buy=False))
        for event in events:
            expected = engine.step(copy.deepcopy(event))
            actual = LiveTrader(db.get_live_account("__unified_test__"), self.cfg).process_event(copy.deepcopy(event), "sell")
            self.assertEqual([asdict(t) for t in actual["trades"]], [asdict(t) for t in expected["trades"]])
            self.assertEqual(db.load_live_engine_state(self.account)[0], state.to_dict())
        trades = db.list_live_trades(self.account["id"])
        self.assertEqual([t["qty"] for t in trades], [500, 500])
        self.assertEqual(db.list_live_positions(self.account["id"]), [])
        self.assertAlmostEqual(db.get_live_account("__unified_test__")["cash"], state.cash)

    def test_repeated_command_does_not_duplicate(self):
        trader = LiveTrader(self.account, self.cfg)
        event = MarketEvent(at(DAY, "09:30"), "open", {CAND["code"]: row()}, [CAND])
        trader.process_event(event)
        self.assertEqual(trader.process_event(event)["reason_code"], "already_processed")
        self.assertEqual(len(db.load_live_engine_state(self.account)[0]["pending"]), 1)

    def test_concurrent_stale_revision_rejected(self):
        state = AccountState(100000).to_dict()
        result = {"positions": [], "trades": []}
        db.save_live_engine_step(self.account["id"], 0, state, result, "buy")
        with self.assertRaisesRegex(ValueError, "另一命令"):
            db.save_live_engine_step(self.account["id"], 0, {**state, "cash": 0}, result, "buy")
        self.assertEqual(db.get_live_account("__unified_test__")["cash"], 100000)

    def test_account_changes_rollback_on_error(self):
        state = AccountState(0).to_dict()
        with self.assertRaises(TypeError):
            db.save_live_engine_step(self.account["id"], 0, state, {"positions": [], "trades": [object()]}, "buy")
        self.assertEqual(db.get_live_account("__unified_test__")["cash"], 100000)
        self.assertEqual(db.load_live_engine_state(self.account)[1], 0)

    def test_config_saved_and_change_requires_new_account(self):
        cfg = _account_config(self.account, 123, "v2", None)
        self.assertEqual(cfg, self.cfg)
        with self.assertRaises(ValueError):
            _account_config(self.account, 100000, "v2", {"min_score": 99})

    def test_historical_command_never_fetches_current_quote(self):
        quotes = MagicMock()
        trader = LiveTrader(self.account, self.cfg, quote_provider=quotes, data=FakeData())
        with patch("dragon_quant.live_trade.trader.db.get_dragons_by_date", return_value=[CAND]):
            result = trader.buy(DAY, "10:00")
        self.assertEqual(result["action"], "buy")
        quotes.get_quote.assert_not_called()
        with patch("dragon_quant.live_trade.trader.db.get_dragons_by_date", return_value=[CAND]):
            result = trader.buy(DAY, "10:00")
        self.assertEqual(result["trades"], [])

    def test_current_quote_is_not_reused_before_signal(self):
        from datetime import datetime
        from dragon_quant.models.types import Quote
        from dragon_quant.review_account.market import SHANGHAI
        qp = MagicMock()
        ts = at(DAY, "09:30:10")
        q = Quote(CAND["code"], "样本", 10, 10, 10, 10.1, 9.9, 0, 0, 12, 0,
                  10000, 100000, 0, 0, 0, 0, 11, 9, 10, timestamp=ts)
        qp.get_quote.return_value = q
        trader = LiveTrader(self.account, self.cfg, quote_provider=qp, data=FakeData())
        with patch("dragon_quant.live_trade.trader.datetime") as clock, \
             patch("dragon_quant.live_trade.trader.db.get_dragons_by_date", return_value=[CAND]):
            clock.now.return_value = datetime.fromtimestamp(ts / 1000, SHANGHAI)
            first = trader.buy(DAY)
            self.assertEqual(first["trades"], [])
            clock.now.return_value = datetime.fromtimestamp(ts / 1000 + 1, SHANGHAI)
            self.assertEqual(trader.buy(DAY)["trades"], [])
            q.timestamp += 2000
            clock.now.return_value = datetime.fromtimestamp(ts / 1000 + 2, SHANGHAI)
            self.assertEqual(trader.buy(DAY)["action"], "buy")

    def test_historical_date_requires_explicit_time(self):
        trader = LiveTrader(self.account, self.cfg, data=FakeData())
        with patch("dragon_quant.live_trade.trader.db.get_dragons_by_date", return_value=[]), self.assertRaisesRegex(ValueError, "--at"):
            trader.buy(DAY)


if __name__ == "__main__":
    unittest.main()
