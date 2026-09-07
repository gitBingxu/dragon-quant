"""tests for live_trade buy/sell commands (实盘辅助交易)."""

import datetime as dt
import unittest
from unittest.mock import MagicMock, patch

from dragon_quant.live_trade.row_builder import build_buy_row, build_sell_row
from dragon_quant.live_trade.trader import LiveTrader
from dragon_quant.models.types import KBar, Quote
from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.storage import db

TEST_ACCOUNT = "__test_live_trade__"


def _kbar(date: str, open_: float, close: float, high: float, low: float,
          pct: float = 0.0, volume: float = 1_000_000, turnover: float = 10.0,
          amount: float = 1_000_000_000) -> KBar:
    ts = int(dt.datetime.strptime(date, "%Y-%m-%d").timestamp() * 1000)
    return KBar(timestamp=ts, volume=volume, open=open_, high=high, low=low,
                close=close, chg=close - open_, pct=pct, turnover=turnover, amount=amount)


def _quote(code="000001", name="样本", price=10.0, prev_close=10.0, open_px=10.0,
           high=10.5, low=9.8, turnover_rate=12.0, amount=1_000_000_000,
           volume=1_000_000, limit_up=0.0) -> Quote:
    pct = (price / prev_close - 1) * 100 if prev_close else 0.0
    return Quote(
        code=code, name=name, price=price, prev_close=prev_close, open_px=open_px,
        high=high, low=low, pct=pct, chg=price - prev_close, turnover_rate=turnover_rate,
        amplitude=0.0, volume=volume, amount=amount, market_cap=0.0, float_market_cap=0.0,
        volume_ratio=0.0, pe=0.0, limit_up=limit_up or round(prev_close * 1.1, 2),
        limit_down=0.0, avg_price=price,
    )


def _hist(prefix_close=10.0):
    """一段平稳历史日 K（截至 2026-05-21），MA5≈10。"""
    return [
        _kbar("2026-05-15", 10, 10, 10.2, 9.9),
        _kbar("2026-05-18", 10, 10, 10.2, 9.9),
        _kbar("2026-05-19", 10, 10, 10.2, 9.9),
        _kbar("2026-05-20", 10, 10, 10.2, 9.9),
        _kbar("2026-05-21", 10, 10, 10.3, 9.9),
    ]


class TestRowBuilder(unittest.TestCase):
    def test_build_buy_row_open_gap_and_prev_high(self):
        built = build_buy_row(_hist(), _quote(open_px=10.1, prev_close=10.0), "2026-05-22")
        self.assertIsNotNone(built)
        row = built["row"]
        self.assertAlmostEqual(row["open"], 10.1)
        self.assertAlmostEqual(row["open_gap_pct"], 1.0)
        self.assertAlmostEqual(row["prev_high"], 10.3)
        self.assertFalse(row["is_one_word_board"])

    def test_build_buy_row_none_when_history_missing(self):
        self.assertIsNone(build_buy_row([], _quote(), "2026-05-22"))

    def test_build_sell_row_has_today_ma5_and_volume_change(self):
        q = _quote(open_px=10.4, price=10.6, high=10.8, low=10.3,
                   prev_close=10.0, volume=2_000_000)
        built = build_sell_row(_hist(), q, "2026-05-22")
        self.assertIsNotNone(built)
        row = built["row"]
        self.assertEqual(row["date"], "2026-05-22")
        self.assertAlmostEqual(row["close"], 10.6)
        self.assertIsNotNone(row["ma5"])
        # 今日量 2e6 vs 上日 1e6 → +100%
        self.assertAlmostEqual(row["prev_volume"], 1_000_000)


class TestLiveTraderBuy(unittest.TestCase):
    def setUp(self):
        db.ensure_live_account(TEST_ACCOUNT, 100000, reset=True)
        self.account = db.get_live_account(TEST_ACCOUNT)

    def tearDown(self):
        conn = db._connect()
        conn.execute("DELETE FROM live_account WHERE name = ?", (TEST_ACCOUNT,))
        conn.commit()
        conn.close()

    def _trader(self, cfg, quote, klines=None):
        qp = MagicMock()
        qp.get_quote.return_value = quote
        kp = MagicMock()
        kp.get_kline.return_value = klines if klines is not None else _hist()
        trader = LiveTrader(self.account, cfg, quote_provider=qp, kline_provider=kp)
        trader._lookback_pool_dates = lambda td: ["2026-05-21"]
        return trader

    def test_buy_hits_open_ma5_pullback(self):
        cfg = StrategyConfig(min_amount=200_000_000, min_turnover=5)
        cand = {"code": "000001", "name": "样本", "rank": 1, "composite_score": 80.0,
                "turnover_rate": 12.0, "amount": 1_000_000_000, "is_true_dragon": True}
        trader = self._trader(cfg, _quote(open_px=10.1, prev_close=10.0))
        with patch("dragon_quant.live_trade.trader.db.get_dragons_by_date",
                   return_value=[cand]):
            result = trader.buy("2026-05-22")
        self.assertEqual(result["action"], "buy")
        self.assertEqual(result["reason_code"], "buy_open_ma5_pullback")
        positions = db.list_live_positions(self.account["id"], status="open")
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["code"], "000001")
        self.assertLess(db.get_live_account(TEST_ACCOUNT)["cash"], 100000)

    def test_buy_idle_when_pool_empty(self):
        cfg = StrategyConfig()
        trader = self._trader(cfg, _quote())
        with patch("dragon_quant.live_trade.trader.db.get_dragons_by_date",
                   return_value=[]):
            result = trader.buy("2026-05-22")
        self.assertEqual(result["action"], "idle")
        self.assertEqual(result["reason_code"], "empty_pool")


