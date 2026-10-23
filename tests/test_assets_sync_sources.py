"""M2 数据层测试：AssetSource 适配、导出/水位/恢复事务。"""

import json
import tempfile
import unittest
from pathlib import Path

from dragon_quant.assets_sync import codec
from dragon_quant.assets_sync.sources import (
    AuctionAssetSource,
    PrepAssetSource,
    build_sources,
)
from dragon_quant.storage import assets_db


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class SourceTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "assets.db"

    def tearDown(self):
        self._tmp.cleanup()

    def _schema(self):
        conn = assets_db._connect(path=self.db_path)
        assets_db._ensure_schema(conn)
        return conn

    def _add_prep(self, trade_date, asset_type, payload, created_at):
        conn = self._schema()
        try:
            conn.execute(
                "INSERT INTO prep_assets(trade_date, asset_type, payload_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (trade_date, asset_type, _dumps(payload), created_at),
            )
            conn.commit()
        finally:
            conn.close()

    def _add_auction(self, trade_date, code, name, items, quote, created_at):
        conn = self._schema()
        try:
            conn.execute(
                "INSERT INTO auction_assets(trade_date, code, name, items_json, quote_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (trade_date, code, name, _dumps(items),
                 _dumps(quote) if quote is not None else None, created_at),
            )
            conn.commit()
        finally:
            conn.close()


class TestPrepSource(SourceTestBase):
    def test_read_date_shards_and_created_at(self):
        self._add_prep("2026-10-20", "dragons", {"count": 2}, "2026-10-20 17:00:00")
        self._add_prep("2026-10-20", "collection_report", {"dragons": {"status": "ok"}},
                       "2026-10-20 17:00:01")
        batch = PrepAssetSource(db_path=self.db_path).read_date("2026-10-20")
        self.assertEqual(batch.failed, [])
        self.assertEqual(len(batch.shards), 2)
        by_type = {shard.asset_type: shard for shard in batch.shards}
        self.assertEqual(by_type["dragons"].created_at, "2026-10-20 17:00:00")
        self.assertEqual(by_type["dragons"].content, codec.canonical_json({"count": 2}))

    def test_corrupt_payload_is_failure(self):
        self._add_prep("2026-10-20", "dragons", {"count": 2}, "2026-10-20 17:00:00")
        conn = self._schema()
        try:
            conn.execute("UPDATE prep_assets SET payload_json='{bad' WHERE asset_type='dragons'")
            conn.commit()
        finally:
            conn.close()
        batch = PrepAssetSource(db_path=self.db_path).read_date("2026-10-20")
        self.assertEqual(batch.shards, [])
        self.assertEqual(len(batch.failed), 1)
        self.assertIn("payload_json", batch.failed[0]["reason"])

    def test_local_index_matches_shard(self):
        self._add_prep("2026-10-20", "dragons", {"count": 2}, "2026-10-20 17:00:00")
        source = PrepAssetSource(db_path=self.db_path)
        batch = source.read_date("2026-10-20")
        index = source.local_index("2026-10-20")
        self.assertEqual(index["prep:dragons"], batch.shards[0].sha256)

    def test_watermark_changes_with_count_and_bytes(self):
        self._add_prep("2026-10-20", "dragons", {"count": 2}, "2026-10-20 17:00:00")
        source = PrepAssetSource(db_path=self.db_path)
        first = source.watermarks()["2026-10-20"]
        self._add_prep("2026-10-20", "collection_report", {"a": 1}, "2026-10-20 17:00:00")
        second = source.watermarks()["2026-10-20"]
        self.assertNotEqual(first, second)


class TestAuctionSource(SourceTestBase):
    def test_payload_excludes_created_at_and_keeps_map(self):
        self._add_auction("2026-10-21", "002881", "美格智能",
                          [{"time": "09:15:00", "price": 42.3}],
                          {"prev_close": 41.0}, "2026-10-21 22:15:20")
        self._add_auction("2026-10-21", "003021", "示例",
                          [{"time": "09:15:00", "price": 10.0}], None,
                          "2026-10-21 22:15:21")
        batch = AuctionAssetSource(db_path=self.db_path).read_date("2026-10-21")
        self.assertEqual(batch.failed, [])
        shard = batch.shards[0]
        payload = json.loads(shard.content.decode("utf-8"))
        self.assertEqual([item["code"] for item in payload["items"]], ["002881", "003021"])
        self.assertNotIn("created_at", payload["items"][0])
        self.assertEqual(shard.created_at_map, {
            "002881": "2026-10-21 22:15:20",
            "003021": "2026-10-21 22:15:21",
        })
        self.assertEqual(shard.created_at, "2026-10-21 22:15:21")

    def test_created_at_does_not_change_content_hash(self):
        self._add_auction("2026-10-21", "002881", "美格智能",
                          [{"time": "09:15:00"}], {"prev_close": 41.0},
                          "2026-10-21 22:15:20")
        source = AuctionAssetSource(db_path=self.db_path)
        first = source.read_date("2026-10-21").shards[0]
        conn = self._schema()
        try:
            conn.execute("UPDATE auction_assets SET created_at='2026-10-22 09:00:00'")
            conn.commit()
        finally:
            conn.close()
        second = source.read_date("2026-10-21").shards[0]
        self.assertEqual(first.sha256, second.sha256)
        self.assertNotEqual(first.created_at_map, second.created_at_map)

    def test_corrupt_quote_is_failure(self):
        self._add_auction("2026-10-21", "002881", "美格智能",
                          [{"time": "09:15:00"}], {"prev_close": 41.0},
                          "2026-10-21 22:15:20")
        conn = self._schema()
        try:
            conn.execute("UPDATE auction_assets SET quote_json='{bad'")
            conn.commit()
        finally:
            conn.close()
        batch = AuctionAssetSource(db_path=self.db_path).read_date("2026-10-21")
        self.assertEqual(batch.shards, [])
        self.assertEqual(len(batch.failed), 1)


