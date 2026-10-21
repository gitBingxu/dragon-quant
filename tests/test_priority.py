"""P/A 优先级：三值语义、排序键、否决、决策记录、开关矩阵。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dragon_quant.auction.registry import AUCTION_ITEMS_MISSING
from dragon_quant.auction.strength import evaluate as evaluate_auction
from dragon_quant.review_account.engine import TradingEngine, rank_key as engine_rank_key
from dragon_quant.review_account.market import at
from dragon_quant.review_account.models import AccountState, MarketEvent, StrategyConfig
from dragon_quant.review_account.priority import (
    RANKING_MODE_LABELS, auction_state, auction_veto_reason, key_components,
    prep_state, rank_key, ranking_mode, tri_order, would_veto,
)
from dragon_quant.review_account.strategy import BuyRejected, evaluate_buy, explain_buy_candidate
from dragon_quant.live_trade.signal_engine import rank_key as signal_rank_key

DAY = "2026-10-20"
PREV = "2026-10-19"


def _row():
    return {"phase": "open", "open": 10.0, "limit_up": 11.0,
            "limit_down": 9.0, "open_gap_pct": 0.0}


def _prev_row():
    return {"ma5": 10.0, "amount": 300_000_000.0, "turnover": 10.0}


def _engine_row():
    return {**_row(), "prev_row": _prev_row(), "bar_timestamp": 1,
            "bar_close": 10.0, "hist_rows": [], "intraday_bars": []}


def _event(candidates):
    rows = {c["code"]: _engine_row() for c in candidates}
    return MarketEvent(at(DAY, "09:30"), "open", rows, candidates)


def _auction(state, **extra):
    record = {"unverified": False, "is_weak": not state}
    record.update(extra)
    return {"auction": {"state": state, "loaded": True, **record}}


def _candidate(auction_state_value=None, prep_state_value=None, **kwargs):
    cand = {"code": kwargs.pop("code", "A"), "composite_score": 80,
            "is_true_dragon": True,
            "entry_priority": {
                "t": {"state": True},
                "prep": {"state": prep_state_value, "source_date": PREV,
                         "verdict": kwargs.pop("verdict", None)},
                "auction": {"state": auction_state_value, "loaded": True,
                            "source_date": DAY, "strength": 72.0, "level": "强",
                            "is_weak": auction_state_value is False,
                            "unverified": False,
                            "reject_reason": "高位绿柱递增" if auction_state_value is False else None},
            }}
    cand.update(kwargs)
    return cand


class TestTriState(unittest.TestCase):
    def test_prep_state(self):
        self.assertIs(prep_state({"verdict": "strong"}), True)
        self.assertIs(prep_state({"verdict": "light"}), True)
        self.assertIs(prep_state({"verdict": "ignore"}), False)
        self.assertIs(prep_state({"verdict": "unrated"}), False)
        self.assertIsNone(prep_state({"verdict": None}))
        self.assertIsNone(prep_state({}))

    def test_auction_state(self):
        self.assertIs(auction_state({"is_weak": False, "unverified": False}), True)
        self.assertIs(auction_state({"is_weak": True, "unverified": False}), False)
        self.assertIsNone(auction_state({"is_weak": True, "unverified": True}))
        self.assertIsNone(auction_state({"is_weak": True, "unverified": False,
                                         "reject_reason": AUCTION_ITEMS_MISSING}))
        self.assertIsNone(auction_state({"error": "bad"}))
        self.assertIsNone(auction_state(None))

    def test_items_missing_sentinel_matches_producer(self):
        verdict = evaluate_auction("A", [], None)
        self.assertEqual(verdict.reject_reason, AUCTION_ITEMS_MISSING)
        self.assertIsNone(auction_state({"is_weak": True, "unverified": False,
                                         "reject_reason": verdict.reject_reason}))

    def test_tri_order(self):
        self.assertLess(tri_order(True), tri_order(None))
        self.assertLess(tri_order(None), tri_order(False))


class TestRankKey(unittest.TestCase):
    def test_nine_combinations_total_order(self):
        expected = []
        for a in (True, None, False):
            for p in (True, None, False):
                expected.append((a, p))
        # lexicographic by (tri_order(A), tri_order(P))
        expected.sort(key=lambda x: (tri_order(x[0]), tri_order(x[1])))
        signals = [{"priority": 300} for _ in expected]
        cands = [_candidate(a, p, code=f"C{i}") for i, (a, p) in enumerate(expected)]
        order = sorted(range(len(expected)),
                       key=lambda i: rank_key(signals[i], cands[i], True))
        self.assertEqual([expected[i] for i in order], expected)

    def test_tie_breaks(self):
        base = {"priority": 300}
        c1 = _candidate(True, True, code="A", composite_score=80)
        c2 = _candidate(True, True, code="B", composite_score=90)
        c3 = _candidate(True, True, code="C", composite_score=90)
        c4 = _candidate(True, True, code="AA", composite_score=90)
        keys = [rank_key(base, c1, True), rank_key(base, c2, True),
                rank_key({"priority": 400}, c3, True), rank_key(base, c4, True)]
        self.assertLess(keys[1], keys[0])           # composite 优先
        self.assertLess(keys[2], keys[1])           # 同分看 priority
        self.assertLess(keys[3], keys[1])           # 再同分按 code

    def test_auction_flag_off_forces_unknown(self):
        cand = _candidate(True, True)
        off = key_components({"priority": 300}, cand, False)
        on = key_components({"priority": 300}, cand, True)
        self.assertEqual(off["auction_order"], 1)
        self.assertEqual(on["auction_order"], 0)

    def test_engines_share_same_key_function(self):
        self.assertIs(engine_rank_key, signal_rank_key)
        pairs = [({"priority": 300}, _candidate(True, True, code="A")),
                 ({"priority": 400}, _candidate(None, None, code="B")),
                 ({"priority": 250}, _candidate(False, True, code="C"))]
        left = sorted(pairs, key=lambda x: engine_rank_key(x[0], x[1], True))
        right = sorted(pairs, key=lambda x: signal_rank_key(x[0], x[1], True))
        self.assertEqual([p[1]["code"] for p in left],
                         [p[1]["code"] for p in right])


class TestRankingMode(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(ranking_mode(prep_snapshot_date=PREV, auction_rows=0,
                                      auction_verified_rows=0), "prep_only")
        self.assertEqual(ranking_mode(prep_snapshot_date=None, auction_rows=0,
                                      auction_verified_rows=0), "legacy")
        self.assertEqual(ranking_mode(prep_snapshot_date=PREV, auction_rows=3,
                                      auction_verified_rows=0), "auction_unverified")
        self.assertEqual(ranking_mode(prep_snapshot_date=PREV, auction_rows=3,
                                      auction_verified_rows=2), "auction")
        self.assertEqual(set(RANKING_MODE_LABELS) >=
                         {"auction", "auction_unverified", "prep_only", "legacy"}, True)


class TestVeto(unittest.TestCase):
    def test_veto_raises_only_when_enabled_and_verified_weak(self):
        cand = _candidate(False, True)
        cfg = StrategyConfig(auction_veto_enabled=True)
        with self.assertRaises(BuyRejected) as ctx:
            evaluate_buy(cand, _row(), cfg, _prev_row())
        self.assertEqual(ctx.exception.code, "auction_veto")
        self.assertIsNotNone(ctx.exception.signal)
        # 关闭否决：同样输入产生信号
        self.assertIsNotNone(evaluate_buy(cand, _row(), StrategyConfig(), _prev_row()))

    def test_unknown_and_strong_pass(self):
        cfg = StrategyConfig(auction_veto_enabled=True)
        for state in (None, True):
            self.assertIsNotNone(
                evaluate_buy(_candidate(state, True), _row(), cfg, _prev_row()))

    def test_exempt_signal_passes(self):
        cfg = StrategyConfig(auction_veto_enabled=True,
                             auction_veto_exempt_signals=["buy_open_ma5_pullback"])
        self.assertIsNotNone(
            evaluate_buy(_candidate(False, True), _row(), cfg, _prev_row()))

    def test_veto_reason_disabled_without_switch(self):
        self.assertIsNone(auction_veto_reason(
            StrategyConfig(), _candidate(False, True), {"code": "buy_open_ma5_pullback"}))
        self.assertTrue(would_veto(_candidate(False, True)))


class TestSwitchMatrix(unittest.TestCase):
    """4 种开关组合：读取次数 / 排序是否受 A 影响 / 是否否决 / would_veto。"""

    def _collect(self, ranking, veto, rows):
        from dragon_quant.review_account.market import collect_candidates_ex
        snap = {"candidates": [
            {"code": "A", "composite_score": 80, "is_true_dragon": True,
             "verdict": "strong"},
            {"code": "B", "composite_score": 70, "is_true_dragon": True,
             "verdict": "light"},
        ]}
        cfg = StrategyConfig(source="v2", auction_ranking_enabled=ranking,
                             auction_veto_enabled=veto)
        loader_calls = []

        def loader(day, source="v2"):
            loader_calls.append(day)
            return rows

        with patch("dragon_quant.storage.assets_db.get_prep_snapshot",
                   return_value=snap):
            pool, ctx = collect_candidates_ex(
                [PREV, DAY], DAY, cfg, lambda *a, **k: [], auction_loader=loader)
        return pool, ctx, loader_calls, cfg

    def test_all_off_zero_read(self):
        pool, ctx, calls, cfg = self._collect(False, False, [])
        self.assertEqual(calls, [])
        self.assertFalse(any(c["entry_priority"]["auction"]["loaded"] for c in pool))
        sig = {"priority": 300}
        self.assertEqual(key_components(sig, pool[0], cfg.auction_ranking_enabled)["auction_order"], 1)

    def test_ranking_only(self):
        weak = [{"code": "A", "is_weak": True, "unverified": False,
                 "strength": 0, "level": "弱", "reject_reason": "高位绿柱递增"}]
        pool, ctx, calls, cfg = self._collect(True, False, weak)
        self.assertEqual(len(calls), 1)
        a = next(c for c in pool if c["code"] == "A")
        sig = {"priority": 300, "code": "buy_open_ma5_pullback"}
        self.assertEqual(key_components(sig, a, True)["auction_order"], 2)
        self.assertIsNone(auction_veto_reason(cfg, a, sig))
        self.assertTrue(would_veto(a))

    def test_veto_only(self):
        weak = [{"code": "A", "is_weak": True, "unverified": False,
                 "strength": 0, "level": "弱", "reject_reason": "高位绿柱递增"}]
        pool, ctx, calls, cfg = self._collect(False, True, weak)
        self.assertEqual(len(calls), 1)
        a = next(c for c in pool if c["code"] == "A")
        sig = {"priority": 300, "code": "buy_open_ma5_pullback"}
        self.assertEqual(key_components(sig, a, False)["auction_order"], 1)
        self.assertIsNotNone(auction_veto_reason(cfg, a, sig))

    def test_both_on(self):
        weak = [{"code": "A", "is_weak": True, "unverified": False,
                 "strength": 0, "level": "弱", "reject_reason": "高位绿柱递增"}]
        pool, ctx, calls, cfg = self._collect(True, True, weak)
        self.assertEqual(len(calls), 1)
        a = next(c for c in pool if c["code"] == "A")
        sig = {"priority": 300, "code": "buy_open_ma5_pullback"}
        self.assertEqual(key_components(sig, a, True)["auction_order"], 2)
        self.assertIsNotNone(auction_veto_reason(cfg, a, sig))


class TestDecisionDetail(unittest.TestCase):
    def test_passed_detail_has_priority_evidence(self):
        cand = _candidate(False, True)   # veto off：A=False 只影响排序
        detail = explain_buy_candidate(cand, _row(), StrategyConfig(), _prev_row())
        self.assertTrue(detail["passed"])
        self.assertIn("entry_priority", detail)
        self.assertIn("priority_key", detail)
        self.assertTrue(detail["would_veto"])

    def test_veto_detail_carries_vetoed_signal(self):
        cand = _candidate(False, True)
        cfg = StrategyConfig(auction_veto_enabled=True)
        detail = explain_buy_candidate(cand, _row(), cfg, _prev_row())
        self.assertFalse(detail["passed"])
        self.assertEqual(detail["reason_code"], "auction_veto")
        self.assertEqual(detail["vetoed_signal"]["code"], "buy_open_ma5_pullback")
        self.assertIn("entry_priority", detail)
        self.assertIn("priority_key", detail)


class TestEngineDecisionOrder(unittest.TestCase):
    """AC5/AC7：同一 MarketEvent 喂两引擎，决策顺序一致；ranked_out/daily_buy_limit。"""

    def test_same_event_two_engines_same_order_and_ranked_out(self):
        from dragon_quant.live_trade.signal_engine import SignalEngine

        cands = [_candidate(True, True, code="A", composite_score=80),
                 _candidate(False, True, code="B", composite_score=90)]
        cfg = StrategyConfig(auction_ranking_enabled=True)
        t = TradingEngine(cfg, AccountState(100_000))
        t_result = t.step(_event(cands))
        s = SignalEngine(cfg)
        s_result = s.step(_event(cands))

        self.assertEqual(t_result["pending"][0]["stock_code"], "A")
        self.assertEqual([p["stock_code"] for p in s_result["pending"]], ["A", "B"])
        ranked = [d for d in t_result["details"] if d["reason_code"] == "ranked_out"]
        self.assertEqual(ranked[0]["code"], "B")
        self.assertIn("A=弱", ranked[0]["reason_text"])

    def test_ranking_off_uses_composite_not_a(self):
        from dragon_quant.live_trade.signal_engine import SignalEngine

        cands = [_candidate(True, True, code="A", composite_score=80),
                 _candidate(False, True, code="B", composite_score=90)]
        cfg = StrategyConfig(auction_ranking_enabled=False)
        t = TradingEngine(cfg, AccountState(100_000))
        t_result = t.step(_event(cands))
        s = SignalEngine(cfg)
        s_result = s.step(_event(cands))
        self.assertEqual(t_result["pending"][0]["stock_code"], "B")
        self.assertEqual([p["stock_code"] for p in s_result["pending"]], ["B", "A"])

    def test_daily_buy_limit_recorded_once_per_day(self):
        cfg = StrategyConfig()
        state = AccountState(100_000, buys_today=1, trade_date=DAY)
        engine = TradingEngine(cfg, state)
        cand = _candidate(None, True, code="A")
        first = engine.step(_event([cand]))
        second = engine.step(MarketEvent(at(DAY, "09:31"), "open",
                                         {"A": _engine_row()}, [cand]))
        limits1 = [d for d in first["details"]
                   if d["reason_code"] == "daily_buy_limit"]
        limits2 = [d for d in second["details"]
                   if d["reason_code"] == "daily_buy_limit"]
        self.assertEqual(len(limits1), 1)
        self.assertEqual(limits2, [])


class _FakeSimData:
    def calendar(self, start, end, include_today=False):
        return [DAY]


class TestSimulatorDecisionLog(unittest.TestCase):
    """AC7/M4：有成交日与否决日都必须能查到 details/vetoed_signal。"""

    def test_veto_details_merged_into_first_event(self):
        from dragon_quant.review_account.simulator import AccountSimulator

        cand = _candidate(False, True, code="A")
        cfg = StrategyConfig(auction_veto_enabled=True)
        ctx = {"ranking_mode": "prep_only", "pool_source": "prep",
               "fallback_reason": None, "prep_snapshot_date": PREV,
               "auction_rows": 0, "degraded": []}
        event = MarketEvent(at(DAY, "09:30"), "open", {"A": _engine_row()}, [cand])
        with patch("dragon_quant.review_account.simulator.collect_candidates_ex",
                   return_value=([cand], ctx)), \
             patch("dragon_quant.review_account.simulator.load_market_sentiment",
                   return_value=1.0), \
             patch("dragon_quant.review_account.simulator.historical_events",
                   return_value=iter([event])):
            result = AccountSimulator(cfg, data=_FakeSimData()).run(DAY, DAY)
        details = result["events"][0].signal["details"]
        veto = [d for d in details if d["reason_code"] == "auction_veto"]
        self.assertEqual(veto[0]["vetoed_signal"]["code"],
                         "buy_open_ma5_pullback")
        self.assertEqual(result["ranking_modes"][DAY], "prep_only")


class TestConfigValidation(unittest.TestCase):
    def test_defaults(self):
        cfg = StrategyConfig()
        self.assertFalse(cfg.auction_ranking_enabled)
        self.assertFalse(cfg.auction_veto_enabled)
        self.assertTrue(cfg.prep_verdict_filter_enabled)
        self.assertEqual(cfg.auction_veto_exempt_signals, [])

    def test_exempt_signal_validation(self):
        cfg = StrategyConfig.from_dict(
            {"auction_veto_exempt_signals": ["buy_open_ma5_pullback"]})
        self.assertEqual(cfg.auction_veto_exempt_signals,
                         ["buy_open_ma5_pullback"])
        with self.assertRaises(ValueError):
            StrategyConfig.from_dict({"auction_veto_exempt_signals": ["nope"]})
        with self.assertRaises(ValueError):
            StrategyConfig.from_dict({"auction_veto_exempt_signals": "nope"})

    def test_to_json_copies_list(self):
        cfg = StrategyConfig(auction_veto_exempt_signals=["buy_open_ma5_pullback"])
        data = cfg.to_json_dict()
        data["auction_veto_exempt_signals"].append("x")
        self.assertEqual(cfg.auction_veto_exempt_signals,
                         ["buy_open_ma5_pullback"])


if __name__ == "__main__":
    unittest.main()
