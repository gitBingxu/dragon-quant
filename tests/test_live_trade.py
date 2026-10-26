"""tests for review_account.live_trade — 纯信号 buy/sell 命令（复用 review_account 策略与成交）。"""
import copy
import datetime
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from dragon_quant.infra.models import KBar
from dragon_quant.review_account.live_trade.service import _make_config, _print_buy, _print_sell, run_buy, run_sell
from dragon_quant.review_account.live_trade.shadow import build_shadow_state, position_as_of
from dragon_quant.review_account.live_trade.signal_engine import SignalEngine
from dragon_quant.review_account.live_trade.trader import LiveTrader
from dragon_quant.review_account.data import historical_events
from dragon_quant.review_account.engine import TradingEngine
from dragon_quant.review_account.strategy.execution import (
    apply_sell_fill, break_even_price, buy_quantity, notional_quantity,
)
from dragon_quant.review_account.market import (
    SHANGHAI, at, bar_times, build_row, DataCoverageError, validate_bars,
)
from dragon_quant.review_account.models import AccountState, MarketEvent, StrategyConfig
from dragon_quant.infra.storage import connection
from dragon_quant.review_account.live_trade import storage as signal_store
from tests.test_review_account import CAND, DAY, FakeData, bars, history, position, row


def _mock_datetime(day, hour, minute, second=0):
    """构造可 patch 到 trader.datetime 的假 datetime（strptime 保留真实实现）。"""
    fake = MagicMock()
    fake.strptime = datetime.datetime.strptime
    fake.now.return_value = datetime.datetime.fromisoformat(day).replace(
        hour=hour, minute=minute, second=second, tzinfo=SHANGHAI)
    return fake


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


