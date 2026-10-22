"""停牌标的处理：按 code 三态可用性分类、跳过与标记（AC1-AC8）。"""

import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from dragon_quant.live_trade.signal_engine import SignalEngine
from dragon_quant.live_trade.trader import LiveTrader
from dragon_quant.models.types import KBar
from dragon_quant.review_account.data import historical_events
from dragon_quant.review_account.engine import TradingEngine
from dragon_quant.review_account.market import (
    DataCoverageError, at, bar_times, event_date, validate_bars,
)
from dragon_quant.review_account.models import (
    UNAVAILABLE_DAILY_MISSING, UNAVAILABLE_SUSPENDED, AccountState, MarketEvent, Position,
    StrategyConfig,
)
from dragon_quant.review_account.simulator import AccountSimulator
from dragon_quant.storage import db
from tests.test_review_account import CAND, DAY, FakeData, row

SUSP = "600002"
CAND_S = {"code": SUSP, "name": "停牌样本", "rank": 2, "composite_score": 70,
          "is_true_dragon": True, "trade_date": "2026-09-04"}
INTRADAY_ONLY = "600003"
DAILY_ONLY = "600004"


def _no_day_row(bars, day=DAY):
    return [b for b in bars if event_date(b.timestamp) != day]


class SuspendedData(FakeData):
    """按 code 控制 DAY 当日数据形态（DAY 相对当前日期是历史交易日）。

    - ``suspended``：当日日K缺行 + 无分时；
    - ``zero_volume``：当日日K有行但 volume/amount 双零 + 无分时；
    - ``intraday_only``：有完整分时但当日日K缺行；
    - ``daily_only``：当日日K有效但无分时。
    """

    def __init__(self, suspended=(), zero_volume=(), intraday_only=(), daily_only=(),
                 intraday_prices=None, **kw):
        super().__init__(**kw)
        self.suspended = set(suspended)
        self.zero_volume = set(zero_volume)
        self.intraday_only = set(intraday_only)
        self.daily_only = set(daily_only)
        self.intraday_prices = dict(intraday_prices or {})

    def daily(self, code, start, refresh=False):
        out = super().daily(code, start, refresh)
        if code in self.suspended or code in self.intraday_only:
            return _no_day_row(out)
        if code in self.zero_volume:
            return [
                KBar(at(DAY, "15:00"), 0, 10.0, 10.0, 10.0, 10.0, 0, 0, 0, 0)
                if event_date(b.timestamp) == DAY else b
                for b in out
            ]
        return out

    def intraday(self, code, day, until=None):
        if code in self.suspended or code in self.zero_volume or code in self.daily_only:
            raise DataCoverageError(f"{code} 无完整5分钟K")
        if code in self.intraday_prices:
            price = self.intraday_prices[code]
            raw = [KBar(t, 10000, price, price + .05, price - .05, price, 0, 0, 1.0, 100000)
                   for t in bar_times(day)]
            return validate_bars(raw, day, until)
        return super().intraday(code, day, until)


def _ctx(**kw):
    base = {"ranking_mode": "legacy", "pool_source": "legacy", "fallback_reason": None,
            "pool_empty": False, "prep_snapshot_date": None, "auction_rows": 0,
            "degraded": []}
    base.update(kw)
    return base


