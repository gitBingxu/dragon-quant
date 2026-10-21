"""auction.report 纯函数测试：排序 / 行格式 / 空分支 / 汇总。"""

import unittest

from dragon_quant.auction.report import (
    format_auction_report,
    format_summary,
    format_verdict_line,
    sort_snapshots,
)


def _snapshot(code, *, rank=None, strength=50.0, is_weak=False,
              unverified=False, level="中", direction="neutral",
              reject_reason=None):
    return {
        "code": code,
        "rank": rank,
        "strength": strength,
        "is_weak": is_weak,
        "unverified": unverified,
        "level": level,
        "direction": direction,
        "reject_reason": reject_reason,
    }


class TestSortSnapshots(unittest.TestCase):
    def test_order_ranked_unverified_fallback_weak(self):
        snapshots = [
            _snapshot("W", is_weak=True, unverified=True, strength=90, level="弱"),
            _snapshot("U2", unverified=True, strength=60),
            _snapshot("U1", unverified=True, strength=70),
            _snapshot("R2", rank=2, strength=80, level="强"),
            _snapshot("R1", rank=1, strength=70, level="强"),
            _snapshot("F", strength=65),
        ]
        codes = [s["code"] for s in sort_snapshots(snapshots)]
        self.assertEqual(codes, ["R1", "R2", "U1", "U2", "F", "W"])

    def test_within_group_strength_desc_then_code(self):
        snapshots = [
            _snapshot("B", unverified=True, strength=60),
            _snapshot("A", unverified=True, strength=60),
            _snapshot("C", unverified=True, strength=70),
        ]
        codes = [s["code"] for s in sort_snapshots(snapshots)]
        self.assertEqual(codes, ["C", "A", "B"])


class TestVerdictLine(unittest.TestCase):
    def test_ranked_line(self):
        line = format_verdict_line(
            _snapshot("600001", rank=1, strength=78.34, level="强", direction="bull"),
            "甲")
        self.assertEqual(line, "1. 600001 甲 强 78.3 偏多")

    def test_unverified_line(self):
        line = format_verdict_line(
            _snapshot("600004", unverified=True, strength=52.1, direction="bull"),
            "丁")
        self.assertEqual(line, "? 600004 丁 中 52.1 偏多｜未验证")

    def test_veto_and_unverified_stack(self):
        line = format_verdict_line(
            _snapshot("600003", is_weak=True, unverified=True, strength=0.0,
                      level="弱", direction="bear", reject_reason="数据不足"),
            "丙")
        self.assertEqual(line, "- 600003 丙 弱 0.0 偏空｜否决：数据不足｜未验证")

    def test_reject_reason_fallback(self):
        line = format_verdict_line(
            _snapshot("600003", is_weak=True, strength=0.0, level="弱",
                      direction="bear", reject_reason=None))
        self.assertIn("｜否决：未说明原因", line)

    def test_missing_name_does_not_add_empty_token(self):
        line = format_verdict_line(
            _snapshot("600003", strength=0.0, level="弱"))
        self.assertEqual(line, "? 600003 弱 0.0 中性")
        self.assertNotIn("  弱", line)
        self.assertNotIn("  中性", line)

    def test_direction_mapping(self):
        cases = {"bull": "偏多", "bear": "偏空", "neutral": "中性", "weird": "weird"}
        for direction, label in cases.items():
            line = format_verdict_line(
                _snapshot("600001", strength=50.0, direction=direction))
            self.assertTrue(line.endswith(label), line)


class TestSummary(unittest.TestCase):
    def test_counts_other_and_unverified_including_weak(self):
        snapshots = [
            _snapshot("A", level="强", unverified=True),
            _snapshot("B", level="中"),
            _snapshot("C", level="弱", is_weak=True, unverified=True),
            _snapshot("D", level=None),
        ]
        text = format_summary(snapshots, failed_count=2)
        self.assertIn("强 1 / 中 1 / 弱 1", text)
        self.assertIn("其他 1", text)
        self.assertIn("未验证 2", text)
        self.assertIn("采集失败 2（详情加 --json）", text)

    def test_no_hint_without_failures(self):
        text = format_summary([_snapshot("A")])
        self.assertNotIn("详情加 --json", text)


class TestEmptyBranches(unittest.TestCase):
    def test_no_candidate(self):
        lines = format_auction_report(
            [], trade_date="2026-10-21", pool_source="prep",
            fallback_reason="all_ignored", candidate_count=0)
        self.assertEqual(len(lines), 1)
        self.assertIn("无参与集合竞价强度判断的标的", lines[0])
        self.assertIn("池来源 prep", lines[0])
        self.assertIn("回退：all_ignored", lines[0])

    def test_collect_all_failed(self):
        lines = format_auction_report(
            [], trade_date="2026-10-21", pool_source="prep",
            candidate_count=3, failed_count=3, saved_count=0)
        self.assertIn("候选 3 只均未产出竞价强度判定", lines[0])
        self.assertIn("采集失败 3 条", lines[0])
        self.assertNotIn("无参与", lines[0])

    def test_scoring_all_failed_after_assets_saved(self):
        lines = format_auction_report(
            [], trade_date="2026-10-21", candidate_count=3,
            failed_count=2, saved_count=3)
        self.assertIn("候选 3 只均未产出评分结果", lines[0])
        self.assertIn("原始资产已写入", lines[0])
        self.assertNotIn("无参与", lines[0])

    def test_all_empty_auction_data(self):
        lines = format_auction_report(
            [], trade_date="2026-10-21", candidate_count=3,
            failed_count=0, empty_count=3, saved_count=0)
        self.assertIn("候选 3 只均无竞价盘口", lines[0])
        self.assertIn("空盘口 3 只", lines[0])
        self.assertNotIn("失败 0 条", lines[0])


class TestReport(unittest.TestCase):
    def test_collect_header_lines_and_summary(self):
        snapshots = [
            _snapshot("600001", rank=1, strength=78.3, level="强",
                      direction="bull"),
            _snapshot("600004", unverified=True, strength=52.1,
                      direction="bull"),
        ]
        lines = format_auction_report(
            snapshots, trade_date="2026-10-21", pool_source="prep",
            fallback_reason="all_ignored", candidate_count=3,
            failed_count=1, empty_count=1, names={"600001": "甲"})
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].startswith("📊 2026-10-21 集合竞价强度（"))
        for part in ("参与 2 只", "池来源 prep", "回退：all_ignored",
                     "候选 3 只", "空盘口 1 只"):
            self.assertIn(part, lines[0])
        self.assertEqual(lines[1], "1. 600001 甲 强 78.3 偏多")
        self.assertIn("? 600004", lines[2])
        self.assertTrue(lines[-1].startswith("汇总："))
        self.assertIn("采集失败 1", lines[-1])

    def test_recompute_header(self):
        lines = format_auction_report(
            [_snapshot("600001")], trade_date="2026-10-20",
            mode="recompute", empty_count=2)
        self.assertEqual(
            lines[0], "📊 2026-10-20 集合竞价强度（重算｜参与 1 只｜空盘口 2 只）")

    def test_candidate_count_below_scored_count_is_not_shown(self):
        """同日早前批次资产也参与评分时，候选数小于评分只数属正常，不应误导。"""
        lines = format_auction_report(
            [_snapshot("600001"), _snapshot("600002")],
            trade_date="2026-10-20", candidate_count=1)
        self.assertNotIn("候选 1 只", lines[0])


if __name__ == "__main__":
    unittest.main()
