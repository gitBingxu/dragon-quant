"""Research facade tests: catalog/query/read-only/account/snapshots/experiments.

All cases run against temporary SQLite files; no real data directory, provider,
network, credential or cache is touched.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.storage.schema import _ensure_schema
from dragon_quant.research import main as research
from dragon_quant.infra.storage.readonly import ReadonlyUnavailable
from dragon_quant.research.envelope import InvalidCursor, InvalidRequest
from dragon_quant.research.envelope import normalize_date
from dragon_quant.research.query import run_query

REPO = Path(__file__).resolve().parents[2]
DATASETS = {
    "scan_runs", "scan_candidates", "dragons", "review_strict",
    "account_runs", "account_snapshots", "account_trades", "account_positions",
    "account_events", "signals", "prep_snapshots", "auction_snapshots",
    "factor_values", "factor_registry", "factor_build_runs", "factor_models",
    "experiments",
}


def _dragon_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    _ensure_schema(conn)
    return conn


def _assets_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(assets_db.BASE_SCHEMA)
    conn.commit()
    return conn


@contextmanager
def research_dbs(tmp_path: Path, *, dragon: bool = True, assets: bool = True):
    dragon_path = tmp_path / "dragon.db"
    assets_path = tmp_path / "assets.db"
    if dragon:
        _dragon_db(dragon_path).close()
    if assets:
        _assets_db(assets_path).close()
    with patch("dragon_quant.infra.storage.paths.DB_PATH", dragon_path), \
            patch("dragon_quant.infra.assets.db.ASSETS_DB_PATH", assets_path):
        yield dragon_path, assets_path


def seed_scan(dragon_path: Path) -> None:

    from dragon_quant.scan.storage.scans import save_scan
    from dragon_quant.tests.support import patch_storage_dbs

    with patch_storage_dbs(dragon_path, dragon_path.with_name("assets.db")):
        stocks = [
            {"code": "600001", "name": "A", "rank": 1, "composite_score": 88.0,
             "board_count": 2, "concepts": ["AI"],
             "dimensions": {"drive": {"score": 90}, "anti_drop": {"score": 75},
                            "leadership": {"score": 80},
                            "absorption": {"score": 49},
                            "liquidity": {"score": 55}},
             "is_true_dragon": True, "open_px": 10, "close_px": 11,
             "high_px": 11, "low_px": 10, "pct": 10, "turnover_rate": 8,
             "amount": 1e8, "market_cap": 1e9},
            {"code": "600002", "name": "B", "rank": 2, "composite_score": 70.0,
             "board_count": 1, "concepts": [],
             "dimensions": {"drive": {"score": 60}, "anti_drop": {"score": 75},
                            "leadership": {"score": 50},
                            "absorption": {"score": 49},
                            "liquidity": {"score": 55}},
             "is_true_dragon": False, "reject_reason": "未达门槛",
             "open_px": 10, "close_px": 10.5, "high_px": 10.6, "low_px": 9.9,
             "pct": 5, "turnover_rate": 5, "amount": 8e7, "market_cap": 8e8},
        ]
        save_scan("v2_20260911_5", "2026-09-11", 1.5, 5, 2, 2, stocks)


def test_catalog_covers_required_datasets():
    payload = research.datasets()
    names = [row["name"] for row in payload["data"]]
    assert set(names) == DATASETS
    assert payload["meta"]["dataset_count"] == 17
    for row in payload["data"]:
        assert row["fields"], row["name"]
        assert row["availability"]["status"] in {
            "available", "database_missing", "table_missing"}
        # 任何数据集都不得暴露凭据/系统表入口。
        assert row["db"] in ("dragon", "assets")


def test_schema_and_unknown_names_are_rejected(tmp_path):
    with research_dbs(tmp_path):
        payload = research.schema("dragons")
        assert payload["data"]["table"] == "dragons_v2"
        assert payload["data"]["keys"] == ["scan_id", "trade_date", "code"]
        with pytest.raises(InvalidRequest):
            research.schema("sqlite_master")
        with pytest.raises(InvalidRequest):
            run_query(name="dragons", fields="no_such_field")
        with pytest.raises(InvalidRequest):
            run_query(name="dragons", filters={"run_id": 1})
        with pytest.raises(InvalidRequest):
            run_query(name="dragons", fields="trade_date,code",
                      filters={"status": "open"})


def test_query_filters_pagination_and_cursor_binding(tmp_path):
    with research_dbs(tmp_path) as (dragon, _assets):
        seed_scan(dragon)
        first = run_query(name="scan_candidates", limit=1)
        assert first["meta"]["returned"] == 1
        assert first["meta"]["truncated"] is True
        cursor = first["meta"]["next_cursor"]
        assert cursor
        second = run_query(name="scan_candidates", limit=1, cursor=cursor)
        assert second["data"][0]["code"] != first["data"][0]["code"]
        assert second["meta"]["truncated"] is False
        with pytest.raises(InvalidCursor):
            run_query(name="scan_candidates", limit=1, cursor=cursor,
                      filters={"code": "600001"})
        with pytest.raises(InvalidCursor):
            run_query(name="dragons", limit=1, cursor=cursor)
        with pytest.raises(InvalidCursor):
            run_query(name="scan_candidates", limit=1, cursor="not-a-cursor")
        filtered = run_query(name="scan_candidates", filters={"code": "600001"})
        assert [row["code"] for row in filtered["data"]] == ["600001"]
        empty = run_query(name="scan_candidates", filters={"code": "600009"})
        assert empty["ok"] is True and empty["data"] == []
        assert empty["meta"]["empty_reason"] == "no_rows_in_scope"


def test_query_argument_validation_rejects_injection_and_bad_dates(tmp_path):
    with research_dbs(tmp_path):
        for code in ("600001' OR 1=1", "ABC123", "60001"):
            with pytest.raises(InvalidRequest):
                run_query(name="dragons", filters={"code": code})
        for raw in ("2026-13-01", "20260230", "yesterday"):
            with pytest.raises(InvalidRequest):
                normalize_date(raw, "--from")
        with pytest.raises(InvalidRequest):
            run_query(name="dragons", filters={
                "date_from": "2026-09-10", "date_to": "2026-09-01"})
        with pytest.raises(InvalidRequest):
            run_query(name="dragons", limit=1001)
        with pytest.raises(InvalidRequest):
            run_query(name="drago' OR 1=1--", limit=1)


def test_missing_database_degrades_without_creating_files(tmp_path):
    dragon = tmp_path / "nested" / "dragon.db"
    assets = tmp_path / "nested" / "assets.db"
    with patch("dragon_quant.infra.storage.paths.DB_PATH", dragon), \
            patch("dragon_quant.infra.assets.db.ASSETS_DB_PATH", assets):
        payload = run_query(name="dragons")
        assert payload["ok"] is True
        assert payload["meta"]["empty_reason"] == "database_missing"
        assert payload["meta"]["data_quality"] == "degraded"
        assert not dragon.exists() and not assets.exists()
        assert not (tmp_path / "nested").exists()


def test_missing_table_is_explained(tmp_path):
    dragon = tmp_path / "dragon.db"
    sqlite3.connect(str(dragon)).execute("CREATE TABLE unrelated (x INTEGER)")
    with patch("dragon_quant.infra.storage.paths.DB_PATH", dragon):
        payload = run_query(name="dragons")
        assert payload["ok"] is True
        assert payload["meta"]["empty_reason"] == "table_missing"


def test_readonly_queries_create_no_sidecars_and_do_not_mutate(tmp_path):
    with research_dbs(tmp_path) as (dragon, _assets):
        seed_scan(dragon)
        conn = sqlite3.connect(str(dragon))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        for suffix in ("-wal", "-shm"):
            Path(f"{dragon}{suffix}").unlink(missing_ok=True)
        before = {path.name: path.stat().st_mtime_ns for path in tmp_path.iterdir()}
        for name in ("scan_runs", "scan_candidates", "dragons", "review_strict"):
            run_query(name=name, limit=5)
        after = {path.name: path.stat().st_mtime_ns for path in tmp_path.iterdir()}
        assert after == before
        assert not Path(f"{dragon}-wal").exists()
        assert not Path(f"{dragon}-shm").exists()


def test_wal_without_shm_fails_closed_without_side_effects(tmp_path):
    from dragon_quant.infra.storage.readonly import open_readonly

    db = tmp_path / "wal.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE t (value TEXT)")
    conn.execute("INSERT INTO t VALUES ('committed')")
    conn.commit()
    wal = Path(f"{db}-wal")
    assert wal.exists() and wal.stat().st_size > 0
    shm = Path(f"{db}-shm")
    shm.unlink(missing_ok=True)
    before_no_shm = {p.name: (p.stat().st_size, p.stat().st_mtime_ns)
                     for p in tmp_path.iterdir()}
    with pytest.raises(ReadonlyUnavailable) as excinfo:
        open_readonly(db)
    assert excinfo.value.reason == "wal_not_checkpointed"
    after = {p.name: (p.stat().st_size, p.stat().st_mtime_ns)
             for p in tmp_path.iterdir()}
    assert after == before_no_shm, "fail-closed path must not create -shm"
    assert set(after) == set(before_no_shm)
    conn.close()


def test_existing_shm_wal_reads_committed_without_mutation(tmp_path):
    from dragon_quant.infra.storage.readonly import open_readonly

    db = tmp_path / "active.db"
    writer = sqlite3.connect(str(db))
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE t (value TEXT)")
    writer.execute("INSERT INTO t VALUES ('committed')")
    writer.commit()
    wal = Path(f"{db}-wal")
    assert wal.stat().st_size > 0
    db_bytes = db.read_bytes()
    wal_bytes = wal.read_bytes()
    shm = Path(f"{db}-shm")
    before_files = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}
    shm_before = shm.stat().st_mtime_ns
    opened = open_readonly(db)
    try:
        assert opened.execute("SELECT value FROM t").fetchone() == ("committed",)
    finally:
        opened.close()
    assert db.read_bytes() == db_bytes
    assert wal.read_bytes() == wal_bytes
    assert {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()} == before_files
    assert shm.stat().st_mtime_ns == shm_before, "existing -shm must not be rewritten"
    writer.close()


def test_checkpointed_database_opens_immutable_readonly(tmp_path):
    from dragon_quant.infra.storage.readonly import open_readonly

    dragon = tmp_path / "clean.db"
    conn = sqlite3.connect(str(dragon))
    conn.execute("CREATE TABLE t (value TEXT)")
    conn.execute("INSERT INTO t VALUES ('ok')")
    conn.commit()
    conn.close()
    opened = open_readonly(dragon)
    assert opened is not None
    try:
        assert opened.execute("SELECT value FROM t").fetchone() == ("ok",)
    finally:
        opened.close()
    assert not Path(f"{dragon}-wal").exists()
    assert not Path(f"{dragon}-shm").exists()


def _seed_account(dragon: Path) -> None:
    from dragon_quant.review_account.storage import save_review_account_results

    conn = _dragon_db(dragon)
    try:
        cur = conn.execute(
            "INSERT INTO review_account_runs(source, strategy_name, strategy_params_json, "
            "date_from, date_to, initial_cash, final_equity, total_return, "
            "max_drawdown, trade_count, win_rate, run_meta_json) "
            "VALUES ('v2','dragon_pullback_daily','{\"min_score\": 50}',"
            "'2026-09-01','2026-09-05',100000,101000,0.01,0.02,2,1.0,'{}')")
        run_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    with patch("dragon_quant.infra.storage.connection._connect",
               lambda: sqlite3.connect(str(dragon))), \
            patch("dragon_quant.infra.storage.paths.DB_PATH", dragon):
        save_review_account_results(
            run_id,
            snapshots=[{
                "trade_date": "2026-09-02", "cash": 50000, "market_value": 50000,
                "total_equity": 100000, "daily_return": 0, "cumulative_return": 0,
                "drawdown": 0, "position_code": "", "position_name": "",
                "position_qty": 0, "position_cost": 0,
                "position_market_price": 0, "position_unrealized_return": 0,
                "positions": []}],
            trades=[{"trade_date": "2026-09-02", "code": "600001", "name": "A",
                     "side": "buy", "price": 10, "qty": 1000, "amount": 10000,
                     "fee": 5, "realized_pnl": 0, "cash_after": 89995,
                     "position_after": 1000, "reason_code": "buy",
                     "reason_text": "", "signal": {}}],
            positions=[{"code": "600001", "name": "A", "entry_date": "2026-09-02",
                        "entry_price": 10, "qty": 1000,
                        "entry_reason_code": "buy", "entry_signal": {},
                        "exit_date": "2026-09-04", "exit_price": 11,
                        "exit_reason_code": "tp", "exit_signal": {},
                        "realized_return": 0.1, "hold_days": 2, "status": "closed",
                        "post_exit_max_return": 0, "post_exit_max_drawdown": 0,
                        "post_exit_window_days": 5, "initial_cost": 10000,
                        "realized_pnl": 1000, "period_pnl": 1000,
                        "period_start_value": 100000, "inherited": False}],
            events=[],
        )


def test_account_summary_reuses_existing_evaluation(tmp_path):
    from dragon_quant.review_account import evaluation

    with research_dbs(tmp_path) as (dragon, _assets):
        _seed_account(dragon)
        payload = research.account(run_id=1, view="summary")
        summary = payload["data"]["summary"]
        run = research.account(run_id=1, view="summary")["data"]["run"]
        run["id"] = run["run_id"]
        expected = evaluation.metrics_from_rows(
            run,
            [dict(row) for row in __import__(
                "dragon_quant.review_account.research_reads",
                fromlist=["account_snapshots"]).account_snapshots(run_id=1)],
            [dict(row) for row in __import__(
                "dragon_quant.review_account.research_reads",
                fromlist=["account_trades"]).account_trades(run_id=1)],
            [dict(row) for row in __import__(
                "dragon_quant.review_account.research_reads",
                fromlist=["account_positions"]).account_positions(run_id=1)],
        )
        assert summary["win_rate"] == 100.0
        assert summary["fees"] == 5.0
        assert summary["total_return"] == expected["total_return"]
        assert payload["meta"]["returned"] == 1
        missing = research.account(run_id=999, view="summary")
        assert missing["ok"] is True
        assert missing["meta"]["empty_reason"] == "run_not_found"
        detail = research.account(run_id=1, view="trades")
        assert detail["data"][0]["side"] == "buy"


def test_signals_status_and_half_leg_are_visible(tmp_path):
    with research_dbs(tmp_path) as (dragon, _assets):
        conn = _dragon_db(dragon)
        try:
            conn.execute(
                "INSERT INTO buy_sell_signals(code, name, source, entry_date, entry_price, "
                "entry_reason_code, entry_reason_text, entry_signal_json, status, "
                "qty, half_exit_date, half_exit_price, half_exit_qty, "
                "half_exit_reason_code) VALUES "
                "('600001','A','v2','2026-09-11',10,'buy','','{}','open',1000,"
                "'2026-09-12',11,500,'take_profit')")
            conn.execute(
                "INSERT INTO buy_sell_signals(code, name, source, entry_date, entry_price, "
                "entry_reason_code, entry_reason_text, entry_signal_json, status, qty, "
                "exit_date, exit_price, exit_reason_code) VALUES "
                "('600002','B','v2','2026-09-10',10,'buy','','{}','closed',1000,"
                "'2026-09-12',9,'stop_loss')")
            conn.commit()
        finally:
            conn.close()
        opened = research.signals(status="open")
        assert [row["code"] for row in opened["data"]] == ["600001"]
        assert opened["data"][0]["half_exit_qty"] == 500
        assert opened["meta"]["signal_scope"]["not_broker_fills"] is True
        closed = research.signals(status="closed")
        assert [row["code"] for row in closed["data"]] == ["600002"]
        assert closed["data"][0]["exit_reason_code"] == "stop_loss"
        with pytest.raises(InvalidRequest):
            research.signals(status="bogus")


def test_snapshots_default_omits_large_payload(tmp_path):
    with research_dbs(tmp_path) as (_dragon, assets):
        conn = _assets_db(assets)
        try:
            conn.execute(
                "INSERT INTO prep_snapshots(trade_date, source, result_json) "
                "VALUES ('2026-09-11','v2','{\"sentiment\": 60, \"plan\": []}')")
            conn.execute(
                "INSERT INTO auction_snapshots(trade_date, code, source, analysis_json) "
                "VALUES ('2026-09-12','600001','v2','{\"strength\": 72.5}')")
            conn.commit()
        finally:
            conn.close()
        prep = research.snapshots(kind="prep", date="20260911")
        assert prep["data"][0]["trade_date"] == "2026-09-11"
        assert "result" not in prep["data"][0]
        full = run_query(name="prep_snapshots", fields="trade_date,result")
        assert full["data"][0]["result"]["sentiment"] == 60
        auction = research.snapshots(kind="auction", code="600001")
        assert auction["data"][0]["code"] == "600001"
        assert "analysis" not in auction["data"][0]
        full_auction = run_query(name="auction_snapshots",
                                 fields="code,analysis")
        assert full_auction["data"][0]["analysis"]["strength"] == 72.5


def test_scan_quality_cli_matches_shared_computation(tmp_path):
    from dragon_quant.infra.assets.research_reads import (
        stock_daily_bars, stock_daily_bar_dates,
    )
    from dragon_quant.research.scan_quality import run_scan_quality
    from dragon_quant.scan.storage import research_reads as scan_reads

    with research_dbs(tmp_path) as (dragon, assets):
        seed_scan(dragon)
        conn = _assets_db(assets)
        try:
            payload = {"rows": {
                "600001": [{"date": "2026-09-11", "open": 10, "high": 11,
                            "low": 9.9, "close": 11, "volume": 1, "amount": 1,
                            "pct": 10, "turnover": 8},
                           {"date": "2026-09-14", "open": 10.5, "high": 11.2,
                            "low": 10.4, "close": 11.0, "volume": 1, "amount": 1,
                            "pct": 0, "turnover": 8},
                           {"date": "2026-09-15", "open": 11, "high": 11.5,
                            "low": 10.9, "close": 11.2, "volume": 1, "amount": 1,
                            "pct": 2, "turnover": 8}],
                "600002": [{"date": "2026-09-11", "open": 10, "high": 10.6,
                            "low": 9.9, "close": 10.5, "volume": 1, "amount": 1,
                            "pct": 5, "turnover": 5},
                           {"date": "2026-09-14", "open": 10.5, "high": 10.6,
                            "low": 10.2, "close": 10.3, "volume": 1, "amount": 1,
                            "pct": -2, "turnover": 5}],
            }}
            conn.execute(
                "INSERT INTO prep_assets(trade_date, asset_type, payload_json) "
                "VALUES ('2026-09-11','stock_daily',?)",
                (json.dumps(payload),))
            conn.commit()
        finally:
            conn.close()
        cli_payload = research.scan_quality(date_from="20260911", date_to="20260918")
        shared = run_scan_quality(
            date_from="2026-09-11", date_to="2026-09-18",
            list_dates=lambda low, high: scan_reads.scan_dates(low, high) or [],
            contributions=lambda day: scan_reads.contributions_by_date(day) or [],
            load_bars=stock_daily_bars,
            load_calendar=stock_daily_bar_dates,
        )
        assert cli_payload["data"] == shared
        assert shared["coverage"]["days_with_scan"] == 1
        assert cli_payload["meta"]["scan_quality_version"]
        assert cli_payload["meta"]["versions_available"]["score"] is True


def test_experiments_list_show_are_readonly_and_create_no_lock(tmp_path):
    with research_dbs(tmp_path) as (dragon, _assets):
        conn = _dragon_db(dragon)
        try:
            conn.execute(
                "INSERT INTO review_account_runs(id, source, strategy_name, "
                "strategy_params_json, date_from, date_to) "
                "VALUES (3,'v2','dragon_pullback_daily','{}','2026-09-01','2026-09-05')")
            conn.execute(
                "INSERT INTO review_account_experiments(base_run_id, experiment_version, "
                "status, request_json, report_json) VALUES "
                "(3,'account_experiment_v3','evaluated','{\"run_id\":3}',"
                "'{\"status\":\"evaluated\",\"promoted\":false}')")
            conn.commit()
        finally:
            conn.close()
        listing = research.experiments.list(3)
        assert listing["data"][0]["experiment_id"] == 1
        shown = research.experiments.show(1)
        assert shown["data"]["report"]["promoted"] is False
        assert not (tmp_path / "locks").exists()
        conn = sqlite3.connect(str(dragon))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM review_account_experiments").fetchone()[0]
        finally:
            conn.close()
        assert count == 1


def test_experiment_run_requires_confirm_and_rejects_unknown_rule(tmp_path):
    with research_dbs(tmp_path) as (dragon, _assets):
        with pytest.raises(Exception) as excinfo:
            research.experiments.run(1, confirm=False, sell_rules=None,
                                     lock_path=tmp_path / "lock")
        assert getattr(excinfo.value, "code", None) == "confirmation_required"
        with pytest.raises(InvalidRequest) as rule_exc:
            research.experiments.run(1, confirm=True, sell_rules=["no_such_rule"],
                                     lock_path=tmp_path / "lock")
        assert "未知 sell-rule" in str(rule_exc.value)
        conn = sqlite3.connect(str(dragon))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM review_account_experiments").fetchone()[0]
        finally:
            conn.close()
        assert count == 0


def test_process_lock_is_cross_process_busy_and_releases(tmp_path):
    from dragon_quant.research.experiments import ExperimentBusy, process_lock

    lock = tmp_path / "locks" / "experiment.lock"
    script = tmp_path / "hold.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "from pathlib import Path\n"
        "from dragon_quant.research.experiments import process_lock\n"
        f"with process_lock(Path({str(lock)!r})):\n"
        f"    Path({str(tmp_path / 'held')!r}).write_text('1')\n"
        "    time.sleep(3)\n",
        encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)])
    try:
        deadline = time.time() + 10
        while not (tmp_path / "held").exists():
            if time.time() > deadline:
                raise AssertionError("child never acquired the lock")
            time.sleep(0.02)
        with pytest.raises(ExperimentBusy):
            with process_lock(lock):
                pass
    finally:
        proc.wait(timeout=10)
    with process_lock(lock):
        pass


def _run_cli(args, data_dir: Path, *, home: Path | None = None,
             timeout: int = 60) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DQ_DATA_DIR": str(data_dir),
        "HOME": str(home or data_dir / "home"),
        "PYTHONPATH": str(REPO),
    }
    return subprocess.run(
        [sys.executable, "-m", "dragon_quant", *args],
        cwd=REPO, env=env, text=True, capture_output=True, timeout=timeout)


def test_cli_json_contract_and_exit_codes(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    datasets = _run_cli(["research", "datasets", "--json"], data_dir)
    assert datasets.returncode == 0, datasets.stderr
    payload = json.loads(datasets.stdout)
    assert payload["schema_version"] == "research_v1"
    assert payload["command"] == "research datasets"
    assert len(payload["data"]) == 17

    invalid = _run_cli(["research", "query", "--dataset", "nope", "--json"], data_dir)
    assert invalid.returncode == 2
    error = json.loads(invalid.stdout)
    assert error["ok"] is False
    assert error["error"]["code"] == "invalid_request"

    bad_field = _run_cli(
        ["research", "query", "--dataset", "dragons", "--fields", "x", "--json"],
        data_dir)
    assert bad_field.returncode == 2

    bad_date = _run_cli(
        ["research", "scan-quality", "--from", "2026-02-30", "--to", "2026-03-01",
         "--json"], data_dir)
    assert bad_date.returncode == 2

    missing_run = _run_cli(["research", "account", "--run-id", "1", "--json"],
                           data_dir)
    assert missing_run.returncode == 0
    assert json.loads(missing_run.stdout)["meta"]["empty_reason"] == "database_missing"

    unknown_rule = _run_cli(
        ["research", "experiments", "run", "--run-id", "1", "--confirm",
         "--sell-rule", "nope", "--json"], data_dir)
    assert unknown_rule.returncode in (1, 2)
    assert json.loads(unknown_rule.stdout)["ok"] is False


def test_cli_help_and_research_reads_leave_no_files(tmp_path):
    data_dir = tmp_path / "data"
    result = _run_cli(["research", "-h"], data_dir)
    assert result.returncode == 0
    assert "datasets" in result.stdout
    assert not data_dir.exists()
    read = _run_cli(["research", "datasets"], data_dir)
    assert read.returncode == 0
    assert not data_dir.exists(), "只读命令不得创建数据根目录"


def test_import_research_has_no_side_effects(tmp_path):
    data_dir = tmp_path / "data"
    env = {**os.environ, "DQ_DATA_DIR": str(data_dir), "HOME": str(tmp_path / "home"),
           "PYTHONPATH": str(REPO)}
    result = subprocess.run(
        [sys.executable, "-c",
         "import dragon_quant, dragon_quant.research; "
         "import dragon_quant.research.main; print('ok')"],
        cwd=REPO, env=env, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert not data_dir.exists()


def test_review_account_json_is_a_compatible_receipt(monkeypatch):
    import io
    from argparse import Namespace
    from contextlib import redirect_stdout

    from dragon_quant.commands import review_account

    calls = {}

    def fake_run(**kwargs):
        calls.update(kwargs)
        return {
            "run_id": 42, "source": "v2", "strategy_name": "dragon_pullback_daily",
            "date_from": "2026-09-01", "date_to": "2026-09-05",
            "initial_cash": 100000.0, "final_equity": 101000.0,
            "total_return": 0.01, "max_drawdown": 0.02, "trade_count": 2,
            "win_rate": 1.0,
        }

    monkeypatch.setattr(
        "dragon_quant.review_account.main.run_review_account", fake_run)
    args = Namespace(ui_only=False, date_from="20260901", date_to="20260905",
                     capital=100000.0, source="v2", strategy="dragon_pullback_daily",
                     config=None, json=True, ui=False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        review_account.handle(args, services=None)
    payload = json.loads(buf.getvalue())
    assert payload["ok"] is True and payload["run_id"] == 42
    assert calls["verbose"] is False


def test_research_command_never_constructs_providers():
    from dragon_quant.commands import COMMANDS

    entry = COMMANDS["research"]
    assert entry.providers_needed(None) is False


def test_old_schema_degrades_with_schema_mismatch(tmp_path):
    dragon = tmp_path / "old.db"
    conn = sqlite3.connect(str(dragon))
    conn.execute("CREATE TABLE dragons_v2 (trade_date TEXT, code TEXT)")
    conn.execute("INSERT INTO dragons_v2 VALUES ('2026-09-11','600001')")
    conn.commit()
    conn.close()
    with patch("dragon_quant.infra.storage.paths.DB_PATH", dragon):
        payload = run_query(name="dragons")
        assert payload["ok"] is True
        assert payload["meta"]["empty_reason"] == "schema_mismatch"
        assert any("旧 schema" in warning for warning in payload["warnings"])


def test_factor_reads_are_readonly_and_versioned(tmp_path):
    with research_dbs(tmp_path) as (_dragon, assets):
        conn = _assets_db(assets)
        try:
            conn.execute(
                "INSERT INTO factor_values(trade_date, code, factor_name, "
                "factor_version, value, available_at, source, source_meta_json) "
                "VALUES ('2026-09-11','600001','five_dim_composite','v2',88.0,"
                "'2026-09-11T15:00:00+08:00','scan','{\"rows\": 1}')")
            conn.execute(
                "INSERT INTO factor_registry(factor_name, factor_version, label, "
                "frequency, source, direction, available_phase, pit_rule, spec_hash) "
                "VALUES ('five_dim_composite','v2','五维','daily','scan','higher',"
                "'close','asof','abc')")
            conn.commit()
        finally:
            conn.close()
        values = run_query(name="factor_values", filters={"code": "600001"})
        assert values["data"][0]["factor_version"] == "v2"
        assert values["data"][0]["source_meta"]["rows"] == 1
        registry = run_query(name="factor_registry")
        assert registry["data"][0]["factor_name"] == "five_dim_composite"
        assert registry["data"][0]["params"] == {}
        before = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}
        run_query(name="factor_values", limit=1, fields="trade_date,value")
        after = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}
        assert after == before


def test_package_level_experiments_is_the_real_module():
    script = (
        "from dragon_quant.research import experiments; "
        "assert hasattr(experiments, 'run_account_experiment'); "
        "assert hasattr(experiments, 'ExperimentBusy'); "
        "print('ok')"
    )
    env = {**os.environ, "DQ_DATA_DIR": str(Path(__file__).with_name("unused")),
           "PYTHONPATH": str(REPO)}
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO, env=env,
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_scan_quality_sample_ledger_paginates(tmp_path):
    with research_dbs(tmp_path) as (dragon, assets):
        seed_scan(dragon)
        conn = _assets_db(assets)
        try:
            payload = {"rows": {
                "600001": [{"date": "2026-09-11", "open": 10, "high": 11,
                            "low": 9.9, "close": 11, "volume": 1, "amount": 1,
                            "pct": 10, "turnover": 8},
                           {"date": "2026-09-14", "open": 10.5, "high": 11.2,
                            "low": 10.4, "close": 11.0, "volume": 1, "amount": 1,
                            "pct": 0, "turnover": 8}],
                "600002": [{"date": "2026-09-11", "open": 10, "high": 10.6,
                            "low": 9.9, "close": 10.5, "volume": 1, "amount": 1,
                            "pct": 5, "turnover": 5},
                           {"date": "2026-09-14", "open": 10.5, "high": 10.6,
                            "low": 10.2, "close": 10.3, "volume": 1, "amount": 1,
                            "pct": -2, "turnover": 5}],
            }}
            conn.execute(
                "INSERT INTO prep_assets(trade_date, asset_type, payload_json) "
                "VALUES ('2026-09-11','stock_daily',?)", (json.dumps(payload),))
            conn.commit()
        finally:
            conn.close()
        first = research.scan_quality(date_from="20260911", date_to="20260918",
                                      include_samples=True, sample_limit=1)
        assert len(first["data"]["samples"]) == 1
        assert first["meta"]["samples_total"] == 2
        assert first["meta"]["samples_truncated"] is True
        cursor = first["meta"]["samples_next_cursor"]
        assert cursor
        second = research.scan_quality(date_from="20260911", date_to="20260918",
                                       include_samples=True, sample_limit=1,
                                       sample_cursor=cursor)
        assert len(second["data"]["samples"]) == 1
        assert second["meta"]["samples_next_cursor"] is None


def test_dedicated_entries_share_table_and_schema_error_mapping(tmp_path):
    dragon = tmp_path / "dragon.db"
    conn = sqlite3.connect(str(dragon))
    conn.execute("CREATE TABLE review_account_runs (id INTEGER, source TEXT)")
    conn.commit()
    conn.close()
    with patch("dragon_quant.infra.storage.paths.DB_PATH", dragon):
        account = research.account(run_id=1, view="summary")
        assert account["ok"] is True
        assert account["meta"]["empty_reason"] == "schema_mismatch"
        strategy = research.strategy(kind="account", run_id=1)
        assert strategy["meta"]["empty_reason"] == "schema_mismatch"
        listing = research.experiments.list(1)
        assert listing["meta"]["empty_reason"] == "table_missing"
        shown = research.experiments.show(1)
        assert shown["meta"]["empty_reason"] == "table_missing"


def test_corrupt_json_degrades_instead_of_claiming_success(tmp_path):
    with research_dbs(tmp_path) as (dragon, _assets):
        conn = _dragon_db(dragon)
        try:
            conn.execute(
                "INSERT INTO review_account_runs(id, source, strategy_name, "
                "strategy_params_json, date_from, date_to) VALUES "
                "(1,'v2','dragon_pullback_daily','{broken','2026-09-01','2026-09-05')")
            conn.execute(
                "INSERT INTO review_account_experiments(base_run_id, experiment_version, "
                "status, request_json, report_json) VALUES "
                "(1,'v3','evaluated','{}','{broken')")
            conn.commit()
        finally:
            conn.close()
        summary = research.account(run_id=1, view="summary")
        assert summary["ok"] is True
        assert summary["meta"]["data_quality"] == "degraded"
        assert any("corrupt_json_fields" in warning
                   for warning in summary["warnings"])
        strategy = research.strategy(kind="account", run_id=1)
        assert strategy["meta"]["empty_reason"] == "corrupt_strategy_params"
        shown = research.experiments.show(1)
        assert shown["meta"]["data_quality"] == "degraded"
        assert any("corrupt" in warning for warning in shown["warnings"])
        listing = research.experiments.list(1)
        assert listing["data"][0]["report_omitted"] is True
        assert "report" not in listing["data"][0]


def test_experiments_list_has_bounded_pagination(tmp_path):
    with research_dbs(tmp_path) as (dragon, _assets):
        conn = _dragon_db(dragon)
        try:
            conn.execute("INSERT INTO review_account_runs(id, source, strategy_name, "
                         "strategy_params_json, date_from, date_to) VALUES "
                         "(1,'v2','s','{}','2026-09-01','2026-09-05')")
            for idx in range(3):
                conn.execute(
                    "INSERT INTO review_account_experiments(base_run_id, "
                    "experiment_version, status, report_json) VALUES (1,'v3','ok','{}')")
            conn.commit()
        finally:
            conn.close()
        first = research.experiments.list(1, limit=2)
        assert first["meta"]["truncated"] is True
        assert first["meta"]["next_cursor"]
        second = research.experiments.list(1, limit=2,
                                           cursor=first["meta"]["next_cursor"])
        assert len(first["data"]) + len(second["data"]) == 3
        ids = {row["experiment_id"] for row in first["data"] + second["data"]}
        assert ids == {1, 2, 3}


def test_human_cli_account_and_experiments_are_readable(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dragon = data_dir / "dragon.db"
    _seed_account(dragon)
    conn = sqlite3.connect(str(dragon))
    try:
        conn.execute(
            "INSERT INTO review_account_experiments(base_run_id, experiment_version, "
            "status, request_json, report_json) VALUES "
            "(1,'account_experiment_v3','evaluated','{}','{\"promoted\": false}')")
        conn.commit()
    finally:
        conn.close()
    account = _run_cli(["research", "account", "--run-id", "1"], data_dir)
    assert account.returncode == 0, account.stderr
    assert "run_id=1" in account.stdout
    assert "run_id=None" not in account.stdout
    assert "total_return" in account.stdout
    listing = _run_cli(["research", "experiments", "list", "--run-id", "1"],
                       data_dir)
    assert listing.returncode == 0, listing.stderr
    assert "returned=" in listing.stdout or "experiment_id" in listing.stdout
    assert not listing.stdout.lstrip().startswith("{"), "list must not fall back to JSON"
    shown = _run_cli(["research", "experiments", "show", "--experiment-id", "1"],
                     data_dir)
    assert shown.returncode == 0, shown.stderr
    assert "experiment_id=1" in shown.stdout


def test_corrupt_query_json_marks_dataset_degraded(tmp_path):
    with research_dbs(tmp_path) as (dragon, assets):
        conn = _dragon_db(dragon)
        try:
            conn.execute("INSERT INTO scans_v2(id, scan_date, top_n, source) "
                         "VALUES ('v2_20260911_5','2026-09-11',5,'v2')")
            conn.execute(
                "INSERT INTO scan_stocks_v2(scan_id, code, concepts_json, source) "
                "VALUES ('v2_20260911_5','600001','{broken','v2')")
            conn.commit()
        finally:
            conn.close()
        payload = run_query(name="scan_candidates")
        assert payload["meta"]["data_quality"] == "degraded"
        assert any("corrupt_json_rows" in warning for warning in payload["warnings"])
        aconn = _assets_db(assets)
        try:
            aconn.execute(
                "INSERT INTO factor_values(trade_date, code, factor_name, "
                "factor_version, value, available_at, source, source_meta_json) "
                "VALUES ('2026-09-11','600001','f','v2',1,'2026-09-11','scan','{broken')")
            aconn.commit()
        finally:
            aconn.close()
        factors = run_query(name="factor_values")
        assert factors["meta"]["data_quality"] == "degraded"


def test_account_ledger_cap_marks_truncated():
    from dragon_quant.research import account as account_module

    def endless(limit, offset, **kwargs):
        return [{"i": offset + idx} for idx in range(limit)]

    rows, truncated = account_module._page_all(endless, max_rows=5)
    assert len(rows) == 5
    assert truncated is True
