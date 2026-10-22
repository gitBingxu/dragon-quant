"""四类买点诊断（checks）与龙回头默认开启的单元测试。"""
import json
import unittest

from dragon_quant.live_trade.signal_engine import SignalEngine
from dragon_quant.review_account.engine import TradingEngine
from dragon_quant.review_account.market import at, build_daily_row, build_row
from dragon_quant.review_account.models import AccountState, MarketEvent, StrategyConfig
from dragon_quant.review_account.strategy import (
    BUY_CHECK_DISPLAY_ORDER, BUY_SIGNAL_DIVERGENCE_BREAK, BUY_SIGNAL_DRAGON_COMEBACK,
    BUY_SIGNAL_MA5_PULLBACK, BUY_SIGNAL_OPEN_TURN_STRONG, evaluate_buy,
    explain_buy_candidate, explain_buy_checks,
)

from tests.test_review_account import CAND, DAY, bars, history, row
from tests.test_review_account_timing import _dragon_hist, _make_hist

MA5 = BUY_SIGNAL_MA5_PULLBACK
STRONG = BUY_SIGNAL_OPEN_TURN_STRONG
DIVERGENCE = BUY_SIGNAL_DIVERGENCE_BREAK
DRAGON = BUY_SIGNAL_DRAGON_COMEBACK


def _checks(detail):
    checks = detail["checks"] if isinstance(detail, dict) else detail
    return {c["rule"]: c for c in checks}


def _one_word_hist():
    return [{"is_one_word_board": True, "pct": 10, "volume": 200},
            {"is_one_word_board": True, "pct": 10, "volume": 100}]


