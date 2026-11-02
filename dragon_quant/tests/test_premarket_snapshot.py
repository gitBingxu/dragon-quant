"""premarket.snapshot 只读 Adapter 测试（阶段 3 Task 1）。

覆盖：``mode=ro`` 零 DDL / 零 sidecar、缺库不建库、缺表不建表、完整
prep / auction 快照与 meta、严格早于 T、损坏 / 旧 schema / 09:25 / 周末。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

SHANGHAI = timezone(timedelta(hours=8))
T = "2026-10-30"
T_MINUS_1 = "2026-10-29"


def _prep_payload(trade_date: str = T_MINUS_1, *, version: str = "0.8",
                  frozen_at: str | None = "2026-10-29T16:40:00+08:00",
                  review: dict | None = None,
                  review_candidates: list | None = None) -> dict:
    payload = {
        "trade_date": trade_date,
        "version": version,
        "sentiment": {"up_cnt": 2100, "down_cnt": 950},
        "candidates": [{"code": "600001", "facts": {"limit_up_type": "换手板"}}],
        "review_candidates": list(review_candidates or []),
    }
    if review is not None:
        payload["review"] = review
    if frozen_at is not None:
        payload["strategy_context"] = {"frozen_at": frozen_at, "positions": [],
                                       "feedback_plans": []}
    return payload


def _auction_payload(code: str = "600001", *, legacy: bool = False,
                     is_weak: bool = False, version: str = "2") -> dict:
    payload = {
        "version": version,
        "code": code,
        "is_weak": is_weak,
        "strength": 69.9,
        "level": "中",
        "direction": "neutral",
        "rank": 1,
        "dims": {},
        "reject_reason": "high_green" if is_weak else None,
        "unverified": False,
        "rank_reason": None,
        "pre_veto_strength": 69.9,
        "validation_status": "baseline_unvalidated",
        "config_version": "auction_v2_baseline",
        "config_hash": "hash-a",
        "semantics": {"semantics_version": "auction_semantics_v1"},
        "raw_factors": {"official_open_gap_pct": 0.0},
        "evidence": {"last_time": "09:25:00"},
        "reference_prices": {"prev_close": 10.0, "official_open": 10.4},
        "windows": {"core": ["09:20:00", "09:25:00"]},
        "actions": [{"kind": "await_confirmation", "requires": "post_open"}],
        "risk_events": [],
    }
    if not legacy:
        payload["data_quality"] = {
            "status": "ok", "completeness": 1.0,
            "reason_codes": [], "reasons": []}
    return payload


def _create_db(path: Path, *, prep_rows=(), auction_rows=(), dragon_rows=(),
               prep_table: bool = True, auction_table: bool = True,
               dragon_table: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        with conn:
            if prep_table:
                conn.execute(
                    "CREATE TABLE prep_snapshots (trade_date TEXT NOT NULL, "
                    "source TEXT NOT NULL, result_json TEXT NOT NULL, "
                    "created_at TEXT, PRIMARY KEY (trade_date, source))")
            if auction_table:
                conn.execute(
                    "CREATE TABLE auction_snapshots (trade_date TEXT NOT NULL, "
                    "code TEXT NOT NULL, source TEXT NOT NULL, "
                    "analysis_json TEXT NOT NULL, created_at TEXT, "
                    "PRIMARY KEY (trade_date, code, source))")
            if dragon_table:
                conn.execute(
                    "CREATE TABLE dragons (trade_date TEXT NOT NULL, "
                    "code TEXT NOT NULL, PRIMARY KEY (trade_date, code))")
            for row in prep_rows:
                conn.execute(
                    "INSERT INTO prep_snapshots VALUES (?, ?, ?, ?)", row)
            for row in auction_rows:
                conn.execute(
                    "INSERT INTO auction_snapshots VALUES (?, ?, ?, ?, ?)", row)
            for row in dragon_rows:
                conn.execute("INSERT INTO dragons VALUES (?, ?)", row)
    finally:
        conn.close()
    return path


def _prep_row(trade_date: str, payload: dict, created_at: str = "2026-10-29 16:52:11"):
    return (trade_date, "v2", json.dumps(payload, ensure_ascii=False), created_at)


def _auction_row(trade_date: str, code: str, payload: dict,
                 created_at: str = "2026-10-30 09:26:01"):
    return (trade_date, code, "v2", json.dumps(payload, ensure_ascii=False), created_at)


def _now(hour: int = 9, minute: int = 26, day: int = 30) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=SHANGHAI)


class SnapshotTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "data" / "assets.db"
        self._patch = patch("dragon_quant.infra.assets.db.ASSETS_DB_PATH",
                            str(self.db_path))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def files(self) -> list[str]:
        return sorted(p.name for p in self.db_path.parent.glob("*")) \
            if self.db_path.parent.exists() else []

    def sqlite_master(self) -> list[str]:
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            conn.execute("SELECT 1")
        except sqlite3.OperationalError:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro&immutable=1", uri=True)
        try:
            return [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        finally:
            conn.close()


class TestReadonlyStorage(SnapshotTestBase):
    def test_readonly_reads_leave_schema_and_siblings_untouched(self):
        from dragon_quant.infra.assets import db as assets_db
        from dragon_quant.infra.assets import dragons_mirror

        _create_db(
            self.db_path,
            prep_rows=[_prep_row(T_MINUS_1, _prep_payload())],
            auction_rows=[_auction_row(T, "600001", _auction_payload())],
            dragon_rows=[(T_MINUS_1, "600001")],
        )
        before = self.sqlite_master()
        files_before = self.files()

        assets_db.get_previous_prep_snapshot(T, readonly=True)
        assets_db.list_auction_snapshots(T, with_meta=True, readonly=True)
        dragons_mirror.list_dragon_trade_dates(readonly=True)

        self.assertEqual(self.sqlite_master(), before)
        self.assertEqual(self.files(), files_before)
        self.assertNotIn("assets.db-wal", self.files())
        self.assertNotIn("assets.db-shm", self.files())

    def test_readonly_never_calls_the_writing_connect(self):
        from dragon_quant.review_account.premarket import snapshot

        _create_db(self.db_path)
        with patch("dragon_quant.infra.assets.db._connect",
                   side_effect=AssertionError("write path used")):
            snapshot.load_prep(T)
            snapshot.load_auction(T, now=_now(), planned_codes=set())

    def test_missing_database_is_not_created(self):
        from dragon_quant.review_account.premarket import snapshot

        result = snapshot.load_prep(T)
        self.assertFalse(self.db_path.exists())
        self.assertFalse(self.db_path.parent.exists())
        self.assertEqual(result["state"], "missing")
        self.assertFalse(result["available"])

    def test_readonly_reads_a_wal_database(self):
        from dragon_quant.review_account.premarket import snapshot

        _create_db(self.db_path, prep_rows=[_prep_row(T_MINUS_1, _prep_payload())])
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.close()
        finally:
            pass
        before = self.sqlite_master()
        prep = snapshot.load_prep(T)
        self.assertTrue(prep["available"])
        self.assertEqual(prep["state"], "ready")
        self.assertEqual(self.sqlite_master(), before)

    def test_missing_tables_never_create_ddl(self):
        from dragon_quant.review_account.premarket import snapshot

        _create_db(self.db_path, prep_table=False, auction_table=False,
                   dragon_table=False)
        before = self.sqlite_master()
        prep = snapshot.load_prep(T)
        auction = snapshot.load_auction(T, now=_now(), planned_codes=set())
        self.assertEqual(prep["state"], "missing")
        self.assertEqual(auction["state"], "missing")
        self.assertEqual(self.sqlite_master(), before)


class TestPrepSnapshot(SnapshotTestBase):
    def test_picks_latest_strictly_before_and_embeds_full_result(self):
        from dragon_quant.review_account.premarket import snapshot

        old = _prep_payload("2026-10-28", frozen_at="2026-10-28T16:40:00+08:00")
        review_candidates = [{"code": "600001", "eligibility": "eligible",
                              "position_pct": 0.04}]
        latest = _prep_payload(T_MINUS_1, review_candidates=review_candidates)
        latest["review"] = {
            "enabled": True, "config_version": "review_baseline_v1",
            "config_hash": "cfg-hash", "review_version": "review_v1",
            "market": {"state": "strong"}, "mainlines": [],
            "review_candidates": review_candidates,
        }
        current = _prep_payload(T, frozen_at="2026-10-30T16:40:00+08:00")
        future = _prep_payload("2026-11-02", frozen_at="2026-11-02T16:40:00+08:00")
        _create_db(self.db_path, prep_rows=[
            _prep_row("2026-10-28", old),
            _prep_row(T_MINUS_1, latest),
            _prep_row(T, current),
            _prep_row("2026-11-02", future),
        ])

        result = snapshot.load_prep(T)

        self.assertTrue(result["available"])
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["trade_date"], T_MINUS_1)
        self.assertEqual(result["created_at"], "2026-10-29 16:52:11")
        self.assertEqual(result["frozen_at"], "2026-10-29T16:40:00+08:00")
        self.assertIsNone(result["frozen_at_reason"])
        self.assertEqual(result["schema_version"], "0.8")
        self.assertTrue(result["schema_supported"])
        self.assertTrue(result["review_available"])
        self.assertEqual(result["review_config"]["config_version"],
                         "review_baseline_v1")
        self.assertEqual(result["result"]["review"]["market"], {"state": "strong"})
        self.assertEqual(result["result"]["review_candidates"][0]["code"], "600001")
        self.assertEqual(result["result"]["candidates"][0]["facts"]["limit_up_type"],
                         "换手板")

    def test_corrupt_json_is_structured_and_never_raises(self):
        from dragon_quant.review_account.premarket import snapshot

        _create_db(self.db_path, prep_rows=[
            (T_MINUS_1, "v2", "{not-json", "2026-10-29 16:52:11")])
        result = snapshot.load_prep(T)
        self.assertFalse(result["available"])
        self.assertEqual(result["state"], "corrupt")
        self.assertEqual(result["trade_date"], T_MINUS_1)
        self.assertIsNone(result["result"])

    def test_unsupported_schema_still_embeds_and_marks_flag(self):
        from dragon_quant.review_account.premarket import snapshot

        _create_db(self.db_path, prep_rows=[
            _prep_row(T_MINUS_1, _prep_payload(version="0.6"))])
        result = snapshot.load_prep(T)
        self.assertTrue(result["available"])
        self.assertEqual(result["state"], "unsupported_schema")
        self.assertFalse(result["schema_supported"])
        self.assertEqual(result["schema_version"], "0.6")
        self.assertIsNotNone(result["result"])

    def test_missing_frozen_at_stays_null_with_reason(self):
        from dragon_quant.review_account.premarket import snapshot

        _create_db(self.db_path, prep_rows=[
            _prep_row(T_MINUS_1, _prep_payload(frozen_at=None))])
        result = snapshot.load_prep(T)
        self.assertIsNone(result["frozen_at"])
        self.assertEqual(result["frozen_at_reason"], "strategy_context_missing")

    def test_review_disabled_marks_unavailable(self):
        from dragon_quant.review_account.premarket import snapshot

        _create_db(self.db_path, prep_rows=[
            _prep_row(T_MINUS_1, _prep_payload(review={"enabled": False}))])
        result = snapshot.load_prep(T)
        self.assertTrue(result["available"])
        self.assertFalse(result["review_available"])
        self.assertEqual(result["review_config"], None)


class TestAuctionSnapshot(SnapshotTestBase):
    def test_ready_embeds_full_analysis_and_storage_meta(self):
        from dragon_quant.review_account.premarket import snapshot

        rows = [
            _auction_row(T, "600001", _auction_payload("600001"),
                         created_at="2026-10-30 09:26:01"),
            _auction_row(T, "600003", _auction_payload("600003"),
                         created_at="2026-10-30 09:26:04"),
        ]
        _create_db(self.db_path, auction_rows=rows)
        result = snapshot.load_auction(
            T, now=_now(), planned_codes={"600001", "600003"})

        self.assertTrue(result["available"])
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["as_of"], "2026-10-30T09:25:00+08:00")
        self.assertEqual(result["ready_at"], "2026-10-30T09:25:00+08:00")
        self.assertEqual(result["created_at"],
                         {"min": "2026-10-30 09:26:01",
                          "max": "2026-10-30 09:26:04"})
        self.assertEqual(result["cache"],
                         {"status": "unknown",
                          "reason": "not_persisted_in_snapshot"})
        self.assertEqual(result["config"]["config_versions"],
                         ["auction_v2_baseline"])
        self.assertEqual(result["config"]["semantics_versions"],
                         ["auction_semantics_v1"])
        self.assertFalse(result["config"]["mixed"])
        self.assertEqual(result["legacy_schema_codes"], [])
        self.assertEqual(result["corrupt_codes"], [])
        first = result["positions"][0]
        self.assertEqual(first["code"], "600001")
        self.assertTrue(first["planned"])
        self.assertTrue(first["usable"])
        self.assertNotIn("trade_date", first["analysis"])
        self.assertNotIn("created_at", first["analysis"])
        self.assertNotIn("error", first["analysis"])
        for key in ("risk_events", "data_quality", "raw_factors", "windows",
                    "actions", "reference_prices", "evidence", "semantics"):
            self.assertIn(key, first["analysis"])

    def test_before_0925_is_not_ready_but_rows_are_auditable(self):
        from dragon_quant.review_account.premarket import snapshot

        _create_db(self.db_path, auction_rows=[
            _auction_row(T, "600001", _auction_payload("600001"))])
        result = snapshot.load_auction(
            T, now=_now(9, 10), planned_codes={"600001"})
        self.assertFalse(result["available"])
        self.assertEqual(result["state"], "not_ready")
        self.assertTrue(result["pre_ready_rows_present"])
        self.assertFalse(result["positions"][0]["usable"])

    def test_weekend_is_missing(self):
        from dragon_quant.review_account.premarket import snapshot

        # 2026-10-31 is a Saturday.
        _create_db(self.db_path, auction_rows=[])
        result = snapshot.load_auction(
            "2026-10-31", now=datetime(2026, 10, 31, 10, 0, tzinfo=SHANGHAI),
            planned_codes=set())
        self.assertFalse(result["available"])
        self.assertEqual(result["state"], "missing")
        self.assertEqual(result["weekend"], True)

    def test_missing_rows_partial_corrupt_and_legacy(self):
        from dragon_quant.review_account.premarket import snapshot

        _create_db(self.db_path, auction_rows=[])
        missing = snapshot.load_auction(T, now=_now(), planned_codes=set())
        self.assertEqual(missing["state"], "missing")
        self.assertFalse(missing["available"])

        _create_db(self.db_path.with_name("partial.db"), auction_rows=[
            _auction_row(T, "600001", _auction_payload("600001")),
            (T, "600002", "v2", "{bad-json", "2026-10-30 09:26:02"),
        ])
        with patch("dragon_quant.infra.assets.db.ASSETS_DB_PATH",
                   str(self.db_path.with_name("partial.db"))):
            partial = snapshot.load_auction(T, now=_now(), planned_codes=set())
        self.assertEqual(partial["state"], "partial")
        self.assertTrue(partial["available"])
        self.assertEqual(partial["corrupt_codes"], ["600002"])
        self.assertFalse(partial["positions"][1]["usable"])

        _create_db(self.db_path.with_name("legacy.db"), auction_rows=[
            _auction_row(T, "600001", _auction_payload("600001", legacy=True))])
        with patch("dragon_quant.infra.assets.db.ASSETS_DB_PATH",
                   str(self.db_path.with_name("legacy.db"))):
            legacy = snapshot.load_auction(T, now=_now(), planned_codes=set())
        self.assertEqual(legacy["state"], "partial")
        self.assertEqual(legacy["legacy_schema_codes"], ["600001"])

    def test_config_mixed_and_created_at_missing(self):
        from dragon_quant.review_account.premarket import snapshot

        first = _auction_payload("600001")
        second = _auction_payload("600002")
        second["config_version"] = "auction_v2_alt"
        _create_db(self.db_path, auction_rows=[
            (T, "600001", "v2", json.dumps(first), None),
            (T, "600002", "v2", json.dumps(second), None),
        ])
        result = snapshot.load_auction(T, now=_now(), planned_codes=set())
        self.assertTrue(result["config"]["mixed"])
        self.assertEqual(result["created_at"], {"min": None, "max": None})


if __name__ == "__main__":
    unittest.main()
