"""``bs_*`` Repository：run 生命周期、幂等哈希、force、筛选排序与 stale。"""

from __future__ import annotations

import sqlite3

import pytest

from dragon_quant.bs_eval import constants, storage
from dragon_quant.bs_eval.main import _select


@pytest.fixture
def db(tmp_path, monkeypatch):
    from dragon_quant.infra.storage import connection

    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "dragon.db")
    monkeypatch.setattr(connection, "_schema_ready", False)
    return tmp_path / "dragon.db"


def payload(code="600001", **overrides):
    base = {
        "trade_date": "2026-09-01", "code": code, "name": "样本",
        "source": "v2", "strategy_hash": "abcdef1234567890",
        "evaluator_version": constants.EVALUATOR_VERSION,
        "eval_status": "open", "eval_note": None, "is_terminal": 0,
        "data_quality": "strict_5min", "replay_days": 3, "fallback_days": 0,
        "suspended_days": 0,
        "buy_date": "2026-09-02", "buy_rule_code": "buy_open_ma5_pullback",
        "buy_rule_text": "开盘贴近上日MA5", "buy_loose_price": 9.8,
        "buy_strict_price": 10.02, "buy_data_quality": "strict_5min",
        "buy_loose_max_return_5d": 3.0, "buy_loose_max_drawdown_5d": -2.0,
        "buy_strict_max_return_5d": 2.9, "buy_strict_max_drawdown_5d": -2.1,
        "buy_max_return_hold_days": 2, "buy_window_partial": 0,
        "sell_date": None, "sell_rule_code": None, "sell_rule_text": None,
        "sell_price": None, "sell_hold_days": None, "sell_data_quality": None,
        "sell_half_date": None, "sell_half_price": None, "sell_half_qty": None,
        "sell_half_rule_code": None, "sell_max_return_5d": None,
        "sell_max_drawdown_5d": None, "sell_max_return_hold_days": None,
        "sell_window_partial": 0,
    }
    base.update(overrides)
    return base


def start(**overrides):
    values = dict(range_from=None, range_to=None, limit_n=None, force=False,
                  evaluator_version=constants.EVALUATOR_VERSION,
                  strategy_name="dragon_pullback_daily",
                  strategy_params={"initial_cash": 10_000_000.0},
                  strategy_hash="abcdef1234567890")
    values.update(overrides)
    return storage.start_run(**values)


COUNTERS = {
    "rows_scanned": 3, "rows_skipped_terminal": 1, "rows_evaluated": 2,
    "rows_no_signal": 1, "rows_bought": 1, "rows_sold": 0,
    "rows_partial": 1, "rows_error": 0,
}


def test_run_lifecycle_and_latest_run(db):
    run_id = start()
    assert run_id > 0
    storage.finish_run(run_id, COUNTERS, note="strategy_or_evaluator_changed_auto_force")
    latest = storage.latest_run()
    assert latest["id"] == run_id
    assert latest["counters"] == COUNTERS
    assert latest["note"] == "strategy_or_evaluator_changed_auto_force"
    assert latest["strategy_params"] == {"initial_cash": 10_000_000.0}
    assert latest["force"] is False
    assert latest["finished_at"]