class TestEventAvailability(unittest.TestCase):
    def test_suspended_candidate_keeps_strict_intraday_for_others(self):
        data = SuspendedData(suspended={SUSP})
        events = list(historical_events(DAY, [CAND, CAND_S], {CAND["code"], SUSP}, data))
        self.assertEqual(len(events), 1 + 2 * len(bar_times(DAY)))
        for event in events:
            self.assertEqual(list(event.rows), [CAND["code"]])
            self.assertEqual(event.suspended, {SUSP})
            self.assertEqual(event.unavailable, {SUSP: UNAVAILABLE_SUSPENDED})
            # 停牌候选保留在事件里，不得静默消失
            self.assertEqual([c["code"] for c in event.candidates], [CAND["code"], SUSP])
        first = next(e for e in events if e.rows)
        self.assertNotIn("data_quality", next(iter(first.rows.values())))
        self.assertAlmostEqual(events[0].last_closes[SUSP], 10.0)

    def test_all_suspended_yields_single_placeholder(self):
        data = SuspendedData(suspended={SUSP})
        events = list(historical_events(DAY, [CAND_S], {SUSP}, data))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].phase, "late")
        self.assertEqual(events[0].rows, {})
        self.assertEqual(events[0].suspended, {SUSP})
        self.assertEqual(events[0].candidates, [CAND_S])

    def test_zero_volume_daily_row_is_suspended(self):
        data = SuspendedData(zero_volume={SUSP})
        events = list(historical_events(DAY, [CAND_S], {SUSP}, data))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].rows, {})
        self.assertEqual(events[0].unavailable, {SUSP: UNAVAILABLE_SUSPENDED})

    def test_mixed_day_intraday_only_code_is_daily_unavailable(self):
        # 分时价格 11.5 ≠ 昨收 10.0：锁住「daily_unavailable 用当日末根5分钟K估值」口径
        data = SuspendedData(intraday_only={INTRADAY_ONLY}, daily_only={DAILY_ONLY},
                             intraday_prices={INTRADAY_ONLY: 11.5})
        cands = [{"code": INTRADAY_ONLY, "name": "只有分时"}, {"code": DAILY_ONLY, "name": "只有日K"}]
        events = list(historical_events(DAY, cands, {INTRADAY_ONLY, DAILY_ONLY}, data))
        self.assertEqual(len(events), 4)  # open/fill/late/fill
        for event in events:
            self.assertEqual(list(event.rows), [DAILY_ONLY])
            self.assertEqual(event.unavailable, {INTRADAY_ONLY: UNAVAILABLE_DAILY_MISSING})
            self.assertEqual(event.suspended, set())
            self.assertEqual(next(iter(event.rows.values()))["data_quality"], "daily_fallback")
            self.assertAlmostEqual(event.last_closes[INTRADAY_ONLY], 11.5)

    def test_today_is_never_classified_suspended(self):
        """当日（盘中/收盘后）沿用旧契约：缺当日日K 必须可见失败，不得静默判停牌。"""
        data = SuspendedData(suspended={SUSP})
        with patch("dragon_quant.review_account.data.historical_day", return_value=False):
            with self.assertRaises(DataCoverageError) as cm:
                list(historical_events(DAY, [CAND, CAND_S], {CAND["code"], SUSP}, data))
        self.assertIn("缺少当日日K", str(cm.exception))

    def test_today_intraday_only_still_replays_strict(self):
        data = SuspendedData(intraday_only={CAND["code"]})
        with patch("dragon_quant.review_account.data.historical_day", return_value=False):
            events = list(historical_events(DAY, [CAND], {CAND["code"]}, data))
        self.assertEqual(len(events), 1 + 2 * len(bar_times(DAY)))
        self.assertFalse(any(event.unavailable for event in events))

    def test_normal_day_stream_unchanged(self):
        """正常日事件序列与改造前全等（timestamp/phase 逐帧 + rows 恒为候选集合）。"""
        events = list(historical_events(DAY, [CAND], {CAND["code"]}, FakeData()))
        expected = [(at(DAY, "09:30"), "open")]
        for i, ts in enumerate(bar_times(DAY)):
            opening_ts = at(DAY, "09:30") if i == 0 else ts - 300_000
            expected.append((opening_ts + 1, "fill"))
            phase = ("late" if ts == at(DAY, "14:55")
                     else "close" if ts == at(DAY, "15:00") else "bar")
            expected.append((ts, phase))
        self.assertEqual([(e.timestamp, e.phase) for e in events], expected)
        self.assertTrue(all(set(e.rows) == {CAND["code"]} for e in events))
        self.assertFalse(any(e.unavailable for e in events))
        self.assertFalse(any(e.last_closes for e in events))


