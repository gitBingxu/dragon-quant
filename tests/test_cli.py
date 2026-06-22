"""tests for dragon_quant.cli — 命令行入口。"""
import io
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

    def test_long_version_option(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "--version"]):
            with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
                cli.main()

        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), f"dragon-quant {__version__}")


if __name__ == "__main__":
    unittest.main()