class TestBuyCheckStatuses(unittest.TestCase):
    def test_ma5_check_failed_at_open_then_window_closed(self):
        cfg = StrategyConfig()
        r = row(price=10.9)
        checks = _checks(explain_buy_candidate(CAND, r, cfg, r["prev_row"]))
        self.assertEqual(checks[MA5]["status"], "failed")
        self.assertIn("距MA5", checks[MA5]["reason"])
        later = row(phase="bar", price=10.9, timestamp=at(DAY, "09:35"),
                    known=bars(price=10.9)[:1])
        checks = _checks(explain_buy_candidate(CAND, later, cfg, later["prev_row"]))
        self.assertEqual(checks[MA5]["status"], "window_closed")

    def test_turn_strong_pending_at_open_then_failed_at_first_bar(self):
        cfg = StrategyConfig()
        opening = row(price=10.5)
        checks = _checks(explain_buy_candidate(
            CAND, opening, cfg, opening["prev_row"], None, opening["intraday_bars"]))
        self.assertEqual(checks[STRONG]["status"], "pending")
        self.assertIn("待09:35", checks[STRONG]["reason"])
        b = bars(price=10.5, turnover=0.2)[:1]
        first_bar = row(phase="bar", price=10.5, timestamp=at(DAY, "09:35"), known=b)
        checks = _checks(explain_buy_candidate(
            CAND, first_bar, cfg, first_bar["prev_row"], None, first_bar["intraday_bars"]))
        self.assertEqual(checks[STRONG]["status"], "failed")
        self.assertIn("换手", checks[STRONG]["reason"])

    def test_divergence_pending_then_passes_then_fails(self):
        cfg = StrategyConfig()
        hist = _one_word_hist()
        b2 = bars(price=10.9)[:2]
        r2 = row(phase="bar", price=10.9, timestamp=at(DAY, "09:40"), known=b2)
        checks = _checks(explain_buy_candidate(
            CAND, r2, cfg, r2["prev_row"], hist, b2))
        self.assertEqual(checks[DIVERGENCE]["status"], "pending")
        self.assertIn("承接窗口需6根", checks[DIVERGENCE]["reason"])

        b6 = bars(price=10.9)[:6]
        r6 = row(phase="bar", price=10.9, timestamp=at(DAY, "10:00"), known=b6)
        detail = explain_buy_candidate(CAND, r6, cfg, r6["prev_row"], hist, b6)
        checks = _checks(detail)
        self.assertEqual(detail["reason_code"], DIVERGENCE)
        self.assertEqual(checks[DIVERGENCE]["status"], "passed")
        self.assertTrue(checks[DIVERGENCE]["selected"])

        b6[0].low = 9.9
        detail = explain_buy_candidate(CAND, r6, cfg, r6["prev_row"], hist, b6)
        checks = _checks(detail)
        self.assertEqual(checks[DIVERGENCE]["status"], "failed")
        self.assertIn("跌破昨收", checks[DIVERGENCE]["reason"])

    def test_divergence_window_uses_configured_bars(self):
        cfg = StrategyConfig(divergence_confirm_bars=4)
        hist = _one_word_hist()
        b4 = bars(price=10.9)[:4]
        r = row(phase="bar", price=10.9, timestamp=b4[-1].timestamp, known=b4)
        checks = _checks(explain_buy_candidate(CAND, r, cfg, r["prev_row"], hist, b4))
        self.assertIn("09:50 前4根5分钟K", checks[DIVERGENCE]["window"])

    def test_divergence_reason_identical_at_later_frames(self):
        cfg = StrategyConfig()
        hist = _one_word_hist()
        b6 = bars(price=10.9)[:6]
        r10 = row(phase="bar", price=10.9, timestamp=at(DAY, "10:00"), known=b6)
        c10 = _checks(explain_buy_checks(
            CAND, r10, cfg, r10["prev_row"], hist, b6))[DIVERGENCE]
        full = bars(price=10.9)
        r55 = row(phase="late", price=10.9, timestamp=at(DAY, "14:55"), known=full)
        c55 = _checks(explain_buy_checks(
            CAND, r55, cfg, r55["prev_row"], hist, full))[DIVERGENCE]
        self.assertEqual(c10["status"], c55["status"])
        self.assertEqual(c10["reason"], c55["reason"])

    def test_dragon_comeback_reason_reports_pullback_days(self):
        cfg = StrategyConfig()
        hist = _make_hist(
            [(7.0, 6.9, 200, 0.0, False)] * 14
            + [(8.0, 7.3, 250, 10.0, True), (9.0, 8.0, 280, 11.1, True)])
        r = row(price=10.9)
        detail = explain_buy_candidate(CAND, r, cfg, r["prev_row"], hist)
        checks = _checks(detail)
        self.assertEqual(checks[DRAGON]["status"], "failed")
        self.assertIn("回调天数0 < 3", checks[DRAGON]["reason"])

    def test_dragon_comeback_passes_by_default(self):
        cfg = StrategyConfig()
        self.assertTrue(cfg.dragon_comeback_enabled)
        r = row(price=10.9)
        detail = explain_buy_candidate(CAND, r, cfg, r["prev_row"], _dragon_hist())
        checks = _checks(detail)
        self.assertEqual(detail["reason_code"], DRAGON)
        self.assertEqual(checks[DRAGON]["status"], "passed")
        self.assertTrue(checks[DRAGON]["selected"])