class TestEngineUnavailableDetails(unittest.TestCase):
    def test_detail_emitted_outside_buy_limit_and_deduped_per_day(self):
        cfg = StrategyConfig(max_daily_buys=1)
        state = AccountState(100000)
        state.trade_date = DAY
        state.buys_today = 1  # 当日买入次数已满：买点循环被闸门挡住
        engine = TradingEngine(cfg, state)
        first = row()
        first["phase"] = "open"
        event = MarketEvent(first["observed_at"], "open", {CAND["code"]: first}, [CAND, CAND_S],
                            unavailable={SUSP: UNAVAILABLE_SUSPENDED})
        details = [d for d in engine.step(event)["details"] if d["code"] == SUSP]
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]["reason_code"], "suspended")
        self.assertIn("停牌", details[0]["reason_text"])
        self.assertFalse(details[0]["passed"])
        # 同一交易日后续事件不得重复产出
        later = MarketEvent(first["observed_at"] + 300_000, "bar", {CAND["code"]: first},
                            [CAND, CAND_S], unavailable={SUSP: UNAVAILABLE_SUSPENDED})
        self.assertFalse([d for d in engine.step(later)["details"] if d["code"] == SUSP])

    def test_signal_engine_gated_branch_does_not_duplicate(self):
        account = AccountState(100000)
        account.sold_today = True  # 走 buy_eval_skipped 分支
        engine = SignalEngine(StrategyConfig(), positions=[], account=account)
        event = MarketEvent(at(DAY, "09:30"), "open", {}, [CAND_S],
                            unavailable={SUSP: UNAVAILABLE_SUSPENDED},
                            last_closes={SUSP: 9.8})
        first = [d for d in engine.step(event)["details"] if d["code"] == SUSP]
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["reason_code"], "suspended")
        # 停牌持仓按 last_closes 估值，不回落成本价
        self.assertAlmostEqual(account.marks[SUSP], 9.8)
        second = MarketEvent(at(DAY, "09:35"), "bar", {}, [CAND_S],
                             unavailable={SUSP: UNAVAILABLE_SUSPENDED},
                             last_closes={SUSP: 9.8})
        self.assertFalse([d for d in engine.step(second)["details"] if d["code"] == SUSP])

    def test_pending_sell_fills_on_resumption_day(self):
        cfg = StrategyConfig()
        p = Position(SUSP, "停牌样本", 1000, "2026-09-04", 10.0, 10000.0, "buy", "买",
                     {"fill_timestamp": at("2026-09-04", "10:00")}, highest_price=10.0)
        state = AccountState(90000, positions=[p])
        state.trade_date = "2026-09-08"
        state.last_event = at("2026-09-08", "15:00")
        state.marks[SUSP] = 10.0
        state.pending = [{"stock_code": SUSP, "name": "停牌样本", "side": "SELL",
                          "code": "hard_stop_loss", "reason_text": "止损",
                          "signal": {"action": "SELL"},
                          "created_at": at("2026-09-08", "10:00")}]
        engine = TradingEngine(cfg, state)
        fill = row("2026-09-09", "fill", 9.2, at("2026-09-09", "09:30") + 1)
        result = engine.step(MarketEvent(fill["observed_at"], "fill", {SUSP: fill}, []))
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0].trade_date, "2026-09-09")


D0, D1, D2, D3 = "2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09"


def _weekdays_before(end, count=60):
    out, d = [], end
    while len(out) < count:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return list(reversed(out))


class MultiDayData:
    """D2 停牌（缺日K行、无分时），D3 复牌价 9.2（触发止损）。"""

    def __init__(self):
        self.days = [D0, D1, D2, D3]
        self._history = [
            KBar(at(d.isoformat(), "15:00"), 1_000_000, 10.0, 10.2, 9.8, 10.0, 0, 0, 12, 1e9)
            for d in _weekdays_before(date(2026, 9, 10))
        ]

    def calendar(self, start, end, include_today=False):
        return [d for d in self.days if start <= d <= end]

    def daily(self, code, start, refresh=False):
        if code != SUSP:
            return list(self._history)
        out = []
        for bar in self._history:
            day = event_date(bar.timestamp)
            if day == D2:
                continue
            if day == D3:
                out.append(KBar(bar.timestamp, 1_000_000, 9.2, 9.4, 9.0, 9.2, 0, 0, 12, 1e9))
            else:
                out.append(bar)
        return out

    def intraday(self, code, day, until=None):
        if code == SUSP and day == D2:
            raise DataCoverageError(f"{code} 无完整5分钟K")
        price = 9.2 if day == D3 else 10.0
        bars = [KBar(t, 10000, price, price + .05, price - .05, price, 0, 0, 1.0, 100000)
                for t in bar_times(day)]
        return validate_bars(bars, day, until)

    def try_intraday(self, code, day, until=None):
        try:
            return self.intraday(code, day, until)
        except DataCoverageError:
            return None