def _chk(rule, label, status, reason, selected=False):
    return {"rule": rule, "label": label, "window": "w", "status": status,
            "selected": selected, "reason": reason}


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
        self.assertIn("其他候选（2 只）", out)
        self.assertEqual(out.count("（600001）"), 1)
        self.assertEqual(out.count("（600002）"), 1)
        # 无 checks 的旧 detail：同 code 取最后一条（last-wins）
        self.assertIn("r3", out)

    def test_last_wins_with_checks(self):
        """同 code 两条带 checks：取最后一条（14:55 终态），不是 09:30 的 pending 帧。"""
        details = [
            {"code": "600001", "name": "a", "passed": False, "reason_code": "no_buy_pattern",
             "checks": [_chk("buy_open_turn_strong", "弱转强", "pending", "OPEN_PENDING")]},
            {"code": "600001", "name": "a", "passed": False, "reason_code": "no_buy_pattern",
             "checks": [_chk("buy_open_turn_strong", "弱转强", "window_closed", "LATE_FINAL")]},
        ]
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_buy(DAY, {"buys": [], "details": details, "reason_text": "x"})
        out = buf.getvalue()
        self.assertEqual(out.count("（600001）"), 1)
        self.assertIn("LATE_FINAL", out)
        self.assertNotIn("OPEN_PENDING", out)

    def test_bought_code_not_in_other_list(self):
        """买入代码只在 🟢 段出现；selected 由 buys.reason_code 回填。"""
        checks = [
            _chk("buy_open_ma5_pullback", "开盘回踩MA5", "passed", "hit"),
            _chk("buy_open_turn_strong", "弱转强", "window_closed", "fail"),
            _chk("buy_divergence_first_break", "分歧买龙", "window_closed", "fail"),
            _chk("buy_dragon_comeback", "龙回头", "window_closed", "fail"),
        ]
        result = {
            "buys": [{"code": "600001", "name": "a", "entry_price": 10.0,
                      "reason_code": "buy_open_ma5_pullback", "reason_text": "hit",
                      "persisted": True}],
            "details": [
                {"code": "600001", "name": "a", "passed": False,
                 "reason_code": "already_holding", "reason_text": "已买入", "checks": checks},
                {"code": "600002", "name": "b", "passed": False,
                 "reason_code": "no_buy_pattern", "reason_text": "x",
                 "checks": [_chk("buy_open_ma5_pullback", "开盘回踩MA5",
                                 "window_closed", "no")]},
            ],
            "reason_text": "买入 1 只",
        }
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_buy(DAY, result)
        out = buf.getvalue()
        self.assertEqual(out.count("（600001）"), 1)
        self.assertIn("其他候选（1 只）", out)
        self.assertIn("通过，命中买点", out)
        self.assertIn("（600002）", out)

    def test_four_checks_for_rejected(self):
        checks = [
            _chk("buy_open_ma5_pullback", "开盘回踩MA5", "window_closed", "距MA5 +5.4% > 3%"),
            _chk("buy_open_turn_strong", "弱转强", "window_closed", "未突破上日高点"),
            _chk("buy_divergence_first_break", "分歧买龙", "window_closed", "连续一字板0 < 2"),
            _chk("buy_dragon_comeback", "龙回头", "window_closed", "回调天数0 < 3"),
        ]
        result = {"buys": [], "details": [
            {"code": "600001", "name": "a", "passed": False,
             "reason_code": "no_buy_pattern", "reason_text": "x", "checks": checks}],
            "reason_text": "x"}
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_buy(DAY, result)
        out = buf.getvalue()
        for text in ("开盘回踩MA5", "弱转强", "分歧买龙", "龙回头",
                     "窗口已过（未通过）", "距MA5", "未突破上日高点"):
            self.assertIn(text, out)

    def test_summary_suppressed_for_no_pattern(self):
        result = {"buys": [], "details": [
            {"code": "600001", "name": "a", "passed": False,
             "reason_code": "no_buy_pattern", "reason_text": "SHOULD_NOT_PRINT",
             "checks": [_chk("buy_open_ma5_pullback", "开盘回踩MA5", "failed", "r")]}],
            "reason_text": "x"}
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_buy(DAY, result)
        self.assertNotIn("SHOULD_NOT_PRINT", buf.getvalue())

    def test_event_gate_note(self):
        result = {"buys": [], "details": [], "reason_text": "x",
                  "buy_eval_skipped": ["组合风控闸门（当日暂停开新仓）"]}
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_buy(DAY, result)
        self.assertIn("本轮买点未评估：组合风控闸门", buf.getvalue())

    def test_suggested_position_line(self):
        """建议仓位展示：有值按百分比，缺失/无 key 显示占位；0.0 不落入缺失分支。"""
        def render(result):
            buf = io.StringIO()
            with redirect_stdout(buf):
                _print_buy(DAY, result)
            return buf.getvalue()

        base = {"buys": [], "details": [], "reason_text": "候选池暂无触发买点的标的"}
        self.assertIn("建议仓位 80%", render({**base, "sentiment_pct": 0.8}))
        self.assertIn("建议仓位 0%", render({**base, "sentiment_pct": 0.0}))
        self.assertIn("建议仓位 —", render({**base, "sentiment_pct": None}))
        self.assertIn("建议仓位 —", render(base))  # 无 key（向后兼容）

    def test_separates_blocking_and_delevel_gates(self):
        def render(gates):
            buf = io.StringIO()
            with redirect_stdout(buf):
                _print_buy(DAY, {"buys": [], "details": [], "reason_text": "x",
                                 "fired_gate": gates})
            return buf.getvalue()
        soft = render(["streak_delevel"])
        self.assertIn("仓位降级提示", soft)
        self.assertNotIn("暂停开新仓", soft)
        hard = render(["shadow_equity_floor"])
        self.assertIn("暂停开新仓", hard)
        self.assertNotIn("仓位降级提示", hard)


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

    def _fake_now(self, hour, minute, second=0):
        return _mock_datetime("2026-10-09", hour, minute, second)

    @staticmethod
    def _trader():
        return LiveTrader(StrategyConfig(), source="v2",
                          data=FakeData(days=["2026-09-04", "2026-09-07",
                                              "2026-09-08", "2026-10-09"]))

    def _run_patched(self, hour, minute, force, command="buy", day="2026-10-09",
                     second=0, **kw):
        """patch 掉 _run_buy/_run_sell，只看闸门判定与传给引擎的截断点。"""
        method = "_run_buy" if command == "buy" else "_run_sell"
        with patch("dragon_quant.review_account.live_trade.trader.datetime",
                   self._fake_now(hour, minute, second)), \
                patch.object(LiveTrader, method, return_value={}) as run:
            result = getattr(self._trader(), command)(day, force=force, **kw)
        return result, run

    def test_historical_requires_at(self):
        with self.assertRaises(ValueError):
            self._trader().buy(DAY)

    def test_rejects_non_trade_day(self):
        with self.assertRaises(ValueError):
            self._trader().buy("2026-09-06", as_of="10:00")

    def test_before_open_rejected(self):
        with patch("dragon_quant.review_account.live_trade.trader.datetime", self._fake_now(9, 0)), \
                self.assertRaises(ValueError):
            self._trader().buy("2026-10-09")

    def test_noon_break_rejected(self):
        with patch("dragon_quant.review_account.live_trade.trader.datetime", self._fake_now(12, 0)), \
                self.assertRaises(ValueError):
            self._trader().buy("2026-10-09")

    def test_after_close_rejected(self):
        with patch("dragon_quant.review_account.live_trade.trader.datetime", self._fake_now(15, 30)), \
                self.assertRaises(ValueError):
            self._trader().buy("2026-10-09")

    # ── --force：仅放行午休/收盘，09:30 前与数据校验照旧 ──

    def test_force_after_close_replays_last_completed_bar(self):
        result, run = self._run_patched(15, 30, force=True)
        self.assertTrue(result["forced"])
        day, target, until, _calendar, cutoff, forced = run.call_args.args
        self.assertEqual(until, bar_times("2026-10-09")[-1])   # 收盘后：15:00 那根
        self.assertEqual(cutoff, until)                         # 事件流截断到最后完整K
        self.assertGreater(target, cutoff)                      # shadow as-of 仍是真实 now
        self.assertTrue(forced)

    def test_force_allows_noon_break(self):
        result, run = self._run_patched(12, 0, force=True)
        self.assertTrue(result["forced"])
        day, target, until, _calendar, cutoff, _forced = run.call_args.args
        self.assertEqual(until, at("2026-10-09", "11:30"))
        self.assertEqual(cutoff, until)

    def test_force_before_open_still_rejected(self):
        with patch("dragon_quant.review_account.live_trade.trader.datetime", self._fake_now(9, 0)), \
                self.assertRaises(ValueError) as cm:
            self._trader().buy("2026-10-09", force=True)
        self.assertIn("尚未开盘", str(cm.exception))

    def test_force_before_first_bar_still_rejected(self):
        """09:30–09:35：属于交易时段但没有完整5分钟K，force 不放行。"""
        with patch("dragon_quant.review_account.live_trade.trader.datetime", self._fake_now(9, 32)), \
                self.assertRaises(ValueError) as cm:
            self._trader().buy("2026-10-09", force=True)
        self.assertIn("尚无完整5分钟K", str(cm.exception))

    def test_force_within_session_not_flagged(self):
        result, run = self._run_patched(10, 0, force=True)
        self.assertFalse(result["forced"])
        day, target, until, _calendar, cutoff, _forced = run.call_args.args
        self.assertEqual(cutoff, target)   # 未触发 force 放行 → 截断点等于原 target

    def test_force_noon_break_boundaries(self):
        """11:30:00 仍属交易时段（不标记）；11:30:01 起算午休（force 放行）。"""
        result, _run = self._run_patched(11, 30, force=True)
        self.assertFalse(result["forced"])
        result, run = self._run_patched(11, 30, force=True, second=1)
        self.assertTrue(result["forced"])
        self.assertEqual(run.call_args.args[2], at("2026-10-09", "11:30"))

    def test_force_close_boundaries(self):
        """15:05:59 仍属交易时段（不标记）；15:06:00 起算收盘（force 放行）。"""
        result, _run = self._run_patched(15, 5, force=True, second=59)
        self.assertFalse(result["forced"])
        result, run = self._run_patched(15, 6, force=True)
        self.assertTrue(result["forced"])
        self.assertEqual(run.call_args.args[2], bar_times("2026-10-09")[-1])

    def test_force_with_as_of_is_silently_ignored(self):
        """--at 路径优先：force 静默无效、不报错。"""
        result, run = self._run_patched(10, 0, force=True, as_of="10:00")
        self.assertFalse(result["forced"])
        self.assertEqual(run.call_args.args[1], at("2026-10-09", "10:00"))

    def test_historical_replay_reports_not_forced(self):
        result, _run = self._run_patched(10, 0, force=True, day="2026-09-07", as_of="10:00")
        self.assertFalse(result["forced"])