class TestRestore(SourceTestBase):
    def test_build_sources_uses_explicit_db_path(self):
        sources = build_sources(db_path=self.db_path)
        self.assertEqual([source.name for source in sources],
                         ["prep", "auction", "prepsnap", "auctionsnap",
                          "dragons", "factor", "fin"])
        self.assertTrue(all(source.db_path == self.db_path for source in sources))

    def test_restore_prep_created_at_conflict_and_force(self):
        source = PrepAssetSource(db_path=self.db_path)
        shard = codec.build_shard(
            "2026-10-20", "dragons", "prep", {"count": 2},
            created_at="2026-10-20 17:00:00")
        report = source.restore("2026-10-20", [shard])
        self.assertEqual(report.restored, 1)
        conn = self._schema()
        try:
            row = conn.execute(
                "SELECT payload_json, created_at FROM prep_assets "
                "WHERE trade_date='2026-10-20' AND asset_type='dragons'").fetchone()
        finally:
            conn.close()
        self.assertEqual(json.loads(row[0]), {"count": 2})
        self.assertEqual(row[1], "2026-10-20 17:00:00")

        changed = codec.build_shard(
            "2026-10-20", "dragons", "prep", {"count": 3},
            created_at="2026-10-21 09:00:00")
        conflict = source.restore("2026-10-20", [changed])
        self.assertEqual(conflict.conflicts[0]["key"], "prep:dragons")
        forced = source.restore("2026-10-20", [changed], force=True)
        self.assertEqual(forced.restored, 1)
        conn = self._schema()
        try:
            row = conn.execute(
                "SELECT payload_json, created_at FROM prep_assets "
                "WHERE trade_date='2026-10-20' AND asset_type='dragons'").fetchone()
        finally:
            conn.close()
        self.assertEqual(json.loads(row[0]), {"count": 3})
        # 已存在行保留本地 created_at（本地权威）
        self.assertEqual(row[1], "2026-10-20 17:00:00")

    def test_restore_auction_quote_null_preserved(self):
        self._add_auction("2026-10-21", "002881", "本地名",
                          [{"time": "09:15:00", "price": 1.0}],
                          {"prev_close": 41.0}, "2026-10-21 22:15:20")
        remote = codec.build_shard(
            "2026-10-21", "assets", "auction",
            {"trade_date": "2026-10-21", "items": [
                {"code": "002881", "name": "云端名",
                 "items": [{"time": "09:15:00", "price": 2.0}], "quote": None}
            ]},
            created_at_map={"002881": "2026-10-22 09:00:00"})
        source = AuctionAssetSource(db_path=self.db_path)
        report = source.restore("2026-10-21", [remote], force=True)
        self.assertEqual(report.restored, 1)
        conn = self._schema()
        try:
            row = conn.execute(
                "SELECT name, items_json, quote_json, created_at FROM auction_assets "
                "WHERE code='002881'").fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], "云端名")
        self.assertEqual(json.loads(row[1])[0]["price"], 2.0)
        self.assertEqual(json.loads(row[2]), {"prev_close": 41.0})  # 不被 NULL 抹掉
        self.assertEqual(row[3], "2026-10-21 22:15:20")

    def test_restore_auction_inserts_created_at_map(self):
        remote = codec.build_shard(
            "2026-10-21", "assets", "auction",
            {"trade_date": "2026-10-21", "items": [
                {"code": "002881", "name": "A", "items": [], "quote": {"x": 1}}
            ]},
            created_at_map={"002881": "2026-10-21 22:15:20"})
        report = AuctionAssetSource(db_path=self.db_path).restore(
            "2026-10-21", [remote])
        self.assertEqual(report.restored, 1)
        conn = self._schema()
        try:
            created = conn.execute(
                "SELECT created_at FROM auction_assets WHERE code='002881'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(created, "2026-10-21 22:15:20")

    def test_restore_prep_rolls_back_on_serialize_error(self):
        good = {"asset_type": "dragons", "payload": {"a": 1}, "created_at": ""}
        bad = {"asset_type": "broken", "payload": float("nan"), "created_at": ""}
        with self.assertRaises(ValueError):
            assets_db.restore_prep_assets(
                "2026-10-20", [good, bad], db_path=self.db_path)
        conn = self._schema()
        try:
            count = conn.execute("SELECT COUNT(*) FROM prep_assets").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 0)


class TestDatesAndWatermarks(SourceTestBase):
    def test_dates_and_watermarks(self):
        self._add_prep("2026-10-20", "dragons", {"a": 1}, "2026-10-20 17:00:00")
        self._add_prep("2026-10-21", "dragons", {"a": 2}, "2026-10-21 17:00:00")
        self.assertEqual(assets_db.list_prep_asset_dates(db_path=self.db_path),
                         ["2026-10-20", "2026-10-21"])
        marks = assets_db.prep_watermarks(db_path=self.db_path)
        self.assertTrue(marks["2026-10-20"].startswith("2026-10-20 17:00:00|1|"))


if __name__ == "__main__":
    unittest.main()
