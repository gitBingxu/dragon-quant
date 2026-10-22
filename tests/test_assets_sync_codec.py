"""codec 单元测试：规范化、压缩确定性、哈希口径、manifest。"""

import json
import unittest

from dragon_quant.assets_sync import codec
from dragon_quant.assets_sync.errors import IntegrityError


class TestCanonicalJson(unittest.TestCase):
    def test_sorted_and_compact(self):
        left = codec.canonical_json({"b": 1, "a": [2, 3]})
        right = codec.canonical_json({"a": [2, 3], "b": 1})
        self.assertEqual(left, right)
        self.assertEqual(left, b'{"a":[2,3],"b":1}')

    def test_nan_rejected(self):
        with self.assertRaises(ValueError):
            codec.canonical_json({"x": float("nan")})

    def test_unicode_preserved(self):
        self.assertEqual(codec.canonical_json({"name": "龙头"}).decode("utf-8"),
                         '{"name":"龙头"}')


class TestCompression(unittest.TestCase):
    def test_roundtrip(self):
        data = b'{"a":1}'
        self.assertEqual(codec.decompress(codec.compress(data)), data)

    def test_deterministic(self):
        data = b"repeat" * 100
        self.assertEqual(codec.compress(data), codec.compress(data))


class TestShard(unittest.TestCase):
    def test_content_hash_is_uncompressed_identity(self):
        shard = codec.build_shard("2026-10-19", "dragons", "prep", {"count": 2})
        self.assertEqual(shard.content, b'{"count":2}')
        sha256, md5 = codec.hashes(shard.content)
        self.assertEqual(shard.sha256, sha256)
        self.assertEqual(shard.md5, md5)
        self.assertNotEqual(shard.sha256, codec.hashes(codec.compress(shard.content))[0])

    def test_blob_key_is_self_describing(self):
        shard = codec.build_shard("2026-10-19", "dragons", "prep", {"count": 2})
        self.assertEqual(codec.blob_key(shard),
                         f"blobs/2026-10-19/prep_dragons.{shard.sha256}.json.gz")

    def test_metadata_roundtrip_fields(self):
        shard = codec.build_shard("2026-10-19", "dragons", "prep", {"count": 2})
        blob = codec.compress(shard.content)
        meta = codec.blob_metadata(shard, blob)
        self.assertEqual(meta["sha256"], shard.sha256)
        self.assertEqual(meta["md5"], shard.md5)
        self.assertEqual(meta["source"], "prep")
        self.assertEqual(meta["asset-type"], "dragons")
        self.assertEqual(meta["trade-date"], "2026-10-19")
        self.assertEqual(meta["blob-sha256"], codec.hashes(blob)[0])


class TestManifest(unittest.TestCase):
    def _manifest(self, payload):
        shard = codec.build_shard("2026-10-19", "dragons", "prep", payload)
        blob = codec.compress(shard.content)
        return codec.manifest_from_entries(
            "2026-10-19", {f"{shard.source}:{shard.asset_type}": codec.make_entry(shard, blob)}
        )

    def test_id_stable_across_blob_metadata(self):
        shard = codec.build_shard("2026-10-19", "dragons", "prep", {"count": 2})
        entry = codec.make_entry(shard, codec.compress(shard.content))
        same_identity = entry.__class__(
            source=entry.source, asset_type=entry.asset_type, sha256=entry.sha256,
            md5=entry.md5, size=entry.size, blob=entry.blob,
            blob_sha256="different", blob_size=999, schema=entry.schema,
        )
        left = codec.manifest_from_entries("2026-10-19", {"prep:dragons": entry})
        right = codec.manifest_from_entries("2026-10-19", {"prep:dragons": same_identity})
        self.assertEqual(left.manifest_id, right.manifest_id)

    def test_id_changes_with_content(self):
        self.assertNotEqual(self._manifest({"count": 2}).manifest_id,
                            self._manifest({"count": 3}).manifest_id)

    def test_bytes_roundtrip(self):
        manifest = self._manifest({"count": 2})
        loaded = codec.manifest_from_bytes(codec.manifest_to_bytes(manifest))
        self.assertEqual(loaded.manifest_id, manifest.manifest_id)
        self.assertEqual(loaded.assets["prep:dragons"].sha256,
                         manifest.assets["prep:dragons"].sha256)

    def test_parse_rejects_corrupt(self):
        with self.assertRaises(IntegrityError):
            codec.manifest_from_bytes(b"{bad")

    def test_parse_rejects_tampered_id(self):
        manifest = self._manifest({"count": 2})
        raw = json.loads(codec.manifest_to_bytes(manifest))
        raw["manifest_id"] = "0" * 64
        with self.assertRaises(IntegrityError):
            codec.manifest_from_bytes(json.dumps(raw).encode("utf-8"))

    def test_parse_rejects_non_object_assets(self):
        raw = {
            "schema": 1,
            "trade_date": "2026-10-19",
            "manifest_id": "0" * 64,
            "assets": [],
        }
        with self.assertRaises(IntegrityError):
            codec.manifest_from_bytes(json.dumps(raw).encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
