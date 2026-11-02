"""premarket CLI / handler 测试（阶段 3 Task 3）。

覆盖日期矩阵、exit 0/1/2、JSON/human 通道、B1 零创建、无 provider / 无写库。
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SHANGHAI = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[2]
T = "2026-10-30"
NOW = datetime(2026, 10, 30, 9, 26, tzinfo=SHANGHAI)


def _handle(args, services=None):
    from dragon_quant.commands import premarket

    out, err = io.StringIO(), io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            premarket.handle(args, services or SimpleNamespace(providers=None))
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


class CliTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "data" / "assets.db"
        self._patch = patch("dragon_quant.infra.assets.db.ASSETS_DB_PATH",
                            str(self.db))
        self._patch.start()
        self._clock = patch("dragon_quant.infra.clock.now", return_value=NOW)
        self._clock.start()

    def tearDown(self):
        self._clock.stop()
        self._patch.stop()
        self._tmp.cleanup()


class TestDateMatrix(CliTestBase):
    def test_accepts_three_date_formats_and_normalises(self):
        from dragon_quant.review_account.premarket.main import (
            normalize_trade_date,
        )

        for raw in ("20261030", "2026-10-30", "2026/10/30"):
            self.assertEqual(normalize_trade_date(raw), T)

    def test_invalid_date_exits_two(self):
        for raw in ("2026-13-01", "20261301", "abc"):
            with self.subTest(raw=raw):
                code, out, err = _handle(
                    SimpleNamespace(date=raw, json=False))
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("非法日期", err)

    def test_future_date_exits_two_in_both_output_modes(self):
        for as_json in (False, True):
            with self.subTest(json=as_json):
                code, out, err = _handle(
                    SimpleNamespace(date="20261101", json=as_json))
                self.assertEqual(code, 2)
                payload = out if as_json else err
                self.assertIn("未来日期", payload)


class TestOutputs(CliTestBase):
    def _seed(self):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db)
        prep = {
            "trade_date": "2026-10-29", "version": "0.8",
            "sentiment": {"up_cnt": 2100, "down_cnt": 950,
                          "limit_up_cnt": 62, "limit_down_cnt": 3,
                          "broken_rate": 0.016,
                          "yesterday_premium_pct": 5.67,
                          "promotion_rate": 0.667, "max_board_height": 4},
            "candidates": [],
            "review_candidates": [{
                "code": "600001", "name": "龙头A", "role": "leader",
                "mainline": {"board_name": "机器人", "rank": 1,
                             "state": "strengthening"},
                "review_score": 70.3, "review_grade": "B", "confidence": 0.7,
                "risk_level": "low", "eligibility": "eligible",
                "position_pct": 0.04, "position_basis": ["factor"],
                "reason_code": None, "gate_trace": [], "warnings": [],
                "expectations": {"gap_low": -0.02, "gap_high": 0.04,
                                 "ratio_low": 0.8, "seal_by": "10:00"},
            }],
            "review": {"enabled": True,
                       "config_version": "review_baseline_v1",
                       "config_hash": "h", "review_version": "review_v1",
                       "market": {"state": "strong", "score": 72.0,
                                  "coeff": 1.0, "evidence": [],
                                  "unknown_reason": None},
                       "mainlines": [{"board_name": "机器人",
                                      "state": "strengthening",
                                      "role_map": {"leader": "600001"}}],
                       "anchors": {"positive": [], "negative": [],
                                   "risk": {}},
                       "portfolio": {"sum_pct": 4.0, "warning": None},
                       "degraded": []},
            "strategy_context": {
                "frozen_at": "2026-10-29T16:40:00+08:00",
                "feedback_plans": [{
                    "code": "600001", "role": "leader",
                    "position_pct": 0.04,
                    "expected": {"gap_low": -0.02, "gap_high": 0.04,
                                 "ratio_low": 0.8, "seal_by": "10:00"},
                    "conditions": [
                        {"state": state, "allowed_actions": [action]}
                        for state, action in (
                            ("confirmed", "execute_plan"),
                            ("weakened", "downgrade_half"),
                            ("invalidated", "cancel_entry"),
                            ("unknown", "observe_only"))],
                    "allowed_actions": ["execute_plan"],
                    "unknown_reason": None}]},
        }
        analysis = {
            "version": "2", "code": "600001", "is_weak": False,
            "strength": 69.9, "level": "中", "direction": "neutral", "rank": 1,
            "dims": {}, "reject_reason": None, "unverified": False,
            "config_version": "auction_v2_baseline", "config_hash": "a",
            "validation_status": "baseline_unvalidated",
            "semantics": {"semantics_version": "auction_semantics_v1"},
            "data_quality": {"status": "ok", "completeness": 1.0,
                             "reason_codes": [], "reasons": []},
            "risk_events": [],
            "raw_factors": {"official_open_gap_pct": 4.0},
            "evidence": {"last_time": "09:25:00", "official_open_px": 10.4},
            "reference_prices": {"prev_close": 10.0, "official_open": 10.4},
            "windows": {"core": ["09:20:00", "09:25:00"]}, "actions": [],
        }
        with conn:
            conn.execute(
                "CREATE TABLE prep_snapshots (trade_date TEXT, source TEXT, "
                "result_json TEXT, created_at TEXT, PRIMARY KEY(trade_date, source))")
            conn.execute(
                "CREATE TABLE auction_snapshots (trade_date TEXT, code TEXT, "
                "source TEXT, analysis_json TEXT, created_at TEXT, "
                "PRIMARY KEY(trade_date, code, source))")
            conn.execute(
                "INSERT INTO prep_snapshots VALUES (?, ?, ?, ?)",
                ("2026-10-29", "v2", json.dumps(prep), "2026-10-29 16:52:11"))
            conn.execute(
                "INSERT INTO auction_snapshots VALUES (?, ?, ?, ?, ?)",
                (T, "600001", "v2", json.dumps(analysis),
                 "2026-10-30 09:26:01"))
        conn.close()

    def test_json_mode_emits_only_json(self):
        self._seed()
        code, out, err = _handle(SimpleNamespace(date=T, json=True))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["context_version"], "premarket_sc_v1")
        self.assertEqual(payload["trade_date"], T)
        self.assertEqual(payload["prep"]["trade_date"], "2026-10-29")
        self.assertEqual(payload["auction"]["state"], "ready")
        self.assertEqual(payload["data_quality"]["status"], "ok")
        self.assertEqual(payload["candidates"][0]["action_ceiling"]["status"],
                         "wait")

    def test_human_summary_has_fixed_line_order(self):
        self._seed()
        code, out, err = _handle(SimpleNamespace(date=None, json=False))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        lines = out.strip().splitlines()
        self.assertEqual(lines[0], f"盘前预案上下文｜{T}")
        self.assertIn("T0 只读：不写库、不触网、不重算", lines[1])
        self.assertTrue(lines[2].startswith("T-1 复盘：2026-10-29"))
        self.assertTrue(lines[3].startswith("T 竞价：state=ready"))
        self.assertTrue(lines[4].startswith("数据质量：ok"))
        self.assertTrue(lines[5].startswith("市场（T-1）：state=strong"))
        self.assertTrue(lines[6].startswith("主线（T-1）：机器人=strengthening"))
        self.assertTrue(lines[7].startswith("候选：1 只"))
        self.assertTrue(lines[8].startswith("行动上限：wait=1"))
        self.assertEqual(lines[9], "提示：")
        self.assertTrue(lines[-2].startswith("声明：") or lines[-1].startswith("声明："))

    def test_empty_data_root_degrades_with_exit_zero_and_no_writes(self):
        code, out, err = _handle(SimpleNamespace(date=T, json=True))
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["prep"]["state"], "missing")
        self.assertEqual(payload["auction"]["state"], "missing")
        self.assertEqual(payload["data_quality"]["status"], "unavailable")
        self.assertFalse(self.db.exists())

    def test_runtime_error_exits_one_with_json_to_stdout(self):
        with patch("dragon_quant.review_account.premarket.main.load_prep",
                   side_effect=RuntimeError("boom")):
            code, out, err = _handle(SimpleNamespace(date=T, json=True))
        self.assertEqual(code, 1)
        self.assertEqual(err, "")
        self.assertEqual(json.loads(out)["ok"], False)
        self.assertIn("boom", out)

    def test_runtime_error_exits_one_with_human_to_stderr(self):
        with patch("dragon_quant.review_account.premarket.main.load_prep",
                   side_effect=RuntimeError("boom")):
            code, out, err = _handle(SimpleNamespace(date=T, json=False))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("boom", err)

    def test_no_write_connect_is_used(self):
        self._seed()
        with patch("dragon_quant.infra.assets.db._connect",
                   side_effect=AssertionError("write path used")):
            code, out, _ = _handle(SimpleNamespace(date=T, json=True))
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])


class TestCommandRegistration(unittest.TestCase):
    def test_registry_entry_exists_and_never_needs_providers(self):
        from dragon_quant import commands

        entry = commands.COMMANDS["premarket"]
        self.assertEqual(entry.module, "dragon_quant.commands.premarket")
        self.assertFalse(entry.providers_needed(SimpleNamespace()))

    def test_help_lists_premarket(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, "-m", "dragon_quant", "premarket", "-h"],
                capture_output=True, text=True, cwd=str(ROOT),
                env={**os.environ, "DQ_DATA_DIR": str(Path(tmp) / "data")},
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        for token in ("--date", "--json", "premarket"):
            self.assertIn(token, result.stdout)

    def test_b1_help_and_import_create_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            env = {**os.environ, "DQ_DATA_DIR": str(data)}
            result = subprocess.run(
                [sys.executable, "-m", "dragon_quant", "premarket", "-h"],
                capture_output=True, text=True, cwd=str(ROOT), env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(data.exists())
            import_result = subprocess.run(
                [sys.executable, "-c", "import dragon_quant"],
                capture_output=True, text=True, cwd=str(ROOT), env=env)
            self.assertEqual(import_result.returncode, 0, import_result.stderr)
            self.assertFalse(data.exists())


if __name__ == "__main__":
    unittest.main()
