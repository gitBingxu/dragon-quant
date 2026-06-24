"""tests for dragon_quant.cli — 命令行入口。"""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from dragon_quant._version import __version__
from dragon_quant import cli


class TestCliVersion(unittest.TestCase):

    def test_short_version_option(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "-v"]):
            with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
                cli.main()

        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), f"dragon-quant {__version__}")


class TestCliSourceArgs(unittest.TestCase):

    def test_review_source_passes_to_run_review(self):
        with patch("sys.argv", ["dragon-quant", "review", "--source", "v2", "--date", "20260519"]), \
             patch("dragon_quant.review.run_review") as mock_run:
            cli.main()

        mock_run.assert_called_once_with(
            trade_date="2026-05-19", top_n=None, force=False, verbose=True, source="v2"
        )

    def test_review_ui_only_source_passes_to_server(self):
        with patch("sys.argv", ["dragon-quant", "review", "--ui-only", "--source", "v2", "--no-browser"]), \
             patch("web_ui.server.start_server") as mock_start:
            cli.main()

        mock_start.assert_called_once_with(port=8765, open_browser=False, default_source="v2")

    def test_scan_history_uses_v2_source(self):
        scan = {
            "id": "v2_20260519_5",
            "raw_output": '{"ranking": [], "source": "v2"}',
        }
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "scan_v2", "--date", "20260519", "--top", "5"]), \
             patch("dragon_quant.storage.db.get_latest_scan_by_date", return_value=scan) as mock_get, \
             redirect_stdout(buf):
            cli.main()

        mock_get.assert_called_once_with("2026-05-19", 5, source="v2")
        self.assertEqual(json.loads(buf.getvalue()), {"ranking": [], "source": "v2"})

    def test_scan_history_empty_raw_output_returns_error_without_rebuild(self):
        scan = {
            "id": "v1_20260519_5",
            "scan_date": "2026-05-19",
            "top_n": 5,
            "raw_output": "",
        }
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "scan", "--date", "20260519", "--top", "5"]), \
             patch("dragon_quant.storage.db.get_latest_scan_by_date", return_value=scan), \
             patch("dragon_quant.storage.db.get_scan_stocks") as mock_get_stocks, \
             redirect_stdout(buf):
            cli.main()

        self.assertEqual(json.loads(buf.getvalue()), {
            "error": "scan raw_output is empty",
            "scan_id": "v1_20260519_5",
            "scan_date": "2026-05-19",
            "top_n": 5,
            "source": "v1",
        })
        mock_get_stocks.assert_not_called()

    def test_long_version_option(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "--version"]):
            with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
                cli.main()

        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), f"dragon-quant {__version__}")


if __name__ == "__main__":
    unittest.main()