class TestServiceRun(unittest.TestCase):
    """service 层 run_buy/run_sell/_make_config 的装配与打印接线。"""

    def test_make_config_merges_strategy_params(self):
        cfg = _make_config("v2", {"min_score": 60})
        self.assertEqual(cfg.source, "v2")
        self.assertEqual(cfg.min_score, 60)
        self.assertEqual(_make_config("v2", None).min_score, 50.0)

    def test_run_buy_wires_trader_and_print(self):
        result = {"buys": [], "details": [], "reason_text": "候选池暂无触发买点的标的"}
        with patch("dragon_quant.review_account.live_trade.service.LiveTrader") as Trader:
            Trader.return_value.buy.return_value = result
            buf = io.StringIO()
            with redirect_stdout(buf):
                out = run_buy(DAY, source="v2", as_of="10:00")
        self.assertIs(out, result)
        self.assertIn("买入建议", buf.getvalue())
        self.assertNotIn("强制模式", buf.getvalue())
        Trader.return_value.buy.assert_called_once_with(DAY, "10:00", force=False)

    def test_run_sell_wires_trader_and_print(self):
        result = {"sells": [], "held": [], "reason_text": "持仓暂无卖出信号"}
        with patch("dragon_quant.review_account.live_trade.service.LiveTrader") as Trader:
            Trader.return_value.sell.return_value = result
            buf = io.StringIO()
            with redirect_stdout(buf):
                out = run_sell(DAY, source="v2", as_of="10:00")
        self.assertIs(out, result)
        self.assertIn("卖出建议", buf.getvalue())
        self.assertNotIn("强制模式", buf.getvalue())
        Trader.return_value.sell.assert_called_once_with(DAY, "10:00", force=False)

    def test_run_buy_force_passes_through_and_prints_hint(self):
        result = {"buys": [], "details": [], "reason_text": "候选池暂无触发买点的标的",
                  "forced": True}
        with patch("dragon_quant.review_account.live_trade.service.LiveTrader") as Trader:
            Trader.return_value.buy.return_value = result
            buf = io.StringIO()
            with redirect_stdout(buf):
                out = run_buy(DAY, source="v2", as_of="10:00", force=True)
        self.assertIs(out, result)
        Trader.return_value.buy.assert_called_once_with(DAY, "10:00", force=True)
        self.assertIn("强制模式", buf.getvalue())

    def test_run_sell_force_passes_through_and_prints_hint(self):
        result = {"sells": [], "held": [], "reason_text": "持仓暂无卖出信号",
                  "forced": True}
        with patch("dragon_quant.review_account.live_trade.service.LiveTrader") as Trader:
            Trader.return_value.sell.return_value = result
            buf = io.StringIO()
            with redirect_stdout(buf):
                out = run_sell(DAY, source="v2", as_of="10:00", force=True)
        self.assertIs(out, result)
        Trader.return_value.sell.assert_called_once_with(DAY, "10:00", force=True)
        self.assertIn("强制模式", buf.getvalue())

    def test_forced_hint_uses_strict_wording(self):
        result = {"sells": [], "held": [], "reason_text": "持仓暂无卖出信号",
                  "forced": True, "data_quality": "strict_5min"}
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_sell(DAY, result)
        out = buf.getvalue()
        self.assertIn("最后一个完整 5 分钟 K", out)
        self.assertNotIn("日K兜底", out)

    def test_forced_hint_uses_fallback_wording(self):
        """日K兜底没有 5 分钟 K，提示不能声称「最后一个完整 5 分钟 K」。"""
        result = {"sells": [], "held": [], "reason_text": "持仓暂无卖出信号",
                  "forced": True, "data_quality": "daily_fallback"}
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_sell(DAY, result)
        out = buf.getvalue()
        self.assertIn("强制模式", out)
        self.assertIn("日K兜底", out)
        self.assertNotIn("最后一个完整 5 分钟 K", out)


