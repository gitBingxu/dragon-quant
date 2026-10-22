"""M3 manifest 构建 / 解析 / 重建测试。"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from dragon_quant.assets_sync import codec, manifest as manifest_mod
from dragon_quant.assets_sync.backends.local import LocalDirBackend
from dragon_quant.assets_sync.errors import AmbiguousError, IntegrityError


class ManifestTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.backend = LocalDirBackend(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _upload(self, trade_date="2026-10-21", payload=None, asset_type="dragons",
                source="prep", created_at="2026-10-21 17:00:00", tag="a"):
        shard = codec.build_shard(
            trade_date, asset_type, source, payload or {"tag": tag},
            created_at=created_at)
        blob = codec.compress(shard.content)
        self.backend.put(codec.blob_key(shard), blob,
                         codec.blob_metadata(shard, blob))
        return shard


class TestBuildAndParse(ManifestTestBase):
    def test_created_at_roundtrip_and_not_in_identity(self):
        shard = codec.build_shard(
            "2026-10-21", "dragons", "prep", {"a": 1},
            created_at="2026-10-21 17:00:00")
        manifest = manifest_mod.build_manifest(
            "2026-10-21", [shard], generated_at="2026-10-21T18:00:00+08:00",
            generator_version="0.6.2")
        loaded = codec.manifest_from_bytes(codec.manifest_to_bytes(manifest))
        self.assertEqual(loaded.assets["prep:dragons"].created_at, "2026-10-21 17:00:00")
        self.assertEqual(loaded.manifest_id, manifest.manifest_id)

        other = codec.build_shard(
            "2026-10-21", "dragons", "prep", {"a": 1},
            created_at="2026-10-22 09:00:00")
        other_manifest = manifest_mod.build_manifest("2026-10-21", [other])
        self.assertEqual(other_manifest.manifest_id, manifest.manifest_id)

    def test_created_at_map_roundtrip(self):
        shard = codec.build_shard(
            "2026-10-21", "assets", "auction",
            {"trade_date": "2026-10-21", "items": []},
            created_at_map={"002881": "2026-10-21 22:15:20"})
        manifest = manifest_mod.build_manifest("2026-10-21", [shard])
        loaded = codec.manifest_from_bytes(codec.manifest_to_bytes(manifest))
        self.assertEqual(loaded.assets["auction:assets"].created_at_map,
                         {"002881": "2026-10-21 22:15:20"})

    def test_pointer_tamper_rejected(self):
        manifest = manifest_mod.build_manifest(
            "2026-10-21", [self._upload()])
        raw = json.loads(codec.manifest_to_bytes(manifest))
        raw["assets"]["prep:dragons"]["blob"] = "blobs/2026-10-20/other.json.gz"
        with self.assertRaises(IntegrityError):
            codec.manifest_from_bytes(json.dumps(raw).encode("utf-8"))

    def test_key_tamper_rejected(self):
        manifest = manifest_mod.build_manifest("2026-10-21", [self._upload()])
        raw = json.loads(codec.manifest_to_bytes(manifest))
        raw["assets"]["prep:other"] = raw["assets"].pop("prep:dragons")
        with self.assertRaises(IntegrityError):
            codec.manifest_from_bytes(json.dumps(raw).encode("utf-8"))

    def test_manifest_id_for_shards_matches_full(self):
        shard = self._upload()
        full = manifest_mod.build_manifest("2026-10-21", [shard])
        self.assertEqual(codec.manifest_id_for_shards([shard]), full.manifest_id)

    def test_parse_blob_key(self):
        shard = self._upload()
        key = codec.blob_key(shard)
        self.assertEqual(
            manifest_mod.parse_blob_key(key, "2026-10-21"),
            ("prep", "dragons", shard.sha256))
        with self.assertRaises(ValueError):
            manifest_mod.parse_blob_key("blobs/2026-10-21/bad.json.gz", "2026-10-21")


class TestRemoteAndRebuild(ManifestTestBase):
    def test_remote_id_prefers_metadata_then_falls_back(self):
        shard = self._upload()
        manifest = manifest_mod.build_manifest("2026-10-21", [shard])
        key = codec.manifest_key("2026-10-21")
        body = codec.manifest_to_bytes(manifest)
        self.backend.put(key, body, manifest_mod.manifest_metadata(manifest))
        self.assertEqual(
            manifest_mod.remote_manifest_id(self.backend, "2026-10-21"),
            (manifest.manifest_id, True))

        self.backend.put(key, body, {})
        self.assertEqual(
            manifest_mod.remote_manifest_id(self.backend, "2026-10-21"),
            (manifest.manifest_id, False))

    def test_rebuild_from_metadata(self):
        shard = self._upload()
        manifest, degraded = manifest_mod.rebuild_manifest(self.backend, "2026-10-21")
        self.assertFalse(degraded)
        self.assertEqual(manifest.assets["prep:dragons"].sha256, shard.sha256)
        self.assertEqual(manifest.assets["prep:dragons"].created_at, "2026-10-21 17:00:00")

    def test_rebuild_falls_back_to_get_when_metadata_missing(self):
        shard = self._upload()
        self.backend.put(codec.blob_key(shard), codec.compress(shard.content), {})
        manifest, _ = manifest_mod.rebuild_manifest(self.backend, "2026-10-21")
        entry = manifest.assets["prep:dragons"]
        self.assertEqual(entry.sha256, shard.sha256)
        self.assertEqual(entry.size, len(shard.content))

    def test_rebuild_ambiguous_and_pick_latest(self):
        first = self._upload(tag="a")
        second = self._upload(tag="b")
        with self.assertRaises(AmbiguousError):
            manifest_mod.rebuild_manifest(self.backend, "2026-10-21")
        # 让第二个 blob 的 mtime 明显更新
        path = self.root / codec.blob_key(second)
        os.utime(path, (2_000_000_000, 2_000_000_000))
        manifest, degraded = manifest_mod.rebuild_manifest(
            self.backend, "2026-10-21", pick_latest=True)
        self.assertTrue(degraded)
        self.assertEqual(manifest.assets["prep:dragons"].sha256, second.sha256)
        self.assertNotEqual(first.sha256, second.sha256)

    def test_empty_day_returns_none(self):
        self.assertEqual(manifest_mod.rebuild_manifest(self.backend, "2026-10-21"),
                         (None, False))


if __name__ == "__main__":
    unittest.main()