class TestBuyCheckSameSource(unittest.TestCase):
    def test_checks_pass_iff_actual_signal(self):
        cfg = StrategyConfig()
        cases = []

        r = row()
        cases.append((MA5, r, None, None))

        b1 = bars(price=10.5, turnover=0.6)[:1]
        r = row(phase="bar", price=10.5, timestamp=at(DAY, "09:35"), known=b1)
        cases.append((STRONG, r, None, b1))

        hist = _one_word_hist()
        b6 = bars(price=10.9)[:6]
        r = row(phase="bar", price=10.9, timestamp=at(DAY, "10:00"), known=b6)
        cases.append((DIVERGENCE, r, hist, b6))

        r = row(price=10.9)
        cases.append((DRAGON, r, _dragon_hist(), None))

        for rule, r, hist_rows, intraday in cases:
            with self.subTest(rule=rule):
                sig = evaluate_buy(CAND, r, cfg, r["prev_row"], hist_rows, intraday)
                self.assertIsNotNone(sig)
                self.assertEqual(sig["code"], rule)
                checks = _checks(explain_buy_candidate(
                    CAND, r, cfg, r["prev_row"], hist_rows, intraday))
                self.assertEqual(checks[rule]["status"], "passed")
                self.assertTrue(checks[rule]["selected"])
                self.assertEqual(sum(1 for c in checks.values() if c["selected"]), 1)

    def test_selected_requires_passed_status(self):
        cfg = StrategyConfig()
        r = row(price=10.5)  # ① 未通过，② 待确认
        checks = explain_buy_checks(CAND, r, cfg, r["prev_row"], None, [],
                                    selected_rule=STRONG)
        self.assertFalse(any(c["selected"] for c in checks))
        self.assertEqual({c["rule"]: c["status"] for c in checks}[STRONG], "pending")


class TestBuyCheckEarlyExit(unittest.TestCase):
    def _assert_all(self, detail, status):
        self.assertEqual([c["rule"] for c in detail["checks"]], list(BUY_CHECK_DISPLAY_ORDER))
        self.assertEqual({c["status"] for c in detail["checks"]}, {status})
        self.assertFalse(any(c["selected"] for c in detail["checks"]))

    def test_sentiment_ban_is_skipped(self):
        cfg = StrategyConfig(sentiment_ban_enabled=True)
        r = row()
        detail = explain_buy_candidate(CAND, r, cfg, r["prev_row"], sentiment_pct=0.0)
        self.assertEqual(detail["reason_code"], "sentiment_ban")
        self._assert_all(detail, "skipped")

    def test_score_too_low_is_skipped(self):
        cfg = StrategyConfig()
        r = row()
        detail = explain_buy_candidate({**CAND, "composite_score": 40}, r, cfg, r["prev_row"])
        self.assertEqual(detail["reason_code"], "score_too_low")
        self._assert_all(detail, "skipped")

    def test_not_true_dragon_is_skipped(self):
        cfg = StrategyConfig()
        r = row()
        detail = explain_buy_candidate({**CAND, "is_true_dragon": False}, r, cfg, r["prev_row"])
        self.assertEqual(detail["reason_code"], "not_true_dragon")
        self._assert_all(detail, "skipped")

    def test_missing_kline_is_unavailable(self):
        detail = explain_buy_candidate(CAND, None, StrategyConfig())
        self.assertEqual(detail["reason_code"], "missing_kline")
        self._assert_all(detail, "unavailable")
        self.assertIsNone(detail["observed_at"])
        self.assertIsNone(detail["phase"])

    def test_auction_veto_keeps_shape_and_selected(self):
        cfg = StrategyConfig(auction_veto_enabled=True)
        cand = {**CAND, "entry_priority": {"auction": {
            "state": False, "loaded": True, "reject_reason": "竞价弱",
            "strength": 20, "level": "弱"}}}
        r = row()
        detail = explain_buy_candidate(cand, r, cfg, r["prev_row"])
        self.assertEqual(detail["reason_code"], "auction_veto")
        checks = _checks(detail)
        self.assertEqual(checks[MA5]["status"], "passed")
        self.assertTrue(checks[MA5]["selected"])

    def test_daily_fallback_marks_intraday_rules_unavailable(self):
        cfg = StrategyConfig()
        r = build_daily_row(history("2026-09-09"), DAY, "late", at(DAY, "15:00"))
        checks = {c["rule"]: c for c in explain_buy_checks(
            CAND, r, cfg, r["prev_row"], r["hist_rows"], r["intraday_bars"])}
        self.assertEqual(checks[STRONG]["status"], "unavailable")
        self.assertEqual(checks[DIVERGENCE]["status"], "unavailable")
        self.assertNotEqual(checks[MA5]["status"], "unavailable")


