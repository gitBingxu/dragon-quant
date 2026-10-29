"""``assets purge-derived``：dry-run / 单事务复核 / 保护边界 / 进程锁 / 云端零操作。

全部使用临时 SQLite；**绝不** 对真实 assets.db 执行 apply。
"""

from __future__ import annotations

import io
import json
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.factor import maintenance

TARGET = sorted(maintenance.TARGET_FACTOR_NAMES)


def _seed(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        assets_db._ensure_schema(conn)
        conn.execute(
            "INSERT INTO prep_snapshots(trade_date, source, result_json) "
            "VALUES ('2026-10-19','v2','{}'), ('2026-10-18','v2','{}')")
        conn.execute(
            "INSERT INTO prep_assets(trade_date, asset_type, payload_json) "
            "VALUES ('2026-10-19','dragons','{}')")
        conn.execute(
            "INSERT INTO auction_assets(trade_date, code, name, items_json) "
            "VALUES ('2026-10-19','600001','甲','[]')")
        conn.execute(
            "INSERT INTO auction_snapshots(trade_date, code, source, analysis_json) "
            "VALUES ('2026-10-19','600001','v2','{}')")
        for name in TARGET:
            conn.execute(
                "INSERT INTO factor_registry(factor_name, factor_version, "
                "frequency, source, direction, available_phase, pit_rule, "
                "spec_hash) VALUES (?, 'v1', '1d', 'prep', 'higher_better', "
                "'prep', 'derived', 'h')", (name,))
            conn.execute(
                "INSERT INTO factor_values(trade_date, code, factor_name, "
                "factor_version, value, available_at, source) "
                "VALUES ('2026-10-19','600001',?,'v1',1.0,"
                "'2026-10-19T16:40:00','prep')",
                (name,))
        conn.execute(
            "INSERT INTO factor_registry(factor_name, factor_version, "
            "frequency, source, direction, available_phase, pit_rule, spec_hash) "
            "VALUES ('five_dim_composite','v1','1d','scan','higher_better',"
            "'close','derived','h')")
        conn.execute(
            "INSERT INTO factor_values(trade_date, code, factor_name, "
            "factor_version, value, available_at, source) "
            "VALUES ('2026-10-19','600001','five_dim_composite','v1',88.0,"
            "'2026-10-19T15:30:00','scan')")
        conn.execute(
            "INSERT INTO factor_build_runs(trade_date, report_json) "
            "VALUES ('2026-10-19','{}')")
        conn.execute(
            "INSERT INTO factor_model_snapshots(model_name, model_version, "
            "params_json, factor_versions_json) VALUES "
            "('m_target','v1','{}', ?), ('m_other','v1','{}', ?), "
            "('m_mixed','v1','{}', ?)",
            (json.dumps({TARGET[0]: "v1"}),
             json.dumps({"five_dim_composite": "v1"}),
             json.dumps({TARGET[0]: "v1", "five_dim_composite": "v1"})))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def seeded(tmp_path):
    path = tmp_path / "assets.db"
    _seed(path)
    return path


def _counts(path: Path, table: str, where: str = "", params: tuple = ()) -> int:
    conn = sqlite3.connect(path)
    try:
        sql = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def test_target_names_equal_registry_prep_plus_retired():
    assert set(maintenance.RETIRED_FACTOR_NAMES) <= set(TARGET)
    assert set(maintenance.REBUILD_FACTOR_NAMES) == set(TARGET) - set(
        maintenance.RETIRED_FACTOR_NAMES)
    assert len(TARGET) == 25


def test_dry_run_counts_and_protected(seeded):
    plan = maintenance.plan_purge(str(seeded))
    assert plan.db_exists is True
    assert plan.counts["prep_snapshots"]["rows"] == 2
    assert plan.counts["factor_values"]["rows"] == len(TARGET)
    assert plan.counts["factor_registry"]["rows"] == len(TARGET)
    assert plan.counts["factor_model_snapshots"]["rows"] == 2
    mixed = [m for m in plan.models if m["model_name"] == "m_mixed"][0]
    assert mixed["mixed"] is True
    assert mixed["matched_factors"] == [TARGET[0]]
    assert plan.protected["prep_assets"] == 1
    assert plan.protected["auction_assets"] == 1
    assert plan.protected["auction_snapshots"] == 1
    assert plan.protected["factor_values_other"] == 1
    assert plan.protected["factor_registry_other"] == 1
    assert plan.protected["factor_build_runs"] == 1
    assert plan.protected["factor_model_snapshots_other"] == 1
    assert plan.rebuild_steps
    # dry-run 只读：目标行原样存在
    assert _counts(seeded, "prep_snapshots") == 2


def test_missing_db_is_dry_run_zero_counts(tmp_path):
    plan = maintenance.plan_purge(str(tmp_path / "absent.db"))
    assert plan.db_exists is False
    assert plan.counts["prep_snapshots"]["rows"] == 0
    assert not (tmp_path / "absent.db").exists()


def test_apply_deletes_targets_only(seeded):
    plan = maintenance.plan_purge(str(seeded))
    result = maintenance.apply_purge(plan)
    assert result["ok"] is True
    assert result["deleted"]["prep_snapshots"] == 2
    assert result["deleted"]["factor_values"] == len(TARGET)
    assert result["deleted"]["factor_registry"] == len(TARGET)
    assert result["deleted"]["factor_model_snapshots"] == 2
    assert result["model_rebuild_required"] is True
    assert _counts(seeded, "prep_snapshots") == 0
    assert _counts(seeded, "prep_assets") == 1
    assert _counts(seeded, "auction_assets") == 1
    assert _counts(seeded, "auction_snapshots") == 1
    assert _counts(seeded, "factor_build_runs") == 1
    assert _counts(seeded, "factor_values", "factor_name='five_dim_composite'") == 1
    assert _counts(seeded, "factor_registry", "factor_name='five_dim_composite'") == 1
    assert _counts(seeded, "factor_model_snapshots") == 1


def test_apply_rolls_back_when_counts_changed(seeded):
    plan = maintenance.plan_purge(str(seeded))
    conn = sqlite3.connect(seeded)
    try:
        conn.execute(
            "INSERT INTO factor_values(trade_date, code, factor_name, "
            "factor_version, value, available_at, source) "
            "VALUES ('2026-10-19','600002',?,'v1',2.0,"
            "'2026-10-19T16:40:00','prep')",
            (TARGET[0],))
        conn.commit()
    finally:
        conn.close()
    result = maintenance.apply_purge(plan)
    assert result["ok"] is False
    assert result["reason"] == "counts_changed"
    assert _counts(seeded, "prep_snapshots") == 2
    assert _counts(seeded, "factor_values") == len(TARGET) + 2


def test_malformed_model_json_is_kept_with_warning(seeded):
    conn = sqlite3.connect(seeded)
    try:
        conn.execute(
            "INSERT INTO factor_model_snapshots(model_name, model_version, "
            "params_json, factor_versions_json) VALUES ('m_bad','v1','{}','{bad')")
        conn.commit()
    finally:
        conn.close()
    plan = maintenance.plan_purge(str(seeded))
    assert any(w["reason"] == "malformed_json" for w in plan.warnings)
    result = maintenance.apply_purge(plan)
    assert result["ok"] is True
    assert _counts(seeded, "factor_model_snapshots",
                   "model_name='m_bad'") == 1


def _dispatch(monkeypatch, tmp_path, **kwargs):
    from dragon_quant import commands

    db_path = tmp_path / "assets.db"
    monkeypatch.setattr(
        "dragon_quant.infra.storage.paths.ASSETS_DB_PATH", str(db_path))
    args = SimpleNamespace(assets_action="purge-derived", json=True,
                           apply_purge=False, yes=False)
    for key, value in kwargs.items():
        setattr(args, key, value)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            commands.dispatch("assets", args)
        except SystemExit as exc:
            return exc.code, json.loads(out.getvalue() or "{}"), err.getvalue()
    return None, json.loads(out.getvalue() or "{}"), err.getvalue()


def test_cli_apply_without_yes_exits_2_and_writes_nothing(monkeypatch, seeded, tmp_path):
    code, payload, _ = _dispatch(
        monkeypatch, tmp_path, apply_purge=True, yes=False)
    assert code == 2
    assert payload["ok"] is False
    assert _counts(seeded, "prep_snapshots") == 2


def test_cli_dry_run_default(monkeypatch, seeded, tmp_path):
    code, payload, _ = _dispatch(monkeypatch, tmp_path)
    assert code == 0
    assert payload["dry_run"] is True
    assert payload["applied"] is False
    assert payload["counts"]["prep_snapshots"]["rows"] == 2
    assert _counts(seeded, "prep_snapshots") == 2


def test_cli_lock_busy_exits_1_zero_writes(monkeypatch, seeded, tmp_path):
    from dragon_quant.infra.assets.sync.state import ProcessLock, default_paths

    lock = ProcessLock(default_paths().lock_path)
    lock.acquire()
    try:
        code, payload, _ = _dispatch(
            monkeypatch, tmp_path, apply_purge=True, yes=True)
    finally:
        lock.release()
    assert code == 1
    assert payload["ok"] is False
    assert payload["failed"][0]["kind"] == "lock"
    assert _counts(seeded, "prep_snapshots") == 2


def test_purge_module_does_not_import_cloud_sync():
    source = Path(maintenance.__file__).read_text(encoding="utf-8")
    assert "assets.sync.service" not in source
    assert "provider" not in source
    assert "requests" not in source
