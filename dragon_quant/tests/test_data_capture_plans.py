"""事前母集构建：登记、缺口、不可变修订（W0 KR8 的计划侧）。"""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from dragon_quant.data_capture import (
    CaptureError,
    build_plan_payload,
    freeze_plan,
)
from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.assets import observations as store
from dragon_quant.tests.support import patch_storage_dbs

NOW = "2026-11-18T09:10:00+08:00"
TRADE_DATE = "2026-11-18"


@contextmanager
def _seed(tmp_path, *, dragons=True, prep=True, previous=None):
    assets_path = str(tmp_path / "assets.db")
    with patch_storage_dbs(str(tmp_path / "dragon.db"), assets_path):
        conn = assets_db._connect()
        assets_db._ensure_schema(conn)
        if dragons:
            conn.executemany(
                "INSERT INTO dragons(trade_date, code, name, review_status, source) "
                "VALUES (?, ?, ?, 'pending', 'v2')",
                [("2026-11-17", "600001", "甲"), ("2026-11-14", "600002", "乙")],
            )
        if prep:
            payload = {"review_candidates": [
                {"code": "600003", "name": "丙", "position_pct": 0},
                {"code": "600004", "name": "丁", "eligibility": None},
            ]}
            conn.execute(
                "INSERT INTO prep_snapshots(trade_date, source, result_json) "
                "VALUES ('2026-11-17', 'v2', ?)",
                (json.dumps(payload, ensure_ascii=False),),
            )
        conn.commit()
        conn.close()
        if previous is not None:
            store.save_plan(trade_date="2026-11-17", pool={"gaps": []},
                            members=previous, task_config={"topics": []},
                            config_version="v1",
                            created_at="2026-11-17T09:00:00+08:00")
        yield assets_path


def test_plan_registers_all_local_pools_and_carries_pending(tmp_path):
    previous_member = {
        "code": "600005", "name": "戊",
        "first_seen_at": "2026-11-10T09:00:00+08:00",
        "entry_reason": "dragon:2026-11-10",
        "pool_sources": ["dragons:2026-11-10"],
        "track_until": "2026-12-01",
        "coverage_gaps": [],
    }
    with _seed(tmp_path, previous=[previous_member]):
        payload = build_plan_payload(
            trade_date=TRADE_DATE, now=NOW,
            holdings_codes=["600006"], codes=["600007"],
            track_days=10)
    members = {item["code"]: item for item in payload["members"]}
    assert set(members) == {"600001", "600002", "600003", "600004", "600005",
                            "600006", "600007"}
    assert members["600003"]["entry_reason"] == "prep:2026-11-17"
    assert members["600006"]["entry_reason"] == "holdings"
    assert members["600007"]["entry_reason"] == "explicit"
    # 待成熟对象承接原始首见时间，且来源可追溯。
    assert members["600005"]["first_seen_at"] == "2026-11-10T09:00:00+08:00"
    assert "pending:cp_20261117_r1" in members["600005"]["pool_sources"]
    assert payload["pool"]["gaps"] == []
    assert payload["pool"]["prep"]["candidates"] == 2
    assert payload["pool"]["dragons"]["dates"] == [
        "2026-11-14", "2026-11-17"]
    # 截断上游候选不会凭空补齐；这里登记的是真实读取数量。
    assert payload["task_config"]["topics"]


def test_plan_records_gaps_and_never_zero_fills(tmp_path):
    with _seed(tmp_path, dragons=False, prep=False):
        payload = build_plan_payload(trade_date=TRADE_DATE, now=NOW)
    reasons = {gap["reason"] for gap in payload["pool"]["gaps"]}
    assert "pool_empty" in reasons          # dragons
    assert "prep_snapshot_missing" in reasons
    assert "plan_empty" in reasons
    assert payload["members"] == []


def test_plan_revision_is_append_only_and_idempotent(tmp_path):
    with _seed(tmp_path):
        payload = build_plan_payload(trade_date=TRADE_DATE, now=NOW,
                                     codes=["600010"])
        first = freeze_plan(payload, now=NOW)
        same = freeze_plan(payload, now=NOW)
        assert first["created"] is True
        assert same["created"] is False
        assert same["plan"]["plan_id"] == first["plan"]["plan_id"]

        revised = build_plan_payload(trade_date=TRADE_DATE, now=NOW,
                                     codes=["600010", "600011"])
        second = freeze_plan(revised, now=NOW)
        assert second["created"] is True
        assert second["plan"]["revision"] == 2
        assert second["plan"]["parent_plan_id"] == first["plan"]["plan_id"]
        # 旧版本保持原样，不被覆盖（新代码只出现在新 revision）。
        original = store.get_plan(first["plan"]["plan_id"])
        original_codes = [item["code"] for item in original["members"]]
        assert "600010" in original_codes
        assert "600011" not in original_codes


def test_plan_rejects_bad_date_and_unknown_topic(tmp_path):
    with _seed(tmp_path):
        with pytest.raises(CaptureError) as bad_date:
            build_plan_payload(trade_date="2026/11/18", now=NOW)
        assert bad_date.value.reason == "invalid_trade_date"
        with pytest.raises(CaptureError) as bad_topic:
            build_plan_payload(trade_date=TRADE_DATE, now=NOW,
                               topics=["buy_signal"])
        assert bad_topic.value.reason == "unknown_topic"


def test_corrupt_prep_snapshot_is_a_gap_not_a_crash(tmp_path):
    with _seed(tmp_path, prep=False):
        conn = assets_db._connect()
        conn.execute(
            "INSERT INTO prep_snapshots(trade_date, source, result_json) "
            "VALUES ('2026-11-17', 'v2', '{not-json')")
        conn.commit()
        conn.close()
        payload = build_plan_payload(trade_date=TRADE_DATE, now=NOW)
    reasons = [gap["reason"] for gap in payload["pool"]["gaps"]]
    assert any(reason.startswith("prep_snapshot_corrupt") for reason in reasons)


def test_carried_member_keeps_its_predeclared_track_deadline(tmp_path):
    previous_member = {
        "code": "600005", "name": "戊",
        "first_seen_at": "2026-11-10T09:00:00+08:00",
        "entry_reason": "dragon:2026-11-10",
        "pool_sources": ["dragons:2026-11-10"],
        "track_until": "2026-12-01",
        "coverage_gaps": [],
    }
    with _seed(tmp_path, previous=[previous_member]):
        payload = build_plan_payload(trade_date=TRADE_DATE, now=NOW,
                                     track_days=10)
    carried = next(item for item in payload["members"]
                   if item["code"] == "600005")
    # 事前评价截止不被后一日的默认期限延长或缩短。
    assert carried["track_until"] == "2026-12-01"
    assert carried["entry_reason"] == "dragon:2026-11-10"


def test_unreadable_dragons_pool_is_an_error_not_an_empty_pool(tmp_path, monkeypatch):
    from dragon_quant.infra.assets import db as assets_db
    import sqlite3

    class BrokenConnection:
        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("disk I/O error")

        def executescript(self, *args, **kwargs):
            raise sqlite3.OperationalError("disk I/O error")

        def close(self):
            pass

    monkeypatch.setattr(
        assets_db, "_connect",
        lambda *args, **kwargs: BrokenConnection())

    payload = build_plan_payload(trade_date=TRADE_DATE, now=NOW)
    reasons = {gap["reason"] for gap in payload["pool"]["gaps"]}
    assert "pool_unreadable" in reasons
    assert "pool_empty" not in reasons