class TestSimulatorSuspended(unittest.TestCase):
    def _run_sim(self, data, candidates, start=D1, end=D3):
        sim = AccountSimulator(StrategyConfig(), data=data)
        with patch("dragon_quant.review_account.simulator.collect_candidates_ex",
                   return_value=(candidates, _ctx())), \
                patch("dragon_quant.review_account.simulator.load_market_sentiment",
                      return_value=None):
            result = sim.run(start, end)
        return sim, result

    def test_suspended_holding_frozen_and_resumes(self):
        sim = AccountSimulator(StrategyConfig(), data=MultiDayData())
        sim.state.cash = 90000.0
        p = Position(SUSP, "停牌样本", 1000, D0, 10.0, 10000.0, "buy", "买",
                     {"fill_timestamp": at(D0, "10:00")}, highest_price=10.0)
        sim.state.positions = [p]
        sim.state.marks[SUSP] = 10.0
        with patch("dragon_quant.review_account.simulator.collect_candidates_ex",
                   return_value=([], _ctx())), \
                patch("dragon_quant.review_account.simulator.load_market_sentiment",
                      return_value=None):
            result = sim.run(D1, D3)
        by_day = {s.trade_date: s for s in result["snapshots"]}
        # 停牌日：不评估卖出、不更新 mark，净值与上一交易日一致
        self.assertAlmostEqual(by_day[D2].total_equity, by_day[D1].total_equity)
        self.assertEqual(sim.state.marks[SUSP], 9.2)  # 复牌日按复牌价重新估值
        self.assertEqual(result["suspended_days"], {D2: [SUSP]})
        self.assertEqual(result["suspended_codes"], [SUSP])
        self.assertEqual(result["suspended_days_count"], 1)
        # 峰值只来自 D1 分时高点（10.05）；停牌日无行情，不参与峰值更新
        self.assertEqual(p.highest_price, 10.05)
        self.assertAlmostEqual(p.highest_return, 0.5)
        # 停牌日无成交；复牌日恢复评估并触发止损
        self.assertEqual([t.trade_date for t in result["trades"]], [D3])
        self.assertEqual(result["trades"][0].code, SUSP)

    def test_mixed_day_unavailable_days_reported(self):
        data = SuspendedData(intraday_only={INTRADAY_ONLY}, daily_only={DAILY_ONLY},
                             intraday_prices={INTRADAY_ONLY: 11.5})
        cands = [{"code": INTRADAY_ONLY, "name": "只有分时"},
                 {"code": DAILY_ONLY, "name": "只有日K"}]
        _, result = self._run_sim(data, cands, DAY, DAY)
        self.assertEqual(result["unavailable_days"],
                         {DAY: {INTRADAY_ONLY: UNAVAILABLE_DAILY_MISSING}})
        self.assertEqual(result["suspended_codes"], [])
        self.assertEqual(result["suspended_days_count"], 0)
        self.assertTrue(any("日K不可用" in note for note in result["data_degraded"]))
        hold = next(e for e in result["events"]
                    if e.event_date == DAY and e.event_type in {"HOLD", "IDLE"})
        self.assertIn("只停牌", hold.detail)  # 部分不可用：不误述为「未评估」

    def test_all_suspended_day_does_not_crash(self):
        sim, result = self._run_sim(SuspendedData(suspended={SUSP}), [CAND_S], DAY, DAY)
        self.assertEqual(result["final_equity"], result["initial_cash"])
        self.assertFalse(result["trades"])
        self.assertEqual(result["suspended_days"], {DAY: [SUSP]})
        self.assertEqual(result["suspended_days_count"], 1)
        self.assertTrue(any("停牌" in note for note in result["data_degraded"]))
        hold = next(e for e in result["events"] if e.event_date == DAY)
        self.assertIn("停牌", hold.detail)


class TestLiveTraderSuspended(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        self._patch = patch("dragon_quant.storage.db._connect",
                            side_effect=lambda: sqlite3.connect(self._db_path))
        self._patch.start()
        db.init_db()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def test_buy_reports_suspended_and_degrades(self):
        with patch("dragon_quant.live_trade.trader.collect_candidates_ex",
                   return_value=([CAND, CAND_S], _ctx())), \
                patch("dragon_quant.live_trade.trader.load_market_sentiment",
                      return_value=None):
            result = LiveTrader(StrategyConfig(), source="v2",
                                data=SuspendedData(suspended={SUSP})).buy(DAY, "10:00")
        self.assertEqual(result["suspended_codes"], [SUSP])
        self.assertTrue(any("停牌" in note for note in result["degraded"]))
        self.assertTrue(any(d["code"] == SUSP and d["reason_code"] == "suspended"
                            for d in result["details"]))

    def test_sell_freezes_suspended_holding(self):
        db.insert_signal(SUSP, "停牌样本", "2026-09-04", 10.0, "buy", "买", {},
                         "v2", qty=1000)
        with patch("dragon_quant.live_trade.trader.collect_candidates", return_value=[]):
            result = LiveTrader(StrategyConfig(), source="v2",
                                data=SuspendedData(suspended={SUSP})).sell(DAY, "10:00")
        self.assertEqual(result["sells"], [])
        self.assertEqual(result["suspended_codes"], [SUSP])
        self.assertTrue(any("停牌" in note for note in result["degraded"]))
        self.assertEqual([h["code"] for h in result["held"]], [SUSP])


if __name__ == "__main__":
    unittest.main()
