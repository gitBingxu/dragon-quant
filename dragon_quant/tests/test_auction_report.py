"""auction.report 纯函数测试：排序 / 行格式 / 空分支 / 汇总 / v2 证据。"""

import unittest

from dragon_quant.review_account.auction.report import (
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


def _v2_snapshot(code, *, strength=63.4, level="中", direction="bull",
                 status="ok", evidence="开+3.2% 换手0.41%"):
    snapshot = _snapshot(code, rank=1, strength=strength, level=level,
                         direction=direction)
    snapshot.update({
        "data_quality_status": status,
        "evidence_summary": evidence,
        "validation_status": "baseline_unvalidated",
    })
    return snapshot


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

    def test_veto_line_hides_legacy_zero(self):
        line = format_verdict_line(
            _snapshot("600003", is_weak=True, unverified=True, strength=0.0,
                      level="弱", direction="bear", reject_reason="强转弱"),
            "丙")
        self.assertEqual(line, "- 600003 丙 弱 — 偏空｜否决：强转弱｜未验证")

    def test_v2_evidence_and_data_status(self):
        line = format_verdict_line(_v2_snapshot("600001"), "甲")
        self.assertEqual(line, "1. 600001 甲 中 63.4 偏多｜开+3.2% 换手0.41%｜数据 ok")

    def test_v2_unavailable_shows_reason(self):
        snapshot = _snapshot("600002", unverified=True, strength=None, level=None,
                             direction="neutral")
        snapshot.update({"data_quality_status": "unavailable",
                         "evidence_summary": "数据不足：prev_close_missing"})
        line = format_verdict_line(snapshot, "乙")
        self.assertIn("数据不足：prev_close_missing", line)
        self.assertIn("—", line)

    def test_reject_reason_fallback(self):
        line = format_verdict_line(
            _snapshot("600003", is_weak=True, strength=0.0, level="弱",
                      direction="bear", reject_reason=None))
        self.assertIn("｜否决：未说明原因", line)


class TestSummary(unittest.TestCase):
    def test_counts_veto_and_failures(self):
        snapshots = [
            _snapshot("A", level="强", unverified=True),
            _snapshot("B", level="中"),
            _snapshot("C", level="弱", is_weak=True, unverified=True),
            _snapshot("D", level=None, unverified=True),
        ]
        text = format_summary(snapshots, failed_count=2)
        self.assertIn("强 1 / 中 1 / 弱 1", text)
        self.assertIn("否决 1", text)
        self.assertIn("失败 2", text)
        self.assertIn("未验证 3", text)

    def test_v2_counts_unavailable(self):
        first = _v2_snapshot("A", status="unavailable", evidence="数据不足：x")
        first["unverified"] = True
        text = format_summary([first, _v2_snapshot("B")])
        self.assertIn("不可用 1", text)
        self.assertNotIn("其他", text)

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

    def test_all_empty_auction_data(self):
        lines = format_auction_report(
            [], trade_date="2026-10-21", candidate_count=3,
            failed_count=0, empty_count=3, saved_count=0)
        self.assertIn("候选 3 只均无竞价盘口", lines[0])


class TestReport(unittest.TestCase):
    def test_collect_header_lines_and_summary(self):
        snapshots = [
            _snapshot("600001", rank=1, strength=78.3, level="强", direction="bull"),
            _snapshot("600004", unverified=True, strength=52.1, direction="bull"),
        ]
        lines = format_auction_report(
            snapshots, trade_date="2026-10-21", pool_source="prep",
            fallback_reason="all_ignored", candidate_count=3,
            failed_count=1, empty_count=1, names={"600001": "甲"})
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].startswith("📊 2026-10-21 集合竞价强度（"))
        self.assertEqual(lines[1], "1. 600001 甲 强 78.3 偏多")
        self.assertTrue(lines[-1].startswith("汇总："))
        self.assertIn("失败 1", lines[-1])

    def test_v2_report_appends_validation_note(self):
        lines = format_auction_report([_v2_snapshot("600001")],
                                      trade_date="2026-10-29")
        self.assertTrue(any("validation_status=baseline_unvalidated" in line
                            for line in lines))

    def test_recompute_header(self):
        lines = format_auction_report(
            [_snapshot("600001")], trade_date="2026-10-20",
            mode="recompute", empty_count=2)
        self.assertEqual(
            lines[0], "📊 2026-10-20 集合竞价强度（重算｜参与 1 只｜空盘口 2 只）")

    def test_candidate_count_below_scored_count_is_not_shown(self):
        lines = format_auction_report(
            [_snapshot("600001"), _snapshot("600002")],
            trade_date="2026-10-20", candidate_count=1)
        self.assertNotIn("候选 1 只", lines[0])


    def test_snapshot_header_marks_cache_and_corruption(self):
        lines = format_auction_report(
            [_v2_snapshot("600001")], trade_date="2026-10-29",
            mode="snapshot", failed_count=2)
        self.assertEqual(
            lines[0],
            "📊 2026-10-29 集合竞价强度（快照｜来自缓存｜参与 1 只｜快照损坏 2 行）")
        self.assertIn("失败 2", lines[-2])


if __name__ == "__main__":
    unittest.main()
