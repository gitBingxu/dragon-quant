"""因子层集成测试：迁移闭环 / 幂等 / prune / 源故障不删 / CLI 契约（方案 §8.2 P0 部分）。

全部使用临时 dragon.db + assets.db，零网络。
"""

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from dragon_quant.factors import engine
from dragon_quant.factors import storage as fstore
from dragon_quant.factors.registry import compute_version_info
from dragon_quant.factors.types import FactorSpec, FactorValue
from dragon_quant.storage import factors as store


class FactorIntegrationBase(unittest.TestCase):
    TRADE_DATE = "2026-05-19"
    SCAN_ID = "v2_20260519_25"

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.dragon_path = str(Path(self._tmpdir.name) / "dragon.db")
        self.assets_path = str(Path(self._tmpdir.name) / "assets.db")
        self._patches = [
            patch("dragon_quant.storage.db._connect",
                  side_effect=lambda *a, **kw: sqlite3.connect(self.dragon_path)),
            patch("dragon_quant.storage.assets_db._connect",
                  side_effect=self._assets_connect),
        ]
        for item in self._patches:
            item.start()
        from dragon_quant.storage import db
        db.init_db()
        self._seed_scan()
        self._seed_vpa()

    def tearDown(self):
        for item in reversed(self._patches):
            item.stop()
        self._tmpdir.cleanup()

    # ── 基础设施 ──
    def _assets_connect(self, *args, create=True, path=None, **kwargs):
        target = path or self.assets_path
        if not create and not Path(target).exists():
            return None
        return sqlite3.connect(target)

    def _exec(self, sql, params=(), path=None):
        conn = sqlite3.connect(path or self.dragon_path)
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def _query(self, sql, params=(), path=None):
        conn = sqlite3.connect(path or self.assets_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    @staticmethod
    def _stocks():
        return [
            {
                "code": "600001", "name": "甲", "composite_score": 81.5,
                "board_count": 2, "concepts": ["低空经济"],
                "dimensions": {
                    "drive": {"score": 80.0},
                    "leadership": {"score": 82.0},
                    "anti_drop": {"score": 70.0},
                    "liquidity": {"score": 75.0},
                    "absorption": {"score": 61.0},
                },
                "is_true_dragon": True,
            },
            {
                "code": "600002", "name": "乙", "composite_score": 55.0,
                "board_count": 1,
                "dimensions": {"drive": {"score": 50.0}},
                # 其余维度 / is_true_dragon 缺失 → NULL → 不写行
            },
        ]

    @staticmethod
    def _raw_output():
        return {
            "ranking": [
                {"code": "600001", "primary_sector": "881167",
                 "primary_sector_category": "hy", "primary_sector_source": "tdx",
                 "primary_sector_name": "低空经济"},
                {"code": "600002", "primary_sector": "885001",
                 "primary_sector_category": "gn", "primary_sector_source": "ths",
                 "primary_sector_name": "概念X"},
                {"code": "600003", "name": "无板块字段"},
            ]
        }

    def _seed_scan(self, *, trade_date=None, scan_id=None, top_n=25,
                   created_at=None, stocks=None, raw_output=None):
        from dragon_quant.storage import db
        trade_date = trade_date or self.TRADE_DATE
        scan_id = scan_id or self.SCAN_ID
        created_at = created_at or f"{trade_date} 15:30:00"
        payload = raw_output if raw_output is not None else self._raw_output()
        db.save_scan(
            scan_id, trade_date, 1.0, top_n, 5, 2,
            stocks=stocks if stocks is not None else self._stocks(),
            raw_output=json.dumps(payload, ensure_ascii=False),
            source="v2")
        self._exec("UPDATE scans_v2 SET created_at = ? WHERE id = ?",
                   (created_at, scan_id))

    def _seed_vpa(self, *, trade_date=None):
        from dragon_quant.storage import db
        trade_date = trade_date or self.TRADE_DATE
        factors = [
            {"name": "vol_amount", "title": "量能", "signal": "bullish",
             "score": 66.0, "note": "", "evidence": [], "details": {}},
            {"name": "trend_verify", "title": "趋势", "signal": "neutral",
             "score": 55.0, "note": "", "evidence": [], "details": {}},
            {"name": "breakout", "title": "突破", "signal": "bullish",
             "score": 77.0, "note": "", "evidence": [], "details": {}},
            {"name": "divergence", "title": "背离", "signal": "bearish",
             "score": 33.0, "note": "", "evidence": [], "details": {}},
        ]
        db.upsert_vpa(trade_date, "600001", name="甲", source="xueqiu",
                      health_score=70.0, signal="bullish", summary="",
                      factors_json=json.dumps(factors, ensure_ascii=False))
        self._exec("UPDATE vpa_analysis SET created_at = ? "
                   "WHERE trade_date = ? AND code = '600001'",
                   (f"{trade_date} 16:10:00", trade_date))
        # 跨日回填污染样本（review.py 用运行当天 K 线写历史 trade_date）
        db.upsert_vpa(trade_date, "600002", name="乙", source="xueqiu",
                      health_score=40.0, signal="bearish", summary="",
                      factors_json=json.dumps(factors, ensure_ascii=False))
        self._exec("UPDATE vpa_analysis SET created_at = datetime(?, '+1 day') "
                   "WHERE trade_date = ? AND code = '600002'",
                   (f"{trade_date} 16:10:00", trade_date))

    def _build(self, trade_date=None, names=None, calendar=()):
        return engine.build_date(trade_date or self.TRADE_DATE, names=names,
                                 calendar=calendar)


class TestMigrationLoop(FactorIntegrationBase):
    def test_twelve_factors_written_with_lineage(self):
        report = self._build()
        self.assertTrue(report["ok"], report["warnings"])
        self.assertIsNotNone(report["build_id"])
        self.assertEqual(len(report["factors"]), 12)

        names = {r[0] for r in self._query(
            "SELECT DISTINCT factor_name FROM factor_values")}
        self.assertEqual(len(names), 12)
        self.assertTrue({"five_dim_composite", "five_dim_drive",
                         "five_dim_is_true_dragon", "vpa_breakout"} <= names)

        composite = self._query(
            "SELECT code, value, available_at, source, source_scan_id "
            "FROM factor_values WHERE factor_name='five_dim_composite' ORDER BY code")
        self.assertEqual([row[0] for row in composite], ["600001", "600002"])
        self.assertEqual(composite[0][1], 81.5)
        self.assertEqual(composite[0][2], "2026-05-19T15:30:00")
        self.assertEqual(composite[0][3], "scan")
        self.assertEqual(composite[0][4], self.SCAN_ID)

    def test_is_true_dragon_null_not_written(self):
        self._build()
        rows = self._query(
            "SELECT code, value FROM factor_values "
            "WHERE factor_name='five_dim_is_true_dragon' ORDER BY code")
        self.assertEqual(rows, [("600001", 1.0)])

    def test_missing_dimension_has_no_row(self):
        self._build()
        rows = self._query(
            "SELECT code FROM factor_values WHERE factor_name='five_dim_leadership'")
        self.assertEqual(rows, [("600001",)])

    def test_vpa_backfilled_rows_skipped_but_same_day_kept(self):
        report = self._build()
        entry = report["factors"]["vpa_health_score"]
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["rows"], 1)
        self.assertEqual(entry["skipped_backfilled"], 1)
        self.assertEqual(entry["available_at"], "2026-05-19T16:10:00")
        rows = self._query(
            "SELECT code, value FROM factor_values WHERE factor_name='vpa_health_score'")
        self.assertEqual(rows, [("600001", 70.0)])

    def test_sector_snapshot_records_category_as_is(self):
        report = self._build()
        sector = report["sector_snapshot"]
        self.assertEqual(sector["status"], "ok")
        self.assertEqual(sector["rows"], 2)
        self.assertEqual(sector["skipped"], 1)
        self.assertEqual(sector["by_category"], {"gn": 1, "hy": 1})
        rows = self._query(
            "SELECT code, industry_code, category, source FROM factor_sector_snapshots "
            "ORDER BY code")
        self.assertEqual(rows, [
            ("600001", "881167", "hy", "tdx"),
            ("600002", "885001", "gn", "ths"),
        ])
        self.assertEqual(store.get_sector_map(self.TRADE_DATE)["600001"]["category"], "hy")

    def test_registry_snapshot_written_once(self):
        self._build()
        self.assertEqual(len(self._query("SELECT * FROM factor_registry")), 12)
        self._build()   # 幂等：DO NOTHING
        self.assertEqual(len(self._query("SELECT * FROM factor_registry")), 12)

    def test_build_run_report_persisted(self):
        report = self._build()
        run = store.list_build_runs(trade_date=self.TRADE_DATE)[0]
        self.assertEqual(run["id"], report["build_id"])
        self.assertEqual(run["report"]["trade_date"], self.TRADE_DATE)
        self.assertEqual(run["report"]["scan"]["scan_id"], self.SCAN_ID)


