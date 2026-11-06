"""Cross-consumer parity and failure-path evidence for research use cases."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.storage.schema import _ensure_schema
from dragon_quant.research import main as research
from dragon_quant.research.envelope import InvalidRequest
from dragon_quant.research.experiments import (
    ExperimentInputError,
    process_lock,
    run_account_experiment,
)
from web_ui.backend import deps
from web_ui.backend.app import create_app
from web_ui.backend.settings import DashboardSettings

REPO = Path(__file__).resolve().parents[2]


def _seed_scan_and_bars(tmp_path: Path):
    dragon = tmp_path / "dragon.db"
    assets = tmp_path / "assets.db"
    conn = sqlite3.connect(str(dragon))
    _ensure_schema(conn)
    conn.execute("INSERT INTO scans_v2(id, scan_date, top_n, source) "
                 "VALUES ('v2_20260911_5','2026-09-11',5,'v2')")
    conn.execute(
        "INSERT INTO scan_stocks_v2(scan_id, code, name, rank, concepts_json, source) "
        "VALUES ('v2_20260911_5','600001','A',1,'[]','v2')")
    conn.commit()
    conn.close()
    conn = sqlite3.connect(str(assets))
    conn.executescript(assets_db.BASE_SCHEMA)
    payload = {"rows": {"600001": [
        {"date": "2026-09-11", "open": 10, "high": 11, "low": 9.9, "close": 11,
         "volume": 1, "amount": 1, "pct": 10, "turnover": 8},
        {"date": "2026-09-14", "open": 10.5, "high": 11.2, "low": 10.4,
         "close": 11.0, "volume": 1, "amount": 1, "pct": 0, "turnover": 8},
        {"date": "2026-09-15", "open": 11, "high": 11.5, "low": 10.9,
         "close": 11.2, "volume": 1, "amount": 1, "pct": 2, "turnover": 8},
    ]}}
    conn.execute("INSERT INTO prep_assets(trade_date, asset_type, payload_json) "
                 "VALUES ('2026-09-11','stock_daily',?)",
                 (json.dumps(payload),))
    conn.commit()
    conn.close()
    return dragon, assets


def test_http_scan_quality_matches_cli_on_same_fixture(tmp_path):
    dragon, assets = _seed_scan_and_bars(tmp_path)
    from dragon_quant.scan.storage import scans

    def _dragon_connect(*args, **kwargs):
        return sqlite3.connect(str(dragon))

    app = create_app(DashboardSettings(dist_dir=Path(tempfile.mkdtemp())))
    app.dependency_overrides[deps.scan_repository] = lambda: scans
    app.dependency_overrides[deps.local_bars_loader] = lambda: (
        lambda codes, date_from, date_to: assets_db.list_stock_daily_bars(
            codes, date_from, date_to))
    app.dependency_overrides[deps.bar_calendar_loader] = lambda: (
        lambda date_from=None, date_to=None: (
            assets_db.list_stock_daily_bar_dates(date_from, date_to)))
    client = TestClient(app, base_url="http://localhost")
    try:
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=_dragon_connect), \
                patch("dragon_quant.infra.assets.db.ASSETS_DB_PATH", assets), \
                patch("dragon_quant.infra.storage.paths.DB_PATH", dragon):
            http = client.get(
                "/api/research/scan-quality"
                "?date_from=2026-09-11&date_to=2026-09-18")
            assert http.status_code == 200, http.text
            cli = research.scan_quality(date_from="20260911",
                                        date_to="20260918")
        assert http.json() == cli["data"]
        assert cli["data"]["coverage"]["days_with_scan"] == 1
        assert cli["data"]["coverage"]["canonical_candidates"] >= 1
    finally:
        app.dependency_overrides.clear()


class _Account:
    def __init__(self):
        self.saved = 0

    def get_review_account_run(self, run_id):
        if run_id != 1:
            return None
        return {
            "id": 1, "source": "v2", "strategy_name": "dragon_pullback_daily",
            "strategy_params": {}, "date_from": "2026-09-01",
            "date_to": "2026-09-05", "initial_cash": 100000.0,
            "start_positions": [], "base_equity": None, "start_equity": 100000.0,
            "start_cash": 100000.0, "start_peak_equity": 100000.0,
            "start_consecutive_losses": 0, "start_realized_pnl": 0.0,
            "start_marks": {}, "base_run_id": None, "start_state_recorded": True,
        }

    def save_review_account_experiment(self, *args, **kwargs):
        self.saved += 1
        return 9


def test_freeze_failure_releases_lock_and_saves_nothing(tmp_path):
    lock = threading.Lock()
    account = _Account()
    with pytest.raises(ExperimentInputError) as excinfo:
        run_account_experiment(
            run_id=1, sell_rules=None, confirm=True, account=account,
            factor_store=object(), load_bars=lambda *a: {},
            load_calendar=lambda *a: [], loader_factory=lambda source: (lambda *a: []),
            snapshot_reader=lambda *a: (None, None, "missing"),
            assets=object(),
            save_experiment=account.save_review_account_experiment,
            heavy_lock=lock,
            lock_path=tmp_path / "locks" / "experiment.lock",
        )
    assert excinfo.value.code == "local_calendar_missing"
    assert lock.acquire(blocking=False), "heavy lock must be released on failure"
    lock.release()
    assert account.saved == 0, "no fake success report may be persisted"


def test_http_experiment_returns_409_while_cli_holds_process_lock(tmp_path):
    from dragon_quant.infra.storage import paths

    account = _Account()
    app = create_app(DashboardSettings(dist_dir=Path(tempfile.mkdtemp())))
    app.dependency_overrides[deps.account_repository] = lambda: account
    app.dependency_overrides[deps.assets_repository] = lambda: object()
    app.dependency_overrides[deps.factor_storage] = lambda: object()
    app.dependency_overrides[deps.local_bars_loader] = lambda: (
        lambda *a: {"rows": {}, "truncated": False})
    app.dependency_overrides[deps.bar_calendar_loader] = lambda: (
        lambda *a: ["2026-09-01"])
    app.dependency_overrides[deps.candidate_loader_factory] = lambda: (
        lambda source: (lambda *a: []))
    app.dependency_overrides[deps.prep_snapshot_reader] = lambda: (
        lambda *a: (None, None, "missing"))
    client = TestClient(app, base_url="http://localhost")
    lock_path = paths.DATA_DIR / "locks" / "account_experiment.lock"
    try:
        with process_lock(lock_path):
            response = client.post("/api/account/experiments",
                                   json={"run_id": 1, "confirm": True})
        assert response.status_code == 409, response.text
        assert response.json()["error"] == "experiment_running"
        from web_ui.backend.locks import ACCOUNT_HEAVY_LOCK

        assert ACCOUNT_HEAVY_LOCK.acquire(blocking=False)
        ACCOUNT_HEAVY_LOCK.release()
        assert account.saved == 0
    finally:
        app.dependency_overrides.clear()


def test_experiments_unknown_confirm_is_parameter_error(tmp_path):
    with pytest.raises(InvalidRequest):
        run_account_experiment(
            run_id=1, sell_rules=None, confirm=False, account=_Account(),
            factor_store=object(), load_bars=lambda *a: {},
            load_calendar=lambda *a: [], loader_factory=lambda source: (lambda *a: []),
            snapshot_reader=lambda *a: (None, None, "missing"),
            assets=object(), save_experiment=lambda *a, **k: 1,
        )


def test_truncated_bars_and_save_failure_release_lock(tmp_path):
    lock = threading.Lock()
    account = _Account()
    original = account.get_review_account_run
    held = {
        "code": "600001", "name": "A", "qty": 100,
        "entry_date": "2026-08-31", "entry_price": 10.0, "cost": 1000.0,
        "entry_reason_code": "buy", "entry_reason_text": "",
        "mark": 10.0, "initial_qty": 100, "initial_cost": 1000.0,
    }
    account.get_review_account_run = lambda run_id: {
        **(original(run_id) or {}), "start_positions": [held],
        "start_marks": {"600001": 10.0}}

    def truncated(*args, **kwargs):
        return {"rows": {}, "truncated": True}

    with pytest.raises(ExperimentInputError):
        run_account_experiment(
            run_id=1, sell_rules=None, confirm=True, account=account,
            factor_store=object(), load_bars=truncated,
            load_calendar=lambda *a: ["2026-09-01", "2026-09-02"],
            loader_factory=lambda source: (lambda *a: []),
            snapshot_reader=lambda *a: (None, None, "missing"),
            assets=object(), save_experiment=account.save_review_account_experiment,
            heavy_lock=lock, lock_path=tmp_path / "locks" / "experiment.lock")
    assert lock.acquire(blocking=False)
    lock.release()
    assert account.saved == 0

    def exploding_save(*args, **kwargs):
        raise OSError("disk full")

    # Freeze succeeds with one bar code, then saving fails; the lock still releases.
    with pytest.raises(OSError):
        run_account_experiment(
            run_id=1, sell_rules=None, confirm=True, account=account,
            factor_store=object(),
            load_bars=lambda codes, start, end: {"rows": {}, "truncated": False},
            load_calendar=lambda *a: ["2026-09-01", "2026-09-02"],
            loader_factory=lambda source: (lambda *a: []),
            snapshot_reader=lambda *a: (None, None, "missing"),
            assets=object(), save_experiment=exploding_save,
            heavy_lock=lock, lock_path=tmp_path / "locks" / "experiment.lock")
    assert lock.acquire(blocking=False)
    lock.release()
