"""扫描运行态只读聚合单测（方案 §5.6 / §9.2，TDD 先 RED）。

覆盖 ``scans.summarize_scan_stocks`` / ``logs.summarize_scan_logs`` /
``logs.get_scan_logs_page`` / ``logs.count_scan_logs_filtered``：
批量聚合、级别/分页、无日志语义；不新增表、不改既有契约。
"""

from __future__ import annotations

import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dragon_quant.scan.storage import logs as scan_logs
from dragon_quant.scan.storage import scans as scan_storage


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "dragon.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _connect(self, *args, **kwargs):
        return sqlite3.connect(self.db_path)

    @contextlib.contextmanager
    def _patched(self):
        with patch("dragon_quant.scan.storage.scans._connect",
                   side_effect=self._connect), \
             patch("dragon_quant.scan.storage.logs._connect",
                   side_effect=self._connect):
            yield

    def _seed(self):
        scan_storage.save_scan(
            "v2_20260911_5", "2026-09-11", 1.5, 5, 3, 2,
            stocks=[
                {"code": "600001", "name": "龙头A", "rank": 1,
                 "is_true_dragon": True},
                {"code": "600002", "name": "跟风B", "rank": 2,
                 "is_true_dragon": False},
                {"code": "600003", "name": "补涨C", "rank": 3,
                 "is_true_dragon": True},
            ],
            source="v2",
        )
        scan_storage.save_scan(
            "v2_20260910_5", "2026-09-10", 1.0, 5, 1, 2,
            stocks=[{"code": "600004", "name": "银行D", "rank": 1,
                     "is_true_dragon": True}],
            source="v2",
        )
        scan_logs.save_scan_logs("v2_20260911_5", [
            {"ts": 3.0, "category": "phase:A", "level": "info", "message": "A 完成"},
            {"ts": 2.0, "category": "api:tdx", "level": "error",
             "message": "timeout", "data": {"ok": False}},
            {"ts": 1.0, "category": "api:tdx", "level": "warn", "message": "retry",
             "data": {"ok": False}},
        ], source="v2")
        # v2_20260910_5 没有日志行（模拟 logs 被清理 / 从未落库）。


class TestSummarizeScanStocks(_Base):
    def test_counts_stock_and_true_dragon_per_scan(self):
        with self._patched():
            self._seed()
            summary = scan_storage.summarize_scan_stocks(
                ["v2_20260911_5", "v2_20260910_5"], source="v2")

        self.assertEqual(summary["v2_20260911_5"]["stock_n"], 3)
        self.assertEqual(summary["v2_20260911_5"]["true_dragon_n"], 2)
        self.assertEqual(summary["v2_20260910_5"]["stock_n"], 1)
        self.assertEqual(summary["v2_20260910_5"]["true_dragon_n"], 1)

    def test_unknown_scan_ids_are_absent(self):
        with self._patched():
            self._seed()
            summary = scan_storage.summarize_scan_stocks(
                ["v2_20260911_5", "missing"], source="v2")
        self.assertEqual(set(summary), {"v2_20260911_5"})

    def test_empty_input_short_circuits(self):
        self.assertEqual(scan_storage.summarize_scan_stocks([], source="v2"), {})


class TestSummarizeScanLogs(_Base):
    def test_entries_and_error_count_per_scan(self):
        with self._patched():
            self._seed()
            summary = scan_logs.summarize_scan_logs(
                ["v2_20260911_5", "v2_20260910_5"], source="v2")

        hit = summary["v2_20260911_5"]
        self.assertEqual(hit["log_entries"], 3)
        self.assertEqual(hit["error_n"], 1)
        self.assertEqual(hit["first_ts"], 1.0)
        self.assertEqual(hit["last_ts"], 3.0)
        # 无日志的扫描不出现在聚合结果里（调用方按 0 处理）。
        self.assertNotIn("v2_20260910_5", summary)

    def test_empty_input_short_circuits(self):
        self.assertEqual(scan_logs.summarize_scan_logs([], source="v2"), {})


class TestScanLogsPage(_Base):
    def test_page_is_newest_first_and_has_no_data_json(self):
        with self._patched():
            self._seed()
            page = scan_logs.get_scan_logs_page("v2_20260911_5", limit=2)
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["entries"]), 2)
        self.assertEqual([entry["message"] for entry in page["entries"]],
                         ["A 完成", "timeout"])
        for entry in page["entries"]:
            self.assertNotIn("data", entry)
            self.assertNotIn("data_json", entry)
            self.assertEqual(
                set(entry), {"ts", "category", "level", "message", "code"})

    def test_level_filter_and_offset(self):
        with self._patched():
            self._seed()
            page = scan_logs.get_scan_logs_page(
                "v2_20260911_5", level="error", offset=0, limit=10)
            self.assertEqual(page["total"], 1)
            self.assertEqual(page["entries"][0]["level"], "error")

            paged = scan_logs.get_scan_logs_page(
                "v2_20260911_5", offset=1, limit=1)
            self.assertEqual(paged["total"], 3)
            self.assertEqual([entry["message"] for entry in paged["entries"]],
                             ["timeout"])

    def test_missing_scan_returns_empty_page(self):
        with self._patched():
            self._seed()
            page = scan_logs.get_scan_logs_page("nope")
        self.assertEqual(page, {"total": 0, "entries": []})

    def test_count_scan_logs_filtered(self):
        with self._patched():
            self._seed()
            self.assertEqual(
                scan_logs.count_scan_logs_filtered("v2_20260911_5"), 3)
            self.assertEqual(
                scan_logs.count_scan_logs_filtered("v2_20260911_5", level="warn"), 1)
            self.assertEqual(
                scan_logs.count_scan_logs_filtered("nope"), 0)


if __name__ == "__main__":
    unittest.main()
