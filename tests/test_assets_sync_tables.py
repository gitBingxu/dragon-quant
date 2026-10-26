"""M6 表规格 / 分区键 / 表分片编解码单元测试。"""

import base64
import json
import tempfile
import unittest
from pathlib import Path

from dragon_quant.infra.assets.sync import codec, tables
from dragon_quant.infra.assets.sync.models import AssetShard
from dragon_quant.infra.assets import db as assets_db


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class TestTableSpecRegistry(unittest.TestCase):
    def test_registry_invariants(self):
        keys = [spec.unit_key for spec in tables.TABLE_SPECS]
        self.assertEqual(len(keys), len(set(keys)))
        for spec in tables.TABLE_SPECS:
            self.assertNotIn("_", spec.source)
            self.assertIn(spec.partition_kind, codec.PARTITION_KINDS)
            self.assertTrue(spec.columns)
            self.assertEqual(len(spec.columns), len(set(spec.columns)))
            if spec.partition_kind == "static":
                self.assertTrue(spec.static_partition)
                self.assertIsNone(spec.partition_column)
            else:
                self.assertIn(spec.partition_column, spec.columns)
            if spec.identity:
                self.assertTrue(set(spec.identity) <= set(spec.columns))
            else:
                self.assertEqual(spec.identity_hook, "build_run_uid")
            self.assertFalse(set(spec.side_columns) & set(spec.columns))
            self.assertTrue(set(spec.json_columns) <= set(spec.columns))

    def test_covers_every_new_table(self):
        table_names = {spec.table for spec in tables.TABLE_SPECS}
        self.assertEqual(table_names, {
            "prep_snapshots", "auction_snapshots", "dragons",
            "factor_values",
            "factor_build_runs", "factor_sector_snapshots", "factor_registry",
            "factor_model_snapshots", "fin_observed_snapshots",
            "fin_report_files", "fin_report_records",
            "fin_field_calibrations", "fin_xdxr_records",
        })
        # fin_report_files 拆成元数据 + raw 两个单元
        units = [spec.unit_key for spec in tables.TABLE_SPECS]
        self.assertIn("fin:report_files", units)
        self.assertIn("fin:report_raw", units)
        self.assertIn("dragons:mirror", units)

    def test_volatile_set_is_explicit(self):
        volatile = {spec.unit_key for spec in tables.TABLE_SPECS if spec.volatile}
        self.assertEqual(volatile, {
            "prepsnap:snapshots", "auctionsnap:snapshots", "dragons:mirror",
            "fin:observed",
            "fin:report_files", "fin:report_raw", "fin:records",
            "fin:calibrations", "fin:xdxr",
        })

    def test_drift_ok_units(self):
        drift = {spec.unit_key for spec in tables.TABLE_SPECS if spec.drift_ok}
        self.assertEqual(drift, {"factor:build_runs", "fin:observed",
                                 "fin:records", "fin:xdxr"})


class TestPartitionKeys(unittest.TestCase):
    def test_parse_kinds(self):
        self.assertEqual(codec.parse_partition("2026-10-21"),
                         codec.PartitionInfo("2026-10-21", "date", "2026-10-21"))
        self.assertEqual(codec.parse_partition("fin-2026-06-30").kind, "period")
        self.assertEqual(codec.parse_partition("fin-2026-06-30").sort_date,
                         "2026-06-30")
        self.assertEqual(codec.parse_partition("finraw-2026-06-30").kind,
                         "period_raw")
        self.assertEqual(codec.parse_partition("static-factor").kind, "static")
        self.assertEqual(codec.parse_partition("static-factor").sort_date, "")
        for bad in ("", "2026-6-1", "fin2026-06-30", "static-Factor", "2026/10/21"):
            with self.assertRaises(ValueError):
                codec.parse_partition(bad)

    def test_sort_order_and_range(self):
        parts = ["static-factor", "fin-2026-06-30", "2026-10-21",
                 "finraw-2026-06-30", "2026-10-20"]
        ordered = sorted(parts, key=codec.partition_sort_key)
        self.assertEqual(ordered, [
            "2026-10-20", "2026-10-21", "fin-2026-06-30",
            "finraw-2026-06-30", "static-factor"])
        self.assertTrue(codec.partition_in_range("static-factor", "2026-10-21",
                                                 "2026-10-21"))
        self.assertFalse(codec.partition_in_range("fin-2026-06-30", "2026-10-21",
                                                  "2026-10-21"))
        self.assertTrue(codec.partition_in_range("fin-2026-10-21", "2026-10-21",
                                                 "2026-10-21"))

    def test_manifest_schema_by_kind(self):
        self.assertEqual(codec.manifest_schema_for("2026-10-21"), 1)
        self.assertEqual(codec.manifest_schema_for("fin-2026-06-30"), 2)
        self.assertEqual(codec.manifest_schema_for("finraw-2026-06-30"), 2)
        self.assertEqual(codec.manifest_schema_for("static-fin"), 2)


