"""双口径 review 存储契约：侧表 schema / UPSERT / 事务 / 汇总 / 查询。"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from dragon_quant.infra.storage import connection
from dragon_quant.scan.storage import dragons as dragon_store
from dragon_quant.scan.storage import review_strict
from dragon_quant.tests.support import patch_storage_dbs


def _init(tmp_path):
    dragon_path = tmp_path / "dragon.db"
    stack = patch_storage_dbs(dragon_path, tmp_path / "assets.db")
    stack.enter_context(patch("dragon_quant.infra.storage.connection._connect",
                              side_effect=lambda: sqlite3.connect(dragon_path)))
    connection.init_db()
    return stack, dragon_path


def _conn(path):
    return sqlite3.connect(path)


def _insert_loose(conn, trade_date, code, *, status="completed",
                  ret=None, drawdown=None, name=None):
    conn.execute(
        "INSERT INTO dragons_v2(trade_date, code, name, max_return_5d, "
        "max_drawdown_5d, review_status) VALUES (?, ?, ?, ?, ?, ?)",
        (trade_date, code, name or f"名字{code}", ret, drawdown, status),
    )
    conn.commit()


def _insert_strict(conn, trade_date, code, *, status="completed",
                   ret=None, drawdown=None, reason=None):
    review_strict.upsert(
        conn, trade_date=trade_date, code=code, source="v2",
        buy_date=trade_date, buy_price=10.0,
        max_return_5d=ret, max_drawdown_5d=drawdown,
        max_return_hold_days=2, review_status=status, reason=reason,
    )
    conn.commit()


# ─── schema ────────────────────────────────────────────────────────────────


def test_strict_table_schema_is_created_idempotently(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        # 第二次 init_db 必须幂等（CREATE IF NOT EXISTS）。
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(dragon_path)):
            connection.init_db()
    finally:
        stack.close()

    conn = _conn(dragon_path)
    try:
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(dragon_review_strict)")}
        indexes = {row[1] for row in conn.execute(
            "PRAGMA index_list(dragon_review_strict)")}
    finally:
        conn.close()

    assert {
        "trade_date", "code", "source", "buy_date", "buy_price",
        "max_return_5d", "max_drawdown_5d", "max_return_hold_days",
        "review_status", "reason", "review_version", "computed_at",
    } <= cols
    assert "idx_dragon_review_strict_status" in indexes
    assert "idx_dragon_review_strict_code" in indexes


def test_upsert_get_and_list_keys(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _insert_strict(conn, "2026-09-01", "600001", ret=6.9,
                           drawdown=-4.95)
        finally:
            conn.close()

        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(dragon_path)):
            row = review_strict.get("2026-09-01", "600001")
            keys = review_strict.list_keys()
    finally:
        stack.close()

    assert row["review_status"] == "completed"
    assert row["max_return_5d"] == 6.9
    assert row["max_drawdown_5d"] == -4.95
    assert keys == {("2026-09-01", "600001")}


def test_upsert_overwrites_existing_strict_row(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _insert_strict(conn, "2026-09-01", "600001", ret=1.0)
            _insert_strict(conn, "2026-09-01", "600001", ret=2.0)
        finally:
            conn.close()
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(dragon_path)):
            row = review_strict.get("2026-09-01", "600001")
    finally:
        stack.close()
    assert row["max_return_5d"] == 2.0


def test_save_dual_review_rolls_back_when_strict_write_fails(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _insert_loose(conn, "2026-09-01", "600001", ret=1.0, drawdown=-1.0)
        finally:
            conn.close()

        loose = {"status": "completed", "buy_date": "2026-09-02",
                 "buy_price": 11.0, "max_return_5d": 9.0,
                 "max_drawdown_5d": -2.0, "max_return_hold_days": 3}
        strict = {"review_status": "completed", "buy_date": "2026-09-02",
                  "buy_price": 11.0, "max_return_5d": 9.0,
                  "max_drawdown_5d": -2.0, "max_return_hold_days": 3}

        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(dragon_path)), \
                patch("dragon_quant.scan.storage.dragons.review_strict.upsert",
                      side_effect=RuntimeError("strict write failed")):
            with pytest.raises(RuntimeError, match="strict write failed"):
                dragon_store.save_dual_review(
                    "2026-09-01", "600001", loose=loose, strict=strict)

        conn = _conn(dragon_path)
        try:
            row = conn.execute(
                "SELECT buy_price, max_return_5d, review_status "
                "FROM dragons_v2 WHERE trade_date='2026-09-01' AND code='600001'"
            ).fetchone()
            strict_count = conn.execute(
                "SELECT COUNT(*) FROM dragon_review_strict").fetchone()[0]
        finally:
            conn.close()
    finally:
        stack.close()

    assert row == (None, 1.0, "completed")  # 宽松写入也被回滚
    assert strict_count == 0


def test_save_dual_review_without_strict_keeps_existing_strict_row(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _insert_loose(conn, "2026-09-01", "600001", ret=1.0, drawdown=-1.0)
            _insert_strict(conn, "2026-09-01", "600001", ret=5.0,
                           drawdown=-2.0)
        finally:
            conn.close()

        loose = {"status": "error", "buy_date": None, "buy_price": None,
                 "max_return_5d": None, "max_drawdown_5d": None,
                 "max_return_hold_days": None}
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(dragon_path)):
            dragon_store.save_dual_review(
                "2026-09-01", "600001", loose=loose, strict=None)
            strict_row = review_strict.get("2026-09-01", "600001")
    finally:
        stack.close()

    assert strict_row["max_return_5d"] == 5.0


# ─── summary ───────────────────────────────────────────────────────────────


def test_summary_dual_win_rates_and_boundaries(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            # A: 两口径都赢
            _insert_loose(conn, "2026-09-01", "600001", ret=8.5, drawdown=-3.2)
            _insert_strict(conn, "2026-09-01", "600001", ret=9.0, drawdown=-4.0)
            # B: 回撤恰为 -5.0 → 不算赢（严格大于）
            _insert_loose(conn, "2026-09-02", "600002", ret=6.0, drawdown=-5.0)
            _insert_strict(conn, "2026-09-02", "600002", ret=6.0, drawdown=-5.0)
            # C: 收益恰为 0 → 不算赢
            _insert_loose(conn, "2026-09-03", "600003", ret=0.0, drawdown=0.0)
            _insert_strict(conn, "2026-09-03", "600003", ret=0.0, drawdown=0.0)
            # D: 严格 cannot_evaluate → 从宽松分母剔除
            _insert_loose(conn, "2026-09-04", "600004", ret=4.5, drawdown=-6.2)
            _insert_strict(conn, "2026-09-04", "600004",
                           status="cannot_evaluate", reason="停牌或数据缺失无法区分")
            # E: 无严格行 → 从宽松分母剔除
            _insert_loose(conn, "2026-09-05", "600005", ret=5.0, drawdown=-1.0)
        finally:
            conn.close()

        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(dragon_path)):
            summary = dragon_store.get_review_summary()
    finally:
        stack.close()

    assert summary["win_rate_strict_n"] == 3
    assert summary["win_rate_strict"] == 33.3
    assert summary["win_rate_loose_n"] == 3
    assert summary["win_rate_loose"] == 33.3
    assert summary["cannot_evaluate"] == 1
    assert summary["pending_recompute"] == 1
    assert summary["failed_recompute"] == 0
    assert summary["avg_return_strict"] == 5.0
    assert summary["best_stock_code_strict"] == "600001"
    assert summary["best_return_strict"] == 9.0
    # 旧键保持宽松口径：A/B/C/D/E 中 A、E 赢 → 2/5。
    assert summary["win_rate"] == 40.0


# ─── query ─────────────────────────────────────────────────────────────────


def _seed_query_rows(conn):
    _insert_loose(conn, "2026-09-01", "600001", ret=100.0, drawdown=-100.0)
    _insert_strict(conn, "2026-09-01", "600001", ret=9.0, drawdown=-4.0)
    # 宽松 completed 但无严格 → pending_recompute
    _insert_loose(conn, "2026-09-02", "600002", ret=8.0, drawdown=-3.0)
    # 宽松 pending
    _insert_loose(conn, "2026-09-03", "600003", status="pending")
    # 宽松 error 且无严格 → error
    _insert_loose(conn, "2026-09-04", "600004", status="error")
    # 严格 cannot_evaluate
    _insert_loose(conn, "2026-09-05", "600005", ret=7.0, drawdown=-2.0)
    _insert_strict(conn, "2026-09-05", "600005",
                   status="cannot_evaluate", reason="停牌或数据缺失无法区分")


def test_query_dragons_uses_strict_filters_and_derived_status(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_query_rows(conn)
        finally:
            conn.close()

        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(dragon_path)):
            all_rows = dragon_store.query_dragons({})
            completed = dragon_store.query_dragons(
                {"status": ["completed"]})
            pending_recompute = dragon_store.query_dragons(
                {"status": ["pending_recompute"]})
            cannot = dragon_store.query_dragons(
                {"status": ["cannot_evaluate"]})
            failed = dragon_store.query_dragons({"status": ["error"]})
            # 宽松值 100 / -100 不得参与筛选：严格 9 / -4 才命中。
            by_return = dragon_store.query_dragons({"return_min": 8.5})
            by_drawdown = dragon_store.query_dragons({"drawdown_max": -4.0})
            sorted_rows = dragon_store.query_dragons(
                {}, sort_by="strict_max_return_5d", sort_dir="desc")
    finally:
        stack.close()

    assert len(all_rows) == 5
    assert [r["code"] for r in completed] == ["600001"]
    assert [r["code"] for r in pending_recompute] == ["600002"]
    assert [r["code"] for r in cannot] == ["600005"]
    assert [r["code"] for r in failed] == ["600004"]
    assert [r["code"] for r in by_return] == ["600001"]
    assert [r["code"] for r in by_drawdown] == ["600001"]

    first = completed[0]
    assert first["strict_reviewed"] is True
    assert first["strict_max_return_5d"] == 9.0
    assert first["max_return_5d"] == 100.0  # 旧宽松字段保留
    assert sorted_rows[0]["code"] == "600001"
    assert sorted_rows[0]["strict_max_return_5d"] == 9.0


def test_query_dragons_v1_keeps_old_columns(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            conn.execute(
                "INSERT INTO dragons_v1(trade_date, code, name, max_return_5d, "
                "max_drawdown_5d, review_status) VALUES (?,?,?,?,?,?)",
                ("2026-09-01", "600001", "旧样本", 5.0, -2.0, "completed"),
            )
            conn.commit()
        finally:
            conn.close()

        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(dragon_path)):
            rows = dragon_store.query_dragons(
                {"return_min": 4.0}, source="v1")
            summary = dragon_store.get_review_summary(source="v1")
    finally:
        stack.close()

    assert [r["code"] for r in rows] == ["600001"]
    assert rows[0]["strict_reviewed"] is False
    assert rows[0]["strict_max_return_5d"] is None
    assert summary["avg_return_strict"] is None
    assert summary["win_rate_strict"] is None
    assert summary["pending_recompute"] == 0
