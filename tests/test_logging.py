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


if __name__ == "__main__":
    unittest.main()