class TestSignalStorage(unittest.TestCase):
    """buy_sell_signals 表的幂等写入 / before_date 过滤 / 平仓 / 峰值写回。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        self._conn = sqlite3.connect(self._db_path)
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            connection.init_db()

    def tearDown(self):
        self._conn.close()
        self._tmpdir.cleanup()

    def _connect(self):
        return patch("dragon_quant.infra.storage.connection._connect",
                     side_effect=lambda: sqlite3.connect(self._db_path))

    def test_signal_roundtrip_idempotent_and_before_date(self):
        code = "600001"
        with self._connect():
            i1, created1 = signal_store.insert_signal(code, "样本", "2026-09-07", 10.0,
                                            "buy_open_ma5_pullback", "开盘承接", {"a": 1})
            i2, created2 = signal_store.insert_signal(code, "样本", "2026-09-07", 10.0,
                                            "buy_open_ma5_pullback", "开盘承接", {"a": 1})
            self.assertEqual(i1, i2)  # 幂等：同一 code 未平仓不重复记录
            self.assertTrue(created1)
            self.assertFalse(created2)
            self.assertEqual(
                [s["code"] for s in signal_store.list_open_signals(source="v2")],
                [code])
            # before_date：只取 entry_date < 目标日的持仓
            self.assertEqual(
                [s["code"] for s in signal_store.list_open_signals(
                    before_date="2026-09-08", source="v2")],
                [code])
            self.assertEqual(signal_store.list_open_signals(before_date="2026-09-07", source="v2"), [])
            # 峰值写回
            self.assertTrue(signal_store.update_signal_peaks(code, 8.5, 10.85))
            row_sig = signal_store.list_open_signals(source="v2")[0]
            self.assertAlmostEqual(row_sig["highest_return"], 8.5)
            self.assertAlmostEqual(row_sig["highest_price"], 10.85)
            # 平仓后不再出现在未平仓列表
            self.assertTrue(signal_store.close_signal(code, "2026-09-08", 9.5, "hard_stop_loss", "止损", {}, 1))
            self.assertEqual(signal_store.list_open_signals(source="v2"), [])


def signal(**kw) -> dict:
    """一条未平仓信号的 DB 行形态（默认 entry 2026-09-04 @10，qty=1000）。"""
    base = {
        "id": 1, "code": "600001", "name": "样本", "source": "v2",
        "entry_date": "2026-09-04", "entry_price": 10.0,
        "entry_reason_code": "buy", "entry_reason_text": "买",
        "entry_signal": {"fill_timestamp": at("2026-09-04", "10:00")},
        "highest_return": 0.0, "highest_price": 0.0, "took_profit_half": False,
        "status": "open", "exit_date": None, "exit_price": None,
        "exit_reason_code": "", "exit_reason_text": "", "exit_signal": {},
        "hold_days": None, "qty": 1000,
        "half_exit_date": None, "half_exit_price": None, "half_exit_qty": None,
        "half_exit_reason_code": "", "half_exit_reason_text": "", "half_exit_signal": {},
    }
    base.update(kw)
    return base


class MissingCodeData(FakeData):
    """指定 code 缺5分钟K（用于验证 codes 全集参与数据分级）。"""

    def __init__(self, missing_code, **kw):
        super().__init__(**kw)
        self.missing_code = missing_code

    def intraday(self, code, day, until=None):
        if code == self.missing_code:
            raise DataCoverageError(f"{code} 无完整5分钟K")
        return super().intraday(code, day, until)


class RisingData(FakeData):
    """当日分时走高，用于验证 buy 运行也会写回峰值。"""

    def intraday(self, code, day, until=None):
        if day in self.intraday_missing:
            raise DataCoverageError(f"{day} 无完整5分钟K")
        bars = [KBar(t, 10000, 10.5, 10.55, 10.45, 10.5, 0, 0, 1.0, 100000)
                for t in bar_times(day)]
        return validate_bars(bars, day, until)


class TestNotionalQuantity(unittest.TestCase):
    def test_matches_backtest_first_position_and_risk_cap(self):
        for risk in (0.0, 1.0):
            cfg = StrategyConfig(risk_per_trade_pct=risk)
            self.assertEqual(notional_quantity(cfg, 10.0),
                             buy_quantity(cfg.initial_cash, cfg.initial_cash, 10.0, cfg))

    def test_break_even_parity_with_backtest_position(self):
        cfg = StrategyConfig()
        sig = SignalEngine(cfg)
        acc = TradingEngine(cfg, AccountState(100000))
        for event in historical_events(DAY, [CAND], {CAND["code"]}, FakeData()):
            event.allow_buy = True
            sig.step(copy.deepcopy(event))
            acc.step(copy.deepcopy(event))
        self.assertEqual(len(sig.positions), 1)
        self.assertEqual(len(acc.state.positions), 1)
        self.assertAlmostEqual(break_even_price(sig.positions[0], cfg),
                               break_even_price(acc.state.positions[0], cfg), places=9)

    def test_zero_qty_skips_buy_like_backtest(self):
        cfg = StrategyConfig(initial_cash=1000)  # 一手 1000 + 费用 5 > 预算
        engine = SignalEngine(cfg)
        cands = [{**CAND, "code": "600001"}]
        engine.step(MarketEvent(at(DAY, "09:30"), "open", {"600001": row()}, cands))
        fill = row(timestamp=at(DAY, "09:30") + 1)
        result = engine.step(MarketEvent(at(DAY, "09:30") + 1, "fill", {"600001": fill}, cands))
        self.assertEqual(result["buys"], [])
        self.assertTrue(any(d.get("reason_code") == "budget_insufficient" for d in result["details"]))
        self.assertEqual(engine.positions, [])


class TestShadowState(unittest.TestCase):
    def test_equity_identity_after_buy_all(self):
        """run 内买入扣影子现金：两票成交后 equity ≈ initial_cash − 费用，不膨胀。"""
        cfg = StrategyConfig()
        shadow = build_shadow_state([], cfg, DAY, as_of=at(DAY, "10:00"))
        engine = SignalEngine(cfg, account=shadow, evaluate_sells=False)
        cands = [{**CAND, "code": "600001"}, {**CAND, "code": "600002"}]
        rows = {"600001": row(), "600002": row()}
        engine.step(MarketEvent(at(DAY, "09:30"), "open", rows, cands))
        fill = {"600001": row(timestamp=at(DAY, "09:30") + 1),
                "600002": row(timestamp=at(DAY, "09:30") + 1)}
        result = engine.step(MarketEvent(at(DAY, "09:30") + 1, "fill", fill, cands))
        self.assertEqual(len(result["buys"]), 2)
        equity = shadow.cash + sum(p.qty * p.entry_price for p in shadow.positions)
        fees = sum(b["qty"] * b["entry_price"] * cfg.commission_rate for b in result["buys"])
        self.assertLess(equity, cfg.initial_cash)
        self.assertAlmostEqual(equity, cfg.initial_cash - fees, places=2)

    def test_daily_loss_gate_blocks_buy(self):
        """当日实现亏损超阈值 → 日级熔断真的拦住买单。"""
        cfg = StrategyConfig(max_daily_loss_pct=5)
        loss = signal(status="closed", exit_date=DAY, exit_price=4.0,
                      exit_signal={"fill_timestamp": at(DAY, "10:00")})
        shadow = build_shadow_state([loss], cfg, DAY, as_of=at(DAY, "11:00"))
        self.assertLess(shadow.realized_today, 0)
        engine = SignalEngine(cfg, account=shadow)
        result = engine.step(MarketEvent(at(DAY, "11:00"), "bar", {"600001": row()}, [CAND]))
        self.assertEqual(result["pending"], [])
        self.assertIn("daily_loss_circuit_breaker", result["fired_gate"])

    def test_equity_floor_blocks_buy_when_net_worth_negative(self):
        """杠杆下净值转负：risk_governor 的 equity>0 守卫会跳过熔断，live 破产闸兜底。"""
        cfg = StrategyConfig(max_daily_loss_pct=5)
        wiped = signal(status="closed", exit_date="2026-09-04", exit_price=0.1,
                       qty=100000, exit_signal={"fill_timestamp": at("2026-09-04", "10:00")})
        shadow = build_shadow_state([wiped], cfg, DAY, as_of=at(DAY, "10:00"))
        self.assertLessEqual(shadow.cash, 0)
        engine = SignalEngine(cfg, account=shadow)
        result = engine.step(MarketEvent(at(DAY, "10:00"), "bar", {"600001": row()}, [CAND]))
        self.assertEqual(result["pending"], [])
        self.assertIn("shadow_equity_floor", engine.fired_gate)

    def test_streak_gate_and_pending_preserved(self):
        """6 连亏 → 连亏熔断；风控不通过时 pending BUY 保留（continue），不清单。"""
        cfg = StrategyConfig(streak_delevel_enabled=True)
        losses = [signal(id=i, code=f"60000{i}", status="closed", exit_date=f"2026-08-{20 + i:02d}",
                         exit_price=9.0, exit_signal={"fill_timestamp": at(f"2026-08-{20 + i:02d}", "10:00")})
                  for i in range(6)]
        shadow = build_shadow_state(losses, cfg, DAY, as_of=at(DAY, "10:00"))
        self.assertEqual(shadow.consecutive_losses, 6)
        engine = SignalEngine(cfg)
        engine.step(MarketEvent(at(DAY, "09:30"), "open", {"600001": row()}, [CAND]))
        self.assertEqual(len(engine.pending), 1)
        engine.account = shadow  # 只从下一个事件起启用风控
        result = engine.step(MarketEvent(at(DAY, "09:30") + 1, "fill",
                                         {"600001": row(timestamp=at(DAY, "09:30") + 1)}, [CAND]))
        self.assertIn("streak_circuit_breaker", result["fired_gate"])
        self.assertEqual(result["buys"], [])
        self.assertEqual(len(engine.pending), 1)  # continue 保留，等待风控恢复

    def test_sold_today_blocks_new_and_drops_pending(self):
        cfg = StrategyConfig()
        sold = signal(took_profit_half=True, half_exit_date=DAY, half_exit_price=10.5,
                      half_exit_qty=400, half_exit_signal={"fill_timestamp": at(DAY, "10:00")})
        shadow = build_shadow_state([sold], cfg, DAY, as_of=at(DAY, "11:00"))
        self.assertTrue(shadow.sold_today)
        engine = SignalEngine(cfg, account=shadow)
        result = engine.step(MarketEvent(at(DAY, "11:00"), "bar", {"600001": row()}, [CAND]))
        self.assertEqual(result["pending"], [])
        self.assertEqual(result["buys"], [])

    def test_sold_today_removes_existing_pending_buy(self):
        """当期已卖出：存量 pending BUY 被 remove（不是 continue 保留）。"""
        cfg = StrategyConfig()
        engine = SignalEngine(cfg)
        engine.step(MarketEvent(at(DAY, "09:30"), "open", {"600001": row()}, [CAND]))
        self.assertEqual(len(engine.pending), 1)
        shadow = build_shadow_state([], cfg, DAY)
        shadow.sold_today = True
        engine.account = shadow
        result = engine.step(MarketEvent(at(DAY, "09:30") + 1, "fill",
                                         {"600001": row(timestamp=at(DAY, "09:30") + 1)}, [CAND]))
        self.assertEqual(result["buys"], [])
        self.assertEqual(engine.pending, [])

    def test_pending_buy_expires_after_300s(self):
        cfg = StrategyConfig()
        engine = SignalEngine(cfg)
        engine.step(MarketEvent(at(DAY, "09:30"), "open", {"600001": row()}, [CAND]))
        self.assertEqual(len(engine.pending), 1)
        late = row(timestamp=at(DAY, "09:30") + 301_000)
        result = engine.step(MarketEvent(at(DAY, "09:30") + 301_000, "fill", {"600001": late}, [CAND]))
        self.assertEqual(result["buys"], [])
        self.assertEqual(engine.pending, [])

    def test_as_of_excludes_same_day_future_entry(self):
        future = signal(entry_date=DAY, entry_signal={"fill_timestamp": at(DAY, "14:00")})
        self.assertIsNone(position_as_of(future, StrategyConfig(), DAY, at(DAY, "10:00")))
        self.assertIsNotNone(position_as_of(future, StrategyConfig(), DAY, at(DAY, "15:00")))
        self.assertEqual(build_shadow_state([future], StrategyConfig(), DAY,
                                            at(DAY, "10:00")).positions, [])

    def test_as_of_excludes_same_day_future_close(self):
        closed = signal(status="closed", exit_date=DAY, exit_price=10.5,
                        exit_signal={"fill_timestamp": at(DAY, "14:55")})
        early = build_shadow_state([closed], StrategyConfig(), DAY, at(DAY, "10:00"))
        late = build_shadow_state([closed], StrategyConfig(), DAY, at(DAY, "15:00"))
        self.assertEqual([p.code for p in early.positions], ["600001"])
        self.assertFalse(early.sold_today)
        self.assertEqual(late.positions, [])
        self.assertTrue(late.sold_today)

    def test_half_projection_before_and_after_leg(self):
        half = signal(took_profit_half=True, half_exit_date=DAY, half_exit_price=10.5,
                      half_exit_qty=400, half_exit_signal={"fill_timestamp": at(DAY, "14:00")})
        before = position_as_of(half, StrategyConfig(), DAY, at(DAY, "10:00"))
        after = position_as_of(half, StrategyConfig(), DAY, at(DAY, "15:00"))
        self.assertEqual(before.qty, 1000)
        self.assertFalse(before.took_profit_half)
        self.assertEqual(after.qty, 600)
        self.assertTrue(after.took_profit_half)
        self.assertAlmostEqual(after.cost, before.cost * 600 / 1000, places=9)

    def test_legacy_signal_without_qty_uses_notional(self):
        legacy = signal(qty=None)
        pos = position_as_of(legacy, StrategyConfig(), DAY, at(DAY, "15:00"))
        self.assertEqual(pos.qty, notional_quantity(StrategyConfig(), 10.0))

    def test_dirty_signal_null_price_does_not_break_shadow(self):
        """方案 §6：缺 exit_price / entry_price 的脏数据按 0 盈亏跳过，不中断命令。"""
        bad_close = signal(status="closed", exit_date=DAY, exit_price=None,
                           exit_signal={"fill_timestamp": at(DAY, "10:00")})
        bad_entry = signal(id=2, code="600002", entry_price=None)
        clean = signal(id=3, code="600003")
        shadow = build_shadow_state([bad_close, bad_entry, clean], StrategyConfig(), DAY,
                                    at(DAY, "15:00"))
        self.assertEqual([p.code for p in shadow.positions], ["600003"])
        self.assertEqual(shadow.realized_today, 0.0)
        self.assertIsNone(position_as_of(bad_entry, StrategyConfig(), DAY, at(DAY, "15:00")))

    def test_closed_signal_with_null_entry_price_has_zero_pnl(self):
        """脏 entry_price + 已平仓 + qty 非空：不得把卖出金额当纯利润虚增净值/屏蔽熔断。"""
        cfg = StrategyConfig()
        dirty = signal(status="closed", entry_price=None, qty=1000, exit_date=DAY,
                       exit_price=10.5, exit_signal={"fill_timestamp": at(DAY, "10:00")})
        shadow = build_shadow_state([dirty], cfg, DAY, at(DAY, "15:00"))
        self.assertEqual(shadow.realized_today, 0.0)
        self.assertEqual(shadow.cash, cfg.initial_cash)
        self.assertEqual(shadow.positions, [])

    def test_half_leg_with_null_price_counts_zero_but_marks_sold(self):
        cfg = StrategyConfig()
        half = signal(took_profit_half=True, half_exit_date=DAY, half_exit_price=None,
                      half_exit_qty=400,
                      half_exit_signal={"fill_timestamp": at(DAY, "14:55")})
        shadow = build_shadow_state([half], cfg, DAY, at(DAY, "15:00"))
        self.assertEqual(shadow.realized_today, 0.0)
        self.assertTrue(shadow.sold_today)
        self.assertEqual([p.qty for p in shadow.positions], [600])

    def test_realized_today_attributes_each_leg_to_its_own_day(self):
        """平仓腿 D1、半仓腿 D2：各自只计入当日，不重复。"""
        cfg = StrategyConfig()
        s = signal(took_profit_half=True, half_exit_date="2026-09-08", half_exit_price=10.5,
                   half_exit_qty=400,
                   half_exit_signal={"fill_timestamp": at("2026-09-08", "14:55")},
                   status="closed", exit_date=DAY, exit_price=11.0,
                   exit_signal={"fill_timestamp": at(DAY, "10:00")})
        day1 = build_shadow_state([s], cfg, DAY, at(DAY, "15:00"))
        day2 = build_shadow_state([s], cfg, "2026-09-08", at("2026-09-08", "15:00"))
        # D1 只有平仓腿（半仓腿尚未发生，全 1000 股）：11000 − 佣金 5 − 印花税 5.5 − 成本 10005
        self.assertAlmostEqual(day1.realized_today, 984.5, places=2)
        # D2 只有半仓腿（400 股，成本按 40% 摊）：4200 − 佣金 5 − 印花税 2.1 − 4002
        self.assertAlmostEqual(day2.realized_today, 190.9, places=2)

    def test_small_cash_half_leg_prorates_cost_like_backtest(self):
        """最低佣金生效的小资金场景：半仓后剩余成本按比例摊，break_even 与回测一致。"""
        from dragon_quant.review_account.models import Position
        cfg = StrategyConfig(initial_cash=10000)
        backtest_pos = Position("600001", "样本", 200, "2026-09-04", 10.0, 2005.0,
                                "buy", "buy", highest_price=10.0)
        apply_sell_fill(backtest_pos, 10.5, "next_day_limit_up_half", cfg)
        shadow_pos = position_as_of(
            signal(qty=200, took_profit_half=True, half_exit_date=DAY, half_exit_price=10.5,
                   half_exit_qty=100, half_exit_signal={"fill_timestamp": at(DAY, "14:55")}),
            cfg, DAY, at(DAY, "15:00"))
        self.assertEqual(shadow_pos.qty, backtest_pos.qty)
        self.assertAlmostEqual(shadow_pos.cost, backtest_pos.cost, places=9)
        self.assertAlmostEqual(break_even_price(shadow_pos, cfg),
                               break_even_price(backtest_pos, cfg), places=9)


class TestHalfAccounting(unittest.TestCase):
    def test_half_fill_keeps_position_and_parity_with_backtest(self):
        cfg = StrategyConfig()
        p = position()
        sig = SignalEngine(cfg, positions=[copy.deepcopy(p)])
        acc = TradingEngine(cfg, AccountState(100000, [copy.deepcopy(p)]))
        r = row(phase="late", price=11, timestamp=at(DAY, "14:55"))
        e1 = MarketEvent(at(DAY, "14:55"), "late", {p.code: r})
        e1.allow_buy = False
        self.assertEqual([o["code"] for o in sig.step(copy.deepcopy(e1))["pending"]],
                         ["next_day_limit_up_half"])
        self.assertEqual([o["code"] for o in acc.step(copy.deepcopy(e1))["pending"]],
                         ["next_day_limit_up_half"])
        r2 = row(phase="fill", price=11, timestamp=at(DAY, "14:55") + 1000)
        e2 = MarketEvent(r2["observed_at"], "fill", {p.code: r2})
        e2.allow_buy = False
        s2 = sig.step(copy.deepcopy(e2))
        a2 = acc.step(copy.deepcopy(e2))
        self.assertTrue(s2["sells"][0]["partial"])
        self.assertEqual(s2["sells"][0]["qty"], a2["trades"][0].qty)
        self.assertEqual(sig.positions[0].qty, acc.state.positions[0].qty)
        self.assertTrue(sig.positions[0].took_profit_half)
        self.assertEqual(sig.positions[0].qty, 500)
        self.assertAlmostEqual(sig.positions[0].cost, acc.state.positions[0].cost, places=9)
        self.assertAlmostEqual(sig.positions[0].realized_pnl,
                               acc.state.positions[0].realized_pnl, places=9)

    def test_apply_sell_fill_is_the_shared_primitive(self):
        cfg = StrategyConfig()
        p = position()
        fill = apply_sell_fill(p, 11.0, "next_day_limit_up_half", cfg)
        self.assertTrue(fill["partial"])
        self.assertEqual(fill["qty"], 500)
        self.assertEqual(p.qty, 500)
        self.assertTrue(p.took_profit_half)
        self.assertGreater(fill["pnl"], 0)


def _pool_ctx():
    """trader 现在消费 collect_candidates_ex 的 (candidates, ctx) 二元组。"""
    return {"ranking_mode": "legacy", "pool_source": "legacy",
            "fallback_reason": None, "pool_empty": False,
            "prep_snapshot_date": None, "auction_rows": 0, "degraded": []}


class _CrashCloseData(FakeData):
    """日K兜底：当日开盘 10.0、收盘 9.0（触发 -3.5% 首日止损），分时缺失。"""

    def daily(self, code, start, refresh=False):
        out = []
        for bar in history("2026-09-09", price=10.0):
            if bar.timestamp == at(DAY, "15:00"):
                bar = KBar(bar.timestamp, bar.volume, 10.0, 10.2, 8.9, 9.0,
                           0, -10.0, 12, 1_000_000_000)
            out.append(bar)
        return out


class _CrashIntradayData(FakeData):
    """严格盘中：全天 5 分钟 K 都是 9.5（触发 -3.5% 首日止损且未封死跌停，可成交）。"""

    def intraday(self, code, day, until=None):
        return validate_bars(bars(day, price=9.5), day, until)


class _FirstDayDipData(FakeData):
    """首根5分钟K插针到 -5%，其余时段回到 10.0：触发首日减半且剩余半仓当日不动。"""

    def intraday(self, code, day, until=None):
        b = bars(day, price=10.0)
        b[0] = KBar(b[0].timestamp, 10000, 10.0, 10.05, 9.5, 10.0, 0, 0, 1.0, 100000)
        return validate_bars(b, day, until)


class TestLiveTraderAlignment(unittest.TestCase):
    """trader 层：数据分级（codes 全集）、峰值写回、as-of、落库标注。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        self._conn = sqlite3.connect(self._db_path)
        self._patch = patch("dragon_quant.infra.storage.connection._connect",
                            side_effect=lambda: sqlite3.connect(self._db_path))
        self._patch.start()
        connection.init_db()

    def tearDown(self):
        self._patch.stop()
        self._conn.close()
        self._tmpdir.cleanup()

    def _trader(self, data):
        return LiveTrader(StrategyConfig(), source="v2", data=data)

    def test_sell_coverage_includes_candidates(self):
        """候选分时缺失 → 整日 daily_fallback（与回测 codes = candidates ∪ positions 一致）。"""
        signal_store.insert_signal("600001", "持仓", "2026-09-04", 10.0, "buy", "买", {},
                         "v2", qty=1000)
        cand = {"code": "600002", "name": "候选", "rank": 1, "composite_score": 80,
                "is_true_dragon": True, "trade_date": "2026-09-04"}
        with patch("dragon_quant.review_account.live_trade.trader.collect_candidates",
                   return_value=[cand]):
            result = self._trader(MissingCodeData("600002")).sell(DAY, "10:00")
        self.assertEqual(result["data_quality"], "daily_fallback")
        # 非强制运行不打标（卖出路径独立于买入路径的 mutation）。
        for s in result["sells"]:
            self.assertNotIn("forced", s["signal"])
        self.assertNotIn(
            "forced", signal_store.list_all_signals(source="v2")[0]["exit_signal"])

    def test_buy_coverage_includes_positions(self):
        """持仓分时缺失 → buy 同样整日 daily_fallback（codes 含 as-of 持仓）。"""
        signal_store.insert_signal("600002", "持仓", "2026-09-04", 10.0, "buy", "买", {},
                         "v2", qty=1000)
        with patch("dragon_quant.review_account.live_trade.trader.collect_candidates_ex",
                   return_value=([CAND], _pool_ctx())):
            result = self._trader(MissingCodeData("600002")).buy(DAY, "10:00")
        self.assertEqual(result["data_quality"], "daily_fallback")

    def test_buy_updates_existing_position_peaks(self):
        signal_store.insert_signal(CAND["code"], "持仓", "2026-09-04", 10.0, "buy", "买", {},
                         "v2", qty=1000)
        with patch("dragon_quant.review_account.live_trade.trader.collect_candidates_ex",
                   return_value=([], _pool_ctx())):
            self._trader(RisingData()).buy(DAY, "10:00")
        row_sig = signal_store.list_open_signals(source="v2")[0]
        self.assertGreater(row_sig["highest_price"], 10.3)
        self.assertGreater(row_sig["highest_return"], 3.0)

    def test_buy_flags_unpersisted_when_row_already_open(self):
        """回放到当日早盘：未来（09-08）的 open 行不参与 as-of，但写库命中幂等 → 标注未落库。"""
        signal_store.insert_signal(CAND["code"], "未来", "2026-09-08", 10.0, "buy", "买", {},
                         "v2", qty=1000)
        signal_store.update_signal_peaks(CAND["code"], 3.0, 9.9)  # 该未来行的既有峰值
        with patch("dragon_quant.review_account.live_trade.trader.collect_candidates_ex",
                   return_value=([CAND], _pool_ctx())):
            result = self._trader(FakeData()).buy(DAY, "10:00")
        self.assertEqual(len(result["buys"]), 1)
        self.assertFalse(result["buys"][0]["persisted"])
        # 未落库的买入不得把本次回放峰值写到那条无关 open 行上
        row_sig = signal_store.list_open_signals(source="v2")[0]
        self.assertAlmostEqual(row_sig["highest_price"], 9.9)
        self.assertAlmostEqual(row_sig["highest_return"], 3.0)

    def test_buy_records_qty_for_break_even_parity(self):
        with patch("dragon_quant.review_account.live_trade.trader.collect_candidates_ex",
                   return_value=([CAND], _pool_ctx())):
            result = self._trader(FakeData()).buy(DAY, "10:00")
        b = result["buys"][0]
        self.assertEqual(b["qty"], notional_quantity(StrategyConfig(), b["entry_price"]))
        row_sig = signal_store.list_open_signals(source="v2")[0]
        self.assertEqual(row_sig["qty"], b["qty"])
        self.assertNotIn("forced", row_sig["entry_signal"])  # 非强制运行不打标

    def test_buy_pending_unfilled_detail(self):
        """回放结束仍有 pending BUY：保留「命中但未成交」诊断（--force 截断场景）。

        用 open + bar 两帧，让末帧 phase 与决策帧不同；断言 unfilled detail 的
        observed_at/phase 仍取决策帧（W2 回归）。
        """
        open_row = row()
        bar_row = build_row(history(DAY), DAY, 10, [], at(DAY, "09:35"))
        bar_row["phase"] = "bar"
        events = [
            MarketEvent(at(DAY, "09:30"), "open", {"600001": open_row}, [CAND]),
            MarketEvent(at(DAY, "09:35"), "bar", {"600001": bar_row}, [CAND]),
        ]
        with patch("dragon_quant.review_account.live_trade.trader.collect_candidates_ex",
                   return_value=([CAND], _pool_ctx())), \
                patch("dragon_quant.review_account.live_trade.trader.historical_events",
                      return_value=iter(events)):
            result = self._trader(FakeData()).buy(DAY, "10:00")
        unfilled = [d for d in result["details"]
                    if d.get("reason_code") == "buy_pending_unfilled"]
        self.assertEqual(len(unfilled), 1)
        self.assertEqual(len(unfilled[0]["checks"]), 4)
        self.assertEqual(unfilled[0]["phase"], "open")
        self.assertEqual(unfilled[0]["observed_at"], open_row["observed_at"])

    def test_first_day_half_routes_to_half_close_and_rebuilds(self):
        """首日减半（first_day_stop 开启）：partial 走 half_close_signal，影子状态重建 qty/took_profit_half。"""
        signal_store.insert_signal("600001", "样本", "2026-09-04", 10.0, "buy", "买", {},
                         "v2", qty=1000)
        cfg = StrategyConfig(sell_rule_toggles={"first_day_stop": True})
        trader = LiveTrader(cfg, source="v2", data=_FirstDayDipData())
        with patch("dragon_quant.review_account.live_trade.trader.collect_candidates", return_value=[]):
            result = trader.sell(DAY, "10:00")
        self.assertEqual(len(result["sells"]), 1)
        sell = result["sells"][0]
        self.assertTrue(sell["partial"])
        self.assertEqual(sell["reason_code"], "first_day_stop_half")
        self.assertEqual(sell["qty"], 500)
        self.assertEqual(result["held"][0]["qty"], 500)
        self.assertTrue(result["held"][0]["took_profit_half"])
        row_sig = signal_store.list_all_signals(source="v2")[0]
        self.assertEqual(row_sig["status"], "open")          # 剩余半仓继续跟踪
        self.assertTrue(row_sig["took_profit_half"])
        self.assertEqual(row_sig["half_exit_qty"], 500)
        self.assertEqual(row_sig["half_exit_reason_code"], "first_day_stop_half")
        # 同一信号不得再写第二条半仓腿（took_profit_half 幂等守卫）
        self.assertFalse(signal_store.half_close_signal("600001", DAY, 9.9, 100,
                                              "next_day_limit_up_half", "重复减半", {}))
        self.assertEqual(signal_store.list_all_signals(source="v2")[0]["half_exit_qty"], 500)
        rebuilt = position_as_of(row_sig, cfg, DAY, at(DAY, "15:00"))
        self.assertTrue(rebuilt.took_profit_half)
        self.assertEqual(rebuilt.qty, 500)

    def test_forced_buy_persists_forced_marker(self):
        with patch("dragon_quant.review_account.live_trade.trader.datetime",
                   _mock_datetime(DAY, 15, 30)), \
                patch("dragon_quant.review_account.live_trade.trader.collect_candidates_ex",
                      return_value=([CAND], _pool_ctx())):
            result = self._trader(FakeData()).buy(DAY, force=True)
        self.assertTrue(result["forced"])
        self.assertTrue(result["buys"])
        self.assertIs(signal_store.list_open_signals(source="v2")[0]["entry_signal"]["forced"], True)

    def test_forced_sell_persists_forced_marker(self):
        signal_store.insert_signal("600001", "样本", "2026-09-04", 10.0, "buy", "买", {},
                         "v2", qty=1000)
        trader = self._trader(_CrashIntradayData())
        with patch("dragon_quant.review_account.live_trade.trader.datetime",
                   _mock_datetime(DAY, 15, 30)), \
                patch("dragon_quant.review_account.live_trade.trader.collect_candidates",
                      return_value=[]), \
                patch("dragon_quant.review_account.live_trade.trader.signal_store.close_signal",
                      wraps=signal_store.close_signal) as close:
            result = trader.sell(DAY, force=True)
        self.assertTrue(result["forced"])
        self.assertEqual(len(result["sells"]), 1)
        self.assertFalse(result["sells"][0]["partial"])
        self.assertIs(close.call_args.args[5]["forced"], True)
        self.assertIs(signal_store.list_all_signals(source="v2")[0]["exit_signal"]["forced"], True)

    def test_forced_half_close_persists_forced_marker(self):
        signal_store.insert_signal("600001", "样本", "2026-09-04", 10.0, "buy", "买", {},
                         "v2", qty=1000)

        class _HalfEngine:
            positions = []

            def __init__(self, *args, **kwargs):
                self._fired = False

            def step(self, event):
                if self._fired:
                    return {"sells": []}
                self._fired = True
                return {"sells": [{
                    "code": "600001", "name": "样本", "exit_date": DAY,
                    "exit_price": 10.5, "qty": 500, "partial": True,
                    "reason_code": "next_day_limit_up_half",
                    "reason_text": "次日涨停减半", "hold_days": 1,
                    "signal": {"kind": "half"}}]}

        trader = self._trader(FakeData())
        with patch("dragon_quant.review_account.live_trade.trader.datetime",
                   _mock_datetime(DAY, 15, 30)), \
                patch("dragon_quant.review_account.live_trade.trader.collect_candidates",
                      return_value=[]), \
                patch("dragon_quant.review_account.live_trade.trader.SignalEngine", _HalfEngine), \
                patch("dragon_quant.review_account.live_trade.trader.signal_store.half_close_signal",
                      return_value=True) as half:
            result = trader.sell(DAY, force=True)
        self.assertTrue(result["forced"])
        self.assertEqual(len(result["sells"]), 1)
        self.assertIs(half.call_args.args[6]["forced"], True)

        # 非强制（10:00 盘中）同一半仓腿不得打标。
        with patch("dragon_quant.review_account.live_trade.trader.datetime",
                   _mock_datetime(DAY, 10, 0)), \
                patch("dragon_quant.review_account.live_trade.trader.collect_candidates",
                      return_value=[]), \
                patch("dragon_quant.review_account.live_trade.trader.SignalEngine", _HalfEngine), \
                patch("dragon_quant.review_account.live_trade.trader.signal_store.half_close_signal",
                      return_value=True) as half_normal:
            result_normal = trader.sell(DAY)
        self.assertFalse(result_normal["forced"])
        self.assertNotIn("forced", half_normal.call_args.args[6])

    def test_force_after_close_daily_fallback_does_not_fill_at_close(self):
        """强制盘后 + 日K兜底：收盘价成交腿（15:00:00.001）必须被截断，不伪造成交。"""
        signal_store.insert_signal("600001", "样本", "2026-09-04", 10.0, "buy", "买", {},
                         "v2", qty=1000)
        trader = self._trader(_CrashCloseData(intraday_missing=[DAY]))

        with patch("dragon_quant.review_account.live_trade.trader.datetime",
                   _mock_datetime(DAY, 15, 30)), \
                patch("dragon_quant.review_account.live_trade.trader.collect_candidates",
                      return_value=[]):
            forced = trader.sell(DAY, force=True)
        self.assertTrue(forced["forced"])
        self.assertEqual(forced["data_quality"], "daily_fallback")
        self.assertEqual(forced["sells"], [])
        # 15:00 的 late 决策事件仍执行（更新峰值），只是 15:00:00.001 的成交腿被截断。
        self.assertEqual(len(forced["held"]), 1)
        self.assertGreater(forced["held"][0]["highest_return"], 0)

        # 对照：同为日K兜底，盘中 15:05（未强制）会以收盘价成交 → 证明确有可触发的平仓腿。
        with patch("dragon_quant.review_account.live_trade.trader.datetime",
                   _mock_datetime(DAY, 15, 5)), \
                patch("dragon_quant.review_account.live_trade.trader.collect_candidates",
                      return_value=[]):
            control = trader.sell(DAY)
        self.assertFalse(control["forced"])
        self.assertEqual(len(control["sells"]), 1)
        self.assertLess(control["sells"][0]["exit_price"], 10.0)