class TestShardEnvelope(unittest.TestCase):
    def test_asset_shard_partition_and_trade_date_alias(self):
        shard = codec.build_shard("fin-2026-06-30", "records", "fin", {"a": 1})
        self.assertEqual(shard.partition, "fin-2026-06-30")
        self.assertEqual(shard.trade_date, "")
        self.assertEqual(codec.blob_key(shard),
                         f"blobs/fin-2026-06-30/fin_records.{shard.sha256}.json.gz")
        legacy = AssetShard(trade_date="2026-10-21", asset_type="dragons",
                            source="prep", content=b"{}", sha256="", md5="")
        self.assertEqual(legacy.partition, "2026-10-21")
        self.assertEqual(legacy.trade_date, "2026-10-21")

    def test_blob_metadata_dual_key(self):
        shard = codec.build_shard("2026-10-21", "dragons", "prep", {"a": 1})
        meta = codec.blob_metadata(shard, codec.compress(shard.content))
        self.assertEqual(meta["partition"], "2026-10-21")
        self.assertEqual(meta["trade-date"], "2026-10-21")
        part = codec.build_shard("fin-2026-06-30", "records", "fin", {"a": 1})
        meta2 = codec.blob_metadata(part, codec.compress(part.content))
        self.assertEqual(meta2["partition"], "fin-2026-06-30")
        self.assertNotIn("trade-date", meta2)


class TestManifestSchemas(unittest.TestCase):
    def _entry(self, partition, payload):
        shard = codec.build_shard(partition, "records", "fin", payload)
        return codec.make_entry(shard, codec.compress(shard.content), rows=2)

    def test_schema2_roundtrip_and_id_stable_across_schema(self):
        entry = self._entry("fin-2026-06-30", {"a": 1})
        manifest = codec.manifest_from_entries("fin-2026-06-30",
                                               {"fin:records": entry})
        self.assertEqual(manifest.schema, 2)
        body = json.loads(codec.manifest_to_bytes(manifest))
        self.assertEqual(body["partition"], "fin-2026-06-30")
        self.assertEqual(body["partition_kind"], "period")
        self.assertEqual(body["sort_date"], "2026-06-30")
        self.assertNotIn("trade_date", body)
        self.assertEqual(body["assets"]["fin:records"]["rows"], 2)
        loaded = codec.manifest_from_bytes(codec.manifest_to_bytes(manifest))
        self.assertEqual(loaded.partition, "fin-2026-06-30")
        self.assertEqual(loaded.assets["fin:records"].rows, 2)

        # rows 不参与 manifest_id
        same = codec.manifest_from_entries(
            "fin-2026-06-30",
            {"fin:records": codec.make_entry(
                codec.build_shard("fin-2026-06-30", "records", "fin", {"a": 1}),
                codec.compress(codec.build_shard(
                    "fin-2026-06-30", "records", "fin", {"a": 1}).content),
                rows=99)})
        self.assertEqual(same.manifest_id, manifest.manifest_id)

    def test_schema2_self_consistency(self):
        entry = self._entry("fin-2026-06-30", {"a": 1})
        manifest = codec.manifest_from_entries("fin-2026-06-30",
                                               {"fin:records": entry})
        raw = json.loads(codec.manifest_to_bytes(manifest))
        raw["sort_date"] = "2026-07-01"
        with self.assertRaises(codec.IntegrityError):
            codec.manifest_from_bytes(json.dumps(raw).encode("utf-8"))
        raw = json.loads(codec.manifest_to_bytes(manifest))
        with self.assertRaises(codec.IntegrityError):
            codec.manifest_from_bytes(json.dumps(raw).encode("utf-8"),
                                      expected_partition="fin-2026-09-30")

    def test_schema1_still_parses(self):
        shard = codec.build_shard("2026-10-21", "dragons", "prep", {"a": 1})
        manifest = codec.manifest_from_entries(
            "2026-10-21",
            {"prep:dragons": codec.make_entry(shard, codec.compress(shard.content))})
        self.assertEqual(manifest.schema, 1)
        loaded = codec.manifest_from_bytes(
            codec.manifest_to_bytes(manifest), expected_trade_date="2026-10-21")
        self.assertEqual(loaded.trade_date, "2026-10-21")

    def test_schema1_with_schema2_partition_rejected(self):
        raw = {"schema": 2, "partition": "fin-2026-06-30",
               "partition_kind": "date", "sort_date": "2026-06-30",
               "manifest_id": "0" * 64, "assets": {}}
        with self.assertRaises(codec.IntegrityError):
            codec.manifest_from_bytes(json.dumps(raw).encode("utf-8"))


