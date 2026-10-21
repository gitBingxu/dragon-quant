"""逐只参与判定与 CLI 清单单测：规则分支 / 优先级 / 降级 / 排序 / 未评级。"""

import unittest
from unittest.mock import MagicMock

from dragon_quant.review_account.advice import (
    build_advice_reason, format_candidate_lines,
)
from dragon_quant.review_account.verdicts import (
    VERDICT_IGNORE, VERDICT_LIGHT, VERDICT_STRONG, VERDICT_UNRATED,
    build_facts, evaluate_candidates, evaluate_one, find_risk_announcement,
)

T = "2026-10-19"


def _facts(**overrides):
    base = {
        "code": "600001", "name": "样本", "trade_date": T,
        "composite_score": 82.0, "rank": 1, "concepts": ["低空经济"],
        "reason_type": None,
        "pool_available": True, "in_pool_today": True,
        "t_pct": 9.99, "t_turnover": 10.0, "t_amount": 2.0e9,
        "main_net": 1.2e8, "flow_available": True,
        "announcements_available": True, "announcement_count": 0,
        "open_num": 0, "high_days": "3天3板", "limit_up_type": "换手板",
        "first_limit_up_time": "09:32:00", "limit_up_suc_rate": 1.0,
        "board_height": 3, "flow_ratio": 0.06, "risk_announcement": None,
        "mainline_hit": True, "mainline_name": "低空经济", "mainline_rank": 1,
    }
    base.update(overrides)
    return base


class TestVerdictBranches(unittest.TestCase):
    def test_strong(self):
        self.assertEqual(evaluate_one(_facts())["verdict"], VERDICT_STRONG)

    def test_non_mainline_is_light(self):
        out = evaluate_one(_facts(mainline_hit=False))
        self.assertEqual(out["verdict"], VERDICT_LIGHT)
        self.assertIn("非今日主线", " ".join(out["dimensions"]))

    def test_missing_flow_or_announcement_downgrades(self):
        self.assertEqual(
            evaluate_one(_facts(flow_available=False))["verdict"], VERDICT_LIGHT)
        self.assertEqual(
            evaluate_one(_facts(announcements_available=False))["verdict"],
            VERDICT_LIGHT)

    def test_open_num_2_downgrades(self):
        self.assertEqual(evaluate_one(_facts(open_num=2))["verdict"], VERDICT_LIGHT)

    def test_suc_rate_below_half_does_not_block_strong(self):
        # 方案 §5.5 未设封板成功率门槛；回归防止后续误加
        self.assertEqual(evaluate_one(_facts(limit_up_suc_rate=0.4))["verdict"],
                         VERDICT_STRONG)

    def test_soft_announcement_downgrades_not_ignores(self):
        facts = _facts(risk_announcement={"level": "soft", "title": "股东减持"})
        out = evaluate_one(facts)
        self.assertEqual(out["verdict"], VERDICT_LIGHT)
        self.assertIn("股东减持", " ".join(out["dimensions"]))

    def test_limit_up_with_outflow_is_light_not_ignore(self):
        facts = _facts(main_net=-1e8, flow_ratio=-0.08)
        self.assertEqual(evaluate_one(facts)["verdict"], VERDICT_LIGHT)

    def test_hard_announcement_ignores(self):
        facts = _facts(risk_announcement={"level": "hard", "title": "立案调查"})
        out = evaluate_one(facts)
        self.assertEqual(out["verdict"], VERDICT_IGNORE)
        self.assertIn("立案调查", out["ignore_reason"])
        self.assertEqual(out["dimensions"], [])

    def test_high_board_broken_ignores(self):
        facts = _facts(high_days="4天4板", board_height=4, open_num=3)
        out = evaluate_one(facts)
        self.assertEqual(out["verdict"], VERDICT_IGNORE)
        self.assertIn("炸板 3 次", out["ignore_reason"])

    def test_non_limit_up_outflow_below_3pct_ignores(self):
        facts = _facts(in_pool_today=False, t_pct=1.2, main_net=-1e8,
                       flow_ratio=-0.08, mainline_hit=None)
        out = evaluate_one(facts)
        self.assertEqual(out["verdict"], VERDICT_IGNORE)
        self.assertIn("资金离场", out["ignore_reason"])

    def test_non_limit_up_outflow_above_3pct_has_explicit_reason(self):
        facts = _facts(in_pool_today=False, t_pct=4.0, main_net=-1e8,
                       flow_ratio=-0.08, mainline_hit=None)
        out = evaluate_one(facts)
        self.assertEqual(out["verdict"], VERDICT_IGNORE)
        self.assertIn("主力净流出", out["ignore_reason"])

    def test_dropped_pool_ignores(self):
        facts = _facts(in_pool_today=False, t_pct=2.0, main_net=1e7,
                       flow_ratio=0.01, mainline_hit=None)
        out = evaluate_one(facts)
        self.assertEqual(out["verdict"], VERDICT_IGNORE)
        self.assertIn("已退出涨停梯队", out["ignore_reason"])

    def test_low_score_ignores(self):
        facts = _facts(composite_score=40.0)
        out = evaluate_one(facts)
        self.assertEqual(out["verdict"], VERDICT_IGNORE)
        self.assertIn("低于参与阈值", out["ignore_reason"])

    def test_data_insufficient_ignores(self):
        facts = _facts(pool_available=False, in_pool_today=False, t_pct=None,
                       main_net=None, flow_available=False, mainline_hit=None)
        out = evaluate_one(facts)
        self.assertEqual(out["verdict"], VERDICT_IGNORE)
        self.assertIn("均缺失", out["ignore_reason"])

    def test_non_limit_up_cross_day_positive_is_light(self):
        facts = _facts(in_pool_today=False, t_pct=4.2, main_net=1e7,
                       flow_ratio=0.01, mainline_hit=None)
        self.assertEqual(evaluate_one(facts)["verdict"], VERDICT_LIGHT)


