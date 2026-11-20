"""C3a：真实运行事实、冻结退出状态与历史 DecisionRecord（A1–A6）。

覆盖：先前已知峰值触发旧冻结移动止盈并在下一支持事件成交；买入当根高点不算
买后历史；T+1 锁仓仍跟踪峰值但不卖当日新增批次；重启与不中断一致；半仓标志
只由真实成交推进；闭合交易连亏只计一次且买入不重置；checkpoint 原子性；有界
只读历史读回（幂等/冲突/隔离/坏库）；两适配器共享事实与执行核心。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from dragon_quant.review_account.execution_core.matching import ExecutionQuote
from dragon_quant.review_account.execution_core.models import (
    Batch, Fill, SELLABLE_ON,
)
from dragon_quant.review_account.execution_core.risk import RiskCaps
from dragon_quant.review_account.execution_core.runtime_facts import (
    MARK_LAST_CLOSE, MARK_QUOTE, BatchFacts, MarkFact, apply_fill_facts,
    TEACHING_QUOTE_MAX_AGE_MS, reduce_batch_facts, reduce_mark_facts,
)
from dragon_quant.review_account.execution_core.transition import (
    TransitionCommand, TransitionLimits, transition_event,
)
from dragon_quant.review_account.ledger import (
    AccountLedger, LedgerConflictError, LedgerError, LedgerHistory,
    LedgerIntegrityError,
)
from dragon_quant.review_account.ledger.matching import ExecutionCostModel
from dragon_quant.review_account.ledger.models import LedgerState
from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.review_account.strategy.account_engine import AccountEngine
from dragon_quant.review_account.strategy.account_runtime import (
    AccountPolicyRuntime,
)
from dragon_quant.review_account.strategy.bs_policy.scenario_execution import (
    ScenarioExecution, ScenarioRun,
)
from dragon_quant.review_account.strategy.runtime import make_provider
from dragon_quant.tests.support import patch_storage_dbs
from dragon_quant.workspace import snapshots
from dragon_quant.workspace.config import BUILTIN_STRATEGY_ID

T0 = 1_700_000_000_000
DAY = "2026-11-18"
NEXT = "2026-11-19"
CALENDAR = ("2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19")


@contextmanager
def _env(tmp_path):
    dragon = str(tmp_path / "dragon.db")
    with patch_storage_dbs(dragon, str(tmp_path / "assets.db")), \
            patch("dragon_quant.infra.storage.paths.DB_PATH", Path(dragon)):
        yield


def _config(stop_pct: float) -> bs.BSPolicyConfig:
    base = bs.teaching_fixture()
    disaster = max(-99.0, stop_pct - 10.0)
    return bs.teaching_fixture(invalidation={
        **base.invalidation.to_dict(), "price_stop_pct": stop_pct,
        "disaster_stop_pct": disaster, "confirm_deadline_days": 2})


def _active_ref(config) -> dict:
    return snapshots.builtin_ref(strategy_id=bs.BS_ACCOUNT_STRATEGY_ID,
                                 params={"bs_policy": config.to_dict()})


def _legacy_ref() -> dict:
    return snapshots.builtin_ref(strategy_id=BUILTIN_STRATEGY_ID, params={})


def _batch(batch_id, code, *, cost, owner, plan_id="p1", remaining=1000,
           entry="2026-11-16", sellable_mode=SELLABLE_ON,
           sellable_date="2026-11-17") -> Batch:
    return Batch(batch_id=batch_id, code=code, qty=remaining,
                 remaining=remaining, entry_date=entry, cost_price=cost,
                 strategy_ref=owner, plan_id=plan_id,
                 sellable_mode=sellable_mode, sellable_date=sellable_date)


class _Event:
    def __init__(self, ts, day, rows, *, last_closes=None):
        self.timestamp = ts
        self.trade_date = day
        self.rows = rows
        self.unavailable = {}
        self.last_closes = last_closes or {}
        self.candidates = []
        self.phase = "bar"
        self.allow_buy = True
        self.evidence_available = ()


def _row(ts, price, *, bar_low=None, bar_close=None, capacity=10 ** 9,
         tradable=True, stale=False, available_at=None):
    row = {"execution_price": price, "execution_timestamp": ts,
           "available_at": ts if available_at is None else available_at,
           "available_qty": capacity, "tradable": tradable, "phase": "bar"}
    if stale:
        row["stale"] = True
    row["bar_low"] = price if bar_low is None else bar_low
    row["bar_close"] = price if bar_close is None else bar_close
    return row


def _engine(ledger, runtime, calendar=CALENDAR):
    return AccountEngine(ledger, runtime, ExecutionCostModel(),
                         calendar=list(calendar), order_ttl_ms=None,
                         risk_caps=RiskCaps.teaching_fixture(),
                         max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)


def _seed(ledger, batches):
    state = ledger.load_state()
    state.batches = list(batches)
    ledger.commit_event(expected_revision=state.revision, event_id="seed",
                        source_hash="seed", content_hash="seed", state=state)


def _runtime_with_legacy(config, provider=None):
    return AccountPolicyRuntime(
        config, strategy_ref=_active_ref(config),
        provider=provider or make_provider(StrategyConfig()))


# ─── A1/A2：先前已知峰值 → 旧冻结移动止盈 → 下一支持事件成交 ───────────────


def test_prior_peak_triggers_frozen_trailing_then_next_event_fills(tmp_path):
    config = _config(-20.0)
    legacy = _legacy_ref()
    with _env(tmp_path):
        ledger = AccountLedger("peak")
        ledger.create(initial_cash=100_000.0, strategy_ref=_active_ref(config))
        _seed(ledger, [_batch("b1", "600001", cost=10.0, owner=legacy)])
        runtime = _runtime_with_legacy(config)
        try:
            engine = _engine(ledger, runtime)
            # 事件1：11.0，峰值未知（买入后首个跟踪事件不算峰值）。
            first = engine.process_event(
                _Event(T0, DAY, {"600001": _row(T0, 11.0)}), decide=True)
            assert first["documented_intents"] == []
            # 事件2：11.5 抬升已知峰值；决策用的是先前事实，不触发。
            second = engine.process_event(
                _Event(T0 + 3_600_000, DAY,
                       {"600001": _row(T0 + 3_600_000, 11.5)}), decide=True)
            assert second["documented_intents"] == []
            facts = engine.state().batch_facts["b1"]
            assert facts.peak_price == pytest.approx(11.5)
            # 事件3：回落 11.0 → 由先前峰值触发移动止盈，同一事件不成交。
            third = engine.process_event(
                _Event(T0 + 7_200_000, DAY,
                       {"600001": _row(T0 + 7_200_000, 11.0, bar_low=10.9,
                                       bar_close=11.0)}), decide=True)
            intents = third["documented_intents"]
            assert len(intents) == 1
            assert intents[0].action == "sell" and intents[0].qty == 1000
            assert intents[0].batch_ids == ("b1",)
            assert third["fills"] == []
            # 事件4：下一支持事件成交。
            fourth = engine.process_event(
                _Event(T0 + 10_800_000, DAY,
                       {"600001": _row(T0 + 10_800_000, 11.0, bar_low=10.9,
                                       bar_close=11.0)}))
            assert [fill["qty"] for fill in fourth["fill_records"]] == [1000]
            state = engine.state()
            assert {b.batch_id: b.remaining for b in state.batches} == {"b1": 0}
        finally:
            runtime.close()


def test_buy_bar_high_is_not_post_buy_history(tmp_path):
    """买入本根高点不能算买后历史：同事件 mark 不抬升峰值。"""
    config = _config(-20.0)
    legacy = _legacy_ref()
    with _env(tmp_path):
        ledger = AccountLedger("buyhigh")
        ledger.create(initial_cash=100_000.0, strategy_ref=_active_ref(config))
        _seed(ledger, [_batch("b1", "600001", cost=10.0, owner=legacy)])
        runtime = _runtime_with_legacy(config)
        try:
            engine = _engine(ledger, runtime)
            engine.process_event(
                _Event(T0, DAY, {"600001": _row(T0, 20.0, bar_close=20.0)}),
                decide=True)
            facts = engine.state().batch_facts["b1"]
            assert facts.entry_source_ts == T0
            assert facts.peak_price is None       # 同事件高点不算买后历史
        finally:
            runtime.close()


def test_restart_matches_uninterrupted_peak_and_protection(tmp_path):
    config = _config(-20.0)
    legacy = _legacy_ref()
    rows = [
        (T0, 11.0), (T0 + 3_600_000, 11.5), (T0 + 7_200_000, 11.0),
        (T0 + 10_800_000, 11.0)]
    with _env(tmp_path):
        led_a = AccountLedger("cont")
        led_a.create(initial_cash=100_000.0, strategy_ref=_active_ref(config))
        _seed(led_a, [_batch("b1", "600001", cost=10.0, owner=legacy)])
        run_a = _runtime_with_legacy(config)
        try:
            engine = _engine(led_a, run_a)
            for index, (ts, price) in enumerate(rows):
                engine.process_event(
                    _Event(ts, DAY, {"600001": _row(ts, price, bar_low=10.9,
                                                    bar_close=price)}),
                    decide=index < 3)
            state_a = engine.state()
        finally:
            run_a.close()
        led_b = AccountLedger("restart")
        led_b.create(initial_cash=100_000.0, strategy_ref=_active_ref(config))
        _seed(led_b, [_batch("b1", "600001", cost=10.0, owner=legacy)])
        for index, (ts, price) in enumerate(rows):
            run_b = _runtime_with_legacy(config)
            try:
                engine_b = _engine(led_b, run_b)
                engine_b.process_event(
                    _Event(ts, DAY, {"600001": _row(ts, price, bar_low=10.9,
                                                    bar_close=price)}),
                    decide=index < 3)
            finally:
                run_b.close()
        state_b = led_b.load_state()
        assert state_a.cash == pytest.approx(state_b.cash)
        assert [(b.batch_id, b.remaining) for b in state_a.batches] == \
            [(b.batch_id, b.remaining) for b in state_b.batches]
        for key in ("peak_price", "took_profit_half", "first_day_seen"):
            assert getattr(state_a.batch_facts["b1"], key) == \
                getattr(state_b.batch_facts["b1"], key)
        assert state_a.mark_facts["600001"].to_row() == \
            state_b.mark_facts["600001"].to_row()


def test_t1_locked_batch_tracks_peak_without_same_day_sell(tmp_path):
    config = _config(-20.0)
    legacy = _legacy_ref()
    with _env(tmp_path):
        ledger = AccountLedger("t1peak")
        ledger.create(initial_cash=100_000.0, strategy_ref=_active_ref(config))
        # 当日新批：entry=DAY，sellable_date=NEXT → 当日不可卖。
        _seed(ledger, [_batch("n1", "600001", cost=10.0, owner=legacy,
                              entry=DAY, sellable_date=NEXT)])
        runtime = _runtime_with_legacy(config)
        try:
            engine = _engine(ledger, runtime)
            engine.process_event(
                _Event(T0, DAY, {"600001": _row(T0, 11.0, bar_low=10.9,
                                                bar_close=11.0)}), decide=True)
            engine.process_event(
                _Event(T0 + 3_600_000, DAY,
                       {"600001": _row(T0 + 3_600_000, 11.5, bar_low=11.4,
                                       bar_close=11.5)}), decide=True)
            facts = engine.state().batch_facts["n1"]
            assert facts.peak_price == pytest.approx(11.5)   # 仍跟踪买后峰值
            assert facts.first_day_seen is False             # 当日不可卖，尚未首日评估
            third = engine.process_event(
                _Event(T0 + 7_200_000, DAY,
                       {"600001": _row(T0 + 7_200_000, 11.0, bar_low=10.9,
                                       bar_close=11.0)}), decide=True)
            intents = third["documented_intents"]
            # T+1 锁仓：义务保留但不可执行（不卖当日新增批次）。
            assert all(i.qty == 0 or i.action != "sell" for i in intents)
            assert engine.state().batches[0].remaining == 1000
            # 下一交易日（首个可卖日）才可卖，且首日状态被记录。
            next_ts = T0 + 86_400_000
            fourth = engine.process_event(
                _Event(next_ts, NEXT,
                       {"600001": _row(next_ts, 11.0, bar_low=10.9,
                                       bar_close=11.0)}), decide=True)
            assert engine.state().batch_facts["n1"].first_day_seen is True
            assert any(i.action == "sell" for i in fourth["documented_intents"])
        finally:
            runtime.close()


# ─── A1：标记新鲜度（未来/过期/旧收盘/不刷新）──────────────────────────────


def test_mark_facts_reject_future_and_keep_last_close_as_estimate():
    facts, reasons = reduce_mark_facts({}, rows={
        "600001": {"execution_price": 10.0, "execution_timestamp": 1000,
                   "available_at": 2000}}, last_closes={}, event_ts=1500)
    assert facts == {} and reasons["600001"] == "mark_from_future"
    facts, _ = reduce_mark_facts({}, rows={
        "600001": {"execution_price": 10.0, "execution_timestamp": 1000,
                   "available_at": 1000, "stale": True}},
        last_closes={}, event_ts=1500)
    assert facts["600001"].kind == MARK_QUOTE
    assert facts["600001"].executable is False
    facts, _ = reduce_mark_facts({}, rows={}, last_closes={"600001": 9.5},
                                 event_ts=1500)
    close = facts["600001"]
    assert close.kind == MARK_LAST_CLOSE and close.executable is False
    # 没有新行情不刷新旧标记的新鲜度。
    facts2, _ = reduce_mark_facts(facts, rows={}, last_closes={}, event_ts=9000)
    assert facts2["600001"].source_ts == 1500
    # 旧收盘不覆盖真实行情。
    facts3, _ = reduce_mark_facts(facts, rows={
        "600001": {"execution_price": 10.1, "execution_timestamp": 2000,
                   "available_at": 2000}}, last_closes={"600001": 1.0},
        event_ts=2500)
    assert facts3["600001"].price == pytest.approx(10.1)
    assert facts3["600001"].kind == MARK_QUOTE


def test_batch_facts_same_source_and_imported_unknown_peak():
    batch = _batch("imp", "600001", cost=10.0, owner=_legacy_ref())
    mark = MarkFact(code="600001", price=12.0, source_ts=T0, available_at=T0)
    facts = reduce_batch_facts({}, batches=[batch], marks={"600001": mark},
                               event_ts=T0, trade_date=DAY, calendar=CALENDAR)
    assert facts["imp"].peak_price is None      # 导入批次不伪造当前水平为峰值
    later = MarkFact(code="600001", price=12.5, source_ts=T0 + 1,
                     available_at=T0 + 1)
    facts = reduce_batch_facts(facts, batches=[batch],
                               marks={"600001": later}, event_ts=T0 + 1,
                               trade_date=DAY, calendar=CALENDAR)
    assert facts["imp"].peak_price == pytest.approx(12.5)


def test_half_flag_advances_only_on_real_partial_sell_fill():
    batch = _batch("b1", "600001", cost=10.0, owner=_legacy_ref())
    facts = {"b1": BatchFacts(batch_id="b1", entry_source_ts=T0)}
    unchanged = apply_fill_facts(facts, fills=[], batches_before={"b1": batch},
                                 event_ts=T0 + 1)
    assert unchanged["b1"].took_profit_half is False   # 拒单/零容量不推进
    fill = Fill(fill_id="f1", order_id="o1", code="600001", side="sell",
                price=11.0, qty=500, fee=5.0, event_id="e", batch_id="b1")
    updated = apply_fill_facts(facts, fills=[fill], batches_before={"b1": batch},
                               event_ts=T0 + 1)
    assert updated["b1"].took_profit_half is True
    assert updated["b1"].last_fill_event == T0 + 1
    # 小幅减仓不是半仓腿：不误置唯一性守卫。
    small = Fill(fill_id="f2", order_id="o2", code="600001", side="sell",
                 price=11.0, qty=100, fee=1.0, event_id="e2", batch_id="b1")
    assert apply_fill_facts(facts, fills=[small],
                            batches_before={"b1": batch},
                            event_ts=T0 + 2)["b1"].took_profit_half is False


# ─── A5：闭合交易结算（买入不重置连亏、部分平仓不重复计）──────────────────


REF = {"strategy_id": "bs_account_decision", "params_hash": "h1"}


def _quote(ts, price, *, day, capacity=10 ** 9):
    return ExecutionQuote(
        code="600001", event_id=f"evt:{ts}", event_ts=ts,
        event_time="2026-11-19T10:00:00+08:00", trade_date=day,
        price=price, available_at="2026-11-19T10:00:00+08:00",
        tradable=True, capacity_qty=capacity)


def _limits(day="2026-11-19"):
    return TransitionLimits(cost_model=ExecutionCostModel(),
                            next_trade_date=lambda _d: "2026-11-19",
                            ttl_ms=None, risk_caps=RiskCaps.teaching_fixture(),
                            allow_unknown_risk=True,
                            max_age_ms=TEACHING_QUOTE_MAX_AGE_MS)


def test_buy_does_not_reset_losses_and_partial_close_counts_once():
    state = LedgerState(cash=100_000.0, consecutive_losses=2)
    limits = _limits()
    # 买入：创建 → 下一事件成交。
    transition_event(state, event_time=1, trade_date="2026-11-18", quotes={},
                     commands=[TransitionCommand(action="buy", code="600001",
                                                 qty=1000, owner=REF,
                                                 plan_id="p1", limit_price=10.5,
                                                 risk_amount=100.0)],
                     limits=limits)
    transition_event(state, event_time=2, trade_date="2026-11-18",
                     quotes={"600001": _quote(2, 10.0, day="2026-11-18")},
                     commands=[], limits=limits)
    assert state.consecutive_losses == 2          # 买入不重置连亏
    # 部分卖出（亏）：不单独计一次连亏。
    transition_event(state, event_time=3, trade_date="2026-11-19",
                     quotes={"600001": _quote(3, 9.0, day="2026-11-19")},
                     commands=[TransitionCommand(action="sell", code="600001",
                                                 qty=400, owner=REF,
                                                 plan_id="p1")],
                     limits=limits)
    transition_event(state, event_time=4, trade_date="2026-11-19",
                     quotes={"600001": _quote(4, 9.0, day="2026-11-19")},
                     commands=[], limits=limits)
    assert state.consecutive_losses == 2
    # 剩余部分卖出：整组清零后才记一次闭合亏损。
    transition_event(state, event_time=5, trade_date="2026-11-19",
                     quotes={"600001": _quote(5, 9.0, day="2026-11-19")},
                     commands=[TransitionCommand(action="sell", code="600001",
                                                 qty=600, owner=REF,
                                                 plan_id="p1")],
                     limits=limits)
    transition_event(state, event_time=6, trade_date="2026-11-19",
                     quotes={"600001": _quote(6, 9.0, day="2026-11-19")},
                     commands=[], limits=limits)
    assert state.consecutive_losses == 3
    assert len(state.closed_trades) == 1
    record = state.closed_trades[0]
    assert record["loss"] is True and record["realized_net"] < 0
    # 净额含买/卖费：低于纯价格差。
    assert record["realized_net"] < (9.0 - 10.0) * 1000


# ─── A3/A4：checkpoint 原子性 + append-only 历史读回 ───────────────────────


def test_decision_history_paged_and_no_decision_event_audited(tmp_path):
    config = _config(-20.0)
    legacy = _legacy_ref()
    with _env(tmp_path):
        ledger = AccountLedger("hist")
        ledger.create(initial_cash=100_000.0, strategy_ref=_active_ref(config))
        _seed(ledger, [_batch("b1", "600001", cost=10.0, owner=legacy)])
        runtime = _runtime_with_legacy(config)
        try:
            engine = _engine(ledger, runtime)
            engine.process_event(
                _Event(T0, DAY, {"600001": _row(T0, 10.5, bar_low=10.4)}),
                decide=True)
            # 无决定事件：仍关联执行审计，不伪造决定。
            engine.process_event(
                _Event(T0 + 3_600_000, DAY,
                       {"600001": _row(T0 + 3_600_000, 10.6, bar_low=10.5)}))
            # 决定 2。
            engine.process_event(
                _Event(T0 + 7_200_000, DAY,
                       {"600001": _row(T0 + 7_200_000, 10.6, bar_low=10.5)}),
                decide=True)
        finally:
            runtime.close()
        page1 = LedgerHistory("hist").read(limit=2)
        assert len(page1.records) == 2 and page1.has_more is True
        page2 = LedgerHistory("hist").read(limit=2,
                                           cursor=page1.next_cursor)
        records = list(page1.records) + list(page2.records)
        assert len(records) == 4 and page2.has_more is False
        # seed 无决策 → 不伪造；真实决策含完整 bundle/冻结引用。
        assert records[0].decision is None
        decision = records[1].decision
        assert decision["bundle"] is not None
        assert {"plans", "rejected", "comparisons"} <= set(decision["bundle"])
        assert decision["frozen_ref"]["strategy_id"] == bs.BS_ACCOUNT_STRATEGY_ID
        assert decision["config_hash"] == config.content_hash
        # 无决定的事件仍关联执行审计（不伪造决策）。
        assert records[2].decision is None
        assert records[2].execution is not None
        assert "mark_facts" in records[2].execution
        assert records[2].execution["trade_date"] == DAY
        assert records[3].decision is not None          # 决定 2 也留痕
        assert records[1].record_version == 1
        # 重启后（独立只读仓储）读回同一历史。
        assert [row.event_id for row in LedgerHistory("hist").read(
            limit=50).records] == [row.event_id for row in records]


def test_history_idempotent_conflict_isolation_and_bad_cursor(tmp_path):
    config = _config(-20.0)
    legacy = _legacy_ref()
    with _env(tmp_path):
        ledger = AccountLedger("idem")
        ledger.create(initial_cash=100_000.0, strategy_ref=_active_ref(config))
        _seed(ledger, [_batch("b1", "600001", cost=10.0, owner=legacy)])
        state = ledger.load_state()
        state.marks = {"600001": 10.0}
        ledger.commit_event(expected_revision=state.revision, event_id="e1",
                            source_hash="s", content_hash="c", state=state)
        before = len(LedgerHistory("idem").read(limit=50).records)
        again = ledger.load_state()
        assert ledger.commit_event(
            expected_revision=again.revision, event_id="e1",
            source_hash="s", content_hash="c", state=again) == "duplicate"
        assert len(LedgerHistory("idem").read(limit=50).records) == before
        with pytest.raises(LedgerConflictError):
            ledger.commit_event(expected_revision=again.revision,
                                event_id="e1", source_hash="s",
                                content_hash="different", state=again)
        with pytest.raises(LedgerError):
            LedgerHistory("idem").read(cursor="bogus")
        other = AccountLedger("other")
        other.create(initial_cash=1000.0, strategy_ref=_active_ref(config))
        assert LedgerHistory("other").read(limit=50).records == ()


def test_history_missing_ledger_creates_nothing_and_corrupt_raises(tmp_path):
    dragon = str(tmp_path / "dragon.db")
    with patch_storage_dbs(dragon, str(tmp_path / "assets.db")), \
            patch("dragon_quant.infra.storage.paths.DB_PATH", Path(dragon)):
        assert LedgerHistory("ghost").read().records == ()
        assert not Path(dragon).exists()          # 只读查询不建库/目录
    with _env(tmp_path):
        ledger = AccountLedger("corrupt")
        ledger.create(initial_cash=1000.0, strategy_ref=REF)
        state = ledger.load_state()
        ledger.commit_event(expected_revision=state.revision, event_id="e1",
                            source_hash="s", content_hash="c", state=state,
                            decision_record={"x": 1})
        conn = sqlite3.connect(str(tmp_path / "dragon.db"))
        conn.execute("UPDATE account_ledger_events SET decision_json = '{bad'")
        conn.commit()
        conn.close()
        with pytest.raises(LedgerIntegrityError):
            LedgerHistory("corrupt").read()


def test_commit_failure_rolls_back_without_advancing_state(tmp_path):
    config = _config(-20.0)
    with _env(tmp_path):
        ledger = AccountLedger("rollback")
        ledger.create(initial_cash=100_000.0, strategy_ref=_active_ref(config))
        _seed(ledger, [_batch("b1", "600001", cost=10.0, owner=_legacy_ref())])
        state = ledger.load_state()
        state.cash = 99_999.0
        with patch.object(AccountLedger, "_apply_batch",
                          side_effect=LedgerError("injected")):
            with pytest.raises(LedgerError):
                ledger.commit_event(expected_revision=state.revision,
                                    event_id="e1", source_hash="s",
                                    content_hash="c", state=state)
        reloaded = ledger.load_state()
        assert reloaded.revision == 1 and reloaded.cash == 100_000.0
        assert [r.event_id for r in LedgerHistory("rollback").read().records] \
            == ["seed"]


# ─── A6：两适配器共享事实与执行核心 ────────────────────────────────────────


def test_scenario_and_engine_share_peak_fact_semantics(tmp_path):
    config = _config(-20.0)
    legacy = _legacy_ref()
    steps_prices = (11.0, 11.5, 11.0, 11.0, 11.0)
    dates = ("2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23",
             "2026-11-24")
    with _env(tmp_path):
        ledger = AccountLedger("parity2")
        ledger.create(initial_cash=100_000.0, strategy_ref=_active_ref(config))
        _seed(ledger, [_batch("b1", "600001", cost=10.0, owner=legacy)])
        runtime = _runtime_with_legacy(config)
        try:
            engine = _engine(ledger, runtime)
            for index, price in enumerate(steps_prices):
                ts = T0 + index * 3_600_000
                engine.process_event(
                    _Event(ts, DAY, {"600001": _row(ts, price, bar_low=price,
                                                    bar_close=price)}),
                    decide=index == 0)
            engine_peak = engine.state().batch_facts["b1"].peak_price
        finally:
            runtime.close()
    from dragon_quant.review_account.strategy.bs_policy.account import (
        AccountView, BatchView, PositionView,
    )

    account = AccountView(cash=100_000.0, positions=(PositionView(
        code="600001", qty=1000, sellable_qty=1000, cost_price=10.0,
        entry_date="2026-11-16", mark=11.0, strategy_ref=legacy,
        batches=(BatchView(batch_id="src", qty=1000, sellable_qty=1000,
                           entry_date="2026-11-16", cost_price=10.0,
                           sellable_date="2026-11-17",
                           strategy_ref=legacy),)),))
    steps = [{"at": f"{day}T15:00:00+08:00",
              "available_at": f"{day}T15:00:00+08:00", "trade_date": day,
              "prices": {"600001": price},
              "tradable": {"600001": True}, "capacity": {"600001": 10 ** 9}}
             for day, price in zip(dates, steps_prices)]
    path = bs.scenario_set_from_records(
        [{"scenario_id": "facts", "kind": "teaching_fixture", "fixture": True,
          "calendar": list(dates), "steps": steps}],
        spec=config.scenario).paths[0]
    run = ScenarioRun.build(account, path, config.scenario,
                            execution=ScenarioExecution(
                                risk_caps=RiskCaps.teaching_fixture()))
    for step in path.steps:
        run.execute(step, [])
    scenario_peaks = [fact.peak_price for fact in run.state.batch_facts.values()
                      if fact.peak_price is not None]
    assert scenario_peaks and scenario_peaks[0] == pytest.approx(11.5)
    assert engine_peak == pytest.approx(11.5)


# ─── A2：逐批 Position 事实（不按 code 平均、不伪造峰值）──────────────────


def test_position_uses_persisted_peak_and_half_flag_not_current_mark():
    from dragon_quant.review_account.strategy.bs_policy.owner_scope import (
        ProtectionBatch,
    )
    from dragon_quant.review_account.strategy.owner_protection import (
        _position,
    )

    item = ProtectionBatch(
        batch_id="b1", code="600001", remaining=1000, sellable=1000,
        cost_price=10.0, entry_date="2026-11-16", peak_price=12.0,
        peak_source_ts=T0, peak_available_at=T0, took_profit_half=True)
    position = _position(item, {"strategy_id": "legacy"})
    assert position.qty == 1000 and position.entry_price == pytest.approx(10.0)
    assert position.highest_price == pytest.approx(12.0)
    assert position.highest_return == pytest.approx(20.0)
    assert position.took_profit_half is True
    unknown = _position(ProtectionBatch(
        batch_id="b2", code="600001", remaining=1000, sellable=1000,
        cost_price=10.0, entry_date="2026-11-16"), {})
    assert unknown.highest_price == 0.0 and unknown.highest_return == 0.0


def test_same_plan_batches_evaluated_separately_by_own_cost(tmp_path):
    config = _config(-20.0)
    legacy = _legacy_ref()
    with _env(tmp_path):
        ledger = AccountLedger("perbatch")
        ledger.create(initial_cash=100_000.0, strategy_ref=_active_ref(config))
        _seed(ledger, [
            _batch("hi", "600001", cost=20.0, owner=legacy, plan_id="same"),
            _batch("lo", "600001", cost=10.0, owner=legacy, plan_id="same")])
        runtime = _runtime_with_legacy(config)
        try:
            engine = _engine(ledger, runtime)
            first = engine.process_event(
                _Event(T0, DAY, {"600001": _row(T0, 11.0, bar_low=11.0,
                                                bar_close=11.0)}),
                decide=True)
            sells = [i for i in first["documented_intents"] if i.action == "sell"]
            assert len(sells) == 1 and sells[0].batch_ids == ("hi",)
        finally:
            runtime.close()