class TestBuildRunUid(unittest.TestCase):
    def test_stable_and_ignores_build_ids(self):
        left = {"trade_date": "2026-10-21", "started_at": "2026-10-21 10:00:00",
                "finished_at": "2026-10-21 10:00:05",
                "report_json": {"ok": True, "build_ids": [1, 2]}}
        right = dict(left, report_json={"ok": True, "build_ids": [99]})
        self.assertEqual(tables.build_run_uid(left), tables.build_run_uid(right))

    def test_changes_with_content(self):
        left = {"trade_date": "2026-10-21", "started_at": "a", "finished_at": "b",
                "report_json": {"ok": True}}
        right = dict(left, report_json={"ok": False})
        self.assertNotEqual(tables.build_run_uid(left), tables.build_run_uid(right))

    def test_json_text_and_object_equivalent(self):
        obj = {"trade_date": "2026-10-21", "started_at": "a", "finished_at": "b",
               "report_json": {"ok": True, "build_ids": [1]}}
        text = dict(obj, report_json=_dumps(obj["report_json"]))
        self.assertEqual(tables.build_run_uid(obj), tables.build_run_uid(text))


class SourceTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "assets.db"

    def tearDown(self):
        self._tmp.cleanup()

    def _conn(self):
        conn = assets_db._connect(path=self.db_path)
        assets_db._ensure_schema(conn)
        return conn