class TestFactsAndAnnouncements(unittest.TestCase):
    def test_build_facts_uses_today_reason_type_for_mainline(self):
        cand = {
            "code": "A", "name": "A", "composite_score": 80, "rank": 1,
            "trade_date": "2026-10-17", "concepts": [], "reason_type": None,
            "facts": {"reason_type_today": "低空经济", "in_pool_today": True,
                      "pool_available": True, "t_pct": 9.99},
        }
        facts = build_facts(cand, [{"board_name": "低空经济"}], T)
        self.assertTrue(facts["mainline_hit"])
        self.assertEqual(facts["reason_type"], "低空经济")

    def test_announcement_hard_soft_negation(self):
        hard = [{"title": "关于收到立案告知书的公告", "date": T}]
        soft = [{"title": "关于股东减持计划的公告", "date": T}]
        neg = [{"title": "关于终止减持计划的公告", "date": T}]
        none_title = [{"title": None, "type": "减持", "date": T}]
        self.assertEqual(find_risk_announcement(hard, T, True)["level"], "hard")
        self.assertEqual(find_risk_announcement(soft, T, True)["level"], "soft")
        self.assertIsNone(find_risk_announcement(neg, T, True))
        self.assertIsNone(find_risk_announcement(none_title, T, True))
        self.assertIsNone(find_risk_announcement(hard, T, False))

    def test_old_announcement_outside_window_skipped(self):
        old = [{"title": "立案调查", "date": "2026-09-01"}]
        self.assertIsNone(find_risk_announcement(old, T, True))

    def test_hard_wins_over_newer_soft(self):
        anns = [{"title": "关于收到立案告知书的公告", "date": "2026-10-16"},
                {"title": "关于股东减持计划的公告", "date": "2026-10-18"}]
        self.assertEqual(find_risk_announcement(anns, T, True)["level"], "hard")

    def test_missing_date_is_not_treated_as_risk(self):
        anns = [{"title": "涉及诉讼的公告", "date": ""}]
        self.assertIsNone(find_risk_announcement(anns, T, True))

    def test_negations_skip_whole_announcement(self):
        for title in ("关于撤销退市风险警示的公告",
                      "关于减持计划终止的公告"):
            self.assertIsNone(
                find_risk_announcement([{"title": title, "date": T}], T, True))

    def test_hard_survives_generic_negation_phrases(self):
        for title in ("关于立案调查进展暨不存在应披露而未披露事项的公告",
                      "关于控股股东股份被冻结及减持计划终止的公告"):
            self.assertEqual(
                find_risk_announcement([{"title": title, "date": T}], T, True)["level"],
                "hard")


