import io
import re
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from dragon_quant import cli
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "skills" / "dragon-quant"


def _help(*args):
    buf = io.StringIO()
    with patch("sys.argv", ["dragon-quant", *args, "-h"]):
        try:
            with redirect_stdout(buf):
                cli.main()
        except SystemExit as exc:
            assert exc.code == 0
    return buf.getvalue()


def _table(text, start, end):
    block = text.split(start, 1)[1].split(end, 1)[0]
    rows = []
    for line in block.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return []
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[1:]]


def _routes():
    text = (ROOT / "references" / "commands.md").read_text(encoding="utf-8")
    return _table(text, "<!-- routes:start -->", "<!-- routes:end -->")


class TestSkillRoutes(unittest.TestCase):

    def test_routes_parse_non_empty(self):
        self.assertGreaterEqual(len(_routes()), 10)

    def test_top_level_commands_exist(self):
        top = _help()
        for route in _routes():
            command = route["command"].split()[1]
            self.assertIn(command, top, command)

    def test_key_flags_exist(self):
        for route in _routes():
            parts = route["command"].split()
            help_args = [p for p in parts[1:] if not p.startswith("--")]
            help_text = _help(*help_args)
            for flag in route["key_flags"].split():
                if flag.startswith("--"):
                    self.assertIn(flag, help_text, f"{' '.join(help_args)} {flag}")

    def test_each_route_has_one_write_class(self):
        for route in _routes():
            self.assertIn(route["write_class"], {"T0", "T1", "T2"}, route["command"])

    def test_top_level_commands_covered_by_safety(self):
        top = _help()
        commands = set(re.findall(r"^\s{2,}([a-z][a-z0-9-]+)\s{2,}", top, re.MULTILINE))
        text = (ROOT / "references" / "safety.md").read_text(encoding="utf-8")
        rows = _table(text, "<!-- safety:start -->", "<!-- safety:end -->")
        safety = {row["command"] for row in rows}
        self.assertEqual(commands, safety)


class TestSafetyTiers(unittest.TestCase):

    def _safety(self):
        text = (ROOT / "references" / "safety.md").read_text(encoding="utf-8")
        return _table(text, "<!-- safety:start -->", "<!-- safety:end -->")

    def _exact(self, command, flags):
        rows = [row for row in self._safety()
                if row["command"] == command and row["flags"] == flags]
        self.assertTrue(rows, (command, flags))
        return rows

    def _assert_tier(self, command, flags, tier):
        for row in self._exact(command, flags):
            self.assertEqual(row["tier"], tier, row)

    def test_high_risk_commands_are_not_read_only(self):
        self._assert_tier("buy", "--date --at --force --json", "T1")
        self._assert_tier("sell", "--date --at --force", "T1")

    def test_destructive_subcommands_are_t2(self):
        cases = [
            ("storage", "clear --all --cache --results --logs --days"),
            ("logs", "clear --days"),
            ("assets", "push"),
            ("assets", "pull --apply / pull --force"),
            ("assets", "backfill-dragons --prune"),
            ("data", "cookie-fetch / cookie-set --cookie"),
        ]
        for command, flags in cases:
            self._assert_tier(command, flags, "T2")

    def test_dry_run_and_read_only_controls(self):
        cases = [
            ("dragons", "--date --true-only"),
            ("scan", "--date --top"),
            ("assets", "status/verify/pull"),
            ("assets", "push --dry-run"),
            ("assets", "backfill-dragons --dry-run"),
            ("vpa", "--no-save"),
        ]
        for command, flags in cases:
            self._assert_tier(command, flags, "T0")

    def test_routes_declare_a_single_write_class(self):
        for route in _routes():
            self.assertNotIn("/", route["write_class"], route["command"])
