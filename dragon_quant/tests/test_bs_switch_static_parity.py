"""C3b3 M3：W2 静态反事实与 W3 引擎/公共 replay 同一真实资金约束。

相同初始完整账户、政策、事件价格/容量、费用与资金来源规则下，`simulate_plan`
的换仓承诺路径（父卖创建/成交、子买依赖/净款资助/残余）必须与公共 replay
（共用 AccountEventProcessor）的成交/现金/残余一致；卖败不得让子买成交。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from dragon_quant.review_account.evidence import CapabilityCheck
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
from dataclasses import replace

from dragon_quant.review_account.strategy.account_runtime import (
    AccountDecision, AccountPolicyRuntime,
)
from dragon_quant.review_account.strategy.bs_policy.scenario_execution import (
    execution_from_config,
)
from dragon_quant.review_account.strategy.bs_policy.simulate import simulate_plan
from dragon_quant.tests._bs_policy_support import _scenarios
from dragon_quant.tests.support import patch_storage_dbs

_CST = timezone(timedelta(hours=8))

D1, D2, D3, D4, D5 = (
    "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24")
CALENDAR = (D1, D2, D3, D4, D5, "2026-11-25")
PRICES = {"600001": [10.5, 10.5, 10.5, 9.9, 9.45],
          "600002": [10.5, 10.5, 10.5, 10.5, 12.0]}


def _ms(day, hour=15):
    return int(datetime.strptime(f"{day} {hour:02d}", "%Y-%m-%d %H")
               .replace(tzinfo=_CST).timestamp() * 1000)


T1, T2, T3, T4, T5 = (_ms(day) for day in (D1, D2, D3, D4, D5))


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


def _row(ts, price, *, capacity=10 ** 9):
    return {"execution_price": price, "execution_timestamp": ts,
            "available_at": ts, "available_qty": capacity, "tradable": True}


def _checks(*rules):
    return tuple(CapabilityCheck(rule_id=rule, capability="intraday_bars",
                                 status="usable", protects="new_risk")
                 for rule in rules)


def _candidate(price=10.5):
    return bs.CandidateView(
        code="600002", name="候选二", eligible=True, tradable=True,
        price=price, pressure_pct=0.10, rank=1, theme_ids=("T2",),
        structure={"breakout_level": 10.2, "pullback_level": 9.8,
                   "confirm_level": 10.5, "probe_level": 10.5},
        evidence_checks=_checks("entry:confirm:600002",
                                "entry:breakout:600002"))


def _ref(config):
    from dragon_quant.workspace import snapshots
    return snapshots.builtin_ref(strategy_id=bs.BS_ACCOUNT_STRATEGY_ID,
                                 params={"bs_policy": config.to_dict()})


def _account(config):
    return bs.AccountView(
        cash=100_000.0,
        positions=(bs.PositionView(
            code="600001", name="持仓", qty=1000, sellable_qty=1000,
            cost_price=10.0, entry_date="2026-11-17", mark=10.5,
            theme_ids=("T1",), pressure_pct=0.10, trend="down", held_days=2,
            strategy_ref=_ref(config),
            batches=(bs.BatchView(
                batch_id="incumbent", qty=1000, sellable_qty=1000,
                entry_date="2026-11-17", cost_price=10.0,
                strategy_ref=_ref(config), plan_id="legacy:600001",
                original_qty=1000, initial_risk=100.0),)),),
    )


def _paths(config, capacity_by_step=None):
    return _scenarios(config, {"up": PRICES},
                      trade_dates=list(CALENDAR[:5]),
                      capacity_by_step=capacity_by_step or {})


def _switch_plan(config, scenarios):
    context = bs.PolicyContext(
        decision_time=f"{D1}T15:00:00+08:00", config=config,
        account=_account(config), candidates=(_candidate(),),
        market=bs.MarketView(regime="trend_up", new_risk_allowed=True),
        scenarios=scenarios)
    bundle = bs.evaluate_account_policy(context)
    plan = next(p for p in bundle.plans if p.plan_kind == bs.PLAN_SWITCH)
    assert plan.executable, plan.blocked_reasons
    return plan


class _ForcedSwitchRuntime(AccountPolicyRuntime):
    """测试适配：把同一真实 switch plan 固定为选中方案，隔离“动态重选”差异，
    使 W2 静态承诺路径与 W3 执行路径比较同一计划（订单/资助/撮合仍是真实实现）。
    """

    def __init__(self, *args, plan_id: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._plan_id = plan_id

    def decide(self, state, *, candidates, decision_time, event_time=None,
               **kwargs):
        decision = super().decide(
            state, candidates=candidates, decision_time=decision_time,
            event_time=event_time, **kwargs)
        plan = next((item for item in decision.bundle.plans
                     if item.plan_id == self._plan_id), None)
        if plan is None:
            return decision
        bundle = replace(decision.bundle, selected_plan_id=self._plan_id,
                         selected_actions=plan.actions)
        return AccountDecision(
            bundle=bundle,
            intents=tuple(self._intents(bundle, state, self._plan_id,
                                        event_time=event_time)))


def _static_fills(config, scenarios, capacity_by_step=None):
    path = scenarios.paths[0]
    plan = _switch_plan(config, scenarios)
    outcome = simulate_plan(
        plan, path, account=_account(config), spec=config.scenario,
        initial_evidence=("entry:confirm:600002", "entry:breakout:600002"),
        execution=replace(execution_from_config(config),
                          calendar=CALENDAR))
    fills = [(fill["side"], fill["code"], fill["price"], fill["qty"],
              fill["fee"])
             for entry in outcome.timeline for fill in entry["fills"]]
    cash = outcome.timeline[-1]["cash"] if outcome.timeline else None
    return fills, cash, plan.plan_id


def _engine_fills(tmp_path, config, scenarios, capacity_by_step=None,
                  plan_id=""):
    with _env(tmp_path):
        ledger = AccountLedger("m3-static-parity")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        state = ledger.load_state()
        state.batches = [Batch(
            batch_id="incumbent", code="600001", qty=1000, remaining=1000,
            entry_date="2026-11-17", cost_price=10.0, initial_risk=100.0,
            strategy_ref=_ref(config), plan_id="legacy:600001",
            sellable_mode=SELLABLE_ON, sellable_date=D1)]
        state.marks = {"600001": 10.5}
        ledger.commit_event(expected_revision=state.revision, event_id="seed",
                            source_hash="seed", content_hash="seed",
                            state=state)
        engine = AccountEngine(
            ledger, _ForcedSwitchRuntime(config, strategy_ref=_ref(config),
                                         plan_id=plan_id),
            ExecutionCostModel(), calendar=list(CALENDAR), order_ttl_ms=None,
            risk_caps=RiskCaps.teaching_fixture(),
            max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)
        capacity = capacity_by_step or {}
        rows = []
        for index, (ts, day) in enumerate(((T2, D2), (T3, D3), (T4, D4),
                                           (T5, D5)), start=2):
            inc_cap = capacity.get(index, {}).get("600001", 10 ** 9)
            rows.append(_Event(
                ts, {"600001": _row(ts, PRICES["600001"][index - 1],
                                    capacity=inc_cap),
                     "600002": _row(ts, PRICES["600002"][index - 1])}, day))
        first = engine.process_event(
            rows[0], decide=True, candidates=(_candidate(),),
            decision_kwargs={
                "scenarios": scenarios,
                "market": bs.MarketView(regime="trend_up",
                                        new_risk_allowed=True),
                "pressures": {"600001": 0.10, "600002": 0.10},
                "themes": {"600001": ("T1",), "600002": ("T2",)},
                "trends": {"600001": "down", "600002": "up"},
                "held_days": {"600001": 2}})
        assert first["status"] == "applied"
        assert first["bundle"].selected_plan_id == plan_id
        fills = [(f["side"], f["code"], f["price"], f["qty"], f["fee"])
                 for f in first["fill_records"]]
        for event in rows[1:]:
            result = engine.process_event(event)
            assert result["status"] == "applied"
            fills.extend((f["side"], f["code"], f["price"], f["qty"], f["fee"])
                         for f in result["fill_records"])
        return fills, engine.state().cash


@pytest.mark.parametrize("capacity_by_step", [
    None,                                   # 父卖全成
    {3: {"600001": 400}},                   # partial 卖
    {3: {"600001": 0}, 4: {"600001": 0}, 5: {"600001": 0}},  # 卖败
])
def test_static_simulate_and_engine_share_funding_rules(tmp_path, capacity_by_step):
    # 只保留换仓作为可执行新风险方案（普通入场/试探 blocked），使 engine 与
    # 静态 plan 比较的是同一承诺路径。
    config = bs.teaching_fixture(supported_entries=["auction_confirm"])
    scenarios = _paths(config, capacity_by_step)
    static_fills, static_cash, plan_id = _static_fills(
        config, scenarios, capacity_by_step)
    engine_fills, engine_cash = _engine_fills(
        tmp_path, config, scenarios, capacity_by_step, plan_id=plan_id)
    # 同事件内静态/账本按 order_id 排序不同，比较成交集合与现金。
    assert sorted(engine_fills) == sorted(static_fills)
    assert engine_cash == pytest.approx(static_cash, abs=1e-6)
    if capacity_by_step is None:
        # 全卖：父卖 + 子买真实成交，且子买按净款额度（不是自由现金）。
        assert [fill[0] for fill in engine_fills] == ["sell", "buy"]
    elif capacity_by_step.get(3, {}).get("600001") == 400:
        assert engine_fills and engine_fills[0][0] == "sell"
        assert engine_fills[0][3] == 400
        assert engine_fills[-1][0] == "buy"
        assert engine_fills[-1][3] < 900
    else:
        # 卖败：不得伪造子买成交，双方一致保持未成交。
        assert engine_fills == []