class TestEvaluateCandidatesIsolation(unittest.TestCase):
    def test_single_candidate_exception_is_unrated_and_kept(self):
        from unittest.mock import patch
        candidates = [
            {"code": "A", "name": "正常", "composite_score": 80,
             "facts": _facts()},
            {"code": "B", "name": "异常", "composite_score": 60},
        ]
        with patch("dragon_quant.review_account.verdicts.build_facts",
                   side_effect=[_facts(), RuntimeError("boom")]):
            out = evaluate_candidates(candidates, [], T)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["verdict"], VERDICT_STRONG)
        self.assertEqual(out[1]["verdict"], VERDICT_UNRATED)
        self.assertEqual(out[1]["ignore_reason"], "评级字段异常")

    def test_candidate_without_facts_does_not_crash(self):
        out = evaluate_candidates([{"code": "A", "name": "A"}], [], T)
        self.assertEqual(len(out), 1)
        self.assertIn(out[0]["verdict"],
                      (VERDICT_IGNORE, VERDICT_UNRATED))


class TestSnapshotConsumption(unittest.TestCase):
    def test_verdict_ignore_is_dropped(self):
        """D4：verdict=ignore 出池；strong/light 保留。"""
        from types import SimpleNamespace
        from unittest.mock import patch
        from dragon_quant.review_account.market import collect_candidates_ex

        snap = {"candidates": [
            {"code": "A", "composite_score": 80, "is_true_dragon": True,
             "verdict": "ignore"},
            {"code": "B", "composite_score": 70, "is_true_dragon": True,
             "verdict": "strong"},
            {"code": "C", "composite_score": 60, "is_true_dragon": True,
             "verdict": "light"},
        ]}
        cfg = SimpleNamespace(source="v2", min_score=50)
        with patch("dragon_quant.storage.assets_db.get_prep_snapshot",
                   return_value=snap):
            out, ctx = collect_candidates_ex(["2026-10-19", "2026-10-20"],
                                             "2026-10-20", cfg, lambda *a, **k: [])
        self.assertEqual([c["code"] for c in out], ["B", "C"])
        self.assertEqual(ctx["pool_source"], "prep")
        self.assertIsNone(ctx["fallback_reason"])

    def test_all_ignored_falls_back_without_refilter(self):
        """D7（裁决 2）：推荐集空 → 回退 legacy，且回退池不再应用 ignore 过滤。"""
        from types import SimpleNamespace
        from unittest.mock import patch
        from dragon_quant.review_account.market import collect_candidates_ex

        snap = {"candidates": [
            {"code": "A", "composite_score": 80, "is_true_dragon": True,
             "verdict": "ignore", "ignore_reason": "资金离场"},
            {"code": "B", "composite_score": 70, "is_true_dragon": True,
             "verdict": "ignore", "ignore_reason": "高位炸板"},
        ]}
        legacy = [{"code": "A", "name": "A", "composite_score": 80,
                   "is_true_dragon": True},
                  {"code": "X", "name": "X", "composite_score": 60,
                   "is_true_dragon": True}]
        cfg = SimpleNamespace(source="v2", min_score=50)
        with patch("dragon_quant.storage.assets_db.get_prep_snapshot",
                   return_value=snap):
            out, ctx = collect_candidates_ex(["2026-10-19", "2026-10-20"],
                                             "2026-10-20", cfg,
                                             lambda *a, **k: legacy)
        self.assertEqual(ctx["fallback_reason"], "all_ignored")
        self.assertEqual(ctx["pool_source"], "legacy")
        self.assertEqual([c["code"] for c in out], ["A", "X"])
        by_code = {c["code"]: c for c in out}
        # 回退后不因 ignore 再次剔除；快照内 ignore → P=False，快照外 → P=None
        self.assertIs(by_code["A"]["entry_priority"]["prep"]["state"], False)
        self.assertIsNone(by_code["X"]["entry_priority"]["prep"]["state"])

    def test_old_snapshot_without_verdict_keeps_snapshot(self):
        """C7：旧快照完全无 verdict → 用快照候选、P=Unknown、不回退。"""
        from types import SimpleNamespace
        from unittest.mock import patch
        from dragon_quant.review_account.market import collect_candidates_ex

        snap = {"candidates": [
            {"code": "A", "composite_score": 80, "is_true_dragon": True},
        ]}
        loader = MagicMock(return_value=[])
        cfg = SimpleNamespace(source="v2", min_score=50)
        with patch("dragon_quant.storage.assets_db.get_prep_snapshot",
                   return_value=snap):
            out, ctx = collect_candidates_ex(["2026-10-19", "2026-10-20"],
                                             "2026-10-20", cfg, loader)
        self.assertEqual([c["code"] for c in out], ["A"])
        self.assertEqual(ctx["pool_source"], "prep")
        self.assertIsNone(ctx["fallback_reason"])
        self.assertIsNone(out[0]["entry_priority"]["prep"]["state"])
        loader.assert_not_called()

    def test_filter_disabled_keeps_ignore(self):
        """消融：prep_verdict_filter_enabled=False 复现旧候选池。"""
        from types import SimpleNamespace
        from unittest.mock import patch
        from dragon_quant.review_account.market import collect_candidates_ex

        snap = {"candidates": [
            {"code": "A", "composite_score": 80, "is_true_dragon": True,
             "verdict": "ignore"},
            {"code": "B", "composite_score": 70, "is_true_dragon": True,
             "verdict": "strong"},
        ]}
        cfg = SimpleNamespace(source="v2", min_score=50,
                              prep_verdict_filter_enabled=False)
        with patch("dragon_quant.storage.assets_db.get_prep_snapshot",
                   return_value=snap):
            out, ctx = collect_candidates_ex(["2026-10-19", "2026-10-20"],
                                             "2026-10-20", cfg, lambda *a, **k: [])
        self.assertEqual([c["code"] for c in out], ["A", "B"])
        self.assertEqual(ctx["pool_source"], "prep")

    def test_unrated_kept_with_false_state(self):
        """C1：unrated 保留在池内但 P=False（不剔除）。"""
        from types import SimpleNamespace
        from unittest.mock import patch
        from dragon_quant.review_account.market import collect_candidates_ex

        snap = {"candidates": [
            {"code": "A", "composite_score": 80, "is_true_dragon": True,
             "verdict": "unrated"},
            {"code": "B", "composite_score": 70, "is_true_dragon": True,
             "verdict": "strong"},
        ]}
        cfg = SimpleNamespace(source="v2", min_score=50)
        with patch("dragon_quant.storage.assets_db.get_prep_snapshot",
                   return_value=snap):
            out, _ = collect_candidates_ex(["2026-10-19", "2026-10-20"],
                                           "2026-10-20", cfg, lambda *a, **k: [])
        by_code = {c["code"]: c for c in out}
        self.assertIn("A", by_code)
        self.assertIs(by_code["A"]["entry_priority"]["prep"]["state"], False)


