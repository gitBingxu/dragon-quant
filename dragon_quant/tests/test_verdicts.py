"""旧评级常量、公告风险共享工具与 CLI 清单展示单测。

旧五维推荐规则链已退役（2026-10-30），本文件不再测试已删除的
``evaluate_one`` / ``evaluate_candidates``；保留：
* ``find_risk_announcement`` 的公告风险语义（新六维 G1 与旧资产共用）；
* 0.7/legacy 候选池读取分支（历史快照必须可复现）；
* CLI 清单的排序与展示。
"""

import unittest
from unittest.mock import MagicMock

from dragon_quant.review_account.advice import (
    build_advice_reason, format_candidate_lines,
)
from dragon_quant.review_account.verdicts import (
    VERDICT_IGNORE, VERDICT_LIGHT, VERDICT_STRONG, VERDICT_UNRATED,
    find_risk_announcement,
)

T = "2026-10-19"


class TestAnnouncementRisk(unittest.TestCase):
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
        with patch("dragon_quant.infra.assets.db.get_prep_snapshot",
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
        with patch("dragon_quant.infra.assets.db.get_prep_snapshot",
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
        with patch("dragon_quant.infra.assets.db.get_prep_snapshot",
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
        with patch("dragon_quant.infra.assets.db.get_prep_snapshot",
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
        with patch("dragon_quant.infra.assets.db.get_prep_snapshot",
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
