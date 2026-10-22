"""M3 同步层集成测试（临时 assets.db + LocalDirBackend，不访问网络）。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dragon_quant.assets_sync import codec, manifest as manifest_mod, service
from dragon_quant.assets_sync.backends.local import LocalDirBackend
from dragon_quant.assets_sync.errors import StorageError
from dragon_quant.assets_sync.sources import (
    AuctionAssetSource,
    build_sources,
)
from dragon_quant.assets_sync.state import default_paths
from dragon_quant.storage import assets_db


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class CountingBackend(LocalDirBackend):
    def __init__(self, root, *, fail_manifest_put=False):
        super().__init__(root)
        self.puts: list[str] = []
        self.fail_manifest_put = fail_manifest_put

    def put(self, key, data, metadata=None, content_md5=None):
        if self.fail_manifest_put and key.startswith("manifests/"):
            raise StorageError("simulated manifest put failure")
        self.puts.append(key)
        return super().put(key, data, metadata, content_md5)

    def reset(self):
        self.puts = []


class ServiceTestBase(unittest.TestCase):
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

    def _conn(self, db=None):
        conn = assets_db._connect(path=db or self.db)
        assets_db._ensure_schema(conn)
        return conn

    def _seed_prep(self, trade_date="2026-10-21", *, types=None,
                   created_at="2026-10-21 17:00:00", db=None):
        types = types or ("dragons", "collection_report")
        conn = self._conn(db)
        try:
            for asset_type in types:
                conn.execute(
                    "INSERT INTO prep_assets(trade_date, asset_type, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (trade_date, asset_type, _dumps({"kind": asset_type}), created_at),
                )
            conn.commit()
        finally:
            conn.close()

    def _seed_auction(self, trade_date="2026-10-21", *, quote=None,
                      created_at="2026-10-21 22:15:20", db=None):
        conn = self._conn(db)
        try:
            conn.execute(
                "INSERT INTO auction_assets(trade_date, code, name, items_json, quote_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (trade_date, "002881", "美格智能",
                 _dumps([{"time": "09:15:00", "price": 42.3}]),
                 _dumps(quote) if quote is not None else None, created_at),
            )
            conn.commit()
        finally:
            conn.close()

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

    def _pull(self, scratch, **kwargs):
        target_sources = build_sources(db_path=scratch / "assets.db")
        return service.pull(
            self.backend, target_sources, paths=default_paths(scratch), **kwargs)


class TestPush(ServiceTestBase):
    def test_push_layout_and_idempotent(self):
        self._seed_prep("2026-10-20", created_at="2026-10-20 17:00:00")
        self._seed_prep("2026-10-21")
        self._seed_auction("2026-10-21")
        first = self._push()
        self.assertTrue(first["ok"], first)
        keys = {item.key for item in self.backend.list("")}
        self.assertTrue(any(key.startswith("manifests/2026-10-20.json") for key in keys))
        self.assertTrue(any(key.startswith("blobs/2026-10-21/auction_assets.") for key in keys))
        self.assertEqual(first["summary"]["pushed"], 2)

        self.backend.reset()
        second = self._push()
        self.assertTrue(second["ok"], second)
        self.assertEqual(self.backend.puts, [])   # AC1：无变化零上传
        self.assertEqual(second["summary"]["skipped"], 2)

    def test_dry_run_writes_nothing(self):
        self._seed_prep()
        result = self._push(dry_run=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.backend.puts, [])
        self.assertFalse(self.paths.state_path.exists())

    def test_timestamp_only_change_rewrites_manifest(self):
        self._seed_prep()
        self._push()
        self.backend.reset()
        self._exec("UPDATE prep_assets SET created_at='2026-10-22 09:00:00'")
        result = self._push()
        self.assertTrue(result["ok"], result)
        self.assertEqual([key for key in self.backend.puts if key.startswith("blobs/")], [])
        self.assertEqual(len([key for key in self.backend.puts
                              if key.startswith("manifests/")]), 1)
        self.assertEqual(result["summary"]["blobs_uploaded"], 0)

    def test_shrink_protection(self):
        self._seed_prep()
        self._push()
        self._exec("DELETE FROM prep_assets WHERE asset_type='collection_report'")
        rejected = self._push(all_dates=True)
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["failed"][0]["kind"], "shrink")
        allowed = self._push(all_dates=True, allow_shrink=True)
        self.assertTrue(allowed["ok"], allowed)
        manifest = manifest_mod.read_remote_manifest(self.backend, "2026-10-21")
        self.assertEqual(set(manifest.assets), {"prep:dragons"})

    def test_local_empty_remote_present(self):
        self._seed_prep()
        self._push()
        self._exec("DELETE FROM prep_assets")
        result = self._push(all_dates=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"][0]["kind"], "local_empty")
        self.assertEqual(result["summary"]["local_empty_remote_present"], ["2026-10-21"])
        self.assertIsNotNone(
            manifest_mod.read_remote_manifest(self.backend, "2026-10-21"))

    def test_failing_backend_recovers_without_reuploading_blobs(self):
        self._seed_prep()
        backend = CountingBackend(self.root / "cloud2", fail_manifest_put=True)
        failed = service.push(backend, self.sources, paths=self.paths)
        self.assertTrue(failed["fatal"])
        self.assertIsNone(manifest_mod.read_remote_manifest(backend, "2026-10-21"))
        backend.fail_manifest_put = False
        backend.reset()
        retried = service.push(backend, self.sources, paths=self.paths)
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(retried["summary"]["blobs_uploaded"], 0)
        self.assertEqual(retried["summary"]["manifests_uploaded"], 1)

    def test_unreadable_remote_manifest_is_not_overwritten(self):
        self._seed_prep()
        self._push()
        key = codec.manifest_key("2026-10-21")
        meta = self.backend.head(key)
        self.backend.put(key, b"{bad", meta.metadata)
        result = self._push(all_dates=True)
        self.assertTrue(result["fatal"])
        self.assertEqual(result["failed"][0]["kind"], "manifest_unreadable")
        self.assertEqual(self.backend.get(key), b"{bad")

    def test_state_missing_second_tier_skips_without_put(self):
        self._seed_prep()
        self._push()
        self.paths.state_path.unlink()
        self.backend.reset()
        result = self._push()
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.backend.puts, [])
        self.assertEqual(result["summary"]["skipped"], 1)
        self.assertTrue(self.paths.state_path.exists())

    def test_corrupt_auction_row_blocks_manifest(self):
        self._seed_auction()
        self._exec("UPDATE auction_assets SET quote_json='{bad'")
        result = self._push()
        self.assertFalse(result["ok"])
        self.assertIsNone(
            manifest_mod.read_remote_manifest(self.backend, "2026-10-21"))


class TestPull(ServiceTestBase):
    def test_restore_scratch_payload_and_created_at(self):
        self._seed_prep("2026-10-20", created_at="2026-10-20 17:00:00")
        self._seed_auction("2026-10-21")
        self._push()
        scratch = self.root / "scratch"
        result = self._pull(
            scratch, date_from="2026-10-20", date_to="2026-10-21", apply=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["summary"]["restored"], 3)
        self.assertFalse((scratch / "assets_sync" / "state.json").exists())

        rows = self._rows(
            "SELECT trade_date, asset_type, payload_json, created_at FROM prep_assets "
            "ORDER BY trade_date, asset_type", db=scratch / "assets.db")
        self.assertEqual(len(rows), 2)
        self.assertEqual(json.loads(rows[0][2]), {"kind": "collection_report"})
        self.assertEqual(rows[0][3], "2026-10-20 17:00:00")
        auction = self._rows(
            "SELECT code, name, created_at FROM auction_assets", db=scratch / "assets.db")
        self.assertEqual(auction, [("002881", "美格智能", "2026-10-21 22:15:20")])

    def test_missing_blob_fails_without_writing(self):
        self._seed_prep()
        self._push()
        manifest = manifest_mod.read_remote_manifest(self.backend, "2026-10-21")
        blob = manifest.assets["prep:dragons"].blob
        self.backend.delete(blob)
        scratch = self.root / "scratch"
        result = self._pull(
            scratch, date_from="2026-10-21", date_to="2026-10-21", apply=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"][0]["kind"], "integrity")
        self.assertFalse((scratch / "assets.db").exists())

    def test_corrupt_blob_fails_without_writing(self):
        self._seed_prep()
        self._push()
        manifest = manifest_mod.read_remote_manifest(self.backend, "2026-10-21")
        blob_key = manifest.assets["prep:dragons"].blob
        meta = self.backend.head(blob_key)
        self.backend.put(blob_key, b"garbage", meta.metadata)
        scratch = self.root / "scratch"
        result = self._pull(
            scratch, date_from="2026-10-21", date_to="2026-10-21", apply=True)
        self.assertFalse(result["ok"])
        self.assertFalse((scratch / "assets.db").exists())

    def test_rebuild_when_manifest_missing(self):
        self._seed_prep()
        self._push()
        self.backend.delete(codec.manifest_key("2026-10-21"))
        scratch = self.root / "scratch"
        result = self._pull(
            scratch, date_from="2026-10-21", date_to="2026-10-21", apply=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["summary"]["manifest_source"], "rebuilt")
        self.assertEqual(len(self._rows("SELECT * FROM prep_assets",
                                        db=scratch / "assets.db")), 2)

    def test_ambiguous_requires_pick_latest(self):
        self._seed_prep(types=("dragons",))
        self._push()
        self.backend.delete(codec.manifest_key("2026-10-21"))
        shard = codec.build_shard("2026-10-21", "dragons", "prep", {"kind": "other"})
        blob = codec.compress(shard.content)
        self.backend.put(codec.blob_key(shard), blob, codec.blob_metadata(shard, blob))
        os.utime(self.root / "cloud" / codec.blob_key(shard), (2_000_000_000, 2_000_000_000))

        scratch = self.root / "scratch"
        failed = self._pull(
            scratch, date_from="2026-10-21", date_to="2026-10-21", apply=True)
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["failed"][0]["kind"], "ambiguous")

        picked = self._pull(
            scratch, date_from="2026-10-21", date_to="2026-10-21", apply=True,
            pick_latest=True)
        self.assertTrue(picked["ok"], picked)
        self.assertTrue(picked["summary"]["degraded"])

    def test_pull_discovers_blob_only_date(self):
        self._seed_prep()
        self._push()
        self.backend.delete(codec.manifest_key("2026-10-21"))
        scratch = self.root / "scratch"
        # 目标库为空：本地日期集合不提供 2026-10-21，只能靠 blobs/ LIST 发现
        empty_sources = build_sources(db_path=scratch / "assets.db")
        result = service.pull(
            self.backend, empty_sources, date_from="2026-10-21",
            date_to="2026-10-21", apply=True, paths=default_paths(scratch))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["summary"]["manifest_source"], "rebuilt")
        self.assertEqual(len(self._rows("SELECT * FROM prep_assets",
                                        db=scratch / "assets.db")), 2)

    def test_quote_null_preserves_local_on_force(self):
        # 云端 quote=None
        self._seed_auction(quote=None)
        self._push()
        scratch = self.root / "scratch"
        scratch.mkdir()
        self._seed_auction(quote={"local": 1}, created_at="2026-10-21 22:00:00",
                           db=scratch / "assets.db")
        result = self._pull(
            scratch, date_from="2026-10-21", date_to="2026-10-21",
            apply=True, force=True)
        self.assertTrue(result["ok"], result)
        row = self._rows(
            "SELECT name, items_json, quote_json FROM auction_assets",
            db=scratch / "assets.db")[0]
        self.assertEqual(row[0], "美格智能")
        self.assertEqual(json.loads(row[2]), {"local": 1})   # 不被 NULL 抹掉

    def test_source_level_failure_isolation(self):
        self._seed_prep()
        self._seed_auction()
        self._push()
        scratch = self.root / "scratch"
        with patch.object(AuctionAssetSource, "restore",
                          side_effect=RuntimeError("boom")):
            result = self._pull(
                scratch, date_from="2026-10-21", date_to="2026-10-21", apply=True)
        self.assertFalse(result["ok"])
        self.assertEqual([item["source"] for item in result["failed"]
                          if item.get("kind") == "db"], ["auction"])
        self.assertEqual(len(self._rows("SELECT * FROM prep_assets",
                                        db=scratch / "assets.db")), 2)

    def test_unknown_source_is_unsupported_not_failure(self):
        self._seed_prep()
        self._push()
        key = codec.manifest_key("2026-10-21")
        raw = json.loads(self.backend.get(key))
        future_shard = codec.build_shard(
            "2026-10-21", "notes", "future", {"x": 1})
        future_blob = codec.compress(future_shard.content)
        self.backend.put(codec.blob_key(future_shard), future_blob,
                         codec.blob_metadata(future_shard, future_blob))
        raw["assets"]["future:notes"] = json.loads(
            codec.manifest_to_bytes(manifest_mod.build_manifest(
                "2026-10-21", [future_shard])))["assets"]["future:notes"]
        identity = {k: {f: v[f] for f in
                        ("source", "asset_type", "sha256", "md5", "size", "schema")}
                    for k, v in raw["assets"].items()}
        raw["manifest_id"] = codec.hashes(codec.canonical_json(identity))[0]
        body = codec.canonical_json(raw)
        self.backend.put(key, body, {"manifest-id": raw["manifest_id"], "schema": "1"})

        scratch = self.root / "scratch"
        result = self._pull(
            scratch, date_from="2026-10-21", date_to="2026-10-21", apply=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["summary"]["unsupported"][0]["source"], "future")

    def test_disk_usage_precheck(self):
        self._seed_prep()
        self._push()
        scratch = self.root / "scratch"
        with patch("dragon_quant.assets_sync.service.shutil.disk_usage",
                   return_value=SimpleNamespace(total=1, used=1, free=1)):
            result = self._pull(
                scratch, date_from="2026-10-21", date_to="2026-10-21", apply=True)
        self.assertTrue(result["fatal"])
        self.assertEqual(result["failed"][0]["kind"], "config")
        self.assertFalse((scratch / "assets.db").exists())

    def test_corrupt_local_payload_fails_pull_and_status(self):
        self._seed_prep()
        self._push()
        scratch = self.root / "scratch"
        scratch.mkdir()
        self._seed_prep(db=scratch / "assets.db")
        self._exec("UPDATE prep_assets SET payload_json='{bad' WHERE asset_type='dragons'",
                   db=scratch / "assets.db")
        result = self._pull(
            scratch, date_from="2026-10-21", date_to="2026-10-21", apply=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"][0]["kind"], "integrity")
        status = service.status(self.backend, self.sources, paths=self.paths)
        self.assertTrue(status["ok"], status)   # 源库干净；损坏在 scratch 目标库

    def test_corrupt_local_payload_blocks_push(self):
        self._seed_prep()
        self._exec("UPDATE prep_assets SET payload_json='{bad' WHERE asset_type='dragons'")
        result = self._push()
        self.assertFalse(result["ok"])
        self.assertIsNone(
            manifest_mod.read_remote_manifest(self.backend, "2026-10-21"))


class TestStatusVerify(ServiceTestBase):
    def test_status_detects_mismatch_and_missing(self):
        self._seed_prep()
        self._push()
        ok = service.status(self.backend, self.sources, paths=self.paths)
        self.assertTrue(ok["ok"], ok)
        self._exec("UPDATE prep_assets SET payload_json='{\"kind\":\"changed\"}' "
                   "WHERE asset_type='dragons'")
        changed = service.status(self.backend, self.sources, paths=self.paths)
        self.assertFalse(changed["ok"])
        self.assertEqual(changed["summary"]["dates_mismatched"][0]["trade_date"],
                         "2026-10-21")
        self._exec("DELETE FROM prep_assets")
        missing = service.status(self.backend, self.sources, paths=self.paths)
        self.assertIn("local_missing",
                      [item["reason"] for item in missing["summary"]["dates_missing"]])

    def test_verify_detects_manifest_tamper(self):
        self._seed_prep()
        self._push()
        key = codec.manifest_key("2026-10-21")
        meta = self.backend.head(key)
        raw = json.loads(self.backend.get(key))
        raw["assets"]["prep:dragons"]["size"] += 1
        self.backend.put(key, codec.canonical_json(raw), meta.metadata)
        result = service.verify(self.backend, self.sources, paths=self.paths)
        self.assertFalse(result["ok"])
        self.assertIn("manifest_id", result["summary"]["mismatched"][0]["reason"])

    def test_verify_detects_body_and_id_rewrite(self):
        self._seed_prep()
        self._push()
        key = codec.manifest_key("2026-10-21")
        raw = json.loads(self.backend.get(key))
        raw["assets"]["prep:dragons"]["size"] += 1
        identity = {k: {f: v[f] for f in
                        ("source", "asset_type", "sha256", "md5", "size", "schema")}
                    for k, v in raw["assets"].items()}
        raw["manifest_id"] = codec.hashes(codec.canonical_json(identity))[0]
        # 保留旧 HEAD 元数据锚点
        self.backend.put(key, codec.canonical_json(raw), {"manifest-id": "0" * 64,
                                                          "schema": "1"})
        result = service.verify(self.backend, self.sources, paths=self.paths)
        self.assertFalse(result["ok"])
        self.assertIn("metadata_id_mismatch",
                      [item["reason"] for item in result["summary"]["mismatched"]])

    def test_verify_deep_detects_blob_corruption(self):
        self._seed_prep()
        self._push()
        manifest = manifest_mod.read_remote_manifest(self.backend, "2026-10-21")
        blob_key = manifest.assets["prep:dragons"].blob
        meta = self.backend.head(blob_key)
        self.backend.put(blob_key, b"garbage", meta.metadata)
        result = service.verify(
            self.backend, self.sources, deep=True, samples=10, paths=self.paths)
        self.assertFalse(result["ok"])
        self.assertTrue(any("blob_invalid" in item["reason"]
                            for item in result["summary"]["mismatched"]))

    def test_verify_metadata_missing_falls_back(self):
        self._seed_prep()
        self._push()
        key = codec.manifest_key("2026-10-21")
        body = self.backend.get(key)
        self.backend.put(key, body, {})
        result = service.verify(self.backend, self.sources, paths=self.paths)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["summary"]["metadata_supported"])

    def test_status_detects_manifest_id_drift(self):
        self._seed_prep()
        self._push()
        key = codec.manifest_key("2026-10-21")
        raw = json.loads(self.backend.get(key))
        raw["assets"]["prep:dragons"]["size"] += 1
        identity = {k: {f: v[f] for f in
                        ("source", "asset_type", "sha256", "md5", "size", "schema")}
                    for k, v in raw["assets"].items()}
        raw["manifest_id"] = codec.hashes(codec.canonical_json(identity))[0]
        self.backend.put(key, codec.canonical_json(raw),
                         {"manifest-id": raw["manifest_id"], "schema": "1"})
        result = service.status(self.backend, self.sources, paths=self.paths)
        self.assertFalse(result["ok"])
        mismatch = result["summary"]["dates_mismatched"][0]
        self.assertEqual(mismatch["keys"], [])   # sha 未变，仅身份字段漂移
        self.assertNotEqual(mismatch["local_manifest_id"],
                            mismatch["remote_manifest_id"])

    def test_status_orphans_null_when_manifest_missing(self):
        self._seed_prep()
        self._push()
        self.backend.delete(codec.manifest_key("2026-10-21"))
        result = service.status(
            self.backend, self.sources, orphans=True, paths=self.paths)
        self.assertIsNone(result["summary"]["orphan_blobs"])
        self.assertEqual(result["summary"]["orphan_reason"]["reason"],
                         "manifest_missing")

    def test_status_orphans_null_wins_over_known_orphans(self):
        self._seed_prep("2026-10-20", created_at="2026-10-20 17:00:00")
        self._seed_prep("2026-10-21")
        self._push()
        self.backend.delete(codec.manifest_key("2026-10-21"))
        result = service.status(
            self.backend, self.sources, orphans=True, paths=self.paths)
        self.assertIsNone(result["summary"]["orphan_blobs"])
        self.assertEqual(result["summary"]["orphan_reason"]["dates"], ["2026-10-21"])

    def test_manifest_body_date_mismatch_rejected(self):
        self._seed_prep()
        self._push()
        source_key = codec.manifest_key("2026-10-21")
        manifest = manifest_mod.read_remote_manifest(self.backend, "2026-10-21")
        self.backend.put(codec.manifest_key("2026-10-22"),
                         self.backend.get(source_key),
                         {"manifest-id": manifest.manifest_id, "schema": "1"})
        verified = service.verify(
            self.backend, self.sources,
            date_from="2026-10-21", date_to="2026-10-22", paths=self.paths)
        self.assertFalse(verified["ok"])
        self.assertTrue(any("trade_date" in item["reason"]
                            for item in verified["summary"]["mismatched"]))

        scratch = self.root / "scratch"
        pulled = self._pull(
            scratch, date_from="2026-10-22", date_to="2026-10-22", apply=True)
        self.assertTrue(pulled["fatal"])
        self.assertEqual(pulled["failed"][0]["kind"], "manifest_unreadable")


if __name__ == "__main__":
    unittest.main()