class TestCandidateLines(unittest.TestCase):
    def _result(self):
        return {"candidates": [
            {"code": "C", "name": "轻票", "composite_score": 70,
             "verdict": VERDICT_LIGHT, "verdict_label": "轻仓博弈",
             "dimensions": ["质量维度：70"], "ignore_reason": None},
            {"code": "A", "name": "弱票", "composite_score": 60,
             "verdict": VERDICT_IGNORE, "verdict_label": "忽略",
             "ignore_reason": "今日涨幅 +1.20%、主力净流出 1.00 亿，资金离场",
             "dimensions": []},
            {"code": "B", "name": "强票", "composite_score": 82,
             "verdict": VERDICT_STRONG, "verdict_label": "强烈建议",
             "dimensions": ["质量维度：82", "涨停维度：换手板"],
             "ignore_reason": None},
            {"code": "D", "name": "坏票", "composite_score": None,
             "verdict": VERDICT_UNRATED, "verdict_label": "未评级",
             "ignore_reason": "评级字段异常", "dimensions": []},
        ]}

    def test_buy_first_ignore_one_line_unrated_last(self):
        lines = format_candidate_lines(self._result())
        self.assertEqual(lines[0], "✅ 强票-强烈建议")
        self.assertIn("✅ 轻票-轻仓博弈", lines)
        self.assertLess(lines.index("✅ 强票-强烈建议"),
                        lines.index("✅ 轻票-轻仓博弈"))
        ignore_lines = [x for x in lines if x.startswith("弱票-忽略：")]
        self.assertEqual(len(ignore_lines), 1)
        self.assertTrue(any(x.startswith("坏票-未评级：") for x in lines))
        # unrated 在 ignore 之后
        self.assertLess(lines.index(ignore_lines[0]),
                        [i for i, x in enumerate(lines)
                         if x.startswith("坏票-未评级：")][0])

    def test_empty_candidates(self):
        self.assertEqual(format_candidate_lines({"candidates": []}),
                         ["无参与复盘标的"])

    def test_macro_fragments_in_reason(self):
        macro = {"available": True, "session": "same_day", "instruments": {
            "jp_nikkei": {"label": "日经225期指", "pct": 1.23,
                          "session_date": "2026-10-19", "stale": False},
            "cn_a50": {"label": "富时A50期指", "pct": -0.87,
                       "session_date": "2026-10-19", "stale": False},
            "ust10y": {"price": 5.15, "chg_bp": 6.0, "chg_bp_approx": True},
            "gold": {"label": "COMEX金", "pct": -0.63},
        }}
        text = build_advice_reason(
            {"position_pct": 0.5, "position_advice": "正常", "degraded": []},
            [], "2026-10-19", macro)
        self.assertIn("外围：日经225期指 +1.23%、富时A50期指 -0.87%（同日收盘）", text)
        self.assertIn("美债10Y 5.15%（较前值 +6bp，近似）", text)
        self.assertIn("COMEX金 -0.63%", text)
        for legacy in ("道指", "纳指", "标普", "隔夜"):
            self.assertNotIn(legacy, text)

    def test_macro_mixed_session_marks_each_instrument_date(self):
        macro = {"available": True, "session": "mixed", "instruments": {
            "jp_nikkei": {"label": "日经225期指", "pct": 1.23,
                          "session_date": "2026-10-19", "stale": False},
            "cn_a50": {"label": "富时A50期指", "pct": -0.87,
                       "session_date": "2026-10-16", "stale": False},
        }}
        text = build_advice_reason(
            {"position_pct": 0.5, "position_advice": "正常", "degraded": []},
            [], "2026-10-19", macro)
        self.assertIn("日经225期指 +1.23%（2026-10-19 收盘）", text)
        self.assertIn("富时A50期指 -0.87%（2026-10-16 收盘）", text)

    def test_macro_previous_session_suffix(self):
        macro = {"available": True, "session": "previous_session", "instruments": {
            "jp_nikkei": {"label": "日经225期指", "pct": 0.61,
                          "session_date": "2026-10-16", "stale": False},
            "cn_a50": {"label": "富时A50期指", "pct": -0.87,
                       "session_date": "2026-10-16", "stale": False},
        }}
        text = build_advice_reason(
            {"position_pct": 0.5, "position_advice": "正常", "degraded": []},
            [], "2026-10-19", macro)
        self.assertIn("（前一交易日收盘）", text)

    def test_legacy_macro_snapshot_without_indices(self):
        """0.4 快照只有 us_*：无外围片段，美债/黄金照常，不崩。"""
        macro = {"available": True, "session": "overnight", "instruments": {
            "us_dji": {"label": "道指", "pct": -0.07, "stale": False},
            "ust10y": {"price": 5.15, "chg_bp": 6.0, "chg_bp_approx": True},
            "gold": {"label": "COMEX金", "pct": -0.63},
        }}
        text = build_advice_reason(
            {"position_pct": 0.5, "position_advice": "正常", "degraded": []},
            [], "2026-10-19", macro)
        self.assertNotIn("外围：", text)
        self.assertNotIn("道指", text)
        self.assertIn("美债10Y 5.15%", text)
        self.assertIn("COMEX金 -0.63%", text)


if __name__ == "__main__":
    unittest.main()
