"""``summarize_review_rows``：同一 rows 源的筛选后汇总契约。

Web UI 的 7 张卡片必须与列表使用**同一份筛选结果**，因此汇总从
``query_dragons`` 返回的完整 rows 计算，而不是另走 SQL 统计。本模块锁定：

- 旧键与 ``get_review_summary`` 在正常数据上逐键等值（兼容 wrapper）；
- 13 项筛选逐项与组合下 ``total == len(rows)`` 且派生计数自洽；
- ``version`` 后置过滤参与汇总；
- cannot / NULL / 空集 / 真实无赢家（0%）口径不变；
- v1 严格键保持 ``None`` / ``0``。
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

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


def _insert_loose(conn, trade_date, code, *, status="completed", ret=None,
                  drawdown=None, name=None, score=None, version=""):
    conn.execute(
        "INSERT INTO dragons_v2(trade_date, code, name, composite_score, "
        "max_return_5d, max_drawdown_5d, review_status, version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (trade_date, code, name or f"名字{code}", score, ret, drawdown,
         status, version),
    )
    conn.commit()


def _insert_strict(conn, trade_date, code, *, status="completed", ret=None,
                   drawdown=None, reason=None):
    review_strict.upsert(
        conn, trade_date=trade_date, code=code, source="v2",
        buy_date=trade_date, buy_price=10.0,
        max_return_5d=ret, max_drawdown_5d=drawdown,
        max_return_hold_days=2, review_status=status, reason=reason,
    )
    conn.commit()


def _seed_mixed(conn):
    """A 赢 / B 回撤恰为 -5 不算赢 / C 无严格行 / D pending / E error / F cannot。"""
    _insert_loose(conn, "2026-09-01", "600001", ret=8.0, drawdown=-3.0,
                  score=88.0, version="0.2.4")
    _insert_strict(conn, "2026-09-01", "600001", ret=9.0, drawdown=-4.0)
    _insert_loose(conn, "2026-09-02", "600002", ret=6.0, drawdown=-5.0,
                  score=72.0, version="0.2.0")
    _insert_strict(conn, "2026-09-02", "600002", ret=5.0, drawdown=-5.0)
    _insert_loose(conn, "2026-09-03", "600003", ret=4.5, drawdown=-2.0,
                  score=64.0, version="0.1.0")
    _insert_loose(conn, "2026-09-04", "600004", status="pending",
                  score=55.0, version="0.1.0")
    _insert_loose(conn, "2026-09-05", "600005", status="error",
                  score=45.0, version="0.1.0")
    _insert_loose(conn, "2026-09-06", "600006", ret=7.0, drawdown=-2.0,
                  score=80.0, version="0.3.0")
    _insert_strict(conn, "2026-09-06", "600006", status="cannot_evaluate",
                   reason="停牌或数据缺失无法区分")


def _rows(dragon_path, filters=None, **kwargs):
    with patch("dragon_quant.infra.storage.connection._connect",
               side_effect=lambda: sqlite3.connect(dragon_path)):
        return dragon_store.query_dragons(filters or {}, **kwargs)


# ─── 旧键等值（兼容 wrapper） ───────────────────────────────────────────────


def test_summarize_matches_get_review_summary_exactly(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_mixed(conn)
        finally:
            conn.close()

        rows = _rows(dragon_path)
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(dragon_path)):
            summarized = dragon_store.summarize_review_rows(rows, source="v2")
            legacy = dragon_store.get_review_summary(source="v2")
    finally:
        stack.close()

    assert summarized == legacy
    # 期望值高亮口径：宽松分母 4（A/B/C/F），赢家 A/C/F → 75%。
    assert summarized["total"] == 6
    assert summarized["completed"] == 4
    assert summarized["pending"] == 1
    assert summarized["avg_return"] == 6.38
    assert summarized["win_rate"] == 75.0
    assert summarized["win_rate_loose"] == 50.0
    assert summarized["win_rate_strict"] == 50.0
    assert summarized["win_rate_loose_n"] == 2
    assert summarized["win_rate_strict_n"] == 2
    assert summarized["cannot_evaluate"] == 1
    assert summarized["pending_recompute"] == 1
    assert summarized["failed_recompute"] == 1
    assert summarized["best_stock_code_strict"] == "600001"
    assert summarized["best_return_strict"] == 9.0


def test_get_review_summary_is_a_wrapper_over_the_same_rows(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_mixed(conn)
        finally:
            conn.close()

        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(dragon_path)):
            direct_rows = dragon_store.query_dragons({}, source="v2")
            summarized = dragon_store.summarize_review_rows(
                direct_rows, source="v2")
            legacy = dragon_store.get_review_summary(source="v2")
    finally:
        stack.close()

    assert legacy == summarized


# ─── 派生状态计数 ──────────────────────────────────────────────────────────


def test_derived_counts_only_present_when_requested(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_mixed(conn)
        finally:
            conn.close()
        rows = _rows(dragon_path)
    finally:
        stack.close()

    plain = dragon_store.summarize_review_rows(rows, source="v2")
    assert "derived_completed" not in plain
    assert "derived_pending" not in plain

    counted = dragon_store.summarize_review_rows(
        rows, source="v2", include_derived_counts=True)
    # derived_status: A/B completed；C pending_recompute + D pending 均属待回测；
    # E error 与 F cannot_evaluate 不计入。
    assert counted["derived_completed"] == 2
    assert counted["derived_pending"] == 2


# ─── 空集 / 无赢家 / NULL ───────────────────────────────────────────────────


def test_empty_rows_yield_zero_counts_and_null_metrics():
    summary = dragon_store.summarize_review_rows(
        [], source="v2", include_derived_counts=True)

    assert summary["total"] == 0
    assert summary["completed"] == 0
    assert summary["pending"] == 0
    assert summary["derived_completed"] == 0
    assert summary["derived_pending"] == 0
    assert summary["avg_return"] is None
    assert summary["win_rate"] is None
    assert summary["avg_return_strict"] is None
    assert summary["win_rate_loose"] is None
    assert summary["win_rate_strict"] is None
    assert summary["best_stock_code"] is None
    assert summary["best_return"] is None
    assert summary["best_stock_code_strict"] is None
    assert summary["best_return_strict"] is None


def test_true_no_winner_reports_zero_not_null(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _insert_loose(conn, "2026-09-01", "600001", ret=-1.0, drawdown=-2.0)
            _insert_strict(conn, "2026-09-01", "600001", ret=-1.0, drawdown=-2.0)
        finally:
            conn.close()
        rows = _rows(dragon_path)
    finally:
        stack.close()

    summary = dragon_store.summarize_review_rows(rows, source="v2")

    assert summary["win_rate"] == 0.0
    assert summary["win_rate_loose"] == 0.0
    assert summary["win_rate_strict"] == 0.0
    assert summary["win_rate_loose_n"] == 1
    assert summary["win_rate_strict_n"] == 1


def test_completed_rows_with_null_metrics_stay_in_denominator(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _insert_loose(conn, "2026-09-01", "600001", ret=8.5, drawdown=-3.2)
            _insert_loose(conn, "2026-09-02", "600002", ret=None, drawdown=-1.0)
            _insert_loose(conn, "2026-09-03", "600003", ret=3.0, drawdown=None)
        finally:
            conn.close()
        rows = _rows(dragon_path)
    finally:
        stack.close()

    summary = dragon_store.summarize_review_rows(rows, source="v2")

    assert summary["completed"] == 3
    assert summary["avg_return"] == round((8.5 + 3.0) / 2, 2)
    # 赢家只有 A（B/C 一边为 NULL 不构成胜利），分母仍是 3。
    assert summary["win_rate"] == 33.3


# ─── 13 项筛选逐项 / 组合 ──────────────────────────────────────────────────


def _filtered(dragon_path, filters):
    rows = _rows(dragon_path, filters)
    return rows, dragon_store.summarize_review_rows(
        rows, source="v2", include_derived_counts=True)


def test_each_filter_drives_the_summary_from_the_same_rows(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_mixed(conn)
        finally:
            conn.close()

        cases = {
            "date_from": ({"date_from": "2026-09-03"}, 4),
            "date_to": ({"date_to": "2026-09-03"}, 3),
            "return_min": ({"return_min": 7.0}, 1),
            "return_max": ({"return_max": 5.0}, 1),
            "drawdown_min": ({"drawdown_min": -4.0}, 1),
            "drawdown_max": ({"drawdown_max": -4.0}, 2),
            "code": ({"code_like": "60000"}, 6),
            "name": ({"name_like": "名字60000"}, 6),
            "status": ({"status": ["pending"]}, 1),
            "score_min": ({"score_min": 80.0}, 2),
            "score_max": ({"score_max": 64.0}, 3),
            "version_min": ({"version_min": "0.2"}, 3),
            "version_max": ({"version_max": "0.2"}, 5),
        }
        for label, (filters, expected) in cases.items():
            rows, summary = _filtered(dragon_path, filters)
            assert len(rows) == expected, label
            assert summary["total"] == len(rows) == expected, label
            assert summary["derived_completed"] + summary[
                "derived_pending"] <= summary["total"], label
    finally:
        stack.close()


def test_source_filter_selects_the_v1_caliber(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_mixed(conn)
            conn.execute(
                "INSERT INTO dragons_v1(trade_date, code, name, max_return_5d, "
                "max_drawdown_5d, review_status) VALUES (?,?,?,?,?,?)",
                ("2026-09-01", "600001", "旧样本", 5.0, -2.0, "completed"),
            )
            conn.commit()
        finally:
            conn.close()

        v2_rows = _rows(dragon_path)
        v2_summary = dragon_store.summarize_review_rows(
            v2_rows, source="v2", include_derived_counts=True)
        with patch("dragon_quant.infra.storage.connection._connect",
                   side_effect=lambda: sqlite3.connect(dragon_path)):
            v1_rows = dragon_store.query_dragons({}, source="v1")
            v1_summary = dragon_store.summarize_review_rows(
                v1_rows, source="v1", include_derived_counts=True)
    finally:
        stack.close()

    assert v2_summary["total"] == len(v2_rows) == 6
    assert v2_summary["source"] == "v2"
    assert v1_summary["total"] == len(v1_rows) == 1
    assert v1_summary["source"] == "v1"
    assert v1_summary["win_rate"] == 100.0
    # v1 没有严格口径。
    assert v1_summary["avg_return_strict"] is None
    assert v1_summary["win_rate_strict"] is None
    assert v1_summary["win_rate_strict_n"] == 0
    assert v1_summary["pending_recompute"] == 0
    assert v1_summary["failed_recompute"] == 0


def test_combined_filters_summary_matches_rows(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_mixed(conn)
        finally:
            conn.close()

        rows, summary = _filtered(
            dragon_path,
            {"status": ["completed"], "date_from": "2026-09-01",
             "date_to": "2026-09-03", "return_min": 4.0},
        )
        assert [r["code"] for r in rows] == ["600001", "600002"]
        assert summary["total"] == 2
        assert summary["derived_completed"] == 2
        assert summary["derived_pending"] == 0

        rows, summary = _filtered(
            dragon_path, {"code_like": "600001", "version_min": "0.2"})
        assert [r["code"] for r in rows] == ["600001"]
        assert summary["total"] == 1
        assert summary["derived_completed"] == 1
    finally:
        stack.close()


def test_version_filter_runs_after_sql_and_still_feeds_summary(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_mixed(conn)
        finally:
            conn.close()

        rows, summary = _filtered(dragon_path, {"version_min": "0.2"})
    finally:
        stack.close()

    assert {r["code"] for r in rows} == {"600001", "600002", "600006"}
    assert summary["total"] == 3
    assert summary["derived_completed"] == 2


def test_best_tie_is_deterministic(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _insert_loose(conn, "2026-09-01", "600002", ret=9.0, drawdown=-1.0)
            _insert_loose(conn, "2026-09-02", "600001", ret=9.0, drawdown=-1.0)
            _insert_strict(conn, "2026-09-02", "600001", ret=9.0, drawdown=-1.0)
            _insert_strict(conn, "2026-09-01", "600002", ret=9.0, drawdown=-1.0)
        finally:
            conn.close()
        rows = _rows(dragon_path)
    finally:
        stack.close()

    summary = dragon_store.summarize_review_rows(rows, source="v2")

    assert summary["best_stock_code"] == "600001"
    assert summary["best_stock_code_strict"] == "600001"
