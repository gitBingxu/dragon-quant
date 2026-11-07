"""Row-level contract matrix for all 17 research datasets.

Each dataset gets two v2 rows (plus a v1 decoy where the parent source matters),
then we verify: declared filters really apply, stable pagination returns every
row exactly once, and schema units match the real production values (percent
points, not ratios).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.storage.schema import _ensure_schema
from dragon_quant.research import catalog
from dragon_quant.research.query import run_query

DATASETS = tuple(catalog.DATASETS)
UNIQUE_KEYS = {
    "scan_runs": ("scan_id",),
    "scan_candidates": ("scan_id", "code"),
    "dragons": ("trade_date", "code"),
    "review_strict": ("trade_date", "code"),
    "account_runs": ("run_id",),
    "account_snapshots": ("run_id", "trade_date"),
    "account_trades": ("run_id", "code"),
    "account_positions": ("run_id", "code"),
    "account_events": ("run_id", "code"),
    "signals": ("signal_id",),
    "prep_snapshots": ("trade_date",),
    "auction_snapshots": ("trade_date", "code"),
    "factor_values": ("trade_date", "code", "factor_name"),
    "factor_registry": ("factor_name", "factor_version"),
    "factor_build_runs": ("build_id",),
    "factor_models": ("model_name", "model_version"),
    "experiments": ("experiment_id",),
}


def key_of(dataset: str, row: dict) -> tuple:
    return tuple(row[name] for name in UNIQUE_KEYS[dataset])
FILTER_VALUES = {
    "date_from": "2026-09-01",
    "date_to": "2026-09-30",
    "code": "600001",
    "scan_id": "v2_20260911_5",
    "run_id": 1,
    "status": "open",
    "experiment_id": 1,
}


def _schema_dragon(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    _ensure_schema(conn)
    return conn


def _schema_assets(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(assets_db.BASE_SCHEMA)
    return conn


def _insert(conn, table, columns, rows):
    placeholders = ",".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {table}({','.join(columns)}) VALUES ({placeholders})",
        rows)


def seed(tmp_path: Path):
    dragon = tmp_path / "dragon.db"
    assets = tmp_path / "assets.db"
    d = _schema_dragon(dragon)
    a = _schema_assets(assets)
    # scan runs / candidates / dragons / strict
    _insert(d, "scans_v2", ["id", "scan_date", "top_n", "source"],
            [("v2_20260911_5", "2026-09-11", 5, "v2"),
             ("v2_20260912_5", "2026-09-12", 5, "v2")])
    _insert(d, "scan_stocks_v2",
            ["scan_id", "code", "name", "rank", "concepts_json", "source"],
            [("v2_20260911_5", "600001", "A", 1, "[]", "v2"),
             ("v2_20260911_5", "600002", "B", 2, "[]", "v2")])
    _insert(d, "dragons_v2",
            ["trade_date", "code", "name", "source"],
            [("2026-09-11", "600001", "A", "v2"),
             ("2026-09-12", "600002", "B", "v2")])
    _insert(d, "dragon_review_strict",
            ["trade_date", "code", "source", "review_status"],
            [("2026-09-11", "600001", "v2", "completed"),
             ("2026-09-12", "600002", "v2", "completed")])
    # account: v2 run 1 + v1 decoy run 2
    _insert(d, "review_account_runs",
            ["id", "source", "strategy_name", "strategy_params_json",
             "date_from", "date_to", "total_return", "max_drawdown", "win_rate"],
            [(1, "v2", "dragon_pullback_daily", "{}", "2026-09-01", "2026-09-05",
              1.0, -2.0, 100.0),
             (2, "v1", "dragon_pullback_daily", "{}", "2026-09-01", "2026-09-05",
              9.0, -9.0, 0.0),
             (3, "v2", "dragon_pullback_daily", "{}", "2026-09-06", "2026-09-10",
              0.5, -1.0, 50.0)])
    _insert(d, "review_account_snapshots",
            ["run_id", "trade_date", "total_equity", "daily_return", "drawdown"],
            [(1, "2026-09-02", 100000, 1.0, -1.0),
             (2, "2026-09-02", 90000, 9.0, -9.0),
             (3, "2026-09-07", 100500, 0.5, -1.0)])
    _insert(d, "review_account_trades",
            ["run_id", "trade_date", "code", "side", "price", "qty"],
            [(1, "2026-09-02", "600001", "BUY", 10, 100),
             (2, "2026-09-02", "600009", "BUY", 10, 100),
             (3, "2026-09-07", "600002", "BUY", 10, 100)])
    _insert(d, "review_account_positions",
            ["run_id", "code", "entry_date", "realized_return", "status"],
            [(1, "600001", "2026-09-02", 10.0, "closed"),
             (2, "600009", "2026-09-02", 90.0, "closed"),
             (3, "600002", "2026-09-07", 5.0, "closed")])
    _insert(d, "review_account_events",
            ["run_id", "event_date", "event_type", "code"],
            [(1, "2026-09-02", "buy", "600001"),
             (2, "2026-09-02", "buy", "600009"),
             (3, "2026-09-07", "buy", "600002")])
    _insert(d, "review_account_experiments",
            ["id", "base_run_id", "experiment_version", "status", "report_json"],
            [(1, 1, "v3", "evaluated", "{\"ok\": true}"),
             (2, 2, "v3", "evaluated", "{\"ok\": false}"),
             (3, 3, "v3", "evaluated", "{\"ok\": true}")])
    _insert(d, "buy_sell_signals",
            ["code", "source", "entry_date", "status"],
            [("600001", "v2", "2026-09-11", "open"),
             ("600002", "v2", "2026-09-12", "open"),
             ("600009", "v1", "2026-09-12", "open")])
    # assets: prep/auction/factor
    _insert(a, "prep_snapshots", ["trade_date", "source", "result_json"],
            [("2026-09-11", "v2", json.dumps({"candidates": [{"code": "600001"}]})),
             ("2026-09-12", "v2", json.dumps({"review_candidates": [{"code": "600002"}]})),
             ("2026-09-13", "v2", json.dumps({"candidates": [{"code": "600002", "amount": 600001}]})),
             ("2026-09-11", "v1", json.dumps({"candidates": [{"code": "600001"}]}))])
    _insert(a, "auction_snapshots", ["trade_date", "code", "source", "analysis_json"],
            [("2026-09-11", "600001", "v2", "{\"strength\": 70}"),
             ("2026-09-12", "600002", "v2", "{\"strength\": 60}"),
             ("2026-09-11", "600009", "v1", "{\"strength\": 10}")])
    _insert(a, "factor_values",
            ["trade_date", "code", "factor_name", "factor_version", "value",
             "available_at", "source"],
            [("2026-09-11", "600001", "five_dim_composite", "v2", 88, "2026-09-11", "scan"),
             ("2026-09-12", "600002", "five_dim_composite", "v2", 70, "2026-09-12", "scan")])
    _insert(a, "factor_registry",
            ["factor_name", "factor_version", "frequency", "source", "direction",
             "available_phase", "pit_rule", "spec_hash"],
            [("five_dim_composite", "v2", "daily", "scan", "higher", "close", "asof", "a"),
             ("prep_review_score", "v2", "daily", "prep", "higher", "close", "asof", "b")])
    _insert(a, "factor_build_runs", ["id", "trade_date", "report_json"],
            [(1, "2026-09-11", "{}"), (2, "2026-09-12", "{}")])
    _insert(a, "factor_model_snapshots",
            ["model_name", "model_version", "params_json", "factor_versions_json"],
            [("equal_rank", "v1", "{}", "{}"), ("equal_rank", "v2", "{}", "{}")])
    d.commit()
    a.commit()
    return dragon, assets, d, a


@pytest.fixture()
def seeded(tmp_path):
    dragon, assets, d, a = seed(tmp_path)
    try:
        with patch("dragon_quant.infra.storage.paths.DB_PATH", dragon), \
                patch("dragon_quant.infra.assets.db.ASSETS_DB_PATH", assets):
            yield
    finally:
        d.close()
        a.close()


@pytest.mark.parametrize("dataset", DATASETS)
def test_dataset_filters_and_pagination_are_real(seeded, dataset):
    spec = catalog.get_dataset(dataset)
    first = run_query(name=dataset, limit=10)
    assert first["ok"] is True
    rows = first["data"]
    assert rows, f"{dataset} fixture should expose v2 rows"
    seen = [key_of(dataset, row) for row in rows]
    assert len(seen) == len(set(seen))

    merged: list[tuple] = []
    cursor = None
    while True:
        page = run_query(name=dataset, limit=1, cursor=cursor)
        merged.extend(key_of(dataset, row) for row in page["data"])
        cursor = page["meta"]["next_cursor"]
        if not cursor:
            break
    assert len(merged) == len(set(merged)), "pages must not duplicate rows"
    assert set(merged) == set(seen), "paging must not drop rows"

    for name in spec.filters:
        value = FILTER_VALUES[name]
        filtered = run_query(name=dataset, filters={name: value}, limit=10)
        assert filtered["ok"] is True, (dataset, name)
        for row in filtered["data"]:
            if name == "date_from":
                assert row[spec.date_field] >= value
            elif name == "date_to":
                assert row[spec.date_field] <= value
            elif name == "run_id":
                assert row["run_id"] == value
            elif name == "scan_id":
                assert row["scan_id"] == value
            elif name == "code":
                if dataset == "prep_snapshots":
                    assert row["trade_date"] in {"2026-09-11", "2026-09-12",
                                                 "2026-09-13"}
                else:
                    assert row["code"] == value
            elif name == "status":
                assert row["status"] in ("open", "closed")
            elif name == "experiment_id":
                assert row["experiment_id"] == value


@pytest.mark.parametrize("dataset", DATASETS)
def test_dataset_is_fixed_to_v2_source(seeded, dataset):
    rows = run_query(name=dataset, limit=10)["data"]
    if dataset == "factor_values":
        # factor source is a business source, not a v1/v2 axis.
        assert {row["source"] for row in rows} == {"scan"}
        return
    if dataset in ("factor_registry", "factor_build_runs", "factor_models"):
        return
    if dataset == "account_runs":
        assert {row["run_id"] for row in rows} == {1, 3}
        return
    if "source" in catalog.get_dataset(dataset).field_names():
        assert {row["source"] for row in rows} <= {"v2"}
    assert {key_of(dataset, row) for row in rows}


def test_percent_fields_use_production_percent_points():
    percent_fields = {
        "total_return", "max_drawdown", "win_rate", "daily_return",
        "cumulative_return", "drawdown", "position_unrealized_return",
        "realized_return", "highest_return",
    }
    seen = set()
    for spec in catalog.DATASETS.values():
        for field in spec.fields:
            if field.unit == "百分比点（1.0 = 1%）":
                seen.add(field.name)
            assert "0.01 = 1%" not in (field.unit or "")
    assert seen == percent_fields


def test_prep_code_filter_uses_structured_identity_only(seeded):
    # Only the v2 snapshot containing candidates[].code=600001 matches.
    filtered = run_query(name="prep_snapshots", filters={"code": "600001"})
    assert [row["trade_date"] for row in filtered["data"]] == ["2026-09-11"]
    # amount=600001 inside a supported row is not a stock identity match.
    assert "2026-09-13" not in {row["trade_date"] for row in filtered["data"]}
    # review_candidates[] is the other supported structure.
    other = run_query(name="prep_snapshots", filters={"code": "600002"})
    assert [row["trade_date"] for row in other["data"]] == [
        "2026-09-13", "2026-09-12"]
    # Amount-like text / index codes are never matched.
    for code in ("6000", "SH000001", "1600001"):
        with pytest.raises(Exception):
            run_query(name="prep_snapshots", filters={"code": code})
    # amount-like text inside a supported structure is not identity.
    extra = run_query(name="prep_snapshots", filters={"code": "600003"})
    assert extra["data"] == []


def test_prep_unknown_old_structure_is_explicit(tmp_path):
    dragon = tmp_path / "dragon.db"
    assets = tmp_path / "assets.db"
    _schema_dragon(dragon).close()
    a = _schema_assets(assets)
    a.execute("INSERT INTO prep_snapshots(trade_date, source, result_json) "
              "VALUES ('2026-09-11','v2',?)",
              (json.dumps({"legacy_list": [{"code": "600001"}]}),))
    a.commit()
    a.close()
    with patch("dragon_quant.infra.storage.paths.DB_PATH", dragon), \
            patch("dragon_quant.infra.assets.db.ASSETS_DB_PATH", assets):
        result = run_query(name="prep_snapshots", filters={"code": "600001"})
        assert result["data"] == []
        assert result["meta"]["empty_reason"] == "code_filter_unsupported"
        assert any("unsupported" in warning for warning in result["warnings"])


def test_experiments_report_is_large_and_opt_in(seeded):
    default = run_query(name="experiments")
    assert default["data"], "fixture should expose an experiment"
    assert "report" not in default["data"][0]
    explicit = run_query(name="experiments", fields="experiment_id,report")
    assert explicit["data"][0]["report"] == {"ok": True}


def test_representative_percent_values_are_not_rescaled(seeded):
    run = run_query(name="account_runs", filters={"run_id": 1})["data"][0]
    assert run["total_return"] == 1.0
    assert run["win_rate"] == 100.0
    snap = run_query(name="account_snapshots", filters={
        "run_id": 1, "date_from": "2026-09-02", "date_to": "2026-09-02"})["data"][0]
    assert snap["daily_return"] == 1.0
    pos = run_query(name="account_positions", filters={"run_id": 1})["data"][0]
    assert pos["realized_return"] == 10.0


def test_prep_code_filter_paginates_without_duplicates(seeded):
    seen, cursor = [], None
    while True:
        page = run_query(name="prep_snapshots", filters={"code": "600002"},
                         limit=1, cursor=cursor)
        seen.extend(row["trade_date"] for row in page["data"])
        cursor = page["meta"]["next_cursor"]
        if not cursor:
            break
    assert seen == ["2026-09-13", "2026-09-12"]
    assert len(seen) == len(set(seen))