class TestBuyCheckDetails(unittest.TestCase):
    def test_checks_json_serializable_and_framed(self):
        r = row()
        detail = explain_buy_candidate(CAND, r, StrategyConfig(), r["prev_row"])
        json.dumps(detail, ensure_ascii=False, allow_nan=False)
        self.assertEqual(detail["observed_at"], r["observed_at"])
        self.assertEqual(detail["phase"], r["phase"])
        for check in detail["checks"]:
            self.assertEqual(set(check), {"rule", "label", "window", "status",
                                          "selected", "reason"})

    def test_late_frame_reason_is_not_fallback(self):
        cfg = StrategyConfig()
        r = row(phase="late", price=10.9, timestamp=at(DAY, "14:55"),
                known=bars(price=10.9))
        detail = explain_buy_candidate(CAND, r, cfg, r["prev_row"], None, r["intraday_bars"])
        self.assertEqual(detail["reason_code"], "no_buy_pattern")
        self.assertIn("距MA5", detail["reason_text"])
        self.assertNotEqual(detail["reason_text"], "当前已知数据未触发买点")

    def test_signal_payload_keys_preserved(self):
        cfg = StrategyConfig()
        b1 = bars(price=10.5, turnover=0.6)[:1]
        r = row(phase="bar", price=10.5, timestamp=at(DAY, "09:35"), known=b1)
        sig = evaluate_buy(CAND, r, cfg, r["prev_row"], None, b1)
        self.assertEqual(sig["code"], STRONG)
        self.assertEqual(sig["signal"]["first_bar_turnover"], 0.6)

        hist = _one_word_hist()
        b6 = bars(price=10.9)[:6]
        r = row(phase="bar", price=10.9, timestamp=at(DAY, "10:00"), known=b6)
        sig = evaluate_buy(CAND, r, cfg, r["prev_row"], hist, b6)
        self.assertEqual(sig["code"], DIVERGENCE)
        self.assertEqual(sig["signal"]["divergence_window_bars"], 6)
        self.assertEqual(sig["signal"]["board_count"], 2)

    def test_rule_exception_degrades_single_check(self):
        cfg = StrategyConfig()
        r = row(price=10.9)
        r["prev_row"] = {**r["prev_row"], "ma5": "bad"}  # 触发 ① 类型异常
        checks = {c["rule"]: c for c in explain_buy_checks(CAND, r, cfg, r["prev_row"])}
        self.assertEqual(checks[MA5]["status"], "unavailable")
        self.assertIn("诊断异常", checks[MA5]["reason"])
        self.assertEqual(len(checks), 4)


