"""C3b：事实前置返工（P1–P4）真实回归。

覆盖 main 给出的四个时点反例与边界：
- P1 源时间/可得时间合法性、缺失不补造、显式年龄模型、旧修订不覆盖新 source；
- P2 已完成 bar 真实 high 并入后续峰值（买入本根不算买后 high，同根不反向激活）；
- P3 整库损坏 → 完整性错误（不是空历史），负 revision 游标显式拒绝；
- P4 全部实际决定输入参与事件身份/冲突检测（同事件不同输入冲突，相同输入幂等）。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from dragon_quant.review_account.execution_core.matching import ExecutionCostModel
from dragon_quant.review_account.execution_core.models import (
    Batch, SELLABLE_ON,
)
from dragon_quant.review_account.execution_core.risk import RiskCaps
from dragon_quant.review_account.execution_core.runtime_facts import (
    TEACHING_QUOTE_MAX_AGE_MS, BatchFacts, MarkFact, observe_row,
    reduce_batch_facts, reduce_mark_facts,
)
from dragon_quant.review_account.ledger import (
    AccountLedger, LedgerError, LedgerHistory, LedgerIntegrityError,
)
from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.review_account.strategy.account_engine import AccountEngine
from dragon_quant.review_account.strategy.account_runtime import AccountDecision
from dragon_quant.strategy_sdk.types import AccountOrderIntent
from dragon_quant.tests.support import patch_storage_dbs

T0 = 1_700_000_000_000
HOUR = 3_600_000
DAY = "2026-11-18"
CALENDAR = ("2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19")


@contextmanager
def _env(tmp_path):
    dragon = str(tmp_path / "dragon.db")
    with patch_storage_dbs(dragon, str(tmp_path / "assets.db")), \
            patch("dragon_quant.infra.storage.paths.DB_PATH", Path(dragon)):
        yield


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


class _StubRuntime:
    strategy_ref = {"strategy_id": "stub"}

    def __init__(self, queue=None):
        self.queue = list(queue or [])

    def decide(self, state, *, candidates, decision_time, **kwargs):
        intents = self.queue.pop(0) if self.queue else []
        return AccountDecision(bundle=None, intents=tuple(intents))


def _row(ts, price, *, available_at=None, bar_high=None, bar_complete=False,
         interval_start=None, interval_end=None, capacity=10 ** 9):
    row = {"execution_price": price, "execution_timestamp": ts,
           "available_at": ts if available_at is None else available_at,
           "available_qty": capacity, "tradable": True, "phase": "bar"}
    if bar_high is not None:
        row["bar_high"] = bar_high
    if bar_complete:
        row["bar_complete"] = True
    if interval_start is not None:
        row["interval_start"] = interval_start
    if interval_end is not None:
        row["interval_end"] = interval_end
    return row


def _buy(qty=1000):
    return AccountOrderIntent(action="buy", code="600001", qty=qty,
                              plan_id="p1", strategy_ref={"strategy_id": "stub"},
                              risk_amount=100.0)


def _engine(ledger, runtime, *, max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS):
    return AccountEngine(ledger, runtime, ExecutionCostModel(),
                         calendar=list(CALENDAR), order_ttl_ms=None,
                         risk_caps=RiskCaps.teaching_fixture(),
                         max_quote_age_ms=max_quote_age_ms)


# ─── P1：源时间/可得时间/新鲜度/修订 ───────────────────────────────────────


def test_observe_row_requires_source_time_and_rejects_future():
    fact, reason = observe_row("600001", {"execution_price": 10.0},
                               event_ts=T0)
    assert fact is None and reason == "source_time_missing"
    # source 晚于事件时刻：未来源，不接受。
    fact, reason = observe_row(
        "600001", {"execution_price": 10.0, "execution_timestamp": T0 + 1,
                   "available_at": T0}, event_ts=T0)
    assert fact is None and reason == "mark_from_future"
    # available 晚于事件时刻：同样不可用。
    fact, reason = observe_row(
        "600001", {"execution_price": 10.0, "execution_timestamp": T0,
                   "available_at": T0 + 1}, event_ts=T0)
    assert fact is None and reason == "mark_from_future"


def test_observe_row_declared_age_marks_stale():
    # 未声明年龄模型：保留原语义（可执行）。
    fact, _ = observe_row("600001", _row(100, 10.0), event_ts=1_000_000)
    assert fact.executable is True
    # 显式声明的年龄模型：超龄只作估值（stale），不能抬升可执行峰值。
    fact, _ = observe_row("600001", _row(100, 10.0), event_ts=1_000_000,
                          max_age_ms=1_000)
    assert fact is not None and fact.stale is True and fact.executable is False


def test_mark_revision_does_not_overwrite_newer_source():
    newer, _ = observe_row("600001", _row(2000, 12.0), event_ts=2000)
    facts = {"600001": newer}
    # 迟到的旧 source（1000）不得覆盖更新的 source（2000）。
    facts2, reasons = reduce_mark_facts(
        facts, rows={"600001": _row(1000, 10.0)}, last_closes={},
        event_ts=3000)
    assert facts2["600001"].price == pytest.approx(12.0)
    assert reasons["600001"] == "stale_source_revision"
    # 更新的 source 正常覆盖。
    facts3, _ = reduce_mark_facts(facts2, rows={"600001": _row(3000, 13.0)},
                                  last_closes={}, event_ts=3000)
    assert facts3["600001"].price == pytest.approx(13.0)


def test_engine_quote_without_source_time_cannot_fill(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("p1-quote")
        ledger.create(initial_cash=100_000.0, strategy_ref={"strategy_id": "s"})
        engine = _engine(ledger, _StubRuntime([[_buy()]]))
        engine.process_event(_Event(T0, DAY, {"600001": _row(T0, 10.0)}),
                             decide=True)
        # 后一事件报价缺源执行时点：不能补事件时钟冒充可执行。
        later = engine.process_event(_Event(T0 + HOUR, DAY, {
            "600001": {"execution_price": 10.0, "available_qty": 10 ** 9,
                       "tradable": True}}))
        assert later["fills"] == []


def test_engine_declared_quote_age_blocks_old_quote(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("p1-age")
        ledger.create(initial_cash=100_000.0, strategy_ref={"strategy_id": "s"})
        engine = _engine(ledger, _StubRuntime([[_buy()]]),
                         max_quote_age_ms=60_000)
        engine.process_event(_Event(T0, DAY, {"600001": _row(T0, 10.0)}),
                             decide=True)
        # 后一事件携带旧源（1 小时后，超过声明年龄）→ 不能成交。
        later = engine.process_event(_Event(
            T0 + HOUR, DAY, {"600001": _row(T0, 10.0)}))
        assert later["fills"] == []


def test_engine_does_not_authorize_new_risk_from_stale_marks(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("p1-marks")
        ledger.create(initial_cash=100_000.0, strategy_ref={"strategy_id": "s"})
        engine = _engine(ledger, _StubRuntime([[], [_buy()]]))
        engine.process_event(_Event(T0, DAY, {"600001": _row(T0, 10.0)}),
                             decide=True)
        # 本事件无报价：历史 mark 存在也不能授权新增风险 → 无订单。
        second = engine.process_event(_Event(T0 + HOUR, DAY, {}), decide=True)
        assert engine.state().orders == []
        assert any(r["reason"] == "no_price_reference"
                   for r in second["rejected"])


# ─── P2：已完成 bar 真实 high 并入后续峰值 ─────────────────────────────────


def _batch(entry="2026-11-16"):
    return Batch(batch_id="b1", code="600001", qty=1000, remaining=1000,
                 entry_date=entry, cost_price=10.0,
                 strategy_ref={"strategy_id": "s"}, plan_id="p1",
                 sellable_mode=SELLABLE_ON, sellable_date="2026-11-17")


def test_completed_bar_high_joins_peak_but_buy_bar_excluded():
    batch = _batch()
    # 买入当根：bar 区间跨越入场（09:30–10:00 含 09:50 入场）→ high 不算买后历史。
    buy_bar = MarkFact(code="600001", price=10.5, source_ts=T0,
                       available_at=T0, bar_high=20.0, bar_complete=True,
                       interval_start_ts=T0 - HOUR, interval_end_ts=T0)
    facts = reduce_batch_facts({}, batches=[batch], marks={"600001": buy_bar},
                               event_ts=T0, trade_date="2026-11-16",
                               calendar=CALENDAR)
    assert facts["b1"].peak_price is None
    # 后续已完成 bar，区间**完全落在买后**：真实 high 并入峰值。
    later = MarkFact(code="600001", price=10.6, source_ts=T0 + HOUR,
                     available_at=T0 + HOUR, bar_high=15.0, bar_complete=True,
                     interval_start_ts=T0 + 1, interval_end_ts=T0 + HOUR)
    facts = reduce_batch_facts(facts, batches=[batch],
                               marks={"600001": later},
                               event_ts=T0 + HOUR, trade_date=DAY,
                               calendar=CALENDAR)
    assert facts["b1"].peak_price == pytest.approx(15.0)
    assert facts["b1"].peak_source_ts == T0 + HOUR


def test_bar_high_without_interval_keeps_quote_lower_bound():
    """缺明确区间：bar_high 不得冒充完整买后高点，只保留 quote 价下界。"""
    batch = _batch()
    mark = MarkFact(code="600001", price=10.6, source_ts=T0 + HOUR,
                    available_at=T0 + HOUR, bar_high=15.0, bar_complete=True)
    facts = reduce_batch_facts(
        {"b1": BatchFacts(batch_id="b1", entry_source_ts=T0)},
        batches=[batch], marks={"600001": mark}, event_ts=T0 + HOUR,
        trade_date=DAY, calendar=CALENDAR)
    assert facts["b1"].peak_price == pytest.approx(10.6)


def test_cross_entry_interval_high_is_excluded():
    """main 反例：bar[09:30,10:00]，09:50 入场，09:40 high=15 不能记为买后峰值。"""
    batch = _batch()
    entry = T0 + HOUR // 2          # 09:50（bar 区间中段）
    mark = MarkFact(code="600001", price=10.0, source_ts=T0 + HOUR,
                    available_at=T0 + HOUR, bar_high=15.0, bar_complete=True,
                    interval_start_ts=T0, interval_end_ts=T0 + HOUR)
    facts = reduce_batch_facts(
        {"b1": BatchFacts(batch_id="b1", entry_source_ts=entry)},
        batches=[batch], marks={"600001": mark}, event_ts=T0 + HOUR,
        trade_date=DAY, calendar=CALENDAR)
    assert facts["b1"].peak_price == pytest.approx(10.0)


def test_incomplete_bar_high_is_ignored():
    batch = _batch()
    # 未完成 bar：high 不作为买后峰值事实（价格本身仍按可执行价入事实）。
    mark = MarkFact(code="600001", price=10.6, source_ts=T0 + HOUR,
                    available_at=T0 + HOUR, bar_high=15.0, bar_complete=False)
    facts = reduce_batch_facts(
        {"b1": BatchFacts(batch_id="b1", entry_source_ts=T0)},
        batches=[batch], marks={"600001": mark}, event_ts=T0 + HOUR,
        trade_date=DAY, calendar=CALENDAR)
    assert facts["b1"].peak_price == pytest.approx(10.6)


def test_same_bar_high_does_not_trigger_same_event_exit(tmp_path):
    config = bs.teaching_fixture()
    with _env(tmp_path):
        ledger = AccountLedger("p2")
        ledger.create(initial_cash=100_000.0, strategy_ref=config.to_dict())
        state = ledger.load_state()
        state.batches = [_batch()]
        ledger.commit_event(expected_revision=state.revision, event_id="seed",
                            source_hash="seed", content_hash="seed", state=state)
        engine = _engine(ledger, _StubRuntime([[], []]))
        # 事件1：建立 entry_source_ts（同根 high 不算买后历史）。
        engine.process_event(_Event(
            T0, DAY, {"600001": _row(
                T0, 10.5, bar_high=30.0, bar_complete=True,
                interval_start=T0 - HOUR, interval_end=T0)}), decide=True)
        # 事件2：本根 high=30（区间完全买后）在撮合后并入峰值；本事件决定仍用
        # 先前事实，不被同根新高反向触发。
        second = engine.process_event(_Event(
            T0 + HOUR, DAY, {"600001": _row(
                T0 + HOUR, 10.6, bar_high=30.0, bar_complete=True,
                interval_start=T0 + 1, interval_end=T0 + HOUR)}), decide=True)
        assert second["documented_intents"] == []
        assert engine.state().batch_facts["b1"].peak_price == pytest.approx(30.0)


# ─── P1（main 决策2）：生产新增风险必须有显式合法新鲜度规格 ──────────────────


def test_missing_freshness_spec_restricts_new_risk_but_keeps_protection(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("spec-missing")
        ledger.create(initial_cash=100_000.0, strategy_ref={"strategy_id": "s"})
        state = ledger.load_state()
        state.batches = [_batch()]
        ledger.commit_event(expected_revision=state.revision, event_id="seed",
                            source_hash="seed", content_hash="seed", state=state)
        sell = AccountOrderIntent(
            action="sell", code="600001", qty=1000, plan_id="exit",
            strategy_ref={"strategy_id": "s"})
        engine = _engine(ledger, _StubRuntime([[_buy()], [sell], []]),
                         max_quote_age_ms=None)
        # 未声明新鲜度规格：新增风险受限（可解释原因），不默认放行。
        first = engine.process_event(
            _Event(T0, DAY, {"600001": _row(T0, 10.0)}), decide=True)
        assert not [o for o in engine.state().orders if o.side == "buy"]
        assert any(r["reason"] == "freshness_model_missing"
                   for r in first["rejected"])
        # 既有保护（减仓/退出）继续按真实新执行支持评估，不被缺配置取消。
        engine.process_event(_Event(T0 + HOUR, DAY,
                                    {"600001": _row(T0 + HOUR, 10.0)}),
                             decide=True)
        assert [o for o in engine.state().orders if o.side == "sell"]


def test_declared_freshness_spec_allows_fresh_buy_fill(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("spec-ok")
        ledger.create(initial_cash=100_000.0, strategy_ref={"strategy_id": "s"})
        engine = _engine(ledger, _StubRuntime([[_buy()]]),
                         max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)
        engine.process_event(_Event(T0, DAY, {"600001": _row(T0, 10.0)}),
                             decide=True)
        later = engine.process_event(
            _Event(T0 + HOUR, DAY, {"600001": _row(T0 + HOUR, 10.0)}))
        assert [fill["qty"] for fill in later["fill_records"]] == [1000]


def test_freshness_spec_validation_rejects_invalid_values():
    from dragon_quant.review_account.execution_core.runtime_facts import (
        RuntimeFactError,
    )

    for bad in (True, -1, float("nan"), float("inf"), "x"):
        with pytest.raises(RuntimeFactError):
            _engine(_NoopLedger(), _StubRuntime(), max_quote_age_ms=bad)


class _NoopLedger:
    ledger_id = "noop"



def test_corrupt_database_history_raises_integrity_error(tmp_path):
    dragon = tmp_path / "dragon.db"
    dragon.write_bytes(b"this is definitely not a sqlite database")
    with patch_storage_dbs(str(dragon), str(tmp_path / "assets.db")), \
            patch("dragon_quant.infra.storage.paths.DB_PATH", dragon):
        with pytest.raises(LedgerIntegrityError):
            LedgerHistory("corrupt-db").read()


def test_negative_revision_cursor_is_rejected(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("neg-cursor")
        ledger.create(initial_cash=1000.0, strategy_ref={"strategy_id": "s"})
        with pytest.raises(LedgerError):
            LedgerHistory("neg-cursor").read(cursor="-1:e1")


# ─── P4：完整决定输入参与事件身份/冲突 ─────────────────────────────────────


def test_same_event_different_decision_inputs_conflict(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("p4")
        ledger.create(initial_cash=100_000.0, strategy_ref={"strategy_id": "s"})
        engine = _engine(ledger, _StubRuntime([[], []]))
        rows = {"600001": _row(T0, 10.0)}
        engine.process_event(
            _Event(T0, DAY, rows), decide=True,
            decision_kwargs={"market": bs.MarketView(new_risk_allowed=True)})
        # 同事件不同市场输入：显式冲突，不静默复用。
        with pytest.raises(LedgerError):
            engine.process_event(
                _Event(T0, DAY, rows), decide=True,
                decision_kwargs={"market": bs.MarketView(
                    new_risk_allowed=False)})
        # 完全相同输入重放：稳定幂等。
        replay = engine.process_event(
            _Event(T0, DAY, rows), decide=True,
            decision_kwargs={"market": bs.MarketView(new_risk_allowed=True)})
        assert replay["status"] == "already_processed"


def test_same_event_different_candidates_conflict(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("p4-cand")
        ledger.create(initial_cash=100_000.0, strategy_ref={"strategy_id": "s"})
        engine = _engine(ledger, _StubRuntime([[], []]))
        rows = {"600001": _row(T0, 10.0)}
        cand_a = bs.CandidateView(code="600001", price=10.0)
        cand_b = bs.CandidateView(code="600002", price=20.0)
        engine.process_event(_Event(T0, DAY, rows), decide=True,
                             candidates=(cand_a,))
        with pytest.raises(LedgerError):
            engine.process_event(_Event(T0, DAY, rows), decide=True,
                                 candidates=(cand_b,))
