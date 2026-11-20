"""观测仓储 / 租约 fencing / 同步注册（W0 KR8 的存储侧）。"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from dragon_quant.data_capture.coverage import build_coverage
from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.assets import observations as store
from dragon_quant.infra.assets import schema
from dragon_quant.infra.assets.sync import tables
from dragon_quant.infra.assets.sync.sources import build_sources
from dragon_quant.tests.support import patch_storage_dbs

NOW = "2026-11-18T09:26:00+08:00"
TRADE_DATE = "2026-11-18"


@contextmanager
def _store(tmp_path):
    with patch_storage_dbs(str(tmp_path / "dragon.db"),
                           str(tmp_path / "assets.db")) as stack:
        conn = assets_db._connect()
        assets_db._ensure_schema(conn)
        conn.close()
        yield stack


def _observation(payload, *, source="tdx"):
    return store.append_observation(
        trade_date=TRADE_DATE, market="sh", code="600001",
        obs_type="auction_path", source=source, payload=payload,
        received_at=NOW, available_at=NOW,
        units={"price": "yuan"}, quality="semantics_unverified")


def _bar(close, *, source="tdx", adjustment="none", market="sh"):
    return store.append_bar(
        trade_date=TRADE_DATE, market=market, code="600001", period="1min",
        interval_start="2026-11-18T09:24:00+08:00",
        interval_end="2026-11-18T09:25:00+08:00",
        open=10.0, high=10.1, low=9.9, close=close, volume=1.0, amount=10.0,
        source=source, observed_at=NOW, available_at=NOW, completeness="final",
        adjustment=adjustment)


def test_observation_dedupes_same_content_and_appends_revision(tmp_path):
    with _store(tmp_path):
        first = _observation({"items": [{"time": "09:20", "price": 10.5}]})
        same = _observation({"items": [{"time": "09:20", "price": 10.5}]})
        assert first["status"] == "written"
        assert same["status"] == "deduped"
        assert same["obs_id"] == first["obs_id"]

        changed = _observation({"items": [{"time": "09:20", "price": 10.6}]})
        assert changed["status"] == "written"
        assert changed["revision"] == 2

        rows = store.query_observations()
        assert [row["revision"] for row in rows] == [1, 2]
        # 同一语义观测保留最早收到时间；新内容用新的 available_at。
        assert {row["first_received_at"] for row in rows} == {NOW}
        assert len({row["content_hash"] for row in rows}) == 2


def test_bar_versions_are_immutable_and_revisioned(tmp_path):
    with _store(tmp_path):
        first = _bar(10.05)
        same = _bar(10.05)
        assert first["status"] == "written"
        assert same["status"] == "deduped"
        changed = _bar(10.06)
        assert changed["status"] == "written"
        assert changed["revision"] == 2
        rows = store.query_bars()
        assert len(rows) == 2
        assert {row["revision"] for row in rows} == {1, 2}
        assert all(row["adjustment"] == "none" for row in rows)


def _run():
    return store.ensure_run(
        plan_id="cp_20261118_r1", trade_date=TRADE_DATE, phase="auction",
        slot="0926", deadline_at=None, created_at=NOW)


def test_lease_fencing_rejects_stale_and_expired_workers(tmp_path):
    with _store(tmp_path):
        run = _run()
        token_a = store.acquire_lease(run["run_id"], owner="a",
                                      ttl_seconds=60, now=NOW)
        assert token_a == 1
        store.start_run(run["run_id"], owner="a", token=token_a,
                        started_at=NOW)
        assert store.acquire_lease(run["run_id"], owner="b", ttl_seconds=60,
                                   now=NOW) is None
        # 租约过期后新 worker 可以抢占，并拿到更大的 fencing token。
        token_b = store.acquire_lease(
            run["run_id"], owner="b", ttl_seconds=60,
            now="2026-11-18T09:27:30+08:00")
        assert token_b == 2

        with pytest.raises(store.LeaseLostError):
            store.finalize_run(run["run_id"], owner="a", token=token_a,
                               status="complete", report={},
                               finished_at="2026-11-18T09:27:40+08:00")
        store.finalize_run(run["run_id"], owner="b", token=token_b,
                           status="complete", report={"ok": True},
                           finished_at="2026-11-18T09:27:41+08:00")
        row = store.get_run(run["run_id"])
        assert row["status"] == "complete"
        assert row["lease_owner"] is None
        # 提交后 token 已推进：重复提交同样被拒绝。
        with pytest.raises(store.LeaseLostError):
            store.finalize_run(run["run_id"], owner="b", token=token_b,
                               status="failed", report={},
                               finished_at="2026-11-18T09:27:42+08:00")


def test_expired_lease_cannot_commit_even_with_matching_token(tmp_path):
    with _store(tmp_path):
        run = _run()
        token = store.acquire_lease(run["run_id"], owner="a",
                                    ttl_seconds=1, now=NOW)
        with pytest.raises(store.LeaseLostError):
            store.finalize_run(run["run_id"], owner="a", token=token,
                               status="complete", report={},
                               finished_at="2026-11-18T09:26:05+08:00")


def test_capture_tables_are_registered_for_sync_without_lease_columns():
    assert len(schema.TABLE_NAMES) == 19
    assert {"capture_plans", "capture_runs", "market_observations",
            "market_bar_versions"} <= set(schema.TABLE_NAMES)
    spec_tables = {spec.table for spec in tables.TABLE_SPECS}
    assert spec_tables <= set(schema.TABLE_NAMES)
    assert spec_tables | {"prep_assets", "auction_assets"} == set(
        schema.TABLE_NAMES)
    run_spec = tables.spec_for("capture", "runs")
    assert "lease_owner" not in run_spec.columns
    assert "lease_token" not in run_spec.columns
    assert "lease_expires_at" not in run_spec.columns
    assert any(source.name == "capture" for source in build_sources())


def test_sync_restore_keeps_rows_and_never_restores_an_active_lease(tmp_path):
    source_path = str(tmp_path / "assets.db")
    target_path = str(tmp_path / "assets-restored.db")
    with patch_storage_dbs(str(tmp_path / "dragon.db"), source_path):
        conn = assets_db._connect()
        assets_db._ensure_schema(conn)
        conn.close()
        _observation({"items": [{"time": "09:20", "price": 10.5}]})
        _bar(10.05)
        run = _run()
        store.acquire_lease(run["run_id"], owner="a", ttl_seconds=60, now=NOW)
        store.finalize_run(run["run_id"], owner="a", token=1,
                           status="complete", report={"ok": True},
                           finished_at="2026-11-18T09:26:30+08:00")
        source = tables.CaptureAssetSource(db_path=source_path)
        batch = source.read_partition(TRADE_DATE)
        assert not batch.failed
        assert {shard.asset_type for shard in batch.shards} == {
            "runs", "observations", "bars"}

        conn = assets_db._connect(path=target_path)
        assets_db._ensure_schema(conn)
        conn.close()
        target = tables.CaptureAssetSource(db_path=target_path)
        report = target.restore(TRADE_DATE, list(batch.shards))
        assert not report.conflicts, report.conflicts
        assert report.rows_inserted > 0
        rows = store.query_observations(db_path=target_path)
        assert len(rows) == 1
        restored_run = store.get_run(run["run_id"], db_path=target_path)
        assert restored_run["status"] == "complete"
        # 租约是本地协调状态：恢复后不得变回活跃锁。
        assert restored_run["lease_owner"] is None
        assert restored_run["lease_token"] == 0
        assert restored_run["lease_expires_at"] is None


def test_coverage_is_read_only_and_never_creates_the_database(tmp_path):
    def _no_create(create=True, path=None):
        if not create:
            return None
        raise AssertionError("只读覆盖视图不得创建 assets.db")

    with patch("dragon_quant.infra.assets.db._connect", side_effect=_no_create):
        payload = build_coverage()
    assert payload["plans"] == []
    assert payload["observations"] == []
    assert payload["gaps"] == []
    assert not (tmp_path / "assets.db").exists()


def test_mark_run_never_overwrites_a_run_with_an_active_lease(tmp_path):
    with _store(tmp_path):
        run = _run()
        store.acquire_lease(run["run_id"], owner="a", ttl_seconds=60, now=NOW)
        store.mark_run(run["run_id"], status="missed",
                       report={"reason": "slot_window_passed"}, finished_at=NOW)
        row = store.get_run(run["run_id"])
        # 活跃 worker 的运行行不被诊断写入覆盖。
        assert row["status"] == "planned"
        assert row["lease_owner"] == "a"


def test_mark_run_never_overwrites_a_finalized_report(tmp_path):
    with _store(tmp_path):
        run = _run()
        token = store.acquire_lease(run["run_id"], owner="a", ttl_seconds=60,
                                    now=NOW)
        store.start_run(run["run_id"], owner="a", token=token,
                        started_at=NOW)
        store.finalize_run(run["run_id"], owner="a", token=token,
                           status="complete", report={"topics": []},
                           finished_at=NOW)
        before = store.get_run(run["run_id"])
        # R5：迟到的诊断写入不得覆盖已完成的报告（finished_at/status 双 CAS）。
        written = store.mark_run(run["run_id"], status="missed",
                                 report={"reason": "late_diagnostic"},
                                 finished_at="2026-11-18T09:30:00+08:00")
        after = store.get_run(run["run_id"])
        assert written is False
        assert after["status"] == "complete"
        assert after["report"] == before["report"]


def test_mark_run_records_diagnostic_on_a_not_started_run(tmp_path):
    with _store(tmp_path):
        run = _run()
        # 合法未开始（无 lease、无 finished_at、非终态）仍可记录。
        written = store.mark_run(run["run_id"], status="missed",
                                 report={"reason": "slot_window_passed"},
                                 finished_at=NOW)
        assert written is True
        assert store.get_run(run["run_id"])["status"] == "missed"


def test_source_and_adjustment_are_part_of_the_identity(tmp_path):
    with _store(tmp_path):
        first = _observation({"items": [{"price": 10.0}]}, source="tdx")
        other_source = _observation({"items": [{"price": 10.0}]}, source="ths")
        # 不同来源不得共用 revision（语义不同）。
        assert first["revision"] == 1
        assert other_source["revision"] == 1
        assert first["obs_id"] != other_source["obs_id"]

        raw = _bar(10.0, adjustment="none")
        adjusted = _bar(10.0, adjustment="qfq", source="other")
        assert raw["revision"] == 1
        assert adjusted["revision"] == 1
        assert raw["bar_id"] != adjusted["bar_id"]
        assert len(store.query_bars()) == 2
