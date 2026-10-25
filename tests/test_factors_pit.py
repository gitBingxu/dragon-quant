"""财务 PIT 回归与因子纯函数测试（方案 §11 P0.5 六类 + §8.1 财务用例）。

零网络：fake tdx/fake 账本；全部临时 assets.db。
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dragon_quant.factors import engine
from dragon_quant.factors import finance as fin
from dragon_quant.factors import storage as fstore
from dragon_quant.factors.builtin import financial
from dragon_quant.factors.types import FactorContext
from dragon_quant.storage import finance as fin_store


class TempDbBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "assets.db")
        self._patch = patch(
            "dragon_quant.storage.assets_db._connect",
            side_effect=self._connect)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def _connect(self, *args, create=True, path=None, **kwargs):
        target = path or self._db_path
        if not create and not Path(target).exists():
            return None
        return sqlite3.connect(target)

    def seed_period(self, period, rows, *, md5="m1", first_seen="2026-08-20T10:00:00",
                    mapping=None, layout="gpcw_20", basis="statutory_backfilled",
                    available_at=None, observed_first_at=None):
        """写一条已校准布局的账本 vintage。rows: {code: {field: value}}"""
        fin_store.upsert_field_calibration(layout, {
            "num_fields": 20,
            "mapping": mapping or {
                field: {"index": i, "scale": 1.0}
                for i, field in enumerate(fin.MAPPED_FIELDS)
            },
            "confidence": {}, "status": "verified", "sample_n": 4,
        })
        fin_store.upsert_report_file(
            period, filename=f"tdxfin/gpcw{period.replace('-', '')}.zip", md5=md5,
            filesize=1, first_seen_at=first_seen, status="ok",
            records_count=len(rows))
        records = []
        for code, values in rows.items():
            if available_at is None:
                available, resolved_basis = fin.resolve_record_available_at(
                    period=period, observed_first_at=observed_first_at,
                    vintage_first_seen=first_seen, is_first_vintage=True)
            else:
                available, resolved_basis = available_at, basis
            records.append({
                "code": code, "values": values, "available_at": available,
                "pit_basis": resolved_basis, "first_seen_at": first_seen,
            })
        fin_store.insert_report_vintage(
            period, records, file_md5=md5, layout_key=layout)


class TestDeadline(unittest.TestCase):
    def test_four_boundaries_and_cross_year(self):
        self.assertEqual(fin.report_period_deadline("2026-03-31"),
                         "2026-04-30T23:59:59")
        self.assertEqual(fin.report_period_deadline("2026-06-30"),
                         "2026-08-31T23:59:59")
        self.assertEqual(fin.report_period_deadline("2026-09-30"),
                         "2026-10-31T23:59:59")
        self.assertEqual(fin.report_period_deadline("2026-12-31"),
                         "2027-04-30T23:59:59")

    def test_invalid_period_rejected(self):
        with self.assertRaises(ValueError):
            fin.report_period_deadline("2026-05-15")


class TestResolveRecordAvailableAt(unittest.TestCase):
    def test_backfilled_uses_deadline(self):
        available, basis = fin.resolve_record_available_at(
            period="2026-06-30", observed_first_at=None,
            vintage_first_seen="2026-11-01T10:00:00", is_first_vintage=True)
        self.assertEqual(available, "2026-08-31T23:59:59")
        self.assertEqual(basis, "statutory_backfilled")

    def test_observed_earlier_than_deadline(self):
        available, basis = fin.resolve_record_available_at(
            period="2026-06-30", observed_first_at="2026-08-15T17:02:11",
            vintage_first_seen="2026-11-01T10:00:00", is_first_vintage=True)
        self.assertEqual(available, "2026-08-15T17:02:11")
        self.assertEqual(basis, "observed")

    def test_observed_cannot_pull_revision_back(self):
        # D 日 observed、D+90 修订：修订值不得借用观测时刻
        available, basis = fin.resolve_record_available_at(
            period="2026-06-30", observed_first_at="2026-08-15T17:02:11",
            vintage_first_seen="2026-11-13T10:00:00", is_first_vintage=False)
        self.assertEqual(available, "2026-11-13T10:00:00")
        self.assertEqual(basis, "revised")


class TestAsOfExpansion(TempDbBase):
    CODE = "600001"
    VALUES = {"meigujing_zichan": 5.5, "jing_zichan": 5.5e8,
              "zhuying_shouru": 3e8, "jing_lirun": 3e7}

    def test_deadline_day_1640_cannot_use_period(self):
        self.seed_period("2026-06-30", {self.CODE: self.VALUES})
        same_day = fin.load_fin_source("2026-08-31", [self.CODE])
        self.assertEqual(same_day["current"], {})
        next_day = fin.load_fin_source("2026-09-01", [self.CODE])
        self.assertEqual(next_day["status"], "ok")
        self.assertEqual(next_day["current"][self.CODE]["pit_basis"],
                         "statutory_backfilled")

    def test_1702_observation_not_used_same_day(self):
        fin_store.upsert_observed_snapshots("2026-05-19", [{
            "code": self.CODE, "updated_date": 20260630,
            "period": "2026-06-30", "values": self.VALUES,
            "values_hash": fin.values_hash(self.VALUES),
            "captured_at": "2026-05-19T17:02:00",
        }])
        same_day = fin.load_fin_source("2026-05-19", [self.CODE])
        self.assertEqual(same_day["current"], {})
        self.assertEqual(same_day["observed"], {})
        next_day = fin.load_fin_source("2026-05-20", [self.CODE])
        self.assertEqual(next_day["status"], "ok")
        self.assertIn(self.CODE, next_day["observed"])

    def test_cohort_keeps_modal_period_only(self):
        self.seed_period("2026-06-30", {
            "600001": self.VALUES, "600002": self.VALUES,
            "600003": self.VALUES,
        })
        self.seed_period("2026-03-31", {"600004": self.VALUES},
                         md5="m2", first_seen="2026-06-20T10:00:00")
        source = fin.load_fin_source("2026-09-01", ["600001", "600002",
                                                   "600003", "600004"])
        self.assertEqual(source["cohort"]["modal_period"], "2026-06-30")
        self.assertEqual(source["cohort"]["n_mismatch"], 1)
        self.assertEqual(set(source["current"]), {"600001", "600002", "600003"})
        self.assertIn("cohort_selection_bias", source["warnings"])
        self.assertEqual(source["cohort"]["excluded_profile"]["count"], 1)

    def test_missing_calibration_fails_closed(self):
        fin_store.upsert_report_file(
            "2026-06-30", filename="x", md5="m9", filesize=1,
            first_seen_at="2026-08-20T10:00:00", status="ok")
        fin_store.insert_report_vintage(
            "2026-06-30", [{"code": self.CODE, "values": self.VALUES,
                            "available_at": "2026-08-31T23:59:59",
                            "pit_basis": "statutory_backfilled"}],
            file_md5="m9", layout_key="gpcw_99")
        source = fin.load_fin_source("2026-09-01", [self.CODE])
        self.assertEqual(source["status"], "field_map_unverified")
        self.assertEqual(source["current"], {})

    def test_stale_period_skipped(self):
        self.seed_period("2026-03-31", {self.CODE: self.VALUES},
                         first_seen="2026-05-01T10:00:00")
        # 2026-03 期的下一期截止日 2026-08-31 + 5 交易日 → 9/8 之后算逾期
        calendar = [f"2026-09-{day:02d}" for day in range(1, 16)]
        source = fin.load_fin_source("2026-09-15", [self.CODE], calendar=calendar)
        self.assertEqual(source["status"], "stale_period_skipped")


class TestCalibrationSolver(unittest.TestCase):
    def _samples(self, n=4):
        samples = []
        for i in range(n):
            factor = 1.0 + i * 0.1
            fields = [0.0] * 8
            fields[0] = 5.5 * factor              # bps
            fields[1] = 5.5e8 * factor            # net assets
            fields[2] = 3e8 * factor              # revenue
            fields[3] = 3e7 * factor              # net profit
            fields[4] = 4e7 * factor              # operating profit
            fields[5] = 4.5e7 * factor            # main profit
            fields[6] = 10000.0                   # total shares (万股)
            named = {
                "meigujing_zichan": fields[0], "jing_zichan": fields[1],
                "zhuying_shouru": fields[2], "jing_lirun": fields[3],
                "yingye_lirun": fields[4], "zhuying_lirun": fields[5],
                "zong_guben": fields[6],
            }
            samples.append((fields, named))
        return samples

    def test_identity_and_hierarchy_verified(self):
        payload = fin._solve_calibration(self._samples())
        self.assertEqual(payload["status"], "verified")
        self.assertTrue(payload["anchors"]["identity"])
        self.assertTrue(payload["anchors"]["hierarchy"])
        for field in fin.REQUIRED_CALIBRATION_FIELDS:
            self.assertIn(field, payload["mapping"])
        self.assertEqual(payload["mapping"]["meigujing_zichan"]["index"], 0)

    def test_missing_evidence_is_unverified(self):
        samples = []
        for i in range(4):
            fields = [5.5 + i, 5.5e8 + i]
            samples.append((fields, {"meigujing_zichan": fields[0],
                                     "jing_zichan": fields[1]}))
        payload = fin._solve_calibration(samples)
        self.assertEqual(payload["status"], "unverified")


class TestBuiltinFinancial(unittest.TestCase):
    def _ctx(self, current, prior=None, cohort=None, codes=None):
        return FactorContext(
            trade_date="2026-09-01", codes=tuple(codes or current),
            source_rows={"current": current, "prior": prior or {},
                         "observed": {}, "cohort": cohort or {},
                         "available_at": "2026-09-01T16:40:00"},
            as_of="2026-09-01T16:40:00")

    @staticmethod
    def _record(values, period="2026-06-30"):
        return {"period": period, "values": values,
                "available_at": "2026-08-31T23:59:59",
                "pit_basis": "statutory_backfilled",
                "layout_key": "gpcw_20", "file_md5": "m1"}

    def test_six_factors_values(self):
        ctx = self._ctx(
            {"600001": self._record({
                "meigujing_zichan": 5.0, "jing_zichan": 100.0,
                "jing_lirun": 10.0, "zhuying_shouru": 200.0})},
            prior={"600001": self._record(
                {"jing_zichan": 90.0, "jing_lirun": 8.0,
                 "zhuying_shouru": 160.0}, period="2025-06-30")})
        self.assertEqual(financial.compute_bps_factor(ctx).values["600001"], 5.0)
        self.assertAlmostEqual(financial.compute_roe_factor(ctx).values["600001"],
                               10.0 * 4 / 2 / 100.0 * 100.0)
        self.assertAlmostEqual(
            financial.compute_net_margin_factor(ctx).values["600001"], 5.0)
        self.assertAlmostEqual(
            financial.compute_net_profit_yoy(ctx).values["600001"], 25.0)
        self.assertAlmostEqual(
            financial.compute_revenue_yoy(ctx).values["600001"], 25.0)
        self.assertEqual(
            financial.compute_net_assets_factor(ctx).values["600001"], 100.0)

    def test_denominator_and_negative_base_protection(self):
        ctx = self._ctx({"600001": self._record({
            "meigujing_zichan": 1.0, "jing_zichan": -5.0,
            "jing_lirun": 10.0, "zhuying_shouru": 0.0})},
            prior={"600001": self._record(
                {"jing_zichan": 90.0, "jing_lirun": -8.0,
                 "zhuying_shouru": 160.0}, period="2025-06-30")})
        self.assertEqual(financial.compute_roe_factor(ctx).values, {})
        self.assertEqual(financial.compute_net_margin_factor(ctx).values, {})
        self.assertEqual(financial.compute_net_profit_yoy(ctx).values, {})

    def test_all_six_specs_are_asof_and_default_off(self):
        from dragon_quant.factors.registry import get_spec
        for spec in financial.SPECS:
            self.assertEqual(spec.source, "fin")
            self.assertEqual(spec.frequency, "1q")
            self.assertEqual(spec.pit_rule, "asof")
            self.assertEqual(spec.available_phase, "prep")
            self.assertFalse(spec.default_enabled)
            self.assertIsNone(spec.valid_from)
            self.assertIs(get_spec(spec.name), spec)


class TestSyncBackfill(TempDbBase):
    VALUES = {"meigujing_zichan": 5.5, "jing_zichan": 5.5e8,
              "zhuying_shouru": 3e8, "jing_lirun": 3e7}

    class FakeTdx:
        def __init__(self, fields):
            self.fields = fields
            self.calls = 0

        def get_financial_files(self, host=None):
            return [{"filename": "tdxfin/gpcw20260630.zip", "md5": "m1",
                     "size": 10}]

        def get_financial_records(self, filename, host=None):
            self.calls += 1
            return [{"code": "600001", "market": 1, "report_date": 20260630,
                     "fields": self.fields}]

    def test_backfill_idempotent_and_layout_unverified_blob(self):
        fake = self.FakeTdx([1.0] * 20)
        first = fin.sync_backfill(tdx=fake, db_path=self._db_path,
                                  now="2026-11-01T10:00:00")
        self.assertEqual(first["downloaded"], 1)
        self.assertEqual(first["layouts"], {"gpcw_20": "unverified"})
        blob = fin_store.get_raw_blob("2026-06-30", "m1", db_path=self._db_path)
        self.assertIsNotNone(blob)
        second = fin.sync_backfill(tdx=fake, db_path=self._db_path,
                                   now="2026-11-01T10:00:00")
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(fake.calls, 1)

    def test_revision_with_partial_change_does_not_retract_unchanged_code(self):
        """BLOCKER 回归：md5 变化但只有部分 code 被重述 → 未变 code 不得被撤销。"""
        mapping = {field: {"index": i, "scale": 1.0}
                   for i, field in enumerate(fin.MAPPED_FIELDS)}
        fin_store.upsert_field_calibration("gpcw_20", {
            "num_fields": 20, "mapping": mapping, "confidence": {},
            "status": "verified", "sample_n": 4,
        })

        def fields(net_assets):
            row = [0.0] * 20
            for field, spec in mapping.items():
                row[spec["index"]] = 0.0
            row[mapping["jing_zichan"]["index"]] = net_assets
            row[mapping["meigujing_zichan"]["index"]] = 5.5
            row[mapping["zhuying_shouru"]["index"]] = 3e8
            row[mapping["jing_lirun"]["index"]] = 3e7
            return row

        class TwoCodeFake:
            def __init__(self):
                self.md5 = "m1"
                self.records = [
                    {"code": "600001", "market": 1, "report_date": 20260630,
                     "fields": fields(5.5e8)},
                    {"code": "600002", "market": 1, "report_date": 20260630,
                     "fields": fields(2.0e8)},
                ]

            def get_financial_files(self, host=None):
                return [{"filename": "tdxfin/gpcw20260630.zip",
                         "md5": self.md5, "size": 10}]

            def get_financial_records(self, filename, host=None):
                return list(self.records)

        fake = TwoCodeFake()
        fin.sync_backfill(tdx=fake, db_path=self._db_path,
                          now="2026-11-01T10:00:00")
        # 同报告期新 md5：仅 600001 被重述，600002 值未变
        fake.md5 = "m2"
        fake.records = [
            {"code": "600001", "market": 1, "report_date": 20260630,
             "fields": fields(6.0e8)},
            {"code": "600002", "market": 1, "report_date": 20260630,
             "fields": fields(2.0e8)},
        ]
        payload = fin.sync_backfill(tdx=fake, db_path=self._db_path,
                                    now="2026-12-01T10:00:00")
        self.assertEqual(payload["inserted"], 1)
        self.assertEqual(payload["unchanged"], 1)
        self.assertEqual(payload["retracted"], 0, "值未变 code 不得被撤销")
        selected = fin_store.get_report_asof(
            ["600001", "600002"], "2026-12-02T16:40:00", db_path=self._db_path)
        self.assertEqual(selected["600001"]["file_md5"], "m2")
        self.assertEqual(selected["600001"]["pit_basis"], "revised")
        self.assertEqual(selected["600002"]["file_md5"], "m1")
        self.assertEqual(selected["600002"]["values"]["jing_zichan"], 2.0e8)

    def test_default_window_limits_to_twelve_periods(self):
        class ManyPeriodsFake:
            def get_financial_files(self, host=None):
                periods = ["20240331", "20240630", "20240930", "20241231",
                           "20250331", "20250630", "20250930", "20251231",
                           "20260331", "20260630", "20260930", "20261231",
                           "20270331", "20270630"]
                return [{"filename": f"tdxfin/gpcw{p}.zip", "md5": f"m-{p}",
                         "size": 1} for p in periods]

            def get_financial_records(self, filename, host=None):
                return []

        payload = fin.sync_backfill(tdx=ManyPeriodsFake(), db_path=self._db_path,
                                    now="2026-11-01T10:00:00")
        self.assertEqual(payload["window"]["candidates"], 12)
        self.assertEqual(payload["window"]["from_period"], "2024-09-30")
        self.assertEqual(payload["window"]["to_period"], "2027-06-30")

    def test_hash_mismatch_counts_observed_unmatched_and_stays_backfilled(self):
        # 已校准布局：index → 语义字段
        mapping = {field: {"index": i, "scale": 1.0}
                   for i, field in enumerate(fin.MAPPED_FIELDS)}
        fin_store.upsert_field_calibration("gpcw_20", {
            "num_fields": 20, "mapping": mapping, "confidence": {},
            "status": "verified", "sample_n": 4,
        })
        fields = [0.0] * 20
        for field, spec in mapping.items():
            fields[spec["index"]] = self.VALUES.get(field, 0.0)
        # 观测期次相同但数值哈希不等 → 不得升级为 observed
        fin_store.upsert_observed_snapshots("2026-08-15", [{
            "code": "600001", "updated_date": 20260630, "period": "2026-06-30",
            "values": {**self.VALUES, "jing_zichan": 1.0},
            "values_hash": fin.values_hash({**self.VALUES, "jing_zichan": 1.0}),
            "captured_at": "2026-08-15T17:02:00",
        }])
        payload = fin.sync_backfill(tdx=self.FakeTdx(fields), db_path=self._db_path,
                                    now="2026-11-01T10:00:00")
        self.assertEqual(payload["observed_unmatched"], 1)
        row = fin_store.get_report_asof(["600001"], "2026-11-02T16:40:00")
        self.assertEqual(row["600001"]["pit_basis"], "statutory_backfilled")


class TestEngineFinWiring(TempDbBase):
    def test_fin_factor_written_with_phase_available_at_and_meta(self):
        source = {
            "status": "ok",
            "current": {"600001": {
                "period": "2026-06-30",
                "values": {"meigujing_zichan": 5.0},
                "available_at": "2026-08-31T23:59:59",
                "pit_basis": "statutory_backfilled",
                "layout_key": "gpcw_20", "file_md5": "m1",
            }},
            "prior": {}, "observed": {},
            "cohort": {"modal_period": "2026-06-30", "n_total": 1,
                       "n_cohort": 1, "n_mismatch": 0,
                       "by_period": {"2026-06-30": 1}, "stale_days": {}},
            "codes": ("600001",), "as_of": "2026-09-01T16:40:00",
            "available_at": "2026-08-31T23:59:59",
            "earliest_available": None, "warnings": [], "degraded": [],
        }
        with patch("dragon_quant.factors.engine._load_fin_source",
                   return_value=source):
            report = engine.build_date(
                "2026-09-01", names=["fin_bps"], db_path=self._db_path)
        entry = report["factors"]["fin_bps"]
        self.assertEqual(entry["status"], "ok", entry.get("reason"))
        payload = fstore.get_day_values("2026-09-01", names=["fin_bps"],
                                        db_path=self._db_path)
        self.assertEqual(payload["values"]["600001"]["fin_bps"], 5.0)
        self.assertEqual(payload["available_at"]["600001"]["fin_bps"],
                         "2026-09-01T16:40:00")
        row = sqlite3.connect(self._db_path).execute(
            "SELECT source_meta_json FROM factor_values "
            "WHERE factor_name='fin_bps'").fetchone()
        meta = json.loads(row[0])
        self.assertEqual(meta["period"], "2026-06-30")
        self.assertEqual(meta["pit_basis"], "statutory_backfilled")


class TestFinanceServicesInjection(TempDbBase):
    """M2C：财务链路可选 Services 注入；显式 ``tdx=`` 优先于 bundle。"""

    class FakeBackfillTdx:
        def __init__(self):
            self.calls = 0

        def get_financial_files(self, host=None):
            return [{"filename": "tdxfin/gpcw20260630.zip", "md5": "m1",
                     "size": 10}]

        def get_financial_records(self, filename, host=None):
            self.calls += 1
            return [{"code": "600001", "market": 1, "report_date": 20260630,
                     "fields": [1.0] * 20}]

    class FakeSnapshotTdx:
        def __init__(self):
            self.codes = []

        def get_finance_info(self, code):
            self.codes.append(code)
            return {"meigujing_zichan": 5.5, "jing_zichan": 5.5e8,
                    "zhuying_shouru": 3e8, "jing_lirun": 3e7}

    @staticmethod
    def _services(tdx):
        from types import SimpleNamespace
        return SimpleNamespace(providers=SimpleNamespace(tdx=tdx))

    def test_sync_backfill_uses_injected_bundle_tdx(self):
        fake = self.FakeBackfillTdx()
        with patch("dragon_quant.providers.tdx.TdxProvider",
                   side_effect=AssertionError("default provider constructed")):
            payload = fin.sync_backfill(services=self._services(fake),
                                        db_path=self._db_path,
                                        now="2026-11-01T10:00:00")

        self.assertEqual(payload["downloaded"], 1)
        self.assertEqual(fake.calls, 1)

    def test_explicit_tdx_argument_wins_over_injected_bundle(self):
        explicit = self.FakeBackfillTdx()
        bundled = self.FakeBackfillTdx()

        payload = fin.sync_backfill(tdx=explicit,
                                    services=self._services(bundled),
                                    db_path=self._db_path,
                                    now="2026-11-01T10:00:00")

        self.assertEqual(payload["downloaded"], 1)
        self.assertEqual(explicit.calls, 1)
        self.assertEqual(bundled.calls, 0)

    def test_sync_snapshots_uses_injected_bundle_tdx(self):
        fake = self.FakeSnapshotTdx()
        with patch("dragon_quant.providers.tdx.TdxProvider",
                   side_effect=AssertionError("default provider constructed")):
            payload = fin.sync_snapshots(fin._today(), ["600001"],
                                         services=self._services(fake),
                                         db_path=self._db_path)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["ok"], 1)
        self.assertEqual(fake.codes, ["600001"])

    def test_calibrate_field_map_uses_injected_bundle_tdx(self):
        class FakeCalibrateTdx:
            def __init__(self):
                self.calls = 0

            def get_financial_files(self, host=None):
                self.calls += 1
                return []

        fake = FakeCalibrateTdx()
        with patch("dragon_quant.providers.tdx.TdxProvider",
                   side_effect=AssertionError("default provider constructed")):
            with self.assertRaises(ValueError):
                fin.calibrate_field_map(services=self._services(fake),
                                        db_path=self._db_path)

        self.assertEqual(fake.calls, 1)

    def test_load_trade_calendar_uses_injected_bundle_tdx(self):
        fake = MagicMock(name="bundle.tdx")
        with patch("dragon_quant.utils.trading.tdx_trade_dates",
                   return_value=["2026-10-19"]) as dates, \
             patch("dragon_quant.providers.tdx.TdxProvider",
                   side_effect=AssertionError("default provider constructed")):
            loaded, error = fin.load_trade_calendar(
                days=5, services=self._services(fake))

        self.assertEqual(loaded, ["2026-10-19"])
        self.assertIsNone(error)
        self.assertIs(dates.call_args.args[0], fake)

    def test_load_fin_source_forwards_services_to_snapshot_sync(self):
        fake = self.FakeSnapshotTdx()
        services = self._services(fake)
        with patch.object(fin.fin_store, "has_observed_snapshots",
                          return_value=False), \
             patch.object(fin, "_today", return_value="2026-10-19"), \
             patch.object(fin, "sync_snapshots") as sync:
            payload = fin.load_fin_source("2026-10-19", ["600001"],
                                          services=services,
                                          db_path=self._db_path)

        sync.assert_called_once_with("2026-10-19", ("600001",),
                                     db_path=self._db_path, services=services)
        self.assertIn(payload["status"], ("no_report_yet", "field_map_unverified"))

    def test_load_fin_source_same_day_auto_sync_never_builds_a_default_tdx(self):
        fake = self.FakeSnapshotTdx()
        with patch.object(fin, "_today", return_value="2026-10-19"), \
             patch("dragon_quant.providers.tdx.TdxProvider",
                   side_effect=AssertionError("default provider constructed")):
            payload = fin.load_fin_source("2026-10-19", ["600001"],
                                          services=self._services(fake),
                                          db_path=self._db_path)

        self.assertEqual(fake.codes, ["600001"])
        self.assertIn(payload["status"], ("no_report_yet", "field_map_unverified"))


class TestEngineFinServicesForwarding(TempDbBase):
    """M3B: services reach the finance source through engine build paths."""

    SCAN = {
        "status": "ok",
        "rows_by_code": {"600001": {"composite_score": 50.0}},
        "codes": ("600001",),
    }

    def test_load_fin_source_forwards_services_to_finance(self):
        services = object()
        with patch.object(engine, "_load_scan_source", return_value=self.SCAN), \
             patch("dragon_quant.factors.finance.load_fin_source",
                   return_value={"status": "no_report_yet"}) as loader:
            engine._load_fin_source("2026-09-01", {}, services=services,
                                    db_path=self._db_path)

        self.assertIs(loader.call_args.kwargs["services"], services)

    def test_build_date_forwards_services_to_the_fin_source(self):
        services = object()
        with patch.object(engine, "_load_scan_source", return_value=self.SCAN), \
             patch("dragon_quant.factors.finance.load_fin_source",
                   return_value={"status": "no_report_yet"}) as loader:
            engine.build_date("2026-09-01", names=["fin_bps"],
                              db_path=self._db_path, services=services)

        self.assertIs(loader.call_args.kwargs["services"], services)


if __name__ == "__main__":
    unittest.main()