def test_upsert_unique_key_and_queries(db):
    run_id = start()
    assert storage.upsert_signal({**payload(), "run_id": run_id}) is True
    assert storage.upsert_signal(
        {**payload(), "run_id": run_id, "name": "改名"}) is True
    rows = storage.query_signals(side="buy", sort_by="buy_date")
    assert len(rows) == 1
    assert rows[0]["name"] == "改名"

    conn = sqlite3.connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) FROM bs_signals").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_same_content_hash_does_not_rewrite_business_fields(db):
    run_id = start()
    original = {**payload(), "run_id": run_id}
    storage.upsert_signal(original)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE bs_signals SET name = '外部修改' WHERE code = '600001'")
        conn.commit()
    finally:
        conn.close()
    assert storage.upsert_signal(original) is False
    rows = storage.query_signals(side="buy")
    assert rows[0]["name"] == "外部修改"
    conn = sqlite3.connect(db)
    try:
        attempt = conn.execute(
            "SELECT last_attempt_at FROM bs_signals WHERE code = '600001'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert attempt


def test_force_rewrites_business_fields(db):
    run_id = start()
    original = {**payload(), "run_id": run_id}
    storage.upsert_signal(original)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE bs_signals SET name = '外部修改' WHERE code = '600001'")
        conn.commit()
    finally:
        conn.close()
    assert storage.upsert_signal(original, force=True) is True
    rows = storage.query_signals(side="buy")
    assert rows[0]["name"] == "样本"


def test_query_filters_partial_rule_and_sort_whitelist(db):
    run_id = start()
    storage.upsert_signal({
        **payload(code="1", trade_date="2026-09-01"),
        "run_id": run_id,
    })
    storage.upsert_signal({
        **payload(code="2", trade_date="2026-09-02",
                   buy_date="2026-09-03", buy_window_partial=1,
                   buy_rule_code="buy_open_turn_strong"),
        "run_id": run_id,
    })
    base = storage.query_signals(side="buy", sort_by="buy_date", sort_dir="asc")
    assert [row["code"] for row in base] == ["1", "2"]

    partial = storage.query_signals(
        side="buy", filters={"exclude_partial": True})
    assert [row["code"] for row in partial] == ["1"]

    rule = storage.query_signals(
        side="buy", filters={"rule_codes": ["buy_open_turn_strong"]})
    assert [row["code"] for row in rule] == ["2"]

    traded = storage.query_signals(
        side="buy", filters={"date_from": "2026-09-02", "date_to": "2026-09-02"})
    assert [row["code"] for row in traded] == ["2"]

    # 未知排序键回退默认列，dir 只接受 asc/desc。
    fallback = storage.query_signals(side="buy", sort_by="evil", sort_dir="drop")
    assert len(fallback) == 2


def test_sell_side_requires_sell_date(db):
    run_id = start()
    storage.upsert_signal({**payload(), "run_id": run_id})
    assert storage.query_signals(side="sell") == []
    storage.upsert_signal({
        **payload(), "run_id": run_id, "eval_status": "closed",
        "is_terminal": 1, "sell_date": "2026-09-04",
        "sell_rule_code": "hard_stop_loss", "sell_price": 9.5,
        "sell_max_return_5d": 1.0, "sell_max_drawdown_5d": -1.0,
    })
    rows = storage.query_signals(side="sell", sort_by="sell_max_return_5d")
    assert len(rows) == 1


def test_count_stale_and_list_current(db):
    run_id = start()
    storage.upsert_signal({**payload(code="1"), "run_id": run_id})
    storage.upsert_signal({
        **payload(code="2"), "run_id": run_id, "strategy_hash": "oldhash"})
    storage.upsert_signal({
        **payload(code="3"), "run_id": run_id, "evaluator_version": "bs_eval/0"})
    current = storage.list_current("abcdef1234567890", constants.EVALUATOR_VERSION)
    assert [row["code"] for row in current] == ["1"]
    assert storage.count_stale("abcdef1234567890", constants.EVALUATOR_VERSION) == 2


def test_selection_skips_terminal_unless_forced_and_rotates_by_attempt():
    rows = [
        {"trade_date": "2026-09-01", "code": "1"},
        {"trade_date": "2026-09-02", "code": "2"},
        {"trade_date": "2026-09-03", "code": "3"},
    ]
    existing = {
        ("2026-09-01", "1"): {"is_terminal": 1, "last_attempt_at": None},
        ("2026-09-02", "2"): {"is_terminal": 0, "last_attempt_at": "2026-10-01 10:00:00"},
        ("2026-09-03", "3"): {"is_terminal": 0, "last_attempt_at": None},
    }
    selected, skipped = _select(rows, existing, force=False, limit=None)
    assert skipped == 1
    assert [row["code"] for row in selected] == ["3", "2"]

    forced, skipped_force = _select(rows, existing, force=True, limit=2)
    assert skipped_force == 0
    assert len(forced) == 2