class TestLiveTraderSell(unittest.TestCase):
    def setUp(self):
        db.ensure_live_account(TEST_ACCOUNT, 100000, reset=True)
        self.account = db.get_live_account(TEST_ACCOUNT)

    def tearDown(self):
        conn = db._connect()
        conn.execute("DELETE FROM live_account WHERE name = ?", (TEST_ACCOUNT,))
        conn.commit()
        conn.close()

    def _seed_position(self, entry_date="2026-05-21", entry_price=10.0, qty=1000):
        db.add_live_position(self.account["id"], {
            "code": "000001", "name": "样本", "qty": qty, "entry_date": entry_date,
            "entry_price": entry_price, "cost": entry_price * qty,
            "entry_reason_code": "buy_open_ma5_pullback", "entry_reason_text": "buy",
            "initial_qty": qty, "initial_cost": entry_price * qty,
        })

    def _trader(self, cfg, quote, klines=None):
        qp = MagicMock()
        qp.get_quote.return_value = quote
        kp = MagicMock()
        kp.get_kline.return_value = klines if klines is not None else _hist()
        return LiveTrader(self.account, cfg, quote_provider=qp, kline_provider=kp)

    def test_t_plus_1_blocks_same_day_sell(self):
        cfg = StrategyConfig()
        self._seed_position(entry_date="2026-05-22")
        trader = self._trader(cfg, _quote(price=9.0, open_px=10.0, high=10.0, low=8.9,
                                          prev_close=10.0))
        result = trader.sell("2026-05-22")
        self.assertEqual(result["results"][0]["reason_code"], "t_plus_1")
        # 未卖出：持仓仍 open
        self.assertEqual(len(db.list_live_positions(self.account["id"], "open")), 1)

    def test_sell_first_day_stop_loss(self):
        cfg = StrategyConfig(first_day_stop_loss_pct=-3.5, stop_loss_pct=-5.0)
        self._seed_position(entry_date="2026-05-21", entry_price=10.0)
        # 次日（hold_days==1）最低 9.6 → -4%，触发首日紧止损
        q = _quote(price=9.7, open_px=9.9, high=9.95, low=9.6, prev_close=10.0)
        trader = self._trader(cfg, q)
        result = trader.sell("2026-05-22")
        actions = [r for r in result["results"] if r["action"] == "sell"]
        self.assertEqual(actions[0]["reason_code"], "first_day_stop_loss")
        self.assertEqual(len(db.list_live_positions(self.account["id"], "open")), 0)

    def test_sell_holds_within_tolerance(self):
        cfg = StrategyConfig(weak_close_tolerance_pct=1.0, stop_loss_pct=-5.0,
                             first_day_stop_loss_pct=-3.5)
        self._seed_position(entry_date="2026-05-20", entry_price=9.5)
        # 历史收盘偏低使 MA5 < 今日收盘，确保未跌破 MA5
        low_hist = [
            _kbar("2026-05-15", 9.5, 9.6, 9.7, 9.4),
            _kbar("2026-05-18", 9.6, 9.6, 9.7, 9.5),
            _kbar("2026-05-19", 9.6, 9.6, 9.7, 9.5),
            _kbar("2026-05-20", 9.6, 9.6, 9.7, 9.5),
            _kbar("2026-05-21", 9.6, 9.6, 9.7, 9.5),
        ]
        # hold_days>=2，收盘较开盘仅低 0.5% 且站上 MA5/昨收 → 继续持有
        q = _quote(price=9.95, open_px=10.0, high=10.2, low=9.9, prev_close=9.9)
        trader = self._trader(cfg, q, klines=low_hist)
        result = trader.sell("2026-05-22")
        self.assertEqual(result["results"][0]["action"], "hold")
        self.assertEqual(len(db.list_live_positions(self.account["id"], "open")), 1)


if __name__ == "__main__":
    unittest.main()
