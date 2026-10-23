"""M6 全表同步集成测试（临时 SQLite + LocalDirBackend，零网络）。

覆盖方案 §15.2 的 16 项场景。所有断言只看本地库与本地对象存储，不触网。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dragon_quant.assets_sync import codec, manifest as manifest_mod, service, tables
from dragon_quant.assets_sync.backends.local import LocalDirBackend
from dragon_quant.assets_sync.errors import IntegrityError
from dragon_quant.assets_sync.sources import build_sources
from dragon_quant.assets_sync.state import default_paths
from dragon_quant.storage import assets_db


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class CountingBackend(LocalDirBackend):
    def __init__(self, root):
        super().__init__(root)
        self.puts: list[str] = []

    def put(self, key, data, metadata=None, content_md5=None):
        self.puts.append(key)
        return super().put(key, data, metadata, content_md5)

    def reset(self):
        self.puts = []


class FullSyncBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "main" / "assets.db"
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.backend = CountingBackend(self.root / "cloud")
        self.paths = default_paths(self.root / "main")
        self.sources = build_sources(db_path=self.db)

    def tearDown(self):
        self._tmp.cleanup()

    # ── 基础工具 ───────────────────────────────────────────────────────────
    def _conn(self, db=None):
        conn = assets_db._connect(path=db or self.db)
        assets_db._ensure_schema(conn)
        return conn

    def _exec(self, sql, params=(), db=None):
        conn = self._conn(db)
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def _rows(self, sql, params=(), db=None):
        conn = self._conn(db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def _push(self, **kwargs):
        return service.push(self.backend, self.sources, paths=self.paths, **kwargs)

    def _pull(self, scratch, sources=None, **kwargs):
        target = sources or build_sources(db_path=scratch / "assets.db")
        kwargs.setdefault("date_from", "2026-01-01")
        kwargs.setdefault("date_to", "2026-12-31")
        return service.pull(self.backend, target,
                            paths=default_paths(scratch), **kwargs)

    # ── 14 张表代表性数据 ──────────────────────────────────────────────────
    def _seed_all(self, db=None):
        day = "2026-10-21"
        period = "2026-06-30"
        conn = self._conn(db)
        try:
            conn.execute(
                "INSERT INTO prep_assets(trade_date, asset_type, payload_json, created_at) "
                "VALUES (?,?,?,?)",
                (day, "dragons", _dumps({"count": 3}), "2026-10-21 17:00:00"))
            conn.execute(
                "INSERT INTO prep_assets(trade_date, asset_type, payload_json, created_at) "
                "VALUES (?,?,?,?)",
                (day, "collection_report", _dumps({"ok": 1}),
                 "2026-10-21 17:00:01"))
            conn.execute(
                "INSERT INTO auction_assets(trade_date, code, name, items_json, "
                "quote_json, created_at) VALUES (?,?,?,?,?,?)",
                (day, "002881", "美格智能", _dumps([{"time": "09:25:00"}]),
                 _dumps({"last": 42.3}), "2026-10-21 09:26:00"))
            conn.execute(
                "INSERT INTO prep_snapshots(trade_date, source, result_json, created_at) "
                "VALUES (?,?,?,?)",
                (day, "v2", _dumps({"sentiment": 0.8}), "2026-10-21 17:05:00"))
            conn.execute(
                "INSERT INTO auction_snapshots(trade_date, code, source, analysis_json, "
                "created_at) VALUES (?,?,?,?,?)",
                (day, "002881", "v2", _dumps({"strength": 72.5}),
                 "2026-10-21 09:26:01"))
            conn.execute(
                "INSERT INTO auction_snapshots(trade_date, code, source, analysis_json, "
                "created_at) VALUES (?,?,?,?,?)",
                (day, "600172", "v2", _dumps({"strength": 60.0}),
                 "2026-10-21 09:26:02"))
            conn.execute(
                "INSERT INTO factor_values(trade_date, code, factor_name, "
                "factor_version, value, available_at, source, source_scan_id, "
                "source_meta_json, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (day, "600172", "five_dim_drive", "v1", 78.5,
                 "2026-10-21T15:05:00", "scan", "v2_20261021_25",
                 _dumps({"coverage": 0.82}), "2026-10-21 15:06:00"))
            conn.execute(
                "INSERT INTO factor_values(trade_date, code, factor_name, "
                "factor_version, value, available_at, source, source_scan_id, "
                "source_meta_json, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (day, "002881", "five_dim_drive", "v1", 65.0,
                 "2026-10-21T15:05:00", "scan", None, None,
                 "2026-10-21 15:06:00"))
            conn.execute(
                "INSERT INTO factor_build_runs(trade_date, started_at, finished_at, "
                "report_json, created_at) VALUES (?,?,?,?,?)",
                (day, "2026-10-21 15:00:00", "2026-10-21 15:05:00",
                 _dumps({"ok": True, "build_ids": [3]}), "2026-10-21 15:05:01"))
            conn.execute(
                "INSERT INTO factor_sector_snapshots(trade_date, code, industry_code, "
                "industry_name, category, source, scan_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (day, "600172", "881101", "半导体", "hy", "scan",
                 "v2_20261021_25", "2026-10-21 15:05:00"))
            conn.execute(
                "INSERT INTO factor_registry(factor_name, factor_version, label, "
                "frequency, source, depends_on_json, lookback, direction, unit, "
                "available_phase, pit_rule, scope_json, params_json, default_enabled, "
                "spec_hash, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("five_dim_drive", "v1", "带动性", "daily", "scan",
                 _dumps(["kline"]), 10, "higher", "score", "scan", "derived",
                 _dumps(["hs300"]), _dumps({"w": 0.3}), 1, "hash1",
                 "2026-10-01 10:00:00"))
            conn.execute(
                "INSERT INTO factor_model_snapshots(model_name, model_version, "
                "params_json, factor_versions_json, created_at) VALUES (?,?,?,?,?)",
                ("equal_rank_v1", "1", _dumps({"k": 1}),
                 _dumps({"five_dim_drive": "v1"}), "2026-10-02 10:00:00"))
            conn.execute(
                "INSERT INTO fin_observed_snapshots(trade_date, code, updated_date, "
                "period, values_json, values_hash, captured_at) VALUES (?,?,?,?,?,?,?)",
                (day, "600172", 20260630, period, _dumps({"bps": 5.1}),
                 "hash-observed", "2026-10-21 18:00:00"))
            conn.execute(
                "INSERT INTO fin_report_files(report_period, filename, md5, filesize, "
                "first_seen_at, status, records_count, raw_blob, error) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (period, "gpcw.zip", "md5-a", 1024, "2026-07-01 10:00:00",
                 "layout_unverified", 2, b"\x1f\x8bRAWBYTES", None))
            conn.execute(
                "INSERT INTO fin_report_records(report_period, code, file_md5, "
                "layout_key, values_json, available_at, pit_basis, first_seen_at, "
                "retracted_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (period, "600172", "md5-a", "gpcw_10", _dumps({"roe": 12.3}),
                 "2026-07-01T10:00:00", "observed", "2026-07-01 10:00:00", None))
            conn.execute(
                "INSERT INTO fin_report_records(report_period, code, file_md5, "
                "layout_key, values_json, available_at, pit_basis, first_seen_at, "
                "retracted_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (period, "002881", "md5-a", "gpcw_10", _dumps({"roe": 9.9}),
                 "2026-07-01T10:00:00", "observed", "2026-07-01 10:00:00",
                 "2026-09-01T10:00:00"))
            conn.execute(
                "INSERT INTO fin_field_calibrations(layout_key, num_fields, "
                "mapping_json, confidence_json, status, sample_n, calibrated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                ("gpcw_10", 10, _dumps({"roe": 1}), _dumps({"roe": 0.9}),
                 "verified", 5, "2026-07-02 10:00:00"))
            conn.execute(
                "INSERT INTO fin_xdxr_records(code, ex_date, category, payload_json, "
                "created_at) VALUES (?,?,?,?,?)",
                ("600172", day, 1, _dumps({"bonus": 0.5}), "2026-10-20 20:00:00"))
            conn.commit()
        finally:
            conn.close()


class TestRoundTrip(FullSyncBase):
    def test_full_roundtrip_all_tables(self):
        self._seed_all()
        pushed = self._push()
        self.assertTrue(pushed["ok"], pushed)
        scratch = self.root / "scratch"
        pulled = self._pull(scratch, apply=True)
        self.assertTrue(pulled["ok"], pulled)

        counts = {
            "prep_assets": 2, "auction_assets": 1, "prep_snapshots": 1,
            "auction_snapshots": 2, "factor_values": 2,
            "factor_build_runs": 1,
            "factor_sector_snapshots": 1, "factor_registry": 1,
            "factor_model_snapshots": 1, "fin_observed_snapshots": 1,
            "fin_report_files": 1, "fin_report_records": 2,
            "fin_field_calibrations": 1, "fin_xdxr_records": 1,
        }
        for table, expected in counts.items():
            self.assertEqual(
                len(self._rows(f"SELECT * FROM {table}", db=scratch / "assets.db")),
                expected, table)

        row = self._rows(
            "SELECT value, source_meta_json, computed_at FROM factor_values "
            "WHERE code='600172'", db=scratch / "assets.db")[0]
        self.assertEqual(row[0], 78.5)
        self.assertEqual(json.loads(row[1]), {"coverage": 0.82})
        row = self._rows(
            "SELECT raw_blob FROM fin_report_files", db=scratch / "assets.db")[0]
        self.assertEqual(row[0], b"\x1f\x8bRAWBYTES")
        retracted = self._rows(
            "SELECT retracted_at FROM fin_report_records WHERE code='002881'",
            db=scratch / "assets.db")[0][0]
        self.assertEqual(retracted, "2026-09-01T10:00:00")
        # 静态分区与报告期分区也恢复
        self.assertEqual(
            self._rows("SELECT factor_name FROM factor_registry",
                       db=scratch / "assets.db")[0][0], "five_dim_drive")
        self.assertFalse((scratch / "assets_sync" / "state.json").exists())

        # 分片类型：period/period_raw/static 各自 manifest
        keys = {item.key for item in self.backend.list("manifests/")}
        for partition in ("2026-10-21", "fin-2026-06-30", "finraw-2026-06-30",
                          "static-factor", "static-fin"):
            self.assertIn(f"manifests/{partition}.json", keys)
        body = json.loads(self.backend.get("manifests/fin-2026-06-30.json"))
        self.assertEqual(body["schema"], 2)
        self.assertEqual(body["sort_date"], "2026-06-30")
        legacy = json.loads(self.backend.get("manifests/2026-10-21.json"))
        self.assertEqual(legacy["schema"], 1)

    def test_row_level_fill_only_missing(self):
        self._seed_all()
        self._push()
        scratch = self.root / "scratch"
        self._pull(scratch, apply=True)
        self._exec("DELETE FROM factor_values WHERE code='002881'",
                   db=scratch / "assets.db")
        self._exec("UPDATE factor_values SET value=1.0 WHERE code='600172'",
                   db=scratch / "assets.db")
        result = self._pull(scratch, apply=True)
        # 已存在且不同的行按本地权威记 conflict（不覆盖），缺失行照常补齐
        self.assertFalse(result["ok"])
        self.assertEqual(result["summary"]["rows_inserted"], 1)
        self.assertTrue(any(item["key"] == "factor:values"
                            for item in result["summary"]["conflicts"]))
        rows = dict(self._rows(
            "SELECT code, value FROM factor_values", db=scratch / "assets.db"))
        self.assertEqual(rows["002881"], 65.0)      # 缺失行被补齐
        self.assertEqual(rows["600172"], 1.0)       # 已存在行不被改动（本地权威）

    def test_row_level_conflict_and_force_preserves_side_columns(self):
        self._seed_all()
        self._push()
        scratch = self.root / "scratch"
        self._pull(scratch, apply=True)
        self._exec("UPDATE factor_values SET value=1.0, computed_at='LOCAL' "
                   "WHERE code='600172'", db=scratch / "assets.db")
        conflicted = self._pull(scratch, apply=True)
        self.assertFalse(conflicted["ok"])
        self.assertTrue(any(item["key"] == "factor:values"
                            for item in conflicted["summary"]["conflicts"]))
        self.assertEqual(self._rows(
            "SELECT value FROM factor_values WHERE code='600172'",
            db=scratch / "assets.db")[0][0], 1.0)

        forced = self._pull(scratch, apply=True, force=True)
        self.assertTrue(forced["ok"], forced)
        row = self._rows("SELECT value, computed_at FROM factor_values "
                         "WHERE code='600172'", db=scratch / "assets.db")[0]
        self.assertEqual(row[0], 78.5)     # 语义列被更新
        self.assertEqual(row[1], "LOCAL")  # 旁路列保留本地值

    def test_retraction_propagates_and_equal_length_change_detected(self):
        self._seed_all()
        self._push()
        scratch = self.root / "scratch"
        self._pull(scratch, apply=True)
        before = manifest_mod.read_remote_manifest(
            self.backend, "fin-2026-06-30").manifest_id
        # 等长、行数不变、available_at 不变，只有 retracted_at 变化
        self._exec(
            "UPDATE fin_report_records SET retracted_at='2026-09-02T10:00:00' "
            "WHERE code='002881'")
        self.backend.reset()
        pushed = self._push()
        self.assertTrue(pushed["ok"], pushed)
        self.assertGreaterEqual(pushed["summary"]["blobs_uploaded"], 1)
        after = manifest_mod.read_remote_manifest(
            self.backend, "fin-2026-06-30").manifest_id
        self.assertNotEqual(before, after)

        conflicted = self._pull(scratch, apply=True)
        self.assertFalse(conflicted["ok"])   # 本地权威：默认冲突
        forced = self._pull(scratch, apply=True, force=True)
        self.assertTrue(forced["ok"], forced)
        self.assertEqual(self._rows(
            "SELECT retracted_at FROM fin_report_records WHERE code='002881'",
            db=scratch / "assets.db")[0][0], "2026-09-02T10:00:00")

    def test_volatile_narrowed_by_source(self):
        self._seed_all()
        self._push()
        counts: dict[str, int] = {}

        def _wrap(name, original):
            def call(partition):
                counts[name] = counts.get(name, 0) + 1
                return original(partition)
            return call

        for source in self.sources:
            source.read_partition = _wrap(source.name, source.read_partition)
        self.backend.reset()
        second = self._push()
        self.assertTrue(second["ok"], second)
        self.assertEqual(counts.get("prep", 0), 0)
        self.assertEqual(counts.get("auction", 0), 0)
        self.assertEqual(counts.get("factor", 0), 0)
        self.assertGreaterEqual(counts.get("fin", 0), 1)   # volatile 强制重读
        self.assertEqual(self.backend.puts, [])            # 内容不变 → 零上传

        # 清空 state 后首次 push 全量读
        self.paths.state_path.unlink()
        counts.clear()
        third = self._push()
        self.assertTrue(third["ok"], third)
        for name in ("prep", "auction", "factor", "fin"):
            self.assertGreaterEqual(counts.get(name, 0), 1, name)

    def test_period_filter_and_raw_ordering(self):
        self._seed_all()
        self._push()
        # --from/--to 按报告期末过滤 period / period_raw，static 始终参与
        filtered = self._push(date_from="2026-10-21", date_to="2026-10-21")
        self.assertTrue(filtered["ok"], filtered)
        self.assertTrue(filtered["summary"]["period_excluded"])
        metadata = manifest_mod.read_remote_manifest(self.backend, "fin-2026-06-30")
        self.assertIsNotNone(metadata)   # 之前已推送的 manifest 不被删除

        scratch = self.root / "scratch"
        pulled = self._pull(scratch, apply=True)
        self.assertTrue(pulled["ok"], pulled)
        order = [item["partition"] for item in pulled["summary"]["dates"]
                 if item.get("partition", "").startswith(("fin-", "finraw-"))]
        self.assertLess(order.index("fin-2026-06-30"),
                        order.index("finraw-2026-06-30"))

        # 元数据行缺失时 raw 记 deferred（不失败）
        empty = self.root / "empty"
        empty.mkdir()
        raw_source = tables.FinAssetSource(db_path=scratch / "assets.db")
        shard = raw_source.read_partition("finraw-2026-06-30").shards[0]
        target = tables.FinAssetSource(db_path=empty / "assets.db")
        report = target.restore("finraw-2026-06-30", [shard])
        self.assertEqual(report.restored, 0)
        self.assertEqual(len(report.deferred), 1)

    def test_raw_null_fill_and_force(self):
        self._seed_all()
        self._push()
        scratch = self.root / "scratch"
        # 空库恢复：raw_blob 通过 NULL-fill 自动补齐，无需 --force
        pulled = self._pull(scratch, apply=True)
        self.assertTrue(pulled["ok"], pulled)
        self.assertEqual(self._rows(
            "SELECT raw_blob FROM fin_report_files",
            db=scratch / "assets.db")[0][0], b"\x1f\x8bRAWBYTES")

        self._exec("UPDATE fin_report_files SET raw_blob=?",
                   (b"LOCALDIFF",), db=scratch / "assets.db")
        conflicted = self._pull(scratch, apply=True)
        self.assertFalse(conflicted["ok"])
        self.assertTrue(any(item["key"] == "fin:report_raw"
                            for item in conflicted["summary"]["conflicts"]))
        forced = self._pull(scratch, apply=True, force=True)
        self.assertTrue(forced["ok"], forced)
        self.assertEqual(self._rows(
            "SELECT raw_blob FROM fin_report_files",
            db=scratch / "assets.db")[0][0], b"\x1f\x8bRAWBYTES")


class TestCompatAndGuards(FullSyncBase):
    def test_legacy_schema1_manifest_is_mismatched_not_broken(self):
        self._seed_all()
        day = "2026-10-21"
        base_sources = build_sources(db_path=self.db)
        prep_shard = base_sources[0].read_partition(day).shards[0]
        legacy = manifest_mod.build_manifest(day, [prep_shard])
        self.assertEqual(legacy.schema, 1)
        body = codec.manifest_to_bytes(legacy)
        self.backend.put(codec.manifest_key(day), body,
                         manifest_mod.manifest_metadata(legacy))

        result = service.status(self.backend, self.sources, paths=self.paths)
        self.assertFalse(result["ok"])
        self.assertFalse(any("unreadable" in str(item) for item in result["failed"]))
        mismatch = result["summary"]["dates_mismatched"][0]
        self.assertIn("factor:values", mismatch["keys"])
        self.assertNotIn("dates_missing", [k for k in result["summary"]
                                          if k == "unreadable"])

        pushed = self._push()
        self.assertTrue(pushed["ok"], pushed)
        loaded = manifest_mod.read_remote_manifest(self.backend, day)
        self.assertIn("factor:values", loaded.assets)

    def test_rollback_push_shrink_rejected(self):
        self._seed_all()
        self._push()
        legacy_sources = build_sources(db_path=self.db)[:2]
        result = service.push(self.backend, legacy_sources,
                              all_dates=True, paths=self.paths)
        self.assertFalse(result["ok"])
        self.assertIn("shrink", [item.get("kind") for item in result["failed"]])
        loaded = manifest_mod.read_remote_manifest(self.backend, "2026-10-21")
        self.assertIn("factor:values", loaded.assets)

    def test_reuse_guard_partial_entries(self):
        self._seed_all()
        self._push()
        day = "2026-10-21"
        remote = manifest_mod.read_remote_manifest(self.backend, day)
        trimmed = {key: entry for key, entry in remote.assets.items()
                   if key != "prep:collection_report"}
        rebuilt = codec.manifest_from_entries(day, trimmed)
        self.backend.put(codec.manifest_key(day), codec.manifest_to_bytes(rebuilt),
                         manifest_mod.manifest_metadata(rebuilt))
        self.backend.reset()
        result = self._push()
        self.assertTrue(result["ok"], result)
        loaded = manifest_mod.read_remote_manifest(self.backend, day)
        self.assertIn("prep:collection_report", loaded.assets)

    def test_codec_upgrade_forces_reread(self):
        self._seed_all()
        self._push()
        state = json.loads(self.paths.state_path.read_text(encoding="utf-8"))
        state["partitions"]["2026-10-21"]["codec"] = 1
        self.paths.state_path.write_text(json.dumps(state), encoding="utf-8")
        self.backend.reset()
        result = self._push()
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.backend.puts, [])   # 内容不变 → 零上传
        state = json.loads(self.paths.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["partitions"]["2026-10-21"]["codec"], 2)

    def test_shard_failure_keeps_remote_manifest(self):
        self._seed_all()
        self._push()
        before = self.backend.get(codec.manifest_key("2026-10-21"))
        self._exec("UPDATE prep_assets SET payload_json='{bad' WHERE asset_type='dragons'")
        result = self._push(all_dates=True)
        self.assertFalse(result["ok"])
        self.assertEqual(self.backend.get(codec.manifest_key("2026-10-21")), before)

    def test_source_rows_deleted_in_alive_partition_triggers_shrink(self):
        self._seed_all()
        self._push()
        # date 分区仍由 prep/fin 维持；factor 整个源在该日消失
        self._exec("DELETE FROM factor_values")
        self._exec("DELETE FROM factor_build_runs")
        self._exec("DELETE FROM factor_sector_snapshots")
        rejected = self._push(all_dates=True)
        self.assertFalse(rejected["ok"])
        self.assertIn("shrink", [item.get("kind") for item in rejected["failed"]])
        allowed = self._push(all_dates=True, allow_shrink=True)
        self.assertTrue(allowed["ok"], allowed)
        loaded = manifest_mod.read_remote_manifest(self.backend, "2026-10-21")
        self.assertFalse([key for key in loaded.assets if key.startswith("factor:")])

    def test_target_dir_isolation(self):
        self._seed_all()
        self._push()
        state_before = self.paths.state_path.read_text(encoding="utf-8")
        rows_before = len(self._rows("SELECT * FROM factor_values"))
        scratch = self.root / "scratch"
        result = self._pull(scratch, apply=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.paths.state_path.read_text(encoding="utf-8"),
                         state_before)
        self.assertEqual(len(self._rows("SELECT * FROM factor_values")), rows_before)
        self.assertEqual(len(self._rows(
            "SELECT * FROM factor_model_snapshots", db=scratch / "assets.db")), 1)
        self.assertEqual(len(self._rows(
            "SELECT * FROM fin_report_records", db=scratch / "assets.db")), 2)

    def test_build_runs_dedup(self):
        day = "2026-10-21"
        conn = self._conn()
        try:
            for build_ids in ([1], [99]):
                conn.execute(
                    "INSERT INTO factor_build_runs(trade_date, started_at, finished_at, "
                    "report_json, created_at) VALUES (?,?,?,?,?)",
                    (day, "2026-10-21 15:00:00", "2026-10-21 15:05:00",
                     _dumps({"ok": True, "build_ids": build_ids}),
                     "2026-10-21 15:05:01"))
            conn.execute(
                "INSERT INTO factor_build_runs(trade_date, started_at, finished_at, "
                "report_json, created_at) VALUES (?,?,?,?,?)",
                (day, "2026-10-21 16:00:00", "2026-10-21 16:01:00",
                 _dumps({"ok": True, "build_ids": [2]}), "2026-10-21 16:01:01"))
            conn.commit()
        finally:
            conn.close()
        self._push()
        scratch = self.root / "scratch"
        self._pull(scratch, apply=True)
        self.assertEqual(len(self._rows(
            "SELECT * FROM factor_build_runs", db=scratch / "assets.db")), 2)
        again = self._pull(scratch, apply=True)
        self.assertTrue(again["ok"], again)
        self.assertEqual(len(self._rows(
            "SELECT * FROM factor_build_runs", db=scratch / "assets.db")), 2)


class TestStatusDrift(FullSyncBase):
    def test_append_only_drift_is_weak_signal(self):
        self._seed_all()
        self._push()
        scratch = self.root / "scratch"
        self._pull(scratch, apply=True)
        self._exec(
            "INSERT INTO fin_report_records(report_period, code, file_md5, layout_key, "
            "values_json, available_at, pit_basis) VALUES (?,?,?,?,?,?,?)",
            ("2026-06-30", "000001", "md5-local", "gpcw_10", _dumps({"roe": 1}),
             "2026-08-01T10:00:00", "observed"),
            db=scratch / "assets.db")
        result = service.status(self.backend,
                                build_sources(db_path=scratch / "assets.db"),
                                paths=default_paths(scratch))
        self.assertTrue(result["ok"], result)
        drift = result["summary"]["dates_drift"]
        self.assertTrue(any(item["partition"] == "fin-2026-06-30"
                            and item["reason"] == "row_count_only"
                            for item in drift), drift)

    def test_equal_row_count_different_content_is_mismatched(self):
        self._seed_all()
        self._push()
        scratch = self.root / "scratch"
        self._pull(scratch, apply=True)
        self._exec("UPDATE fin_report_records SET values_json=? WHERE code='600172'",
                   (_dumps({"roe": 1.0}),), db=scratch / "assets.db")
        result = service.status(self.backend,
                                build_sources(db_path=scratch / "assets.db"),
                                paths=default_paths(scratch))
        self.assertFalse(result["ok"])
        self.assertTrue(any(item["partition"] == "fin-2026-06-30"
                            for item in result["summary"]["dates_mismatched"]))

    def test_non_drift_unit_with_more_local_rows_is_mismatched(self):
        self._seed_all()
        self._push()
        scratch = self.root / "scratch"
        self._pull(scratch, apply=True)
        self._exec(
            "INSERT INTO factor_values(trade_date, code, factor_name, factor_version, "
            "value, available_at, source) VALUES (?,?,?,?,?,?,?)",
            ("2026-10-21", "000001", "five_dim_drive", "v1", 1.0,
             "2026-10-21T15:00:00", "scan"),
            db=scratch / "assets.db")
        result = service.status(self.backend,
                                build_sources(db_path=scratch / "assets.db"),
                                paths=default_paths(scratch))
        self.assertFalse(result["ok"])
        self.assertFalse([item for item in result["summary"]["dates_drift"]
                          if item["partition"] == "2026-10-21"])

    def test_status_tables_coverage_and_not_synced_empty(self):
        self._seed_all()
        self._push()
        result = service.status(self.backend, self.sources, paths=self.paths)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["summary"]["not_synced"], {})
        coverage = result["summary"]["tables"]
        self.assertGreater(coverage["factor:values"]["remote_entries"], 0)
        self.assertGreater(coverage["fin:records"]["remote_entries"], 0)
        self.assertEqual(coverage["factor:values"]["merge"], "row")


class TestRestoreSchemaDrift(FullSyncBase):
    def _scratch_db(self):
        (self.root / "scratch").mkdir(exist_ok=True)
        return self.root / "scratch" / "assets.db"

    def _shard(self, partition, asset_type, source, table, columns, row):
        body = {"schema": 1, "table": table, "partition": partition,
                "columns": columns, "rows": [row]}
        return codec.build_shard(partition, asset_type, source, body)

    def test_nullable_missing_column_filled_with_null(self):
        columns = ["trade_date", "code", "factor_name", "factor_version", "value",
                   "available_at", "source"]
        shard = self._shard(
            "2026-10-21", "values", "factor", "factor_values", columns,
            ["2026-10-21", "600172", "f", "v1", 1.5, "2026-10-21T15:00:00", "scan"])
        source = tables.FactorAssetSource(db_path=self._scratch_db())
        report = source.restore("2026-10-21", [shard])
        self.assertEqual(report.restored, 1)
        row = self._rows("SELECT source_meta_json, source_scan_id FROM factor_values",
                         db=self.root / "scratch" / "assets.db")[0]
        self.assertEqual(row, (None, None))

    def test_extra_remote_columns_ignored(self):
        columns = ["trade_date", "code", "factor_name", "factor_version", "value",
                   "available_at", "source", "future_column"]
        shard = self._shard(
            "2026-10-21", "values", "factor", "factor_values", columns,
            ["2026-10-21", "600172", "f", "v1", 1.5, "2026-10-21T15:00:00",
             "scan", "ignored"])
        source = tables.FactorAssetSource(db_path=self._scratch_db())
        report = source.restore("2026-10-21", [shard])
        self.assertEqual(report.restored, 1)
        self.assertEqual(self._rows("SELECT code FROM factor_values",
                                    db=self.root / "scratch" / "assets.db"),
                         [("600172",)])

    def test_not_null_without_default_missing_column_is_integrity_failure(self):
        columns = ["layout_key", "num_fields", "mapping_json", "confidence_json",
                   "status"]
        shard = self._shard(
            "static-fin", "calibrations", "fin", "fin_field_calibrations", columns,
            ["gpcw_10", 10, {"roe": 1}, {"roe": 0.9}, "verified"])
        source = tables.FinAssetSource(db_path=self._scratch_db())
        with self.assertRaises(IntegrityError):
            source.restore("static-fin", [shard])
        self.assertEqual(len(self._rows(
            "SELECT * FROM fin_field_calibrations",
            db=self.root / "scratch" / "assets.db")), 0)


class TestLimits(FullSyncBase):
    def test_shard_large_warning_and_too_large_failure(self):
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO prep_assets(trade_date, asset_type, payload_json) "
                "VALUES (?,?,?)",
                ("2026-10-21", "dragons", _dumps({"blob": "x" * 200})))
            conn.commit()
        finally:
            conn.close()
        with patch.object(service, "WARN_SHARD_BYTES", 100), \
             patch.object(service, "MAX_SHARD_BYTES", 1000):
            result = self._push()
        self.assertTrue(result["ok"], result)
        self.assertTrue(any(item.get("warning") == "shard_large"
                            for item in result["summary"]["dates"]))

        self._exec("UPDATE prep_assets SET payload_json=?",
                   (_dumps({"blob": "x" * 2000}),))
        before = self.backend.get(codec.manifest_key("2026-10-21"))
        with patch.object(service, "WARN_SHARD_BYTES", 100), \
             patch.object(service, "MAX_SHARD_BYTES", 1000):
            failed = self._push(all_dates=True)
        self.assertFalse(failed["ok"])
        kinds = [item.get("kind") for item in failed["failed"]]
        self.assertIn("shard_too_large", kinds)
        # 分片失败不得覆盖既有 manifest（避免把有损数据当最佳状态）
        self.assertEqual(self.backend.get(codec.manifest_key("2026-10-21")), before)

    def test_oversized_raw_blob_only_breaks_finraw(self):
        self._seed_all()
        self._exec("UPDATE fin_report_files SET raw_blob=?", (b"R" * 800,))
        with patch.object(service, "MAX_SHARD_BYTES", 500):
            result = self._push()
        self.assertFalse(result["ok"])
        kinds = [item.get("kind") for item in result["failed"]]
        self.assertIn("shard_too_large", kinds)
        self.assertIsNotNone(manifest_mod.read_remote_manifest(
            self.backend, "fin-2026-06-30"))
        self.assertIsNone(manifest_mod.read_remote_manifest(
            self.backend, "finraw-2026-06-30"))


if __name__ == "__main__":
    unittest.main()
