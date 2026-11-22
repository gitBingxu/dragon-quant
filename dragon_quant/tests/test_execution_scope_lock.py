"""O2：相交 scope 的卖量锁量共享实现（无 scope 旧单/多 owner/部分/重启）。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from dragon_quant.review_account.execution_core.matching import ExecutionCostModel
from dragon_quant.review_account.execution_core.models import (
    SELLABLE_ON, Batch, LedgerState, Order,
)
from dragon_quant.review_account.execution_core.runtime_facts import (
    TEACHING_QUOTE_MAX_AGE_MS,
)
from dragon_quant.review_account.execution_core.transition import (
    TransitionCommand, TransitionLimits, _unlocked_sellable, transition_event,
)
from dragon_quant.review_account.ledger import AccountLedger
from dragon_quant.tests.support import patch_storage_dbs

DAY = "2026-11-18"
REF = {"strategy_id": "bs_account_decision", "params_hash": "h1"}
REF2 = {"strategy_id": "bs_account_decision", "params_hash": "h2"}


def _batch(batch_id, code="600001", *, owner=REF, remaining=1000,
           entry="2026-11-16", sellable_date="2026-11-17"):
    return Batch(batch_id=batch_id, code=code, qty=remaining,
                 remaining=remaining, entry_date=entry, cost_price=10.0,
                 strategy_ref=owner, sellable_mode=SELLABLE_ON,
                 sellable_date=sellable_date)


def _order(order_id, qty, *, owner=REF, batch_ids=(), filled=0, locked=None,
           code="600001"):
    return Order(order_id=order_id, code=code, side="sell", qty=qty,
                 status="reserved", created_event=1000, filled_qty=filled,
                 locked_qty=qty - filled if locked is None else locked,
                 strategy_ref=owner, batch_ids=batch_ids)


def _state(batches, orders=()):
    return LedgerState(cash=100_000.0, trade_date=DAY,
                       batches=list(batches), orders=list(orders))


def _cmd(*, batch_ids=(), qty=1000, owner=REF):
    return TransitionCommand(action="sell", code="600001", qty=qty,
                             owner=owner, plan_id="p1", batch_ids=batch_ids)


def test_broad_order_blocks_scoped_command_and_vice_versa():
    batch = _batch("b")
    state = _state([batch], [_order("broad", 1000)])
    assert _unlocked_sellable(state, _cmd(batch_ids=("b",)), DAY) == 0
    state = _state([batch], [_order("scoped", 400, batch_ids=("b",))])
    assert _unlocked_sellable(state, _cmd(), DAY) == 600


def test_partial_intersection_allocates_per_batch():
    state = _state([_batch("a"), _batch("b")],
                   [_order("cover", 1200, batch_ids=("a", "b"))])
    # 旧单按批次真实分配：a 1000 + b 200；b 剩余 800，a 已无可用。
    assert _unlocked_sellable(state, _cmd(batch_ids=("b",)), DAY) == 800
    assert _unlocked_sellable(state, _cmd(batch_ids=("a",)), DAY) == 0


def test_other_owner_order_does_not_lock_my_batches():
    state = _state([_batch("b")], [_order("theirs", 1000, owner=REF2,
                                          batch_ids=("b",))])
    assert _unlocked_sellable(state, _cmd(batch_ids=("b",)), DAY) == 1000


def test_partial_fill_residual_lock_uses_remaining():
    state = _state([_batch("b", remaining=400)],
                   [_order("partial", 1000, batch_ids=("b",), filled=600)])
    assert _unlocked_sellable(state, _cmd(batch_ids=("b",)), DAY) == 0
    assert _unlocked_sellable(state, _cmd(), DAY) == 0


def _limits():
    return TransitionLimits(cost_model=ExecutionCostModel(),
                            next_trade_date=lambda day: "2026-11-19", ttl_ms=None,
                            max_age_ms=TEACHING_QUOTE_MAX_AGE_MS)


def test_same_event_broad_then_scoped_does_not_double_lock():
    state = _state([_batch("b")])
    result = transition_event(
        state, event_time=1000, trade_date=DAY, quotes={},
        commands=[_cmd(qty=1000), _cmd(qty=1000, batch_ids=("b",))],
        limits=_limits(), match=False)
    assert len(state.orders) == 1
    assert state.orders[0].qty == 1000
    assert any(item["reason"] == "no_sellable" for item in result.rejected)


def test_same_event_scoped_then_broad_clamps_second_quantity():
    state = _state([_batch("b")])
    transition_event(
        state, event_time=1000, trade_date=DAY, quotes={},
        commands=[_cmd(qty=400, batch_ids=("b",)), _cmd(qty=1000)],
        limits=_limits(), match=False)
    quantities = sorted(order.qty for order in state.orders)
    assert quantities == [400, 600]


@contextmanager
def _env(tmp_path):
    dragon = str(tmp_path / "dragon.db")
    with patch_storage_dbs(dragon, str(tmp_path / "assets.db")), \
            patch("dragon_quant.infra.storage.paths.DB_PATH", Path(dragon)):
        yield


def test_restart_preserves_scope_locks(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("locks")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        state = ledger.load_state()
        state.batches = [_batch("b")]
        transition_event(state, event_time=1000, trade_date=DAY, quotes={},
                         commands=[_cmd(qty=1000, batch_ids=("b",))],
                         limits=_limits(), match=False)
        ledger.commit_event(expected_revision=0, event_id="evt-1",
                            source_hash="s1", content_hash="c1", state=state)
        reloaded = ledger.load_state()
        assert reloaded.orders[0].batch_ids == ("b",)
        assert _unlocked_sellable(reloaded, _cmd(), DAY) == 0
        # 反向：先 broad 落库，重载后 scoped 仍看到锁。
        ledger2 = AccountLedger("locks2")
        ledger2.create(initial_cash=100_000.0, strategy_ref=REF)
        state2 = ledger2.load_state()
        state2.batches = [_batch("b")]
        transition_event(state2, event_time=1000, trade_date=DAY, quotes={},
                         commands=[_cmd(qty=1000)], limits=_limits(),
                         match=False)
        ledger2.commit_event(expected_revision=0, event_id="evt-1",
                             source_hash="s1", content_hash="c1", state=state2)
        reloaded2 = ledger2.load_state()
        assert _unlocked_sellable(reloaded2, _cmd(batch_ids=("b",)), DAY) == 0
