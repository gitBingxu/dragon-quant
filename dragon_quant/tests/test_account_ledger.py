"""W3 账本：revision/CAS、命名空间、不可变身份、检查点、严格撮合（重写）。"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from dragon_quant.tests.support import patch_storage_dbs
from dragon_quant.review_account.ledger import (
    AccountLedger, Batch, ExecutionCostModel, ExecutionQuote, Fill,
    LedgerConflictError, LedgerIntegrityError, LedgerRevisionError, LedgerState,
    Order, OrderStatus, match_order,
)
from dragon_quant.review_account.ledger.models import SELLABLE_ON, SELLABLE_UNKNOWN

REF = {"strategy_id": "bs_account_decision", "params_hash": "h1"}
REF2 = {"strategy_id": "bs_account_decision", "params_hash": "h1",
        "snapshot_id": "snap-v2"}
_CST = timezone(timedelta(hours=8))


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=_CST).isoformat()


@contextmanager
def _env(tmp_path):
    dragon = str(tmp_path / "dragon.db")
    with patch_storage_dbs(dragon, str(tmp_path / "assets.db")), \
            patch("dragon_quant.infra.storage.paths.DB_PATH", Path(dragon)):
        yield


def _batch(batch_id="b1", *, code="600001", qty=1000, remaining=1000,
           entry="2026-11-17", cost=10.0, mode=SELLABLE_ON,
           sellable="2026-11-18", ref=REF, plan_id="p0"):
    return Batch(batch_id=batch_id, code=code, qty=qty, remaining=remaining,
                 entry_date=entry, cost_price=cost, strategy_ref=ref,
                 plan_id=plan_id, sellable_mode=mode, sellable_date=sellable)


def _commit(ledger, state, event_id="e1", *, source="src", content=None,
            fills=()):
    return ledger.commit_event(
        expected_revision=state.revision, event_id=event_id,
        source_hash=source, content_hash=content or event_id,
        state=state, fills=list(fills))


def _order(order_id="o1", *, code="600001", side="buy", qty=1000,
           created=1000, ref=REF, **kw):
    return Order(order_id, code, side, qty, OrderStatus.RESERVED.value,
                 created_event=created, strategy_ref=ref, **kw)


def _quote(code="600001", *, exec_ts=2000, price=10.0, tradable=True,
           day="2026-11-19", capacity=100000, available_ms=None, stale=False,
           reason=""):
    return ExecutionQuote(
        code=code, event_id=f"evt:{exec_ts}", event_ts=exec_ts,
        event_time=_iso(exec_ts), trade_date=day, price=price,
        available_at=_iso(available_ms if available_ms is not None else exec_ts),
        tradable=tradable, capacity_qty=capacity, reason=reason, stale=stale)


# ─── 创建/读取 ───────────────────────────────────────────────────────────────


def test_create_load_and_read_only_query(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("led-1")
        ledger.create(initial_cash=50_000.0, strategy_ref=REF)
        state = ledger.load_state()
        assert state is not None and state.cash == 50_000.0
        assert state.revision == 0
        assert ledger.query_orders() == []


def test_create_conflict_on_different_initial_cash(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("led-c")
        ledger.create(initial_cash=1000.0, strategy_ref=REF)
        with pytest.raises(LedgerConflictError):
            ledger.create(initial_cash=2000.0, strategy_ref=REF)


def test_missing_database_is_not_created(tmp_path):
    dragon = tmp_path / "absent.db"
    with patch("dragon_quant.infra.storage.paths.DB_PATH", dragon):
        assert AccountLedger("nobody").load_state() is None
    assert not dragon.exists()


def test_corrupt_json_reported(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("led-bad")
        ledger.create(initial_cash=1000.0, strategy_ref=REF)
        from dragon_quant.infra.storage._base import _connect
        conn = _connect()
        conn.execute("UPDATE account_ledgers SET marks_json='{bad' "
                     "WHERE ledger_id='led-bad'")
        conn.commit()
        conn.close()
        with pytest.raises(LedgerIntegrityError):
            ledger.load_state()


# ─── L1：CAS / 命名空间 / 不可变身份 ────────────────────────────────────────


def test_stale_revision_is_rejected(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("led-cas")
        ledger.create(initial_cash=1000.0, strategy_ref=REF)
        s0 = ledger.load_state()
        s0.cash = 900.0
        s0.event_cursor = 200
        _commit(ledger, s0, "e1")
        # 另一个基于旧基准 state 的写入（不同事件）必须被 CAS 拒绝。
        stale = LedgerState(cash=800.0, revision=0, event_cursor=100)
        with pytest.raises(LedgerRevisionError):
            _commit(ledger, stale, "e2")
        assert ledger.load_state().cash == 900.0


def test_batch_namespaces_isolated_between_ledgers(tmp_path):
    with _env(tmp_path):
        a = AccountLedger("A")
        b = AccountLedger("B")
        a.create(initial_cash=1000.0, strategy_ref=REF)
        b.create(initial_cash=1000.0, strategy_ref=REF)
        sa = a.load_state()
        sa.batches = [_batch("shared")]
        _commit(a, sa, "a1")
        sb = b.load_state()
        sb.batches = [_batch("shared", qty=500, remaining=500)]
        _commit(b, sb, "b1")
        assert a.load_state().batches[0].qty == 1000
        assert b.load_state().batches[0].qty == 500


def test_immutable_batch_identity_conflict(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("led-imm")
        ledger.create(initial_cash=1000.0, strategy_ref=REF)
        s = ledger.load_state()
        s.batches = [_batch("b1", qty=1000)]
        _commit(ledger, s, "e1")
        s2 = ledger.load_state()
        s2.batches = [_batch("b1", qty=2000)]   # 同 ID 改原始量
        with pytest.raises(LedgerConflictError):
            _commit(ledger, s2, "e2", source="s2")


def test_immutable_batch_allows_remaining_update(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("led-rem")
        ledger.create(initial_cash=1000.0, strategy_ref=REF)
        s = ledger.load_state()
        s.batches = [_batch("b1", remaining=1000)]
        _commit(ledger, s, "e1")
        s2 = ledger.load_state()
        s2.batches = [_batch("b1", remaining=400)]
        assert _commit(ledger, s2, "e2", source="s2") == "applied"
        assert ledger.load_state().batches[0].remaining == 400


# ─── 幂等 / 冲突 / 回滚 ─────────────────────────────────────────────────────


def test_event_idempotent_and_content_conflict(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("led-idem")
        ledger.create(initial_cash=1000.0, strategy_ref=REF)
        s = ledger.load_state()
        s.cash = 900.0
        assert _commit(ledger, s, "e1", source="h1", content="c1") == "applied"
        # 同事件同来源/内容：幂等。
        assert _commit(ledger, ledger.load_state(), "e1", source="h1",
                       content="c1") == "duplicate"
        with pytest.raises(LedgerConflictError):
            _commit(ledger, ledger.load_state(), "e1", source="h1", content="c2")


def test_same_timestamp_new_revision_needs_new_identity(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("led-ts")
        ledger.create(initial_cash=1000.0, strategy_ref=REF)
        s = ledger.load_state()
        s.event_cursor = 2000
        _commit(ledger, s, "e1", source="h1")
        # 时间戳相同但来源不同（新修订）→ 新事件身份；同 event_id 冲突。
        with pytest.raises(LedgerConflictError):
            _commit(ledger, ledger.load_state(), "e1", source="h2")


def test_rollback_keeps_prior_state(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("led-rb")
        ledger.create(initial_cash=1000.0, strategy_ref=REF)
        s = ledger.load_state()
        s.cash = 900.0
        s.batches = [_batch("b1")]
        fill = Fill("f1", "o1", "600001", "buy", 10.0, 100, 5.0, "e1")
        _commit(ledger, s, "e1", fills=[fill])
        before = ledger.load_state()
        dirty = ledger.load_state()
        dirty.cash = 1.0
        with pytest.raises(LedgerConflictError):
            _commit(ledger, dirty, "e2", source="d",
                    fills=[Fill("f1", "o1", "600001", "buy", 99.0, 100, 5.0,
                                "e1")])
        after = ledger.load_state()
        assert after.cash == before.cash and after.revision == before.revision


def test_two_connections_race_one_event(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("led-race")
        ledger.create(initial_cash=1000.0, strategy_ref=REF)
        results: list[str] = []

        def _run():
            s = ledger.load_state()
            try:
                results.append(_commit(ledger, s, "e1", source="h", content="c"))
            except LedgerRevisionError:
                results.append("revision")

        threads = [threading.Thread(target=_run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count("applied") == 1
        assert "duplicate" in results or "revision" in results


# ─── L5：marks/检查点持久化 ────────────────────────────────────────────────


def test_marks_and_checkpoint_are_persisted(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("led-ck")
        ledger.create(initial_cash=1000.0, strategy_ref=REF)
        s = ledger.load_state()
        s.marks = {"600001": 12.0}
        s.recovery_stage = 2
        s.consecutive_losses = 3
        s.policy_states = [{"code": "600001", "adds_done": 1}]
        s.last_decision = {"status": "ok"}
        _commit(ledger, s, "e1")
        got = ledger.load_state()
        assert got.marks == {"600001": 12.0}
        assert got.recovery_stage == 2
        assert got.consecutive_losses == 3
        assert got.policy_states == [{"code": "600001", "adds_done": 1}]
        assert got.last_decision == {"status": "ok"}


# ─── L7：未知可卖 / 严格校验 ───────────────────────────────────────────────


def test_unknown_sellable_date_is_not_all_sellable():
    batch = _batch(mode=SELLABLE_UNKNOWN, sellable=None)
    assert batch.sellable("2026-11-19") == 0
    batch2 = _batch(entry="2026-11-19", sellable="2026-11-20", mode=SELLABLE_ON)
    assert batch2.sellable("2026-11-19") == 0


def test_invalid_numbers_are_rejected():
    with pytest.raises(Exception):
        Batch(batch_id="b", code="600001", qty=1000, remaining=1000,
              entry_date="2026-11-17", cost_price=float("nan"))
    with pytest.raises(Exception):
        Batch(batch_id="b", code="600001", qty=1000, remaining=1000,
              entry_date="2026-11-17", cost_price=True)  # 布尔伪数字
    with pytest.raises(Exception):
        Batch(batch_id="b", code="600001", qty=1000, remaining=1000,
              entry_date="2026-13-40", cost_price=10.0)  # 非法日期


# ─── L6：保守撮合（时点/容量/触发/到期/归属） ──────────────────────────────


def _flat_state(**kw):
    return LedgerState(cash=kw.pop("cash", 100_000.0), **kw)


def test_no_fill_on_same_or_earlier_execution_time():
    order = _order(created=2000)
    res = match_order(order, _quote(exec_ts=2000), _flat_state(),
                      ExecutionCostModel(), next_trade_date="2026-11-20")
    assert res.unfilled and res.reason == "no_later_event"


def test_future_or_missing_quote_time_blocks_fill():
    order = _order(created=1000)
    future = _quote(exec_ts=2000, available_ms=3000)  # 可得时点在执行时点之后
    assert match_order(order, future, _flat_state(), ExecutionCostModel(),
                       next_trade_date="2026-11-20").reason == "quote_from_future"
    missing = ExecutionQuote(code="600001", event_id="e", event_ts=2000,
                             event_time="", trade_date="2026-11-19", price=10.0,
                             available_at="", tradable=True, capacity_qty=100)
    assert match_order(order, missing, _flat_state(), ExecutionCostModel(),
                       next_trade_date="2026-11-20").reason == "quote_time_missing"


def test_unknown_or_zero_capacity_blocks_fill():
    order = _order(created=1000)
    assert match_order(order, _quote(capacity=None), _flat_state(),
                       ExecutionCostModel(),
                       next_trade_date="2026-11-20").reason == "capacity_unknown"
    assert match_order(order, _quote(capacity=0), _flat_state(),
                       ExecutionCostModel(),
                       next_trade_date="2026-11-20").reason == "capacity_zero"


def test_capacity_limits_order_quantity():
    order = _order(created=1000, qty=1000)
    res = match_order(order, _quote(capacity=600), _flat_state(),
                      ExecutionCostModel(), next_trade_date="2026-11-20")
    assert res.capacity_used == 600
    assert res.order.filled_qty == 600


def test_trigger_not_met_keeps_waiting():
    order = _order(created=1000, trigger_kind="price_above", trigger_price=10.5)
    res = match_order(order, _quote(price=10.0), _flat_state(),
                      ExecutionCostModel(), next_trade_date="2026-11-20")
    assert res.reason == "trigger_not_met" and not res.fills


def test_expired_order_is_cancelled():
    order = _order(created=1000, valid_until_event=1500)
    res = match_order(order, _quote(exec_ts=2000), _flat_state(),
                      ExecutionCostModel(), next_trade_date="2026-11-20")
    assert res.order.status == OrderStatus.CANCELLED.value


def test_buy_shrinks_to_affordable_lots():
    order = _order(created=1000, qty=1000)
    res = match_order(order, _quote(price=10.0, capacity=100000),
                      _flat_state(cash=4100.0), ExecutionCostModel(),
                      next_trade_date="2026-11-20")
    assert 0 < res.order.filled_qty < 1000
    assert res.order.filled_qty % 100 == 0


def test_sell_only_same_owner_and_capacity():
    owner_a = _batch("a", ref=REF)
    owner_b = _batch("b", ref=REF2)   # 同 strategy_id/params，但 snapshot 不同
    order = _order(side="sell", ref=REF)
    state = LedgerState(cash=0.0, batches=[owner_a, owner_b],
                        trade_date="2026-11-19")
    res = match_order(order, _quote(price=9.5, capacity=100000), state,
                      ExecutionCostModel())
    assert res.order.filled_qty == 1000
    remaining = {b.batch_id: b.remaining for b in res.batch_updates}
    assert remaining["a"] == 0 and "b" not in remaining
