"""因子引擎测试：canonical scan / available_at 三套语义 / 时区告警（方案 §6、§8.1）。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dragon_quant.factors.engine import (
    PHASE_TIMES,
    AsOfViolation,
    BackfilledSource,
    canonical_scan,
    format_ts,
    parse_local,
    phase_ts,
    resolve_available_at,
    timezone_warning,
)
from dragon_quant.factors.types import FactorSpec


def _spec(pit_rule="recorded", phase="close", name="demo"):
    return FactorSpec(
        name=name, label="示例", frequency="1d", source="scan",
        compute=lambda ctx: None, impl_key="tests.demo",
        available_phase=phase, pit_rule=pit_rule)


class TestPhaseAndParsing(unittest.TestCase):
    def test_phase_bases(self):
        expected = {"auction": (9, 25), "open": (9, 30), "close": (15, 5), "prep": (16, 40)}
        self.assertEqual(PHASE_TIMES, expected)
        for phase, (hour, minute) in expected.items():
            moment = phase_ts("2026-05-19", phase)
            self.assertEqual((moment.hour, moment.minute), (hour, minute))
            self.assertEqual(moment.utcoffset().total_seconds(), 8 * 3600)

    def test_parse_sqlite_and_iso_formats_are_equivalent(self):
        sqlite_style = parse_local("2026-05-19 16:10:00")
        iso_style = parse_local("2026-05-19T16:10:00")
        self.assertEqual(sqlite_style, iso_style)
        self.assertEqual(format_ts(sqlite_style), "2026-05-19T16:10:00")

    def test_string_comparison_trap(self):
        """'T' > ' '：字符串比较会恒取 phase，必须走 datetime。"""
        self.assertGreater("2026-05-19T16:10:00", "2026-05-19 15:05:00")
        self.assertLess(parse_local("2026-05-19T16:10:00"), parse_local("2026-05-19 16:20:00"))

    def test_invalid_phase(self):
        with self.assertRaises(ValueError):
            phase_ts("2026-05-19", "noon")


class TestResolveAvailableAt(unittest.TestCase):
    def test_recorded_takes_later_of_phase_and_created(self):
        # 源在收盘后 16:10 才算出
        self.assertEqual(
            resolve_available_at(_spec(), "2026-05-19", "2026-05-19 16:10:00"),
            "2026-05-19T16:10:00")
        # 源在 10:00 就跑完：仍按 close 记 15:05（刻意保守）
        self.assertEqual(
            resolve_available_at(_spec(), "2026-05-19", "2026-05-19 10:00:00"),
            "2026-05-19T15:05:00")

    def test_recorded_cross_day_rejected(self):
        with self.assertRaises(BackfilledSource):
            resolve_available_at(_spec(), "2026-05-19", "2026-05-20 09:00:00")

    def test_recorded_requires_created_at(self):
        with self.assertRaises(ValueError):
            resolve_available_at(_spec(), "2026-05-19", None)

    def test_derived_ignores_created_at(self):
        spec = _spec(pit_rule="derived", phase="prep")
        self.assertEqual(
            resolve_available_at(spec, "2026-05-19", "2026-05-25 09:00:00"),
            "2026-05-19T16:40:00")

    def test_asof_returns_phase(self):
        spec = _spec(pit_rule="asof", phase="prep")
        self.assertEqual(
            resolve_available_at(spec, "2026-05-19"),
            "2026-05-19T16:40:00")

    def test_asof_hard_gate(self):
        spec = _spec(pit_rule="asof", phase="prep")
        # 账本记录在 16:40 前可用 → 通过
        self.assertEqual(
            resolve_available_at(spec, "2026-05-19",
                                 ledger_available_at="2026-05-19 16:40:00"),
            "2026-05-19T16:40:00")
        # 账本记录 17:02 才可见 → 拒绝
        with self.assertRaises(AsOfViolation):
            resolve_available_at(spec, "2026-05-19",
                                 ledger_available_at="2026-05-19 17:02:00")

    def test_unknown_pit_rule(self):
        spec = _spec()
        object.__setattr__(spec, "pit_rule", "magic")
        with self.assertRaises(ValueError):
            resolve_available_at(spec, "2026-05-19", "2026-05-19 16:00:00")


class TestTimezone(unittest.TestCase):
    def test_timezone_warning_when_not_plus_8(self):
        fake = SimpleNamespace(tm_gmtoff=0)
        with patch("dragon_quant.factors.engine.time.localtime", return_value=fake):
            warning = timezone_warning()
        self.assertIn("timezone_warning", warning)

    def test_no_warning_on_plus_8(self):
        fake = SimpleNamespace(tm_gmtoff=8 * 3600)
        with patch("dragon_quant.factors.engine.time.localtime", return_value=fake):
            self.assertIsNone(timezone_warning())


class TestCanonicalScan(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "dragon.db")
        self._patch = patch(
            "dragon_quant.storage.db._connect",
            side_effect=lambda *a, **kw: sqlite3.connect(self._db_path))
        self._patch.start()
        from dragon_quant.storage import db
        db.init_db()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def _insert(self, scan_id, top_n, created_at):
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO scans_v2(id, scan_date, top_n, created_at) "
                "VALUES (?, '2026-05-19', ?, ?)",
                (scan_id, top_n, created_at))
            conn.commit()
        finally:
            conn.close()

    def test_no_scan_returns_none(self):
        self.assertIsNone(canonical_scan("2026-05-19"))

    def test_latest_created_at_wins(self):
        self._insert("v2_20260519_5", 5, "2026-05-19 15:10:00")
        self._insert("v2_20260519_25", 25, "2026-05-19 15:30:00")
        self.assertEqual(canonical_scan("2026-05-19")["id"], "v2_20260519_25")

    def test_same_second_breaks_by_top_n_not_lexicographic(self):
        # 字典序陷阱：v2_20260519_25 < v2_20260519_5（'2' < '5'）
        self.assertGreater("v2_20260519_5", "v2_20260519_25")
        self._insert("v2_20260519_5", 5, "2026-05-19 15:30:00")
        self._insert("v2_20260519_25", 25, "2026-05-19 15:30:00")
        self.assertEqual(canonical_scan("2026-05-19")["id"], "v2_20260519_25")

    def test_final_tie_breaks_by_id(self):
        self._insert("v2_20260519_25", 25, "2026-05-19 15:30:00")
        self._insert("v2_20260519_25b", 25, "2026-05-19 15:30:00")
        self.assertEqual(canonical_scan("2026-05-19")["id"], "v2_20260519_25b")


if __name__ == "__main__":
    unittest.main()
