"""Review 口径（strict / loose）查询与汇总契约。

覆盖技术方案 §11.2：默认 strict 与显式 strict 逐值等价、宽松范围/状态/排序
使用宽松列、行内严格字段保留、非法口径回退、v1 强制宽松，以及汇总派生计数
随口径切换而旧键 / ``*_strict`` 键不变。
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
                  drawdown=None, score=None, name=None):
    conn.execute(
        "INSERT INTO dragons_v2(trade_date, code, name, composite_score, "
        "max_return_5d, max_drawdown_5d, review_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (trade_date, code, name or f"名字{code}", score, ret, drawdown, status),
    )
    conn.commit()


def _insert_strict(conn, trade_date, code, *, status="completed", ret=None,
                   drawdown=None, reason=None):
    review_strict.upsert(
        conn, trade_date=trade_date, code=code, source="v2",
        buy_date="2026-09-02", buy_price=10.5,
        max_return_5d=ret, max_drawdown_5d=drawdown,
        max_return_hold_days=2, review_status=status, reason=reason,
    )
    conn.commit()


def _seed_rows(conn):
    """A 双口径完成 / B 严格缺行 / C pending / D error / E 严格 cannot / F no_entry。"""
    _insert_loose(conn, "2026-09-01", "600001", ret=100.0, drawdown=-100.0,
                  score=90.0, name="甲")
    _insert_strict(conn, "2026-09-01", "600001", ret=9.0, drawdown=-4.0)
    _insert_loose(conn, "2026-09-02", "600002", ret=8.0, drawdown=-3.0,
                  score=80.0, name="乙")
    _insert_loose(conn, "2026-09-03", "600003", status="pending", score=70.0)
    _insert_loose(conn, "2026-09-04", "600004", status="error", score=60.0)
    _insert_loose(conn, "2026-09-05", "600005", ret=7.0, drawdown=-2.0,
                  score=50.0, name="戊")
    _insert_strict(conn, "2026-09-05", "600005",
                   status="cannot_evaluate", reason="停牌或数据缺失无法区分")
    _insert_loose(conn, "2026-09-06", "600006", status="no_entry", score=40.0)


def _rows(dragon_path, filters=None, **kwargs):
    with patch("dragon_quant.infra.storage.connection._connect",
               side_effect=lambda: sqlite3.connect(dragon_path)):
        return dragon_store.query_dragons(filters or {}, **kwargs)


def _summarize(dragon_path, rows, **kwargs):
    with patch("dragon_quant.infra.storage.connection._connect",
               side_effect=lambda: sqlite3.connect(dragon_path)):
        return dragon_store.summarize_review_rows(rows, source="v2", **kwargs)


# ─── 默认 / 非法口径 ───────────────────────────────────────────────────────


def test_default_equals_explicit_strict(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_rows(conn)
        finally:
            conn.close()
        default = _rows(dragon_path)
        explicit = _rows(dragon_path, caliber="strict")
    finally:
        stack.close()

    assert default == explicit
    assert [r["code"] for r in default] == [
        "600001", "600002", "600003", "600004", "600005", "600006"]
    assert [r["derived_status"] for r in default] == [
        "completed", "pending_recompute", "pending", "error",
        "cannot_evaluate", "pending_recompute"]


def test_invalid_caliber_falls_back_to_strict(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_rows(conn)
        finally:
            conn.close()
        default = _rows(dragon_path)
        invalid = _rows(dragon_path, caliber="foo")
    finally:
        stack.close()

    assert invalid == default


def test_resolve_caliber_rule_table():
    assert dragon_store.resolve_caliber(None, "v2") == "strict"
    assert dragon_store.resolve_caliber("", "v2") == "strict"
    assert dragon_store.resolve_caliber("loose", "v2") == "loose"
    assert dragon_store.resolve_caliber("LOOSE ", "v2") == "loose"
    assert dragon_store.resolve_caliber("strict", "v1") == "loose"
    assert dragon_store.resolve_caliber(None, "v1") == "loose"
    assert dragon_store.resolve_caliber("foo", "v2") == "strict"


# ─── 范围筛选 ──────────────────────────────────────────────────────────────


def test_loose_range_filters_use_loose_columns(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_rows(conn)
        finally:
            conn.close()

        strict_return = _rows(dragon_path, {"return_min": 8.5},
                              caliber="strict")
        loose_return = _rows(dragon_path, {"return_min": 8.5},
                             caliber="loose")
        # 宽松 100 命中而严格 9 未达 50：同一条件命中集不同。
        strict_return_50 = _rows(dragon_path, {"return_min": 50},
                                 caliber="strict")
        loose_return_50 = _rows(dragon_path, {"return_min": 50},
                                caliber="loose")

        strict_drawdown = _rows(dragon_path, {"drawdown_min": -50},
                                caliber="strict")
        loose_drawdown = _rows(dragon_path, {"drawdown_min": -50},
                               caliber="loose")
    finally:
        stack.close()

    assert [r["code"] for r in strict_return] == ["600001"]
    assert [r["code"] for r in loose_return] == ["600001"]
    assert strict_return_50 == []
    assert [r["code"] for r in loose_return_50] == ["600001"]
    assert [r["code"] for r in strict_drawdown] == ["600001"]
    # 宽松回撤：B(-3)、E(-2) 命中，A(-100) 被排除。
    assert sorted(r["code"] for r in loose_drawdown) == ["600002", "600005"]


# ─── 状态筛选 ──────────────────────────────────────────────────────────────


def test_loose_status_uses_dragons_review_status(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_rows(conn)
        finally:
            conn.close()

        def codes(status):
            return sorted(
                r["code"] for r in _rows(
                    dragon_path, {"status": [status]}, caliber="loose"))

        completed = codes("completed")
        pending_recompute = codes("pending_recompute")
        cannot = codes("cannot_evaluate")
        no_entry = codes("no_entry")
        error = codes("error")
    finally:
        stack.close()

    assert completed == ["600001", "600002", "600005"]
    # 宽松模式不存在严格专有状态。
    assert pending_recompute == []
    assert cannot == []
    assert no_entry == ["600006"]
    assert error == ["600004"]


# ─── 行内字段保留 ──────────────────────────────────────────────────────────


def test_loose_rows_keep_strict_fields(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_rows(conn)
        finally:
            conn.close()
        rows = {r["code"]: r for r in _rows(dragon_path, caliber="loose")}
    finally:
        stack.close()

    strict_keys = {
        "strict_buy_date", "strict_buy_price", "strict_max_return_5d",
        "strict_max_drawdown_5d", "strict_max_return_hold_days",
        "strict_review_status", "strict_review_reason",
    }
    assert strict_keys <= set(rows["600001"])
    assert {"max_return_5d", "max_drawdown_5d", "buy_date", "buy_price"} <= set(
        rows["600001"])

    row_a = rows["600001"]
    assert row_a["max_return_5d"] == 100.0
    assert row_a["strict_max_return_5d"] == 9.0
    assert row_a["strict_reviewed"] is True
    assert rows["600002"]["strict_reviewed"] is False
    assert rows["600002"]["strict_max_return_5d"] is None


# ─── 排序键兼容 ────────────────────────────────────────────────────────────


def test_loose_sort_aliases_and_columns(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_rows(conn)
        finally:
            conn.close()

        loose_by_loose = _rows(dragon_path, sort_by="max_return_5d",
                               caliber="loose")
        # 旧客户端传 strict_* 键在 loose 下语义降级为宽松列。
        loose_by_alias = _rows(dragon_path, sort_by="strict_max_return_5d",
                               caliber="loose")
        strict_by_strict = _rows(dragon_path, sort_by="strict_max_return_5d",
                                 caliber="strict")
        strict_by_loose_key = _rows(dragon_path, sort_by="max_return_5d",
                                    caliber="strict")
    finally:
        stack.close()

    assert [r["code"] for r in loose_by_loose][:3] == [
        "600001", "600002", "600005"]
    assert [r["code"] for r in loose_by_alias] == [
        r["code"] for r in loose_by_loose]
    assert strict_by_strict[0]["code"] == "600001"
    assert strict_by_strict[0]["strict_max_return_5d"] == 9.0
    assert [r["code"] for r in strict_by_loose_key] == [
        r["code"] for r in strict_by_strict]


# ─── v1 强制宽松 ───────────────────────────────────────────────────────────


def test_v1_ignores_caliber_and_stays_loose(tmp_path):
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
            default_rows = dragon_store.query_dragons({}, source="v1")
            forced_rows = dragon_store.query_dragons(
                {}, source="v1", caliber="strict")
            by_return = dragon_store.query_dragons(
                {"return_min": 4.0}, source="v1", caliber="strict")
            summary = dragon_store.summarize_review_rows(
                default_rows, source="v1", caliber="strict",
                include_derived_counts=True)
    finally:
        stack.close()

    assert forced_rows == default_rows
    assert [r["code"] for r in by_return] == ["600001"]
    assert default_rows[0]["strict_max_return_5d"] is None
    assert default_rows[0]["derived_status"] == "completed"
    assert summary["avg_return_strict"] is None
    assert summary["win_rate_strict"] is None
    assert summary["derived_completed"] == 1
    assert summary["derived_pending"] == 0


# ─── 汇总派生计数与键稳定性 ────────────────────────────────────────────────


def test_summary_derived_counts_follow_caliber(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_rows(conn)
        finally:
            conn.close()
        rows = _rows(dragon_path)
        strict = _summarize(dragon_path, rows, include_derived_counts=True,
                            caliber="strict")
        loose = _summarize(dragon_path, rows, include_derived_counts=True,
                           caliber="loose")
    finally:
        stack.close()

    assert strict["derived_completed"] == 1  # A
    assert strict["derived_pending"] == 3  # B / C / F
    assert loose["derived_completed"] == 3  # A / B / E
    assert loose["derived_pending"] == 1  # C（F no_entry、D error 不计）


def test_summary_legacy_and_strict_keys_unchanged_by_caliber(tmp_path):
    stack, dragon_path = _init(tmp_path)
    try:
        conn = _conn(dragon_path)
        try:
            _seed_rows(conn)
        finally:
            conn.close()
        rows = _rows(dragon_path)
        strict = _summarize(dragon_path, rows, include_derived_counts=True,
                            caliber="strict")
        loose = _summarize(dragon_path, rows, include_derived_counts=True,
                           caliber="loose")
        legacy = _summarize(dragon_path, rows)
    finally:
        stack.close()

    derived_keys = {"derived_completed", "derived_pending"}
    assert {k: v for k, v in strict.items() if k not in derived_keys} == {
        k: v for k, v in loose.items() if k not in derived_keys}
    assert legacy["avg_return"] == strict["avg_return"]
    assert legacy["avg_return_strict"] == strict["avg_return_strict"]
    assert legacy["win_rate_loose"] == strict["win_rate_loose"]
    assert legacy["win_rate_strict"] == strict["win_rate_strict"]
    assert legacy["best_return_strict"] == 9.0
    assert "derived_completed" not in legacy
