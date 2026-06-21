"""
tests for dragon_quant.logging — ScanLogger + ReportBuilder
"""
import os
import tempfile
import unittest
from pathlib import Path
from dragon_quant.logging.logger import ScanLogger, LogEntry
from dragon_quant.logging.reporter import ReportBuilder


class TestLogEntry(unittest.TestCase):

    def test_creation(self):
        entry = LogEntry(timestamp=1000.0, category="phase:A",
                         level="info", message="done", code="600519",
                         data={"count": 10})
        self.assertEqual(entry.level, "info")
        self.assertEqual(entry.data["count"], 10)


class TestScanLogger(unittest.TestCase):

    def setUp(self):
        self.logger = ScanLogger()

    def test_phase(self):
        self.logger.phase("A", "板块排行", up=10, down=10)
        entries = self.logger.query(category="phase:A")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].data["up"], 10)

    def test_api(self):
        self.logger.api("xueqiu", "kline", ok=True, elapsed_ms=350, code="600519")
        entries = self.logger.query(category="api:xueqiu:kline")
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].data["ok"])

    def test_scorer(self):
        self.logger.scorer("drive", "600519", score=85.0, weight=0.35,
                           limit_up_count=2)
        entries = self.logger.query(category="scorer:drive", code="600519")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].data["score"], 85.0)
        self.assertEqual(entries[0].data["limit_up_count"], 2)

    def test_error(self):
        self.logger.error("network", "timeout", code="600519", exception="TimeoutError")
        errors = self.logger.query(level="error")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].data["exception"], "TimeoutError")

    def test_api_stats(self):
        self.logger.api("xueqiu", "kline", ok=True, elapsed_ms=100)
        self.logger.api("xueqiu", "kline", ok=True, elapsed_ms=200)
        self.logger.api("eastmoney", "ranking", ok=False, elapsed_ms=500, error="403")
        stats = self.logger.api_stats()
        self.assertIn("by_provider", stats)
        self.assertIn("xueqiu", stats["by_provider"])
        self.assertIn("eastmoney", stats["by_provider"])

    def test_query_filter_by_code(self):
        self.logger.scorer("drive", "600519", score=80)
        self.logger.scorer("drive", "000001", score=70)
        entries = self.logger.query(code="600519")
        self.assertEqual(len(entries), 1)