class TestEngineChecks(unittest.TestCase):
    def test_already_holding_detail_has_checks(self):
        cfg = StrategyConfig()
        acc = AccountState(100000)
        engine = SignalEngine(cfg, positions=[], held_codes={"600001"},
                              account=acc, evaluate_sells=False)
        result = engine.step(MarketEvent(at(DAY, "09:30"), "open", {"600001": row()}, [CAND]))
        detail = next(d for d in result["details"] if d["code"] == "600001")
        self.assertEqual(detail["reason_code"], "already_holding")
        self.assertEqual(len(detail["checks"]), 4)

    def test_event_gate_emits_skipped_checks(self):
        cfg = StrategyConfig()
        acc = AccountState(100000)
        acc.sold_today = True
        engine = SignalEngine(cfg, positions=[], account=acc, evaluate_sells=False)
        result = engine.step(MarketEvent(at(DAY, "09:30"), "open", {"600001": row()}, [CAND]))
        self.assertTrue(result["buy_eval_skipped"])
        detail = next(d for d in result["details"] if d["code"] == "600001")
        self.assertEqual(detail["reason_code"], "buy_eval_skipped")
        self.assertEqual({c["status"] for c in detail["checks"]}, {"skipped"})

    def test_event_gate_keeps_held_as_already_holding(self):
        cfg = StrategyConfig()
        acc = AccountState(100000)
        acc.sold_today = True
        engine = SignalEngine(cfg, positions=[], held_codes={"600001"},
                              account=acc, evaluate_sells=False)
        result = engine.step(MarketEvent(at(DAY, "09:30"), "open", {"600001": row()}, [CAND]))
        detail = next(d for d in result["details"] if d["code"] == "600001")
        self.assertEqual(detail["reason_code"], "already_holding")
        self.assertNotEqual({c["status"] for c in detail["checks"]}, {"skipped"})

    def test_passed_but_unfilled_keeps_checks(self):
        cfg = StrategyConfig()
        engine = SignalEngine(cfg, evaluate_sells=False)
        engine.step(MarketEvent(at(DAY, "09:30"), "open", {"600001": row()}, [CAND]))
        self.assertEqual(len(engine.pending), 1)
        fill = build_row(history(DAY), DAY, 10, [], at(DAY, "09:30") + 1,
                         execution_price=11.0)
        fill["phase"] = "fill"
        result = engine.step(MarketEvent(at(DAY, "09:30") + 1, "fill",
                                         {"600001": fill}, [CAND]))
        detail = next(d for d in result["details"] if d.get("reason_code") == "untradable_price")
        self.assertEqual(len(detail["checks"]), 4)
        self.assertTrue(detail["checks"][0]["selected"])

    def test_budget_insufficient_keeps_checks(self):
        cfg = StrategyConfig(initial_cash=1)
        engine = SignalEngine(cfg, evaluate_sells=False)
        engine.step(MarketEvent(at(DAY, "09:30"), "open", {"600001": row()}, [CAND]))
        fill = build_row(history(DAY), DAY, 10, [], at(DAY, "09:30") + 1,
                         execution_price=10.0)
        fill["phase"] = "fill"
        result = engine.step(MarketEvent(at(DAY, "09:30") + 1, "fill",
                                         {"600001": fill}, [CAND]))
        detail = next(d for d in result["details"] if d.get("reason_code") == "budget_insufficient")
        self.assertEqual(len(detail["checks"]), 4)

    def test_unfilled_detail_carries_order_checks(self):
        checks = [{"rule": MA5, "label": "x", "window": "w", "status": "passed",
                   "selected": True, "reason": "r"}]
        order = {"stock_code": "600001", "name": "样本", "side": "BUY",
                 "signal": {}, "checks": checks}
        detail = SignalEngine._unfilled_detail(order, row(), "buy_pending_unfilled", "未成交")
        self.assertEqual(detail["checks"], checks)
        self.assertEqual(detail["reason_code"], "buy_pending_unfilled")

    def test_trading_engine_detail_helpers_include_checks(self):
        cfg = StrategyConfig()
        engine = TradingEngine(cfg, AccountState(100000))
        sig = evaluate_buy(CAND, row(), cfg, row()["prev_row"])
        ranked = engine._ranked_out_detail(CAND, sig, 2, cfg, row=row())
        self.assertEqual(len(ranked["checks"]), 4)
        self.assertTrue(next(c for c in ranked["checks"] if c["rule"] == MA5)["selected"])
        detail = engine._unsignaled_detail(
            CAND, "daily_buy_limit", "x", cfg, row=row(), checks=ranked["checks"])
        self.assertEqual(len(detail["checks"]), 4)
        self.assertIn("observed_at", detail)