class TestIdempotenceAndPrune(FactorIntegrationBase):
    def _snapshot(self):
        return self._query(
            "SELECT trade_date, code, factor_name, factor_version, value, available_at "
            "FROM factor_values ORDER BY trade_date, code, factor_name, factor_version")

    def test_rebuild_is_idempotent(self):
        self._build()
        first = self._snapshot()
        self._build()
        self.assertEqual(first, self._snapshot())

    def test_candidate_shrink_prunes_old_codes(self):
        self._build()
        self.assertTrue(self._query(
            "SELECT 1 FROM factor_values WHERE code='600002' LIMIT 1"))
        self._seed_scan(
            stocks=[self._stocks()[0]],
            raw_output={"ranking": [self._raw_output()["ranking"][0]]})
        self._build()
        self.assertEqual(self._query(
            "SELECT 1 FROM factor_values WHERE code='600002' LIMIT 1"), [])
        self.assertEqual(len(store.get_sector_map(self.TRADE_DATE)), 1)


class TestSourceFailureSemantics(FactorIntegrationBase):
    def test_missing_vpa_source_keeps_old_rows(self):
        self._build()
        before = self._query(
            "SELECT code, value FROM factor_values WHERE factor_name='vpa_health_score'")
        self.assertEqual(len(before), 1)
        self._exec("DELETE FROM vpa_analysis")
        report = self._build()
        entry = report["factors"]["vpa_health_score"]
        self.assertEqual(entry["status"], "missing_source")
        after = self._query(
            "SELECT code, value FROM factor_values WHERE factor_name='vpa_health_score'")
        self.assertEqual(before, after, "源故障不得删除既有因子行")

    def test_empty_source_result_deletes_rows(self):
        self._build()
        # 源可用（行仍在）但没有 health_score → empty → 删除该因子当日旧行
        self._exec("UPDATE vpa_analysis SET health_score = NULL "
                   "WHERE trade_date = ? AND code = '600001'", (self.TRADE_DATE,))
        report = self._build()
        entry = report["factors"]["vpa_health_score"]
        self.assertEqual(entry["status"], "empty")
        self.assertEqual(entry["rows"], 0)
        self.assertIn("empty_unexpected", entry["warnings"])
        self.assertEqual(self._query(
            "SELECT * FROM factor_values WHERE factor_name='vpa_health_score'"), [])
        # 其他 vpa 因子不受影响
        self.assertEqual(len(self._query(
            "SELECT * FROM factor_values WHERE factor_name='vpa_breakout'")), 1)

    def test_all_cross_day_vpa_rows_skipped(self):
        second = "2026-05-20"
        self._seed_scan(trade_date=second, scan_id="v2_20260520_25")
        self._seed_vpa(trade_date=second)
        # 把两条 vpa 行都推成跨日
        self._exec("UPDATE vpa_analysis SET created_at = ? WHERE trade_date = ?",
                   (f"{second} 10:00:00", second))
        self._exec("UPDATE vpa_analysis SET created_at = datetime(?, '+1 day') "
                   "WHERE trade_date = ?", (f"{second} 10:00:00", second))
        report = self._build(second, names=["vpa_breakout"])
        entry = report["factors"]["vpa_breakout"]
        self.assertEqual(entry["status"], "skipped_backfilled")
        self.assertEqual(entry["skipped_backfilled"], 2)
        self.assertEqual(self._query(
            "SELECT * FROM factor_values WHERE trade_date=?", (second,)), [])

    def test_compute_exception_isolated_and_keeps_rows(self):
        def boom(ctx):
            raise RuntimeError("boom")

        spec = FactorSpec(
            name="broken_factor", label="坏因子", frequency="1d", source="scan",
            compute=boom, impl_key="tests.broken.v1")
        version, _ = compute_version_info(spec)
        fstore.replace_factor_day(self.TRADE_DATE, "broken_factor", version, [
            FactorValue(
                trade_date=self.TRADE_DATE, code="600001", factor_name="broken_factor",
                factor_version=version, value=1.0,
                available_at="2026-05-19T15:05:00", source="scan"),
        ])
        entry = engine.build_factor(spec, self.TRADE_DATE)
        self.assertEqual(entry["status"], "error")
        self.assertIn("boom", entry["reason"])
        self.assertEqual(len(self._query(
            "SELECT * FROM factor_values WHERE factor_name='broken_factor'")), 1)

    def test_no_scan_date_reports_and_writes_audit(self):
        report = self._build("2026-05-21")
        self.assertFalse(report["ok"])
        self.assertIsNone(report["scan"])
        self.assertEqual(report["factors"]["five_dim_composite"]["status"],
                         "missing_source")
        self.assertIsNotNone(report["build_id"])


