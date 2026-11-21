"""C3b2：真实 G3 试探 → 独立确认 → 加仓链（真实 SQLite + 真实政策运行时）。

覆盖本包验收（矩阵 2/3/6/7 的 G3 部分）：
- 无容量 probe 不产生持仓/不创建 add；partial probe 按真实成交占比封顶 add；
- probe 真实成交后派生完整 owner+code+family 政策状态，独立确认（时点/独立性/
  版本）通过才可 add；add 引用**真实 probe 父买单**（depends_on），严格下一事件
  成交，同事件新父单不提前解锁；重启继续且不越 max_adds；
- 同 code 不同 owner/family 不串；
- F5：同 event 仅 interval 不同 / 仅真实运行时配置不同 → 输入身份冲突；
- same-family 闭合（probe+add）只计一次亏损；
- Scenario 与 AccountEngine 在相同政策/事实下逐事件成交量价费/现金一致。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from dragon_quant.review_account.evidence import CapabilityCheck
from dragon_quant.review_account.execution_core.matching import ExecutionCostModel
from dragon_quant.review_account.execution_core.models import SELLABLE_ON, Batch
from dragon_quant.review_account.execution_core.risk import RiskCaps
from dragon_quant.review_account.execution_core.runtime_facts import (
    TEACHING_QUOTE_MAX_AGE_MS,
)
from dragon_quant.review_account.ledger import AccountLedger, LedgerError
from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.review_account.strategy.account_engine import AccountEngine
from dragon_quant.review_account.strategy.account_runtime import AccountPolicyRuntime
from dragon_quant.review_account.strategy.policy_events import (
    advance_state_policy_states, cancel_stale_buys, command_from_intent,
    seed_committed_policy_inputs,
)
from dragon_quant.review_account.strategy.bs_policy.scenario_execution import (
    ScenarioRun, execution_from_config,
)
from dragon_quant.tests._bs_policy_support import _scenarios, _steps
from dragon_quant.tests.support import patch_storage_dbs

_CST = timezone(timedelta(hours=8))


def _ms(day, hour, minute=0):
    return int(datetime(2026, 11, day, hour, minute, tzinfo=_CST).timestamp() * 1000)


def _iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=_CST).isoformat()


DAY1, DAY2, DAY3, DAY4, DAY5 = (
    "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24")
T1 = _ms(18, 10)
T2 = _ms(19, 11)
T3 = _ms(20, 14)
T4 = _ms(23, 15)
T5 = _ms(24, 15, 30)
DAY = DAY1
CALENDAR = (DAY1, DAY2, DAY3, DAY4, DAY5, "2026-11-25")
CONFIRM_AT = "2026-11-19T12:00:00+08:00"
UP = {"up": {"600001": [10.0, 10.1, 10.4, 10.5, 10.6]}}
LOW_CAPACITY = {"600001": 250}


@contextmanager
def _env(tmp_path):
    dragon = str(tmp_path / "dragon.db")
    with patch_storage_dbs(dragon, str(tmp_path / "assets.db")), \
            patch("dragon_quant.infra.storage.paths.DB_PATH", Path(dragon)):
        yield


class _Event:
    def __init__(self, ts, rows, *, day=DAY, evidence_available=()):
        self.timestamp = ts
        self.trade_date = day
        self.rows = rows
        self.unavailable = {}
        self.last_closes = {}
        self.candidates = []
        self.phase = "bar"
        self.allow_buy = True
        self.evidence_available = evidence_available


def _row(ts, price, *, capacity=10 ** 9, tradable=True):
    return {"execution_price": price, "execution_timestamp": ts,
            "available_at": ts, "available_qty": capacity,
            "tradable": tradable}


def _checks(*rules):
    return tuple(CapabilityCheck(rule_id=rule, capability="intraday_bars",
                                 status="usable", protects="new_risk")
                 for rule in rules)


def _candidate(price, rules, *, confirmations=(), version="", structure=None):
    return bs.CandidateView(
        code="600001", name="候选一", eligible=True, tradable=True,
        price=price, pressure_pct=0.10, rank=1, theme_ids=("T1",),
        structure=structure or {"breakout_level": 10.2, "pullback_level": 9.8,
                                "confirm_level": 10.0, "probe_level": 10.0},
        evidence_checks=_checks(*rules), confirmations=tuple(confirmations),
        entry_version=version)


def _confirmation(evidence=("obs-confirm",), available_at=CONFIRM_AT):
    return bs.EvidenceConfirmation(
        rule_id="confirm:600001", evidence_ids=tuple(evidence),
        available_at=available_at, version="v2")


def _ref(config):
    from dragon_quant.workspace import snapshots
    return snapshots.builtin_ref(strategy_id=bs.BS_ACCOUNT_STRATEGY_ID,
                                 params={"bs_policy": config.to_dict()})


def _engine(ledger, config):
    return AccountEngine(
        ledger, AccountPolicyRuntime(config, strategy_ref=_ref(config)),
        ExecutionCostModel(), calendar=list(CALENDAR), order_ttl_ms=None,
        risk_caps=RiskCaps.teaching_fixture(),
        max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)


def _scenarios_up(config, **kwargs):
    return _scenarios(config, UP, **kwargs)


def _probe_event(engine, config, ts=T1, price=10.0, capacity=10 ** 9):
    cand = _candidate(price, ("entry:probe:600001",))
    return engine.process_event(
        _Event(ts, {"600001": _row(ts, price, capacity=capacity)}, day=DAY1),
        decide=True, candidates=(cand,),
        decision_kwargs={"scenarios": _scenarios_up(config),
                         "market": bs.MarketView(regime="trend_up",
                                                 new_risk_allowed=True),
                         "pressures": {"600001": 0.10},
                         "themes": {"600001": ("T1",)}})


def _add_event(engine, config, ts=T3, price=10.4, day=DAY3,
               capacity=10 ** 9):
    cand = _candidate(price, ("confirm:600001",),
                      confirmations=(_confirmation(),),
                      version=config.content_hash)
    return engine.process_event(
        _Event(ts, {"600001": _row(ts, price, capacity=capacity)}, day=day),
        decide=True, candidates=(cand,),
        decision_kwargs={"scenarios": _scenarios_up(config),
                         "market": bs.MarketView(regime="trend_up",
                                                 new_risk_allowed=True),
                         "pressures": {"600001": 0.10},
                         "themes": {"600001": ("T1",)}})


def _buy_orders(engine):
    return [o for o in engine.state().orders if o.side == "buy"]


# ─── 真实 probe → 独立确认 → add ──────────────────────────────────────────


def test_no_capacity_probe_creates_no_state_and_no_add(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g3-nocap")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        engine = _engine(ledger, config)
        _probe_event(engine, config, capacity=0)
        # 下一事件零容量 → probe 不成交 → 无持仓/无政策状态。
        engine.process_event(_Event(T2, {"600001": _row(T2, 10.0, capacity=0)}, day=DAY2))
        state = engine.state()
        assert state.batches == []
        assert state.policy_states == []
        # 未成交 probe 不解锁 add（add 计划不存在/不可执行，未创建 add 买单）。
        _add_event(engine, config)
        assert not [o for o in _buy_orders(engine) if o.plan_id.startswith("add:")]


def test_real_probe_independent_confirm_add_partial_then_restart(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g3-chain")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        engine = _engine(ledger, config)
        # 事件1：真实政策选择 probe，创建买单（不在同事件成交）。
        first = _probe_event(engine, config)
        first_intents = first["documented_intents"]
        assert first_intents and first_intents[0].action == "buy"
        assert first_intents[0].plan_id.startswith("probe:")
        assert first["fills"] == []
        # 事件2：probe 真实成交 400 股 → 派生完整政策状态。
        second = engine.process_event(_Event(T2, {"600001": _row(T2, 10.1)}, day=DAY2))
        assert [f["qty"] for f in second["fill_records"]] == [400]
        state = engine.state()
        assert len(state.policy_states) == 1
        probe = bs.PositionPolicyState.from_row(state.policy_states[0])
        probe_order = next(o for o in _buy_orders(engine)
                           if o.plan_id.startswith("probe:"))
        assert probe.probe_order_id == probe_order.order_id
        assert probe.probe_filled_qty == 400
        assert probe.owner and probe.family_id == probe.probe_plan_id
        # 事件3：独立确认（时点/独立/版本）→ 真实政策选择 add，依赖 probe 父买单。
        third = _add_event(engine, config)
        add_intents = [a for a in third["documented_intents"]
                       if a.plan_id.startswith("add:")]
        assert add_intents and add_intents[0].depends_on == (probe_order.order_id,)
        add_order = next(o for o in _buy_orders(engine)
                         if o.plan_id.startswith("add:"))
        assert add_order.depends_on == (probe_order.order_id,)
        assert add_order.family_id == probe.probe_plan_id
        # 同事件不提前成交：本事件 add 未成交。
        assert third["fills"] == []
        # 事件4：受限容量部分成交 add（250 股），只累计真实量、不重复计数。
        fourth = engine.process_event(
            _Event(T4, {"600001": _row(T4, 10.5, capacity=250)}, day=DAY4))
        assert [f["qty"] for f in fourth["fill_records"]] == [250]
        add_order = next(o for o in _buy_orders(engine)
                         if o.plan_id.startswith("add:"))
        assert add_order.filled_qty == 250
        # 重启：新引擎在**真正更晚的事件**（> event_cursor）继续剩余目标。
        engine2 = _engine(ledger, config)
        reloaded = engine2.state().policy_states
        assert len(reloaded) == 1
        reloaded_state = bs.PositionPolicyState.from_row(reloaded[0])
        assert reloaded_state.adds_done == 1        # 同单部分成交只计一次
        assert reloaded_state.add_filled_qty == 250
        assert reloaded_state.cumulative_filled_qty == 650
        later_ts = T4 + 60_000                     # 当日更晚、仍属真实下一事件
        sixth = _add_event(engine2, config, ts=later_ts, price=10.5,
                           day=DAY4, capacity=1000)
        # 已消费证据不反向使原单剩余失效：partial add 未被误撤、继续成交余量。
        assert [f["qty"] for f in sixth["fill_records"]] == [150]
        add_orders = [o for o in _buy_orders(engine2)
                      if o.plan_id.startswith("add:")]
        assert len(add_orders) == 1               # 同一订单，不新建第二张
        assert add_orders[0].order_id == add_order.order_id
        assert add_orders[0].filled_qty == 400
        assert add_orders[0].status == "filled"
        final = bs.PositionPolicyState.from_row(engine2.state().policy_states[0])
        assert final.adds_done == 1               # 余量成交不重复计数
        assert final.add_filled_qty == 400
        assert final.cumulative_filled_qty == 800
        # 余量成交后该单预留全部释放。
        add_reservations = [r for r in engine2.state().reservations
                            if r.order_id == add_order.order_id]
        assert add_reservations and all(r.status == "released"
                                        for r in add_reservations)
        # 历史完整可读回（两次 decide 与两次成交均在事件历史里）。
        history = ledger.read_history()
        assert len(history.records) >= 5


def _revoke_decide(engine, config, *, ts, day, price, market_allowed, regime,
                   capacity=10 ** 9):
    cand = _candidate(price, ("confirm:600001",),
                      confirmations=(_confirmation(),),
                      version=config.content_hash)
    return engine.process_event(
        _Event(ts, {"600001": _row(ts, price, capacity=capacity)}, day=day),
        decide=True, candidates=(cand,),
        decision_kwargs={"scenarios": _scenarios_up(config),
                         "market": bs.MarketView(regime=regime,
                                                 new_risk_allowed=market_allowed),
                         "pressures": {"600001": 0.10},
                         "themes": {"600001": ("T1",)}})


def _partial_add(engine, config):
    _probe_event(engine, config)
    engine.process_event(_Event(T2, {"600001": _row(T2, 10.1)}, day=DAY2))
    _add_event(engine, config)
    engine.process_event(
        _Event(T4, {"600001": _row(T4, 10.5, capacity=250)}, day=DAY4))
    order = next(o for o in _buy_orders(engine)
                 if o.plan_id.startswith("add:"))
    assert order.filled_qty == 250
    return order


def test_price_invalidation_revokes_remaining_partial_no_buy_fill(tmp_path):
    """价格失效（market 仍 True）单独即可撤余量：不倒销 fill、不再越跌越补。"""
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("r1-price")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        engine = _engine(ledger, config)
        add_order = _partial_add(engine, config)
        res = _revoke_decide(engine, config, ts=T4 + 60_000, day=DAY4, price=8.0,
                             market_allowed=True, regime="trend_up")
        reasons = {r["reason"] for r in res["revocations"]
                   if r["order_id"] == add_order.order_id}
        assert "price_invalidation" in reasons
        assert add_order.order_id in res["canceled"]
        assert res["fills"] == []                # 本事件无 buy fill
        state = engine.state()
        cancelled = next(o for o in state.orders
                         if o.order_id == add_order.order_id)
        assert cancelled.status == "cancelled" and cancelled.filled_qty == 250
        assert sum(b.remaining for b in state.batches) == 650   # 已持仓保留


def test_market_block_revokes_remaining_partial_even_above_line(tmp_path):
    """market.new_risk_allowed=False 单独即可撤余量（价格仍高于失效线也撤）。"""
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("r1-market")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        engine = _engine(ledger, config)
        add_order = _partial_add(engine, config)
        res = _revoke_decide(engine, config, ts=T4 + 60_000, day=DAY4, price=10.6,
                             market_allowed=False, regime="trend_down")
        reasons = {r["reason"] for r in res["revocations"]
                   if r["order_id"] == add_order.order_id}
        assert "market_new_risk_blocked" in reasons
        assert add_order.order_id in res["canceled"]
        assert res["fills"] == []
        state = engine.state()
        assert sum(b.remaining for b in state.batches) == 650


def test_main_counterexample_price_and_market_no_adding_into_drop(tmp_path):
    """main 反例：price8 + market newriskFalse → 余 150 撤销、本事件无 buy fill。"""
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("r1-main")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        engine = _engine(ledger, config)
        add_order = _partial_add(engine, config)
        res = _revoke_decide(engine, config, ts=T4 + 60_000, day=DAY4, price=8.0,
                             market_allowed=False, regime="trend_down")
        assert add_order.order_id in res["canceled"]
        assert res["fills"] == []
        assert not [f for f in res["fill_records"]]
        assert sum(b.remaining for b in engine.state().batches) == 650


def test_explicit_revocation_idempotent_and_restart_no_revival(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("r1-idem")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        engine = _engine(ledger, config)
        add_order = _partial_add(engine, config)
        kwargs = dict(ts=T4 + 60_000, day=DAY4, price=8.0,
                      market_allowed=False, regime="trend_down")
        first = _revoke_decide(engine, config, **kwargs)
        assert add_order.order_id in first["canceled"]
        # 相同事件重放：幂等，不重复撤/成交。
        replay = _revoke_decide(engine, config, **kwargs)
        assert replay["status"] == "already_processed"
        # 重启：余量不复活、预留已释放、历史撤销原因可读回。
        engine2 = _engine(ledger, config)
        state = engine2.state()
        persisted = next(o for o in state.orders
                         if o.order_id == add_order.order_id)
        assert persisted.status == "cancelled"
        assert all(r.status == "released" for r in state.reservations
                   if r.order_id == add_order.order_id)
        history = ledger.read_history()
        reasons = [item.get("reason")
                   for record in history.records
                   for item in ((record.execution or {}).get("revocations") or ())]
        assert any(reason in ("price_invalidation", "market_new_risk_blocked")
                   for reason in reasons)


def test_partial_add_expiry_cancels_residual_but_protects_filled(tmp_path):
    """真正到期/失效仍撤余量，已成交批次保护继续。"""
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g3-expiry")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        # 80h：覆盖 probe(T2)/跨周末的 add(T4) partial，之后再到期撤余量。
        ttl = 80 * 3_600_000
        engine = AccountEngine(
            ledger, AccountPolicyRuntime(config, strategy_ref=_ref(config)),
            ExecutionCostModel(), calendar=list(CALENDAR), order_ttl_ms=ttl,
            risk_caps=RiskCaps.teaching_fixture(),
            max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)
        _probe_event(engine, config)
        engine.process_event(_Event(T2, {"600001": _row(T2, 10.1)}, day=DAY2))
        _add_event(engine, config)
        engine.process_event(
            _Event(T4, {"600001": _row(T4, 10.5, capacity=250)}, day=DAY4))
        add_order = next(o for o in _buy_orders(engine)
                         if o.plan_id.startswith("add:"))
        assert add_order.filled_qty == 250
        # 跨过 valid_until（T3 + 30h）后的真实事件：到期撤余量。
        expired_ts = T3 + ttl + 3_600_000
        result = engine.process_event(
            _Event(expired_ts, {"600001": _row(expired_ts, 10.5, capacity=1000)},
                   day=DAY4))
        assert add_order.order_id in result["canceled"]
        state = engine.state()
        cancelled = next(o for o in state.orders
                         if o.order_id == add_order.order_id)
        assert cancelled.status == "cancelled"
        assert cancelled.filled_qty == 250
        # 已成交的 probe 400 + add 250 批次保护继续，不被误删。
        assert sum(b.remaining for b in state.batches) == 650


def test_partial_probe_caps_add_quantity(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g3-partial-probe")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        engine = _engine(ledger, config)
        _probe_event(engine, config)
        # probe 只成交 200 股（计划 400）→ add 授权量按真实占比封顶。
        engine.process_event(_Event(T2, {"600001": _row(T2, 10.1, capacity=200)}, day=DAY2))
        probe = bs.PositionPolicyState.from_row(engine.state().policy_states[0])
        assert probe.probe_filled_qty == 200 and probe.pending_target_qty == 400
        third = _add_event(engine, config)
        add_intents = [a for a in third["documented_intents"]
                       if a.plan_id.startswith("add:")]
        assert add_intents and add_intents[0].qty <= 200


def test_same_code_different_owner_does_not_cross():
    config = bs.teaching_fixture()
    owner_a = {"strategy_id": "bs_account_decision", "params_hash": "A"}
    owner_b = {"strategy_id": "bs_account_decision", "params_hash": "B"}
    state_a = bs.PositionPolicyState(
        code="600001", probe_plan_id="probe:600001:s1:t10.0000",
        probe_filled_at="2026-11-18T11:00:00+08:00",
        probe_evidence_ids=("entry:probe:600001",), adds_done=0,
        version=config.content_hash, owner=owner_a,
        probe_order_id="ord-a", probe_filled_qty=400,
        cumulative_filled_qty=400)
    cand = _candidate(10.4, ("confirm:600001",),
                      confirmations=(_confirmation(),),
                      version=config.content_hash)
    ctx = bs.PolicyContext(
        decision_time="2026-11-18T15:00:00+08:00", config=config,
        account=bs.AccountView(cash=100_000.0, positions=()),
        candidates=(cand,),
        market=bs.MarketView(regime="trend_up", new_risk_allowed=True),
        scenarios=_scenarios_up(config), policy_states=(state_a,),
        active_owner=owner_b)
    bundle = bs.evaluate_account_policy(ctx)
    add = next(p for p in bundle.plans if p.plan_kind == bs.PLAN_ADD)
    # 另一 owner 的真实 probe 状态不得授权本 owner 的 add。
    assert add.executable is False
    assert "probe_not_filled" in add.blocked_reasons


# ─── F5：完整输入身份 ────────────────────────────────────────────────────


def test_source_hash_includes_interval_inputs(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("f5-interval")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        engine = _engine(ledger, config)
        row = _row(T1, 10.0)
        row.update({"bar_complete": True, "bar_high": 14.0,
                    "interval_start": T1 - 3_600_000, "interval_end": T1})
        engine.process_event(_Event(T1, {"600001": row}), decide=True)
        # 仅 interval_start 不同（100 → 900 语义）：必须判为不同输入冲突。
        changed = dict(row)
        changed["interval_start"] = T1 - 3_600_000 + 800
        with pytest.raises(LedgerError):
            engine.process_event(_Event(T1, {"600001": changed}), decide=True)


def test_source_hash_includes_runtime_config(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("f5-runtime")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        _engine(ledger, config).process_event(
            _Event(T1, {"600001": _row(T1, 10.0)}), decide=True)
        other = _engine(ledger, config)
        other.cost_model = ExecutionCostModel(commission_rate=0.001)
        with pytest.raises(LedgerError):
            other.process_event(_Event(T1, {"600001": _row(T1, 10.0)}),
                                decide=True)


# ─── R3：未知 legacy 状态不得按全量授权/无父单反复 add ───────────────────


def _legacy_probe_state(config, **overrides):
    values = dict(
        code="600001", probe_plan_id="probe:600001:s1:t10.0000",
        probe_filled_at="2026-11-18T11:00:00+08:00",
        probe_evidence_ids=("entry:probe:600001",), adds_done=0,
        version=config.content_hash, owner=_ref(config),
        family_id="probe:600001:s1:t10.0000",
        probe_order_id="", probe_filled_qty=0, pending_target_qty=None)
    values.update(overrides)
    return bs.PositionPolicyState(**values)


def _seed_state(ledger, *, batches=(), policy_states=()):
    state = ledger.load_state()
    state.batches = list(batches)
    state.policy_states = [item.to_dict() for item in policy_states]
    ledger.commit_event(expected_revision=state.revision, event_id="seed",
                        source_hash="seed", content_hash="seed", state=state)


def test_legacy_probe_without_fill_proof_cannot_add(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("r3-legacy")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        batch = Batch(batch_id="legacy-probe", code="600001", qty=400,
                      remaining=400, entry_date="2026-11-18",
                      cost_price=10.0, strategy_ref=_ref(config),
                      plan_id="probe:600001:s1:t10.0000",
                      sellable_mode=SELLABLE_ON, sellable_date="2026-11-19")
        _seed_state(ledger, batches=[batch],
                    policy_states=[_legacy_probe_state(config)])
        engine = _engine(ledger, config)
        third = _add_event(engine, config)
        # 缺真实 probe 父单/成交量/target：不新增 add（可解释原因）。
        assert not [o for o in _buy_orders(engine)
                    if o.plan_id.startswith("add:")]
        add_plans = [p for p in third["bundle"].plans
                     if p.plan_kind == bs.PLAN_ADD]
        assert add_plans and "probe_fill_proof_missing" in \
            add_plans[0].blocked_reasons


def test_explicit_policy_state_conflict_with_committed_is_rejected(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("r3-conflict")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        _seed_state(ledger, policy_states=[_legacy_probe_state(config)])
        engine = _engine(ledger, config)
        tampered = _legacy_probe_state(config, adds_done=1)
        with pytest.raises(LedgerError):
            _add_event_with_states(engine, config, (tampered,))


def _add_event_with_states(engine, config, states):
    cand = _candidate(10.4, ("confirm:600001",),
                      confirmations=(_confirmation(),),
                      version=config.content_hash)
    return engine.process_event(
        _Event(T3, {"600001": _row(T3, 10.4)}, day=DAY3), decide=True,
        candidates=(cand,),
        decision_kwargs={"scenarios": _scenarios_up(config),
                         "market": bs.MarketView(regime="trend_up",
                                                 new_risk_allowed=True),
                         "pressures": {"600001": 0.10},
                         "themes": {"600001": ("T1",)},
                         "policy_states": tuple(states)})


# ─── same-family 闭合只计一次（含 code 身份，不跨股票合并）──────────────────


def test_closed_group_identity_includes_code(tmp_path):
    from dragon_quant.review_account.execution_core.settlement import (
        settle_fills, trade_group_key,
    )
    from dragon_quant.review_account.execution_core.models import Fill, LedgerState

    family = "shared-family"
    a = Batch(batch_id="a", code="600001", qty=100, remaining=100,
              entry_date="2026-11-18", cost_price=10.0, family_id=family,
              plan_id=family, strategy_ref={"strategy_id": "s"},
              sellable_mode=SELLABLE_ON, sellable_date="2026-11-19")
    b = Batch(batch_id="b", code="600002", qty=100, remaining=100,
              entry_date="2026-11-18", cost_price=10.0, family_id=family,
              plan_id=family, strategy_ref={"strategy_id": "s"},
              sellable_mode=SELLABLE_ON, sellable_date="2026-11-19")
    # 相同 owner/family/plan、不同 code：稳定闭合身份必须不同。
    assert trade_group_key(a) != trade_group_key(b)
    state = LedgerState(cash=0.0, batches=[a, b])
    before = {item.batch_id: item for item in state.batches}
    # 只闭合 600001，600002 仍持仓。
    state.batches = [a.with_remaining(0), b]
    fills = [Fill(fill_id="f1", order_id="o1", code="600001", side="sell",
                  price=9.0, qty=100, fee=1.0, event_id="e", batch_id="a")]
    closed = settle_fills(state, fills=fills, batches_before=before, event_ts=T5)
    assert len(closed) == 1
    assert closed[0]["code"] == "600001"
    assert state.consecutive_losses == 1
    # 600002 的组仍开放，未被并入本闭合。
    assert any(batch.remaining == 100 for batch in state.batches
               if batch.code == "600002")


def test_probe_and_add_close_as_one_family_group(tmp_path):
    from dragon_quant.review_account.execution_core.settlement import (
        settle_fills, trade_group_key,
    )
    from dragon_quant.review_account.execution_core.models import (
        Fill, LedgerState,
    )

    family = "probe:600001:s1:t10.0000"
    probe = Batch(batch_id="b-probe", code="600001", qty=200, remaining=200,
                  entry_date="2026-11-18", cost_price=10.0, family_id=family,
                  plan_id=family, strategy_ref={"strategy_id": "s"},
                  sellable_mode=SELLABLE_ON, sellable_date="2026-11-19")
    add = Batch(batch_id="b-add", code="600001", qty=200, remaining=200,
                entry_date="2026-11-19", cost_price=10.5, family_id=family,
                plan_id="add:600001:s3:t10.0000",
                strategy_ref={"strategy_id": "s"},
                sellable_mode=SELLABLE_ON, sellable_date="2026-11-20")
    assert trade_group_key(probe) == trade_group_key(add)
    state = LedgerState(cash=0.0, batches=[probe, add])
    before = {b.batch_id: b for b in state.batches}
    # 全部卖出（同一家族）：只应产生一条闭合记录。
    state.batches = [b.with_remaining(0) for b in state.batches]
    fills = [
        Fill(fill_id="f1", order_id="o1", code="600001", side="sell",
             price=9.0, qty=200, fee=0.0, event_id="e", batch_id="b-probe"),
        Fill(fill_id="f2", order_id="o1", code="600001", side="sell",
             price=9.0, qty=200, fee=0.0, event_id="e", batch_id="b-add"),
    ]
    closed = settle_fills(state, fills=fills, batches_before=before,
                          event_ts=T5)
    assert len(closed) == 1
    assert state.consecutive_losses == 1


# ─── Scenario 与 AccountEngine 逐事件一致（G3 链）────────────────────────────


def _run_engine_chain(tmp_path, name):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger(name)
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        engine = _engine(ledger, config)
        audit = []
        audit.append(_probe_event(engine, config))
        audit.append(engine.process_event(_Event(T2, {"600001": _row(T2, 10.1)}, day=DAY2)))
        audit.append(_add_event(engine, config))
        audit.append(engine.process_event(
            _Event(T4, {"600001": _row(T4, 10.5, capacity=250)}, day=DAY4)))
        rows = [(f["price"], f["qty"], f["fee"])
                for result in audit for f in result["fill_records"]]
        return rows, engine.state().cash


def test_scenario_and_engine_g3_parity(tmp_path):
    config = bs.teaching_fixture()
    engine_fills, engine_cash = _run_engine_chain(tmp_path, "g3-parity-engine")

    at_by_step = {1: _iso(T1), 2: _iso(T2), 3: _iso(T3), 4: _iso(T4),
                  5: _iso(T5)}
    capacity_by_step = {4: LOW_CAPACITY}
    paths = bs.scenario_set_from_records(
        _steps(UP, at_by_step=at_by_step,
               capacity_by_step=capacity_by_step,
               trade_dates=[DAY1, DAY2, DAY3, DAY4, DAY5]),
        spec=config.scenario)
    path = paths.paths[0]
    execution = execution_from_config(config)
    from dragon_quant.tests._bs_policy_support import _account
    run = ScenarioRun.build(_account(), path, config.scenario,
                            execution=execution)
    runtime = AccountPolicyRuntime(config, strategy_ref=_ref(config))
    market = bs.MarketView(regime="trend_up", new_risk_allowed=True)
    candidates = {
        1: _candidate(10.0, ("entry:probe:600001",)),
        3: _candidate(10.4, ("confirm:600001",),
                      confirmations=(_confirmation(),),
                      version=config.content_hash),
    }
    scenario_fills = []
    for step in path.steps:
        commands = []
        if step.index in candidates:      # 与 AccountEngine 同为「观察→决策」事件
            kwargs = {"decision_time": step.at, "rows": {},
                      "trade_date": step.trade_date,
                      "calendar": tuple(run.calendar),
                      "event_time": step.index, "scenarios": paths,
                      "market": market, "pressures": {"600001": 0.10},
                      "themes": {"600001": ("T1",)}}
            seed_committed_policy_inputs(run.state, kwargs)
            decision = runtime.decide(run.state,
                                      candidates=(candidates[step.index],),
                                      **kwargs)
            cancel_stale_buys(run.state, decision.intents)
            commands = [command_from_intent(i) for i in decision.intents]
        result = run.execute(step, commands)
        advance_state_policy_states(
            run.state, fills=result.fills, event_time_iso=step.at,
            event_ts=step.index, version=config.content_hash)
        scenario_fills.extend((f.price, f.qty, f.fee) for f in result.fills)

    assert scenario_fills == engine_fills
    assert run.state.cash == pytest.approx(engine_cash, abs=1e-6)
    assert [o.side for o in run.state.orders] == \
        ["buy", "buy"]      # probe + add，无重复/无越界


# ─── C3b3：pure 非 fixture proof 边界与 decide=False 冻结硬失效 ──────────────


def test_non_fixture_pure_context_forces_fill_proof():
    """非 fixture 纯核入口不能靠调用者忘记置 True 绕过真实成交证明。"""
    config = bs.teaching_fixture(fixture=False)
    legacy = _legacy_probe_state(config)
    account = bs.AccountView(
        cash=100_000.0,
        positions=(bs.PositionView(
            code="600001", qty=400, sellable_qty=400, cost_price=10.0,
            entry_date="2026-11-18", mark=10.4, pressure_pct=0.10,
            trend="up"),))
    cand = _candidate(10.4, ("confirm:600001",),
                      confirmations=(_confirmation(),),
                      version=config.content_hash)
    context = bs.PolicyContext(
        decision_time="2026-11-18T15:00:00+08:00", config=config,
        account=account, candidates=(cand,),
        market=bs.MarketView(regime="trend_up", new_risk_allowed=True),
        scenarios=_scenarios_up(config), policy_states=(legacy,),
        active_owner=_ref(config), require_fill_proof=False)
    # 非 fixture：即使显式传 False 也被强制为 True。
    assert context.require_fill_proof is True
    bundle = bs.evaluate_account_policy(context)
    add = next(p for p in bundle.plans if p.plan_kind == bs.PLAN_ADD)
    assert add.executable is False
    assert "probe_fill_proof_missing" in add.blocked_reasons


def test_decide_false_event_revokes_below_frozen_line_before_matching(tmp_path):
    """无新决定的事件也必须先按冻结价格失效线撤未成余量再撮合。"""
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g3-decide-false")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        engine = _engine(ledger, config)
        _probe_event(engine, config)
        order = _buy_orders(engine)[0]
        assert order.invalidate_below is not None
        below = order.invalidate_below - 0.5
        result = engine.process_event(
            _Event(T2, {"600001": _row(T2, below)}, day=DAY2))
        assert order.order_id in result["canceled"]
        assert result["fill_records"] == []
        state = engine.state()
        cancelled = next(o for o in state.orders
                         if o.order_id == order.order_id)
        assert cancelled.status == "cancelled"


def test_decide_false_event_keeps_legit_pending_above_line(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g3-decide-false-ok")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        engine = _engine(ledger, config)
        _probe_event(engine, config)
        order = _buy_orders(engine)[0]
        result = engine.process_event(
            _Event(T2, {"600001": _row(T2, 10.2)}, day=DAY2))
        assert order.order_id not in result["canceled"]
        assert [f["qty"] for f in result["fill_records"]] == [400]


def test_decide_false_event_keeps_pending_when_unfilled(tmp_path):
    """无决定事件不撤全部 pending：容量不足时合法挂单保留，不被当作新政策撤销。"""
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g3-decide-false-pending")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        engine = _engine(ledger, config)
        _probe_event(engine, config)
        order = _buy_orders(engine)[0]
        result = engine.process_event(
            _Event(T2, {"600001": _row(T2, 10.2, capacity=0)}, day=DAY2))
        assert order.order_id not in result["canceled"]
        state = engine.state()
        pending = next(o for o in state.orders if o.order_id == order.order_id)
        assert not pending.is_terminal
        assert any(r.order_id == order.order_id and r.status == "reserved"
                   for r in state.reservations)


def test_decide_false_future_quote_does_not_revoke(tmp_path):
    """未来/迟到报价不参与冻结价格线撤销（PIT：不用未来数据做撤销）。"""
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g3-future-quote")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        engine = _engine(ledger, config)
        _probe_event(engine, config)
        order = _buy_orders(engine)[0]
        future = {
            "execution_price": 8.0, "execution_timestamp": T2 + 60_000,
            "available_at": T2 + 60_000, "available_qty": 10 ** 9,
            "tradable": True,
        }
        result = engine.process_event(_Event(T2, {"600001": future}, day=DAY2))
        assert order.order_id not in result["canceled"]
        assert result["fill_records"] == []