class TestRuleGuards(unittest.TestCase):
    """表驱动覆盖 ②③ 的主要 early-return，防止重构静默改变判定。"""

    def _strong_reason(self, r, bars_):
        return _checks(explain_buy_checks(
            CAND, r, StrategyConfig(), r["prev_row"], None, bars_))[STRONG]["reason"]

    def test_turn_strong_guard_reasons(self):
        b = bars(price=10.5, turnover=0.2)[:1]
        r = row(phase="bar", price=10.1, timestamp=at(DAY, "09:35"), known=b)
        self.assertIn("未突破上日高点", self._strong_reason(r, b))
        r = row(phase="bar", price=10.6, timestamp=at(DAY, "09:35"), known=b)
        self.assertIn("不在[0,", self._strong_reason(r, b))
        r = row(phase="bar", price=10.5, timestamp=at(DAY, "09:35"), known=b)
        r["prev_row"]["turnover"] = 5.0
        self.assertIn("不在弱转强区间", self._strong_reason(r, b))
        r = row(phase="bar", price=10.5, timestamp=at(DAY, "09:35"), known=b)
        r["prev_row"]["amount"] = 300_000_000
        self.assertIn("弱转强要求5亿", self._strong_reason(r, b))
        r = row(phase="bar", price=10.5, timestamp=at(DAY, "09:35"), known=b)
        self.assertIn("换手", self._strong_reason(r, b))

    def _div_reason(self, r, hist, bars_):
        return _checks(explain_buy_checks(
            CAND, r, StrategyConfig(), r["prev_row"], hist, bars_))[DIVERGENCE]["reason"]

    def test_divergence_guard_reasons(self):
        b6 = bars(price=10.9)[:6]
        r = row(phase="bar", price=10.9, timestamp=at(DAY, "10:00"), known=b6)
        self.assertIn("连续一字板0 < 2", self._div_reason(r, [], b6))
        self.assertIn("未持续缩量", self._div_reason(
            r, [{"is_one_word_board": True, "pct": 10, "volume": 100},
                {"is_one_word_board": True, "pct": 10, "volume": 200}], b6))
        self.assertIn("承接窗口需6根", self._div_reason(r, _one_word_hist(), b6[:2]))
        r_near = row(phase="bar", price=10.99, timestamp=at(DAY, "10:00"),
                     known=bars(price=10.99)[:6])
        self.assertIn("开盘仍近涨停", self._div_reason(
            r_near, _one_word_hist(), bars(price=10.99)[:6]))
        b_low = bars(price=10.9)[:6]
        b_low[0].low = 9.9
        r_low = row(phase="bar", price=10.9, timestamp=at(DAY, "10:00"), known=b_low)
        self.assertIn("跌破昨收", self._div_reason(r_low, _one_word_hist(), b_low))


class TestBuySignalGolden(unittest.TestCase):
    """重构前后同一输入必须产出同一买点（AC10 的独立护栏）。"""

    def test_golden_matrix(self):
        cfg = StrategyConfig()
        hist = _one_word_hist()
        b1s = bars(price=10.5, turnover=0.6)[:1]
        b1w = bars(price=10.5, turnover=0.2)[:1]
        b6 = bars(price=10.9)[:6]
        cases = [
            ("ma5", CAND, row(), None, None, cfg, MA5),
            ("ma5_fail", CAND, row(price=10.9), None, None, cfg, None),
            ("turn_strong", CAND, row(phase="bar", price=10.5,
                                     timestamp=at(DAY, "09:35"), known=b1s),
             None, b1s, cfg, STRONG),
            ("turn_strong_fail", CAND, row(phase="bar", price=10.5,
                                          timestamp=at(DAY, "09:35"), known=b1w),
             None, b1w, cfg, None),
            ("divergence", CAND, row(phase="bar", price=10.9,
                                     timestamp=at(DAY, "10:00"), known=b6),
             hist, b6, cfg, DIVERGENCE),
            ("dragon", CAND, row(price=10.9), _dragon_hist(), None, cfg, DRAGON),
            ("dragon_disabled", CAND, row(price=10.9), _dragon_hist(), None,
             StrategyConfig(dragon_comeback_enabled=False), None),
        ]
        for name, cand, r, h, ib, c, expected in cases:
            with self.subTest(name=name):
                sig = evaluate_buy(cand, r, c, r["prev_row"], h, ib)
                self.assertEqual(sig["code"] if sig else None, expected)


if __name__ == "__main__":
    unittest.main()
