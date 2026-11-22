"""C3b3 C8：真实收缩 → 冷却 → 分阶段恢复（锁存、交易日历、PIT 条件）。

覆盖：触发日 0；同一交易日多事件只推进一阶段；周末/休市不推进；条件必须由
PIT 确认（未来/错误 snapshot/裸字符串不恢复）；末阶段 < 1.0 保持上限；重启/重复
幂等；scale 实际限制新增数量；收缩目标只按触发日存量、恢复期新批次不再半仓。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from dragon_quant.review_account.execution_core.matching import ExecutionCostModel
from dragon_quant.review_account.execution_core.models import (
    Batch, SELLABLE_ON,
)
from dragon_quant.review_account.execution_core.risk import RiskCaps
from dragon_quant.review_account.execution_core.runtime_facts import (
    TEACHING_QUOTE_MAX_AGE_MS,
)
from dragon_quant.review_account.ledger import AccountLedger
from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.review_account.strategy.account_engine import AccountEngine
from dragon_quant.review_account.strategy.account_runtime import (
    AccountPolicyRuntime,
)
from dragon_quant.review_account.strategy.bs_policy.recovery import (
    _confirmed_conditions,
)
from dragon_quant.tests._bs_policy_support import _account, _candidate, _context
from dragon_quant.tests.support import patch_storage_dbs

_CST = timezone(timedelta(hours=8))

D1, D2, D3, D4, D5 = (
    "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24")
CALENDAR = (D1, D2, D3, D4, D5, "2026-11-25")
SATURDAY = "2026-11-21"


def _ms(day, hour=10):
    return int(datetime.fromisoformat(day + f"T{hour:02d}:00:00").replace(
        tzinfo=_CST).timestamp() * 1000)


def _ms_of(day_str, hour=10):
    return int(datetime.strptime(f"{day_str} {hour:02d}", "%Y-%m-%d %H")
               .replace(tzinfo=_CST).timestamp() * 1000)


T1, T2, T3, T4, T5 = (_ms_of(day) for day in (D1, D2, D3, D4, D5))


@contextmanager
def _env(tmp_path):
    dragon = str(tmp_path / "dragon.db")
    with patch_storage_dbs(dragon, str(tmp_path / "assets.db")), \
            patch("dragon_quant.infra.storage.paths.DB_PATH", Path(dragon)):
        yield


class _Event:
    def __init__(self, ts, rows, day):
        self.timestamp = ts
        self.trade_date = day
        self.rows = rows
        self.unavailable = {}
        self.last_closes = {}
        self.candidates = []
        self.phase = "bar"
        self.evidence_available = ()


def _row(ts, price):
    return {"execution_price": price, "execution_timestamp": ts,
            "available_at": ts, "available_qty": 10 ** 9, "tradable": True}


def _ref(config):
    from dragon_quant.workspace import snapshots
    return snapshots.builtin_ref(strategy_id=bs.BS_ACCOUNT_STRATEGY_ID,
                                 params={"bs_policy": config.to_dict()})


def _confirmations(config, *, available_at="2026-11-19T08:00:00+08:00",
                   snapshot_id=""):
    return tuple(
        bs.RecoveryConditionConfirmation(
            rule_id=rule, evidence_ids=(f"evidence:{rule}",), source="pit",
            available_at=available_at, snapshot_id=snapshot_id, version="v1")
        for rule in config.recovery.conditions)


def _engine(ledger, config):
    return AccountEngine(
        ledger, AccountPolicyRuntime(config, strategy_ref=_ref(config)),
        ExecutionCostModel(), calendar=list(CALENDAR), order_ttl_ms=None,
        risk_caps=RiskCaps.teaching_fixture(),
        max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)


def _seed_batch(ledger, config, *, batch_id="incumbent", code="600001",
                qty=1000, entry="2026-11-17"):
    state = ledger.load_state()
    state.batches = [Batch(
        batch_id=batch_id, code=code, qty=qty, remaining=qty,
        entry_date=entry, cost_price=10.0, initial_risk=100.0,
        strategy_ref=_ref(config), plan_id=f"legacy:{code}",
        sellable_mode=SELLABLE_ON, sellable_date=D1)]
    state.marks = {code: 10.0}
    ledger.commit_event(expected_revision=state.revision, event_id="seed",
                        source_hash="seed", content_hash="seed", state=state)


def _decide(engine, *, ts, day, confirmations=(), drawdown=12.0,
            candidates=(), extra=None):
    kwargs = {"envelope": bs.RiskEnvelope(drawdown_pct=drawdown),
              "recovery_confirmations": tuple(confirmations)}
    if extra:
        kwargs.update(extra)
    return engine.process_event(
        _Event(ts, {"600001": _row(ts, 10.0)}, day), decide=True,
        candidates=tuple(candidates), decision_kwargs=kwargs)


def _latch(engine):
    return bs.RecoveryState.from_row(engine.state().recovery)


def test_g8_staged_recovery_caps_at_configured_last_stage(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g8-stages")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        _seed_batch(ledger, config)
        engine = _engine(ledger, config)
        confirmations = _confirmations(config)
        scales: list[float] = []
        stages: list[int] = []
        days: list[int] = []
        for ts, day in ((T1, D1), (T2, D2), (T3, D3), (T4, D4), (T5, D5)):
            result = _decide(engine, ts=ts, day=day,
                             confirmations=confirmations if day >= D3 else ())
            assert result["status"] == "applied"
            scales.append(result["bundle"].recovery["scale"])
            latch = _latch(engine)
            stages.append(latch.stage)
            days.append(latch.days_since_trigger)
            assert latch.active is True
        # 触发日 0：stage 0 / scale 0.25；每个新交易日最多升一阶段。
        assert stages == [0, 0, 1, 2, 2]
        assert days == [0, 1, 2, 3, 4]
        assert scales == pytest.approx([0.25, 0.25, 0.25, 0.5, 0.75])
        # 末阶段 0.75 < 1.0：保持该授权上限，不自动恢复 1.0。
        assert scales[-1] == pytest.approx(0.75)


def test_g8_same_trading_day_advances_once(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g8-same-day")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        _seed_batch(ledger, config)
        engine = _engine(ledger, config)
        confirmations = _confirmations(config)
        _decide(engine, ts=T1, day=D1)
        _decide(engine, ts=T2, day=D2)
        applied = _decide(engine, ts=T3, day=D3, confirmations=confirmations)
        assert applied["status"] == "applied"
        after_first = _latch(engine)
        assert (after_first.stage, after_first.days_since_trigger) == (1, 2)
        # 同一交易日第二个事件：不重复推进天数/阶段。
        _decide(engine, ts=T3 + 3_600_000, day=D3,
                confirmations=confirmations)
        after_second = _latch(engine)
        assert after_second.stage == 1
        assert after_second.days_since_trigger == 2


def test_g8_weekend_does_not_advance_days(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g8-weekend")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        _seed_batch(ledger, config)
        engine = _engine(ledger, config)
        _decide(engine, ts=T1, day=D1)
        _decide(engine, ts=T2, day=D2)
        before = _latch(engine).days_since_trigger
        assert before == 1
        # 周六事件不在真实交易日历内：不推进（不用截断日历假精确）。
        _decide(engine, ts=_ms_of(SATURDAY), day=SATURDAY)
        assert _latch(engine).days_since_trigger == before


def test_g8_restart_is_idempotent(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g8-restart")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        _seed_batch(ledger, config)
        engine = _engine(ledger, config)
        confirmations = _confirmations(config)
        for ts, day in ((T1, D1), (T2, D2), (T3, D3)):
            _decide(engine, ts=ts, day=day,
                    confirmations=confirmations if day >= D3 else ())
        latch = _latch(engine)
        engine2 = _engine(ledger, config)
        assert _latch(engine2).stage == latch.stage
        # 相同事件重放幂等，不重复推进。
        replay = _decide(engine2, ts=T3, day=D3,
                         confirmations=confirmations)
        assert replay["status"] == "already_processed"
        assert _latch(engine2).stage == latch.stage


def test_g8_conditions_require_pit_confirmation():
    config = bs.teaching_fixture()
    future = bs.RecoveryState(
        active=True, stage=0, days_since_trigger=5,
        confirmations=_confirmations(
            config, available_at="2026-11-19T08:00:00+08:00"))
    # 决策时点在确认可用之前：迟到修订不回填过去授权。
    context = _context(config=config, recovery=future,
                       decision_time="2026-11-18T16:00:00+08:00")
    assert not _confirmed_conditions(context, config)
    # 裸 conditions_met 字符串不足以解锁。
    bare = bs.RecoveryState(active=True, stage=0, days_since_trigger=5,
                            conditions_met=tuple(config.recovery.conditions))
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=_account(drawdown_pct=12.0),
        candidates=(_candidate(),), recovery=bare))
    assert bundle.recovery["can_resume"] is False
    assert bundle.recovery["scale"] == pytest.approx(0.25)


def test_g8_wrong_snapshot_confirmation_does_not_resume():
    from types import SimpleNamespace

    config = bs.teaching_fixture()
    state = bs.RecoveryState(
        active=True, stage=0, days_since_trigger=5,
        confirmations=_confirmations(config, snapshot_id="other-snapshot"))
    context = bs.PolicyContext(
        decision_time="2026-11-19T16:00:00+08:00", config=config,
        account=_account(drawdown_pct=12.0),
        snapshot=SimpleNamespace(snapshot_id="expected-snapshot"),
        recovery_state=state)
    assert not _confirmed_conditions(context, config)


def test_g8_scale_actually_limits_new_risk_quantity(tmp_path):
    with _env(tmp_path):
        config = bs.teaching_fixture()

        def _probe_qty(prefix: str, recovery=None) -> int:
            ledger = AccountLedger(prefix)
            ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
            engine = _engine(ledger, config)
            kwargs = {"market": bs.MarketView(regime="trend_up",
                                              new_risk_allowed=True),
                      "pressures": {"600002": 0.10},
                      "themes": {"600002": ("T2",)}}
            if recovery is not None:
                kwargs["recovery"] = recovery
            # 无场景 → 只观察；探针计划仍按 scale 计算目标数量。
            result = engine.process_event(
                _Event(T1, {"600002": _row(T1, 10.0)}, D1), decide=True,
                candidates=(_candidate(price=10.0, code="600002"),),
                decision_kwargs=kwargs)
            probe = [p for p in result["bundle"].plans
                     if p.plan_kind == bs.PLAN_PROBE]
            return int(probe[0].target_qty or 0) if probe else 0

        normal = _probe_qty("g8-scale-normal")
        reduced = _probe_qty(
            "g8-scale-reduced",
            recovery=bs.RecoveryState(
                active=True, stage=1, days_since_trigger=2,
                confirmations=_confirmations(config)))
        assert normal > 0 and reduced > 0
        assert reduced < normal


def test_contraction_target_snapshot_excludes_later_and_new_batches(tmp_path):
    """M5：触发时点撮合前快照；之后新增（含同日/恢复期）批次不进旧目标。"""
    from dragon_quant.review_account.strategy.bs_policy.protection import (
        _budget_reduce_snapshot,
    )

    config = bs.teaching_fixture()
    # 纯函数：快照只含 old；current 同时有 old/new 时仍只按 old 计算目标。
    position = _position_view([
        _batch_view("old", qty=1000, entry="2026-11-17"),
        _batch_view("new", qty=1000, entry=D1),
    ])
    snapshot = {"batch_ids": ["old"], "target_qty": 500,
                "remaining": {"old": 1000}}
    qty, scope, ok, _ = _budget_reduce_snapshot(position, snapshot, {}, 100)
    assert qty == 500 and scope == ("old",) and ok
    # 更强保护已覆盖 100 股 → 剩余目标 400（不重新半仓）。
    qty, scope, ok, _ = _budget_reduce_snapshot(
        position, snapshot, {"old": 100}, 100)
    assert qty == 400 and scope == ("old",) and ok
    # old 完全卖出后，快照完成量抵扣完目标：不因只剩 new 重新半仓。
    closed = _position_view([_batch_view("new", qty=1000, entry=D1)])
    qty, scope, ok, _ = _budget_reduce_snapshot(closed, snapshot, {}, 100)
    assert qty == 0 and scope == () and not ok

    with _env(tmp_path):
        ledger = AccountLedger("g8-snapshot")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        _seed_batch(ledger, config, batch_id="old")   # 仅 old 在触发时在册
        engine = _engine(ledger, config)
        first = _decide(engine, ts=T1, day=D1, drawdown=12.0)
        assert first["status"] == "applied"
        latch = _latch(engine)
        assert latch.active and latch.contraction_targets
        targets = dict(latch.contraction_targets)["600001"]
        assert tuple(targets["batch_ids"]) == ("old",)
        assert targets["target_qty"] == 500
        # 触发后同日新增批次：不进入旧 episode 目标。
        state = ledger.load_state()
        state.batches.append(Batch(
            batch_id="new", code="600001", qty=1000, remaining=1000,
            entry_date=D1, cost_price=10.0, initial_risk=100.0,
            strategy_ref=_ref(config), plan_id="add:600001",
            sellable_mode=SELLABLE_ON, sellable_date=D2))
        ledger.commit_event(expected_revision=state.revision, event_id="seed2",
                            source_hash="seed2", content_hash="seed2",
                            state=state)
        engine2 = _engine(ledger, config)
        second = _decide(engine2, ts=T2, day=D2, drawdown=12.0)
        assert second["status"] == "applied"
        assert not [i for i in second["documented_intents"]
                    if "new" in (i.batch_ids or ())]
        reduces = [i for i in second["documented_intents"]
                   if i.action == "sell" and i.batch_ids == ("old",)]
        assert reduces and reduces[0].qty == 500


def _batch_view(batch_id: str, *, qty: int, entry: str):
    return bs.BatchView(batch_id=batch_id, qty=qty, sellable_qty=qty,
                        entry_date=entry, cost_price=10.0)


def _position_view(batches):
    return bs.PositionView(code="600001", qty=sum(b.qty for b in batches),
                           sellable_qty=sum(b.sellable_qty for b in batches),
                           cost_price=10.0, entry_date=batches[0].entry_date,
                           mark=10.0, batches=tuple(batches))


def test_g8_graduated_episode_restarts_at_first_stage():
    """末阶段 1.0 且合法完成后毕业；新一轮触发从 stage 0 重新收缩，不沿用满额。"""
    from dragon_quant.review_account.strategy.bs_policy.recovery import (
        advance_recovery_after_decision, advance_recovery_days,
    )

    base = bs.teaching_fixture()
    recovery = dict(base.recovery.to_dict())
    recovery["stages"] = [0.5, 1.0]
    recovery["cooldown_days"] = 0
    config = bs.teaching_fixture(recovery=recovery)
    confirmations = _confirmations(config)
    calendar = (D1, D2, D3)

    position = bs.PositionView(code="600001", qty=1000, sellable_qty=1000,
                               cost_price=10.0, entry_date="2026-11-17",
                               mark=10.0)
    account = _account(drawdown_pct=12.0, positions=(position,))

    def _bundle(latch, decision_time):
        return bs.evaluate_account_policy(_context(
            config=config, account=account, recovery=latch,
            decision_time=decision_time))

    latch = bs.RecoveryState(active=False)
    first = _bundle(latch, "2026-11-18T16:00:00+08:00")
    assert first.recovery["scale"] == pytest.approx(0.5)
    latch = advance_recovery_after_decision(
        latch, recovery=first.recovery, config=config, trade_date=D1,
        event_ts=1, calendar=calendar, confirmations=confirmations)
    assert latch.active and latch.stage == 0
    assert dict(latch.contraction_targets)["600001"]["target_qty"] == 500
    # 下一有效交易日：升到末阶段 1.0 并毕业回正常。
    latch = advance_recovery_days(latch, trade_date=D2, calendar=calendar)
    second = _bundle(latch, "2026-11-19T16:00:00+08:00")
    assert second.recovery["scale"] == pytest.approx(0.5)
    latch = advance_recovery_after_decision(
        latch, recovery=second.recovery, config=config, trade_date=D2,
        event_ts=2, calendar=calendar, confirmations=confirmations)
    assert latch.active is False and latch.graduated_trade_date == D2
    assert dict(latch.contraction_targets) == {}
    # 再次触发（更坏风险仍在）：从 stage 0 重新开始并按新存量快照，不沿用满额。
    third = _bundle(latch, "2026-11-20T16:00:00+08:00")
    assert third.recovery["active"] is True
    assert third.recovery["stage"] == 0
    assert third.recovery["scale"] == pytest.approx(0.5)
    assert dict(third.recovery["contraction_targets"])["600001"]["target_qty"] == 500


def test_g8_current_drawdown_triggers_in_same_decision_without_injection(tmp_path):
    """M1：本事件真实回撤（无 RiskEnvelope 注入）当次触发收缩；旧峰值不被覆盖。

    触发前已 partial 的买单剩余同属新增风险，按本事件新授权撤余量，不按旧满额
    继续成交；已成交部分保留。
    """
    from dragon_quant.review_account.execution_core.models import (
        Order, Reservation,
    )

    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g8-current-dd")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        state = ledger.load_state()
        state.batches = [Batch(
            batch_id="big", code="600001", qty=10000, remaining=10000,
            entry_date="2026-11-17", cost_price=10.0, initial_risk=100.0,
            strategy_ref=_ref(config), plan_id="legacy",
            sellable_mode=SELLABLE_ON, sellable_date=D1),
            Batch(
            batch_id="steady", code="600003", qty=1000, remaining=1000,
            entry_date="2026-11-17", cost_price=10.0, initial_risk=100.0,
            strategy_ref=_ref(config), plan_id="legacy3",
            sellable_mode=SELLABLE_ON, sellable_date=D1)]
        state.marks = {"600001": 10.0, "600003": 10.0}
        ledger.commit_event(expected_revision=state.revision, event_id="seed",
                            source_hash="seed", content_hash="seed", state=state)
        engine = _engine(ledger, config)
        # 事件1：价格 10，建立真实峰值 200000（回撤 0，不触发）。
        first = engine.process_event(
            _Event(T1, {"600001": _row(T1, 10.0),
                        "600003": _row(T1, 10.0)}, D1), decide=True)
        assert first["status"] == "applied"
        assert engine.state().peak_equity == pytest.approx(210_000.0)
        assert bs.RecoveryState.from_row(engine.state().recovery).active is False
        # 在触发前已存在一张 partial 买单（模拟先前决策留下）。
        state = ledger.load_state()
        state.orders.append(Order(
            order_id="pending-buy", code="600002", side="buy", qty=900,
            status="partially_filled", created_event=T1, filled_qty=250,
            reserved_amount=7000.0, reserved_risk=50.0,
            risk_amount=100.0, strategy_ref=_ref(config),
            plan_id="add:600002", family_id="add:600002"))
        state.reservations.append(Reservation(
            order_id="pending-buy", code="600002", side="buy", qty=650,
            amount=7000.0, status="reserved", purpose="risk",
            risk_amount=50.0))
        ledger.commit_event(expected_revision=state.revision, event_id="seed2",
                            source_hash="seed2", content_hash="seed2",
                            state=state)
        engine2 = _engine(ledger, config)
        # 事件2：**同日**真实价格 4（无 envelope 注入）→ 当次决策即触发收缩。
        crash_ts = T1 + 3_600_000
        second = engine2.process_event(
            _Event(crash_ts, {"600001": _row(crash_ts, 4.0),
                              "600003": _row(crash_ts, 10.0)}, D1),
            decide=True)
        assert second["status"] == "applied"
        bundle = second["bundle"]
        assert bundle.recovery["active"] is True
        assert bundle.recovery["scale"] == pytest.approx(0.25)
        # partial 买单剩余被撤；已成交 250 保留，不按旧满额继续买。
        assert "pending-buy" in second["canceled"]
        persisted = next(o for o in engine2.state().orders
                         if o.order_id == "pending-buy")
        assert persisted.status == "cancelled"
        assert persisted.filled_qty == 250
        assert not [f for f in second["fill_records"] if f["side"] == "buy"]
        # 触发事件当次生效：600001 已被更强灾难保护全退；600003 不是灾难但
        # 按新收缩授权生成 500 股减仓目标（目标快照 = 触发时存量一半）。
        sells = [i for i in second["documented_intents"] if i.action == "sell"]
        assert any(i.code == "600001" and i.qty == 10000 for i in sells)
        assert any(i.code == "600003" and i.qty == 500 for i in sells)
        latch_after = bs.RecoveryState.from_row(engine2.state().recovery)
        targets = dict(latch_after.contraction_targets)
        assert targets["600001"]["target_qty"] == 5000
        assert targets["600003"]["target_qty"] == 500
        state = engine2.state()
        assert state.drawdown_pct == pytest.approx(28.571428, rel=1e-4)
        assert state.peak_equity == pytest.approx(210_000.0)
        latch_persisted = bs.RecoveryState.from_row(state.recovery)
        assert latch_persisted.active is True
        # 重启后同一新 episode 的锚/目标/阶段不靠下一事件才生效。
        restarted = _engine(ledger, config)
        reloaded = bs.RecoveryState.from_row(restarted.state().recovery)
        assert reloaded.active is True and reloaded.stage == latch_persisted.stage
        assert dict(reloaded.contraction_targets) == \
            dict(latch_persisted.contraction_targets)


def test_g8_weekend_does_not_advance_stage_with_valid_conditions(tmp_path):
    """M2：周末即使条件有效也不升阶段/毕业（必须有真实交易日推进锚）。"""
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g8-weekend-stage")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        _seed_batch(ledger, config)
        engine = _engine(ledger, config)
        confirmations = _confirmations(config)
        for ts, day in ((T1, D1), (T2, D2), (T3, D3)):
            _decide(engine, ts=ts, day=day, confirmations=confirmations)
        before = _latch(engine)
        assert before.active and before.stage == 1 and before.days_since_trigger == 2
        weekend = _decide(engine, ts=_ms_of(SATURDAY), day=SATURDAY,
                          confirmations=confirmations)
        assert weekend["status"] == "applied"
        after = _latch(engine)
        assert after.stage == before.stage
        assert after.days_since_trigger == before.days_since_trigger
        assert weekend["bundle"].recovery["scale"] == pytest.approx(0.5)


def test_g8_missing_anchor_does_not_advance_stage():
    """M2：旧行缺触发/推进锚时 fail closed，不靠外部天数逐事件抬阶段。"""
    from dragon_quant.review_account.strategy.bs_policy.recovery import (
        advance_recovery_after_decision, advance_recovery_days,
    )

    config = bs.teaching_fixture()
    legacy = bs.RecoveryState(active=True, stage=0, days_since_trigger=5)
    anchored = advance_recovery_days(legacy, trade_date=D1,
                                     calendar=(D1, D2))
    assert anchored.days_since_trigger == 5      # 不推过去天数
    assert anchored.last_advanced_trade_date == D1
    bundle = bs.evaluate_account_policy(_context(
        config=config, account=_account(drawdown_pct=12.0),
        recovery=anchored))
    assert bundle.recovery["can_resume"] is False
    assert bundle.recovery["anchor_verified"] is False
    advanced = advance_recovery_after_decision(
        anchored, recovery=bundle.recovery, config=config, trade_date=D1,
        event_ts=1, calendar=(D1, D2))
    assert advanced.stage == 0


def test_g8_weekend_cannot_graduate_even_with_conditions():
    """M2：周末/非交易日即使条件满足也不得毕业或提高授权。"""
    from dragon_quant.review_account.strategy.bs_policy.recovery import (
        advance_recovery_after_decision,
    )

    base = bs.teaching_fixture()
    recovery = dict(base.recovery.to_dict())
    recovery["stages"] = [0.5, 1.0]
    recovery["cooldown_days"] = 0
    config = bs.teaching_fixture(recovery=recovery)
    latch = bs.RecoveryState(
        active=True, stage=0, days_since_trigger=5,
        trigger_event=1, trigger_trade_date=D1,
        last_advanced_trade_date=D1, last_stage_advanced_trade_date="",
        confirmations=_confirmations(config))
    advanced = advance_recovery_after_decision(
        latch, recovery={"active": True, "can_resume": True},
        config=config, trade_date=SATURDAY, event_ts=2,
        calendar=(D1, D2))
    assert advanced.active is True and advanced.stage == 0
    assert advanced.graduated_trade_date == ""


def test_g8_unknown_current_valuation_restricts_new_risk(tmp_path):
    """M1：持仓缺新鲜 PIT 估值时不得用旧 quote/last_close 授权新增风险。"""
    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("g8-valuation-missing")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        _seed_batch(ledger, config)
        engine = _engine(ledger, config)
        engine.process_event(_Event(T1, {"600001": _row(T1, 10.0)}, D1),
                             decide=True)
        later = T1 + 5 * 86_400_000
        result = engine.process_event(
            _Event(later, {"600002": _row(later, 10.0)}, D5), decide=True)
        assert result["status"] == "applied"
        limits = result["bundle"].action_limits
        assert limits["new_risk_restricted"] is True
        assert "current_valuation_missing" in limits["restriction_reasons"]
        # 旧 quote 不触发新收缩；账户仍处于未收缩或既有锁存，不伪造新峰值。
        state = engine.state()
        assert state.peak_equity == pytest.approx(110_000.0)