class TestCompositeSourceRead(SourceTestBase):
    def _seed(self, sql, params=()):
        conn = self._conn()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def test_date_partition_watermarks_and_read(self):
        self._seed(
            "INSERT INTO prep_snapshots(trade_date, source, result_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-10-21", "v2", _dumps({"sentiment": 1}), "2026-10-21 17:00:00"))
        source = tables.PrepsnapAssetSource(db_path=self.db_path)
        self.assertEqual(source.partitions(), ["2026-10-21"])
        self.assertEqual(source.volatile_partitions(), {"2026-10-21"})
        self.assertEqual(source.watermarks(),
                         {"2026-10-21": "2026-10-21 17:00:00|1|16"})
        batch = source.read_partition("2026-10-21")
        self.assertEqual(batch.failed, [])
        self.assertEqual(len(batch.shards), 1)
        payload = json.loads(batch.shards[0].content)
        self.assertEqual(payload["table"], "prep_snapshots")
        self.assertEqual(payload["columns"],
                         ["trade_date", "source", "result_json"])
        self.assertEqual(payload["rows"][0], ["2026-10-21", "v2", {"sentiment": 1}])

    def test_period_static_and_filtered_partitions(self):
        self._seed(
            "INSERT INTO fin_report_files(report_period, filename, md5, filesize, "
            "first_seen_at, status, records_count, raw_blob) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("2026-06-30", "gpcw.zip", "m1", 10, "2026-07-01 10:00:00",
             "ok", 3, b"\x1f\x8bdata"))
        self._seed(
            "INSERT INTO fin_report_files(report_period, filename, md5, filesize, "
            "first_seen_at, status, records_count, raw_blob) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("2026-03-31", "gpcw2.zip", "m2", 10, "2026-04-01 10:00:00",
             "ok", 1, None))
        source = tables.FinAssetSource(db_path=self.db_path)
        self.assertEqual(source.partitions(), ["fin-2026-03-31", "fin-2026-06-30",
                                               "finraw-2026-06-30"])
        raw_batch = source.read_partition("finraw-2026-06-30")
        payload = json.loads(raw_batch.shards[0].content)
        self.assertEqual(payload["columns"], ["report_period", "md5", "raw_blob"])
        encoded = payload["rows"][0][2]
        self.assertEqual(base64.b64decode(encoded), b"\x1f\x8bdata")
        # 没有 raw_blob 的期次不产出 finraw 分片
        self.assertEqual(source.read_partition("finraw-2026-03-31").shards, [])

    def test_static_partition(self):
        self._seed(
            "INSERT INTO factor_registry(factor_name, factor_version, frequency, "
            "source, direction, available_phase, pit_rule, spec_hash, params_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("five_dim_drive", "v1", "daily", "scan", "higher", "scan", "derived",
             "hash", _dumps({"w": 1})))
        source = tables.FactorAssetSource(db_path=self.db_path)
        self.assertEqual(source.partitions(), ["static-factor"])
        batch = source.read_partition("static-factor")
        payload = json.loads(batch.shards[0].content)
        self.assertEqual(payload["rows"][0][payload["columns"].index("params_json")],
                         {"w": 1})
        # static 分区不参与日期过滤
        self.assertTrue(codec.partition_in_range("static-factor", "2026-10-21",
                                                 "2026-10-21"))


class TestSchemaDrift(SourceTestBase):
    def _seed(self, sql, params=()):
        conn = self._conn()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def test_missing_local_column_fails_shard(self):
        self._seed(
            "INSERT INTO factor_values(trade_date, code, factor_name, factor_version, "
            "value, available_at, source, source_meta_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("2026-10-21", "600172", "f", "v1", 1.5, "2026-10-21T15:00:00",
             "scan", _dumps({"coverage": 0.8})))
        conn = self._conn()
        try:
            conn.execute("ALTER TABLE factor_values DROP COLUMN source_meta_json")
            conn.commit()
        finally:
            conn.close()
        source = tables.FactorAssetSource(db_path=self.db_path)
        batch = source.read_partition("2026-10-21")
        self.assertEqual(batch.shards, [])
        self.assertEqual(len(batch.failed), 1)
        self.assertIn("缺少列", batch.failed[0]["reason"])

    def test_not_null_without_default_fails_shard(self):
        self._seed(
            "INSERT INTO fin_field_calibrations(layout_key, num_fields, mapping_json, "
            "confidence_json, status, sample_n) VALUES (?, ?, ?, ?, ?, ?)",
            ("gpcw_10", 10, _dumps({"roe": 1}), _dumps({"roe": 0.9}),
             "verified", 5))
        conn = self._conn()
        try:
            conn.execute("ALTER TABLE fin_field_calibrations DROP COLUMN sample_n")
            conn.commit()
        finally:
            conn.close()
        source = tables.FinAssetSource(db_path=self.db_path)
        batch = source.read_partition("static-fin")
        self.assertEqual(batch.shards, [])
        self.assertEqual(len(batch.failed), 1)
        self.assertIn("缺少列", batch.failed[0]["reason"])

    def test_corrupt_json_row_is_shard_failure(self):
        self._seed(
            "INSERT INTO prep_snapshots(trade_date, source, result_json) "
            "VALUES (?, ?, ?)", ("2026-10-21", "v2", "{bad"))
        source = tables.PrepsnapAssetSource(db_path=self.db_path)
        batch = source.read_partition("2026-10-21")
        self.assertEqual(batch.shards, [])
        self.assertEqual(len(batch.failed), 1)

    def test_missing_db_returns_empty(self):
        source = tables.FinAssetSource(db_path=self.db_path)
        self.assertEqual(source.partitions(), [])
        self.assertEqual(source.watermarks(), {})
        self.assertEqual(source.read_partition("2026-10-21").shards, [])


if __name__ == "__main__":
    unittest.main()