class TestScanLoggerDump(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.logger = ScanLogger()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_dump_jsonl(self):
        self.logger.phase("A", "test", count=1)
        self.logger.scorer("drive", "600519", score=80)
        path = Path(self.tmpdir) / "test.jsonl"
        self.logger.dump_jsonl(path)
        self.assertTrue(path.exists())
        with open(path) as f:
            lines = f.readlines()
        self.assertGreaterEqual(len(lines), 2)


class TestReportBuilder(unittest.TestCase):

    def setUp(self):
        self.logger = ScanLogger()
        self.reporter = ReportBuilder(self.logger)

    def test_build_stock_report_header(self):
        report = self.reporter.build_stock_report(
            code="600519", name="贵州茅台",
            board_count=3, concepts=["白酒"],
            composite_score=85.0, dimensions={},
            primary_sector_name="白酒"
        )
        self.assertIn("贵州茅台", report)
        self.assertIn("600519", report)
        self.assertIn("白酒", report)
        self.assertIn("3连板", report)
        self.assertIn("85.0分", report)
        self.assertIn("龙头", report)

    def test_build_stock_report_tier(self):
        tiers = [
            (85.0, "龙头"),
            (70.0, "强票"),
            (55.0, "中等"),
            (30.0, "偏弱"),
        ]
        for score, expected in tiers:
            report = self.reporter.build_stock_report(
                code="000001", name="test", composite_score=score,
            )
            self.assertIn(expected, report, f"score={score} should be {expected}")

    def test_build_stock_report_no_board(self):
        report = self.reporter.build_stock_report(
            code="600519", name="贵州茅台",
            board_count=0, concepts=["白酒"],
            composite_score=50.0,
        )
        self.assertNotIn("连板", report)

    def test_build_summary_report(self):
        display_list = [{
            "code": "600519", "name": "茅台",
            "composite_score": 85.0, "board_count": 3,
            "concepts": ["白酒"],
            "dimensions": {
                "drive": {"score": 80, "weight": 0.35},
                "anti_drop": {"score": 70, "weight": 0.15},
                "leadership": {"score": 90, "weight": 0.25},
                "absorption": {"score": 85, "weight": 0.25},
            },
            "primary_sector_name": "白酒",
        }]
        report = self.reporter.build_summary_report(display_list)
        self.assertIn("茅台", report)
        self.assertIn("600519", report)
        self.assertIn("85.0", report)

    def test_build_stock_report_drive_mentions_total_and_scoring_sample(self):
        report = self.reporter.build_stock_report(
            code="600519", name="贵州茅台",
            composite_score=85.0,
            dimensions={
                "drive": {
                    "score": 88.0,
                    "details": {
                        "best_day_detail": {
                            "voice": 90.0,
                            "follow": 80.0,
                            "board_leadership": 70.0,
                            "voice_raw": {"total": 286, "scoring_total": 50, "sample_limit": 50, "limit_up": 6},
                            "follow_raw": {"total": 286, "scoring_total": 50, "sample_limit": 50, "strong": 12, "down": 3},
                            "board_detail": {"board_time": None, "is_yiziban": False, "sector_limit_up_total": 6},
                        }
                    },
                }
            },
            primary_sector_name="白酒"
        )
        self.assertIn("全量共 286 只票", report)
        self.assertIn("居前 50 只样本", report)

    def test_build_stock_report_absorption_fallback_reason(self):
        report = self.reporter.build_stock_report(
            code="600519", name="贵州茅台",
            composite_score=60.0,
            dimensions={
                "absorption": {
                    "score": 50.0,
                    "details": {"fallback_reason": "目标板块5分K不足"},
                }
            },
            primary_sector_name="白酒",
        )
        self.assertIn("资金承接", report)
        self.assertIn("目标板块5分K不足", report)

    def test_build_stock_report_absorption_single_sector_no_etc(self):
        report = self.reporter.build_stock_report(
            code="600519", name="贵州茅台",
            composite_score=60.0,
            dimensions={
                "absorption": {
                    "score": 50.0,
                    "details": {
                        "event_count": 1,
                        "all_events": [
                            {
                                "dive_time": "5月8日 13:00",
                                "rally_time": "5月8日 13:10",
                                "time_diff_min": 10,
                                "target_pct": 0.4,
                                "fleeing_sectors": [{"name": "白酒"}],
                            }
                        ],
                    },
                }
            },
            primary_sector_name="锂矿概念",
        )
        # fewshot 对齐：单板块时不出现“等板块”
        self.assertNotIn("等板块", report)
        self.assertIn("白酒板块跳水", report)
        # 0.4% 仍视为“小幅拉伸”
        self.assertIn("小幅拉伸", report)
        # fewshot 示例不输出“间隔xx分钟”
        self.assertNotIn("间隔", report)

    def test_build_stock_report_v2_mentions_event_details(self):
        report = self.reporter.build_stock_report_v2(
            code="600519", name="贵州茅台",
            concepts=["白酒"], composite_score=88.0,
            primary_sector_name="白酒",
            is_true_dragon=True,
            dimensions={
                "drive": {
                    "score": 82,
                    "details": {
                        "s_early": 90,
                        "early": {
                            "sealed": True, "seal_time": "09:35",
                            "bid1_volume": 123000, "rank": 1, "pool_size": 5,
                        },
                        "s_lead": 75,
                        "lead": {
                            "n_lead": 1, "n_follow": 1,
                            "lead_events": [{
                                "event_time": "10:02",
                                "stock_gain_pct": 2.4,
                                "sector_gain_pct": 0.8,
                            }],
                            "follow_events": [{
                                "sector_event_time": "10:41",
                                "stock_follow_time": "10:47",
                                "sector_gain_pct": 0.7,
                                "stock_gain_pct": 2.1,
                            }],
                        },
                        "s_voice": 65,
                        "voice": {"n_limit": 3, "n_strong": 12},
                    },
                },
                "leadership": {"score": 80, "details": {}},
                "anti_drop": {
                    "score": 70,
                    "details": {
                        "s_market": 72,
                        "s_sector": 65,
                        "market": {"deepest_event": {
                            "start_time": "10:27", "bottom_time": "10:36",
                            "base_drop_pct": -0.82, "stock_change_pct": 0.15,
                        }},
                        "sector": {"deepest_event": {
                            "start_time": "13:42", "bottom_time": "13:49",
                            "base_drop_pct": -1.10, "stock_change_pct": -0.20,
                        }},
                    },
                },
                "liquidity": {"score": 75, "details": {}},
                "absorption": {
                    "score": 68,
                    "details": {
                        "event_count": 2,
                        "best_event": {
                            "dive_time": "6月18日 10:15",
                            "rally_time": "6月18日 10:25",
                            "target_pct": 1.8,
                            "fleeing_avg_drop": -1.2,
                            "fleeing_sectors": [
                                {"name": "煤炭", "drop_pct": -1.1},
                                {"name": "有色", "drop_pct": -1.3},
                            ],
                        },
                    },
                },
            },
        )

        self.assertIn("09:35封板", report)
        self.assertIn("封单量12.3万手", report)
        self.assertIn("10:02个股拉升+2.40%", report)
        self.assertIn("10:41板块先拉升+0.70%", report)
        self.assertIn("大盘跳水-0.82%", report)
        self.assertIn("该股同期+0.15%", report)
        self.assertIn("白酒回落-1.10%", report)
        self.assertIn("煤炭(-1.10%)", report)
        self.assertIn("承接上述板块出逃资金", report)


if __name__ == "__main__":
    unittest.main()