class TestSignalMigration(unittest.TestCase):
    """旧 schema 迁移：幂等补列 + 旧行可读。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "legacy.db")
        conn = sqlite3.connect(self._db_path)
        conn.execute("""CREATE TABLE buy_sell_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, name TEXT,
            source TEXT NOT NULL DEFAULT 'v2', entry_date TEXT NOT NULL, entry_price REAL,
            entry_reason_code TEXT, entry_reason_text TEXT, entry_signal_json TEXT,
            highest_return REAL DEFAULT 0, highest_price REAL DEFAULT 0,
            took_profit_half INTEGER DEFAULT 0, status TEXT NOT NULL DEFAULT 'open',
            exit_date TEXT, exit_price REAL, exit_reason_code TEXT, exit_reason_text TEXT,
            exit_signal_json TEXT, hold_days INTEGER, created_at TEXT, updated_at TEXT)""")
        conn.execute("INSERT INTO buy_sell_signals(code, entry_date, entry_price, status) "
                     "VALUES ('600001', '2026-09-04', 10, 'open')")
        conn.commit()
        conn.close()
        self._patch = patch("dragon_quant.infra.storage.connection._connect",
                            side_effect=lambda: sqlite3.connect(self._db_path))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def test_adds_columns_and_reads_legacy_rows(self):
        connection.init_db()
        conn = sqlite3.connect(self._db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(buy_sell_signals)")}
        conn.close()
        for name in ("qty", "half_exit_date", "half_exit_price", "half_exit_qty",
                     "half_exit_reason_code", "half_exit_reason_text", "half_exit_signal_json"):
            self.assertIn(name, cols)
        rows = signal_store.list_all_signals(source="v2")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["qty"])
        self.assertFalse(rows[0]["took_profit_half"])


class TestHalfSignalStorage(unittest.TestCase):
    """半仓腿落库：冻结 qty/原因、保持 open、幂等、随后可全清。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        self._conn = sqlite3.connect(self._db_path)
        with self._connect():
            connection.init_db()

    def tearDown(self):
        self._conn.close()
        self._tmpdir.cleanup()

    def _connect(self):
        return patch("dragon_quant.infra.storage.connection._connect",
                     side_effect=lambda: sqlite3.connect(self._db_path))

    def test_insert_signal_positional_source_and_qty_keyword(self):
        """MAJOR #4 回归：位置传 source 仍兼容，qty 为 keyword-only。"""
        with self._connect():
            sid, created = signal_store.insert_signal("600003", "样本", "2026-09-07", 10.0,
                                            "buy", "买", {}, "v2", qty=300)
            self.assertTrue(created)
            s = signal_store.list_open_signals(source="v2")[0]
            self.assertEqual(s["id"], sid)
            self.assertEqual(s["source"], "v2")
            self.assertEqual(s["qty"], 300)

    def test_half_close_freezes_leg_and_keeps_open(self):
        with self._connect():
            signal_store.insert_signal("600001", "样本", "2026-09-07", 10.0, "buy", "买", {},
                             "v2", qty=1000)
            self.assertTrue(signal_store.half_close_signal("600001", "2026-09-08", 10.5, 500,
                                                 "next_day_limit_up_half", "减半", {"x": 1}))
            self.assertFalse(signal_store.half_close_signal("600001", "2026-09-08", 10.5, 500,
                                                  "next_day_limit_up_half", "减半", {"x": 1}))
            rows = signal_store.list_open_signals(source="v2")
            self.assertEqual(len(rows), 1)
            s = rows[0]
            self.assertEqual(s["status"], "open")
            self.assertTrue(s["took_profit_half"])
            self.assertEqual(s["qty"], 1000)
            self.assertEqual(s["half_exit_qty"], 500)
            self.assertAlmostEqual(s["half_exit_price"], 10.5)
            self.assertEqual(s["half_exit_reason_code"], "next_day_limit_up_half")
            self.assertEqual(s["half_exit_signal"], {"x": 1})
            # 剩余半仓继续跟踪，随后可被全清
            self.assertTrue(signal_store.close_signal("600001", "2026-09-09", 11.0,
                                            "trailing_take_profit", "移动止盈", {}, 2))
            self.assertEqual(signal_store.list_open_signals(source="v2"), [])


if __name__ == "__main__":
    unittest.main()