class TestQuerySemantics(FactorIntegrationBase):
    def test_day_query_groups_by_code_and_returns_versions(self):
        self._build()
        payload = fstore.get_day_values(self.TRADE_DATE)
        self.assertEqual(set(payload["values"]["600001"]), {
            "five_dim_composite", "five_dim_drive", "five_dim_leadership",
            "five_dim_anti_drop", "five_dim_liquidity", "five_dim_absorption",
            "five_dim_is_true_dragon", "vpa_health_score", "vpa_vol_amount",
            "vpa_trend_verify", "vpa_breakout", "vpa_divergence"})
        self.assertEqual(len(payload["resolved_versions"]), 12)
        self.assertEqual(payload["missing_versions"], [])
        self.assertEqual(payload["values"]["600001"]["five_dim_composite"], 81.5)

    def test_filter_by_name_and_version(self):
        self._build()
        payload = fstore.get_day_values(
            self.TRADE_DATE, names=["five_dim_composite"])
        self.assertEqual(set(payload["values"]["600001"]), {"five_dim_composite"})
        version = payload["resolved_versions"]["five_dim_composite"]
        payload2 = fstore.get_day_values(
            self.TRADE_DATE, names=["five_dim_composite"], version=version)
        self.assertEqual(payload2["rows"], payload["rows"])

    def test_series(self):
        self._build()
        payload = fstore.get_series("five_dim_composite", ["600001"],
                                    self.TRADE_DATE, self.TRADE_DATE)
        self.assertEqual(len(payload["rows"]), 1)
        self.assertEqual(payload["rows"][0]["value"], 81.5)
        self.assertIn("five_dim_composite", payload["resolved_versions"])


