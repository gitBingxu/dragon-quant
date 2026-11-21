"""W3 账户事件引擎：CAS、真实报价格时/容量、结构化订单、预留/部分成交、恢复。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from dragon_quant.review_account.ledger import (
    AccountLedger, Batch, ExecutionCostModel, LedgerError,
    OrderStatus, SELLABLE_ON,
)
from dragon_quant.review_account.ledger.matching import owner_identity
from dragon_quant.review_account.execution_core.runtime_facts import (
    TEACHING_QUOTE_MAX_AGE_MS,
)
from dragon_quant.review_account.strategy.account_engine import AccountEngine
from dragon_quant.review_account.strategy.account_runtime import (
    AccountDecision, AccountPolicyRuntime,
)
from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.strategy_sdk.types import AccountOrderIntent
from dragon_quant.tests._bs_policy_support import _steps
from dragon_quant.tests.support import patch_storage_dbs

REF = {"strategy_id": "bs_account_decision", "params_hash": "h1"}
REF2 = {"strategy_id": "bs_account_decision", "params_hash": "h1",
        "snapshot_id": "snap-v2"}


@contextmanager
def _env(tmp_path):
    dragon = str(tmp_path / "dragon.db")
    with patch_storage_dbs(dragon, str(tmp_path / "assets.db")), \
            patch("dragon_quant.infra.storage.paths.DB_PATH", Path(dragon)):
        yield


class _Event:
    def __init__(self, ts, day, rows, *, unavailable=None, last_closes=None,
                 evidence_available=()):
        self.timestamp = ts
        self.trade_date = day
        self.rows = rows
        self.unavailable = unavailable or {}
        self.last_closes = last_closes or {}
        self.candidates = []
        self.phase = "bar"
        self.allow_buy = True
        self.evidence_available = evidence_available


class _StubRuntime:
    def __init__(self, queue=None):
        self.strategy_ref = REF
        self.queue = list(queue or [])

    def decide(self, state, *, candidates, decision_time, **kwargs):
        intents = self.queue.pop(0) if self.queue else []
        return AccountDecision(bundle=None, intents=tuple(intents))


def _rows(ts, price, *, capacity=10 ** 9, tradable=True, exec_ts=None):
    e = exec_ts if exec_ts is not None else ts
    return {"600001": {"execution_price": price, "execution_timestamp": e,
                       "available_at": e, "available_qty": capacity,
                       "tradable": tradable}}


def _buy(qty=1000, **kw):
    kw.setdefault("risk_amount", 100.0)
    return AccountOrderIntent(action="buy", code="600001", qty=qty,
                              plan_id="p1", strategy_ref=REF, **kw)


def _sell(qty=1000, **kw):
    return AccountOrderIntent(action="sell", code="600001", qty=qty,
                              plan_id="p1", strategy_ref=REF, **kw)


def _engine(ledger, runtime, calendar=("2026-11-18", "2026-11-19"),
            ttl=86_400_000):
    from dragon_quant.review_account.execution_core.risk import RiskCaps
    # 定向撮合/持久化单元显式使用宽松但**有限**的教学限额（不是“未配置=无限”）。
    return AccountEngine(ledger, runtime, ExecutionCostModel(),
                         calendar=list(calendar), order_ttl_ms=ttl,
                         risk_caps=RiskCaps.teaching_fixture(),
                         max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)


def _seed(ledger, batches):
    state = ledger.load_state()
    state.batches = list(batches)
    ledger.commit_event(expected_revision=state.revision, event_id="seed",
                        source_hash="seed", content_hash="seed", state=state)


T0 = 1_700_000_000_000


def test_decision_fills_on_next_event_with_real_quote_time(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("a")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        engine = _engine(ledger, _StubRuntime([[_buy()]]))
        first = engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 10.0)),
                                     decide=True)
        assert first["fills"] == []
        second = engine.process_event(
            _Event(T0 + 3_600_000, "2026-11-18", _rows(T0 + 3_600_000, 10.0)))
        assert second["fills"]
        batch = engine.state().batches[0]
        assert batch.remaining == 1000 and batch.sellable_mode == SELLABLE_ON
        assert batch.sellable_date == "2026-11-19"


def test_stale_execution_timestamp_cannot_fill(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("stale-ts")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        engine = _engine(ledger, _StubRuntime([[_buy()]]))
        engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 10.0)),
                             decide=True)
        # 后一事件但报价源执行时点在决策之前（exec_ts=T0-1000）→ 不能成交。
        later = engine.process_event(
            _Event(T0 + 3_600_000, "2026-11-18",
                   _rows(T0 + 3_600_000, 10.0, exec_ts=T0 - 1000)))
        assert later["fills"] == []
        assert engine.state().batches == []


def test_unknown_capacity_blocks_fill(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("cap")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        engine = _engine(ledger, _StubRuntime([[_buy()]]))
        engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 10.0)),
                             decide=True)
        rows = {"600001": {"execution_price": 10.0,
                           "execution_timestamp": T0 + 3_600_000,
                           "available_at": T0 + 3_600_000, "tradable": True}}
        later = engine.process_event(
            _Event(T0 + 3_600_000, "2026-11-18", rows))
        assert later["fills"] == []


def test_partial_fill_keeps_residual_reservation(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("partial")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        engine = _engine(ledger, _StubRuntime([[_buy(qty=1000)]]))
        engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 10.0)),
                             decide=True)
        engine.process_event(_Event(T0 + 3_600_000, "2026-11-18",
                                    _rows(T0 + 3_600_000, 10.0, capacity=600)))
        state = engine.state()
        order = state.orders[0]
        assert order.filled_qty == 600
        assert order.status == OrderStatus.PARTIALLY_FILLED.value
        assert order.reserved_amount > 0          # 剩余部分仍预留
        reserved = [r for r in state.reservations
                    if r.order_id == order.order_id and r.status == "reserved"]
        assert reserved and reserved[0].amount > 0
        # L2：风险单位与资金预留分开持久化；部分成交只剩剩余风险。
        assert order.reserved_risk == pytest.approx(40.0)
        assert state.batches[0].initial_risk == pytest.approx(60.0)


def test_buy_without_price_reference_creates_no_order(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("noprice")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        engine = _engine(ledger, _StubRuntime([[_buy()]]))
        # 事件无报价、无历史 mark → 不能新增风险订单。
        engine.process_event(_Event(T0, "2026-11-18", {}), decide=True)
        assert engine.state().orders == []


def test_order_ttl_expires_and_releases(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("ttl")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        engine = _engine(ledger, _StubRuntime([[_buy()]]), ttl=3_600_000)
        engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 10.0)),
                             decide=True)
        # 下一事件距今超过 TTL，且容量为 0 无法成交 → 到期取消。
        later = engine.process_event(
            _Event(T0 + 7_200_000, "2026-11-18",
                   _rows(T0 + 7_200_000, 10.0, capacity=0)))
        state = engine.state()
        assert later["canceled"]
        assert state.orders[0].status == OrderStatus.CANCELLED.value
        assert all(r.status == "released" for r in state.reservations)


def test_replay_and_same_ts_different_content(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("replay")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        engine = _engine(ledger, _StubRuntime([[_buy()], []]))
        engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 10.0)),
                             decide=True)
        # 同输入重放：already_processed。
        again = engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 10.0)),
                                     decide=True)
        assert again["status"] == "already_processed"
        # 同时间戳不同行情（新修订）：必须显式冲突，不能静默忽略。
        with pytest.raises(LedgerError):
            engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 99.0)),
                                 decide=True)


def test_missing_evidence_cancels_pending_buy(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("cancel")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        engine = _engine(ledger, _StubRuntime([[_buy()], []]))
        engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 10.0)),
                             decide=True)
        second = engine.process_event(
            _Event(T0 + 1_000, "2026-11-18", _rows(T0 + 1_000, 10.0)),
            decide=True)
        assert second["canceled"]
        assert engine.state().orders[0].status == OrderStatus.CANCELLED.value


def test_restart_equivalence(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("restart")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        engine = _engine(ledger, _StubRuntime([[_buy()]]))
        engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 10.0)),
                             decide=True)
        # 新引擎实例（模拟重启）继续，结果与不中断一致。
        engine2 = _engine(ledger, _StubRuntime([]))
        engine2.process_event(_Event(T0 + 3_600_000, "2026-11-18",
                                     _rows(T0 + 3_600_000, 10.0)))
        assert ledger.load_state().batches[0].remaining == 1000


def test_owner_identity_includes_snapshot(tmp_path):
    assert owner_identity(REF) != owner_identity(REF2)


def test_real_policy_runtime_protection_exit_chain(tmp_path):
    from dragon_quant.workspace import snapshots

    with _env(tmp_path):
        config = bs.teaching_fixture()
        # C2/O3：活动运行时必须是完整可验证的新 BS 冻结引用（不接受裸 ref）。
        ref = snapshots.builtin_ref(
            strategy_id=bs.BS_ACCOUNT_STRATEGY_ID,
            params={"bs_policy": config.to_dict()})
        ledger = AccountLedger("chain")
        ledger.create(initial_cash=10_000.0, strategy_ref=ref)
        _seed(ledger, [Batch(batch_id="held", code="600001", qty=1000,
                             remaining=1000, entry_date="2026-11-16",
                             cost_price=10.0, strategy_ref=ref,
                             sellable_mode=SELLABLE_ON,
                             sellable_date="2026-11-17")])
        engine = _engine(ledger, AccountPolicyRuntime(config, strategy_ref=ref))
        scenarios = bs.scenario_set_from_records(
            [{"scenario_id": "flat", "kind": "teaching_fixture", "fixture": True,
              "steps": _steps({"flat": {"600001": [8.0] * 5}})[0]["steps"]}],
            spec=config.scenario)
        decision = engine.process_event(
            _Event(T0, "2026-11-18", _rows(T0, 8.0)), decide=True,
            decision_kwargs={"scenarios": scenarios,
                             "market": bs.MarketView(regime="trend_down",
                                                     new_risk_allowed=False)})
        assert any(i.action == "sell" for i in decision["documented_intents"])
        assert decision["fills"] == []
        later = engine.process_event(
            _Event(T0 + 3_600_000, "2026-11-18",
                   _rows(T0 + 3_600_000, 8.0)))
        assert later["fills"]
        assert engine.state().batches[0].remaining == 0
        assert ledger.query_fills()


def test_l2_per_trade_risk_cap_rejects_oversized_buy(tmp_path):
    from dragon_quant.review_account.ledger.transition import RiskCaps
    with _env(tmp_path):
        ledger = AccountLedger("cap-risk")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        engine = AccountEngine(ledger, _StubRuntime([[_buy(risk_amount=5000.0)]]),
                               ExecutionCostModel(),
                               calendar=["2026-11-18", "2026-11-19"],
                               risk_caps=RiskCaps(per_trade=1000.0),
                               max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)
        res = engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 10.0)),
                                   decide=True)
        assert engine.state().orders == []
        assert any(r["reason"] == "per_trade_risk_exceeded"
                   for r in res["rejected"])


def test_l2_account_risk_cap_rejects_when_budget_used(tmp_path):
    from dragon_quant.review_account.ledger.transition import RiskCaps
    with _env(tmp_path):
        ledger = AccountLedger("cap-acct")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        engine = AccountEngine(ledger, _StubRuntime([[_buy(risk_amount=800.0)]]),
                               ExecutionCostModel(),
                               calendar=["2026-11-18", "2026-11-19"],
                               risk_caps=RiskCaps(per_trade=10_000.0,
                                                  account=1000.0,
                                                  unknown_group=10_000.0),
                               max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)
        _seed(ledger, [Batch(batch_id="existing", code="600002", qty=100,
                             remaining=100, entry_date="2026-11-16",
                             cost_price=10.0, initial_risk=500.0,
                             strategy_ref=REF, sellable_mode=SELLABLE_ON,
                             sellable_date="2026-11-17")])
        res = engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 10.0)),
                                   decide=True)
        assert engine.state().orders == []
        assert any(r["reason"] == "account_risk_exceeded"
                   for r in res["rejected"])


def test_unconfigured_risk_limits_cannot_add_new_risk(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("no-caps")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        # 未配置风险上限 = 未知（不是无限）：买单被拒绝而不是默认放行。
        engine = AccountEngine(ledger, _StubRuntime([[_buy()]]),
                               ExecutionCostModel(),
                               calendar=["2026-11-18", "2026-11-19"],
                               max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)
        result = engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 10.0)),
                                      decide=True)
        assert engine.state().orders == []
        assert any(item["reason"] == "risk_limit_unknown"
                   for item in result["rejected"])


def test_unknown_holding_risk_blocks_new_risk_in_engine(tmp_path):
    from dragon_quant.review_account.execution_core.risk import RiskCaps
    with _env(tmp_path):
        ledger = AccountLedger("unknown-holding")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        # 存量批次未声明 initial_risk = 风险未知（不是已知零），新增风险受限。
        _seed(ledger, [Batch(batch_id="legacy", code="600002", qty=100,
                             remaining=100, entry_date="2026-11-16",
                             cost_price=10.0, strategy_ref=REF,
                             sellable_mode=SELLABLE_ON,
                             sellable_date="2026-11-17")])
        engine = AccountEngine(ledger, _StubRuntime([[_buy()]]),
                               ExecutionCostModel(),
                               calendar=["2026-11-18", "2026-11-19"],
                               risk_caps=RiskCaps.teaching_fixture(),
                               max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)
        result = engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 10.0)),
                                      decide=True)
        assert engine.state().orders == []
        assert any(item["reason"] == "risk_unknown_holding"
                   for item in result["rejected"])


def test_g7_dependent_buy_blocked_until_prerequisite_filled(tmp_path):
    with _env(tmp_path):
        ledger = AccountLedger("dep")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        intent = AccountOrderIntent(action="buy", code="600001", qty=1000,
                                    plan_id="p1", strategy_ref=REF,
                                    depends_on=("ord-not-filled",))
        engine = AccountEngine(ledger, _StubRuntime([[intent]]),
                               ExecutionCostModel(),
                               calendar=["2026-11-18", "2026-11-19"],
                               max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)
        res = engine.process_event(_Event(T0, "2026-11-18", _rows(T0, 10.0)),
                                   decide=True)
        assert engine.state().orders == []
        assert any(r["reason"] == "depends_on_unfilled" for r in res["rejected"])