class TestCliContract(FactorIntegrationBase):
    def _run(self, argv):
        from dragon_quant import cli
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with patch("sys.argv", ["dragon-quant", *argv]), \
             redirect_stdout(out), redirect_stderr(err):
            try:
                cli.main()
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()

    def test_list_json(self):
        code, out, _ = self._run(["factor", "list", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(len(payload["factors"]), 18)

    def test_build_json_contract(self):
        code, out, _ = self._run(
            ["factor", "build", "--date", "20260519", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(set(payload), {"ok", "mode", "dates", "build_ids",
                                        "reports", "warnings"})
        self.assertEqual(payload["dates"], [self.TRADE_DATE])
        self.assertEqual(len(payload["reports"]), 1)
        report = payload["reports"][0]
        self.assertTrue({"trade_date", "factors", "sector_snapshot",
                         "build_id", "ok", "warnings"} <= set(report))

    def test_build_range_json(self):
        self._seed_scan(trade_date="2026-05-20", scan_id="v2_20260520_25")
        code, out, _ = self._run([
            "factor", "build", "--from", "20260519", "--to", "20260520", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["mode"], "range")
        self.assertEqual(payload["dates"], ["2026-05-19", "2026-05-20"])
        self.assertEqual(len(payload["build_ids"]), 2)
        self.assertTrue(all(payload["build_ids"]))

    def test_query_json(self):
        self._run(["factor", "build", "--date", "20260519"])
        code, out, _ = self._run([
            "factor", "query", "--date", "20260519",
            "--name", "five_dim_composite", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["mode"], "day")
        self.assertEqual(payload["values"]["600001"]["five_dim_composite"], 81.5)
        self.assertIn("resolved_versions", payload)

    def test_status_json(self):
        self._run(["factor", "build", "--date", "20260519"])
        code, out, _ = self._run(["factor", "status", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["ok"], True)
        self.assertGreater(payload["factor_values"]["rows"], 0)
        self.assertEqual(payload["build_runs"]["rows"], 1)

    def test_missing_date_argument_exit_2_and_stderr(self):
        code, out, err = self._run(["factor", "build"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("❌", err)

    def test_missing_date_argument_json_error_on_stdout(self):
        code, out, _ = self._run(["factor", "build", "--json"])
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertIn("error", payload)

    def test_unknown_factor_query_exit_2(self):
        code, out, err = self._run([
            "factor", "query", "--date", "20260519", "--name", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("未知因子", err)


class TestDependencyIsolation(unittest.TestCase):
    """静态约束：factors/* 不 import scorers / providers / review_account。"""

    def _imports(self, path: Path) -> set[str]:
        import ast
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    def test_factors_do_not_import_scorers_or_providers(self):
        root = Path(__file__).resolve().parents[1] / "dragon_quant" / "factors"
        forbidden_prefixes = (
            "dragon_quant.scorers", "dragon_quant.providers",
            "dragon_quant.review_account", "dragon_quant.vpa",
        )
        for path in root.rglob("*.py"):
            # 方案 §3.4 修订：factors/finance.py 是唯一允许触网的模块
            allowed = {"dragon_quant.providers"} if path.name == "finance.py" else set()
            for module in self._imports(path):
                for prefix in forbidden_prefixes:
                    if prefix in allowed:
                        continue
                    with self.subTest(file=path.name, module=module):
                        self.assertFalse(module.startswith(prefix),
                                         f"{path} 违规 import {module}")

    def test_storage_does_not_import_factors(self):
        root = Path(__file__).resolve().parents[1] / "dragon_quant" / "storage"
        for path in root.rglob("*.py"):
            for module in self._imports(path):
                with self.subTest(file=path.name, module=module):
                    self.assertFalse(module.startswith("dragon_quant.factors"))


if __name__ == "__main__":
    unittest.main()
