"""C1：同一执行序列下 Scenario adapter 与 AccountEngine **逐事件**一致。

构造相同初始完整账户、相同冻结归属/命令与相同真实事件时点（event/available_at/
trade_date 一致），分别走 ``bs_policy.simulate_plan``（Scenario adapter）与
``AccountEngine``（账本 adapter），断言每个事件的真实成交腿（源事件/量/价/费）、
现金、批次剩余/可卖量、预留、未成交/挂单原因与权益一致。只共享费用函数不算通过。

顺序语义（两个适配器一致）：本事件观察（撮合前状态）→ 命令/撤单 → 撮合既有挂单；
本事件新建的订单只在**后续**有支持的事件成交。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from dragon_quant.review_account.execution_core.models import (
    Batch, SELLABLE_ON,
)
from dragon_quant.review_account.execution_core.runtime_facts import (
    TEACHING_QUOTE_MAX_AGE_MS,
)
from dragon_quant.review_account.execution_core.risk import RiskCaps
from dragon_quant.review_account.ledger import AccountLedger
from dragon_quant.review_account.ledger.matching import ExecutionCostModel
from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.review_account.strategy.account_engine import AccountEngine
from dragon_quant.review_account.strategy.account_runtime import AccountDecision
from dragon_quant.review_account.strategy.bs_policy.scenario_execution import (
    ScenarioExecution,
)
from dragon_quant.strategy_sdk.types import AccountOrderIntent
from dragon_quant.tests._bs_policy_support import _account, _position
from dragon_quant.tests.support import patch_storage_dbs

REF = {"strategy_id": "bs_account_decision", "params_hash": "h1"}
DATES = ("2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25")
_CST = timezone(timedelta(hours=8))


def _ms(day: str) -> int:
    moment = datetime.fromisoformat(f"{day}T15:00:00").replace(tzinfo=_CST)
    return int(moment.timestamp() * 1000)


EVENT_TS = tuple(_ms(day) for day in DATES)


@contextmanager
def _env(tmp_path):
    dragon = str(tmp_path / "dragon.db")
    with patch_storage_dbs(dragon, str(tmp_path / "assets.db")), \
            patch("dragon_quant.infra.storage.paths.DB_PATH", Path(dragon)):
        yield


class _Event:
    def __init__(self, index, price, capacity):
        day = DATES[index - 1]
        self.timestamp = EVENT_TS[index - 1]
        self.trade_date = day
        row = {"execution_price": price,
               "execution_timestamp": EVENT_TS[index - 1],
               "available_at": EVENT_TS[index - 1],
               "available_qty": capacity, "tradable": True}
        self.rows = {"600001": dict(row), "600002": dict(row)}
        self.unavailable = {}
        self.last_closes = {}
        self.candidates = []
        self.phase = "bar"
        self.allow_buy = True
        self.evidence_available = ()


class _QueueRuntime:
    def __init__(self, decisions):
        self.strategy_ref = REF
        self.decisions = dict(decisions)

    def decide(self, state, *, candidates, decision_time, **kwargs):
        return AccountDecision(
            bundle=None,
            intents=tuple(self.decisions.get(decision_time, ())))


def _buy(code, qty, risk):
    return AccountOrderIntent(action="buy", code=code, qty=qty, plan_id="p1",
                              strategy_ref=REF, risk_amount=risk)


def _sell(code, qty):
    return AccountOrderIntent(action="sell", code=code, qty=qty, plan_id="p2",
                              strategy_ref=REF, risk_amount=0.0)


def _decision(day: str, intents):
    return {f"{day}T15:00:00+08:00": tuple(intents)}


def _seed(ledger, entry_date):
    state = ledger.load_state()
    state.batches = [Batch(
        batch_id="legacy:600001:sellable", code="600001", qty=1000,
        remaining=1000, entry_date=entry_date, cost_price=10.0,
        strategy_ref=REF, source="position", sellable_mode=SELLABLE_ON,
        sellable_date=DATES[0])]
    ledger.commit_event(expected_revision=state.revision, event_id="seed",
                        source_hash="seed", content_hash="seed", state=state)


def _engine_run(tmp_path, prices, capacities, decisions):
    decision_days = {key[:10] for key in decisions}
    with _env(tmp_path):
        ledger = AccountLedger("c1")
        ledger.create(initial_cash=100_000.0, strategy_ref=REF)
        _seed(ledger, DATES[0])
        # 与场景一致：显式无 TTL（场景适配器 ttl_ms=None），避免跨周末误取消。
        engine = AccountEngine(ledger, _QueueRuntime(decisions),
                               ExecutionCostModel(), calendar=list(DATES),
                               order_ttl_ms=None,
                               risk_caps=RiskCaps.teaching_fixture(),
                               allow_unknown_risk=True,
                               max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)
        snapshots = []
        for index in range(1, 6):
            result = engine.process_event(
                _Event(index, prices[index - 1], capacities[index - 1]),
                decide=DATES[index - 1] in decision_days)
            state = engine.state()
            day = DATES[index - 1]
            reasons = [(item["code"], item["reason"])
                       for item in result.get("matches", ())
                       if item.get("reason") and item["reason"] != "no_later_event"]
            reasons += [(item["code"], item["reason"])
                        for item in result.get("rejected", ())]
            snapshots.append({
                "step": index,
                "at": f"{day}T15:00:00+08:00",
                "cash": state.cash,
                "positions": {code: state.position_qty(code) for code in
                              ("600001", "600002")},
                "sellable": {
                    code: sum(batch.sellable(day)
                              for batch in state.batches if batch.code == code)
                    for code in ("600001", "600002")},
                "reservations": sorted(
                    (item.code, item.side, item.qty, round(item.amount, 6),
                     item.status)
                    for item in state.reservations if item.status == "reserved"),
                "fills": sorted(
                    (fill["side"], fill["code"], fill["qty"],
                     round(fill["price"], 9), round(fill["fee"], 9))
                    for fill in result.get("fill_records", ())),
                "reasons": sorted(reasons),
                "equity": state.marked_equity(),
            })
        return snapshots


def _scenario_path(config, prices, capacities):
    steps = []
    for index in range(1, 6):
        day = DATES[index - 1]
        steps.append({
            "at": f"{day}T15:00:00+08:00",
            "available_at": f"{day}T15:00:00+08:00",
            "trade_date": day,
            "prices": {"600001": prices[index - 1],
                       "600002": prices[index - 1]},
            "tradable": {"600001": True, "600002": True},
            "capacity": {"600001": capacities[index - 1],
                         "600002": capacities[index - 1]},
        })
    return bs.scenario_set_from_records(
        [{"scenario_id": "c1", "kind": "teaching_fixture", "fixture": True,
          "calendar": list(DATES), "steps": steps}],
        spec=config.scenario).paths[0]


def _scenario_run(config, plan, prices, capacities):
    account = _account(
        cash=100_000.0,
        positions=(_position(code="600001", qty=1000, sellable_qty=1000,
                             cost_price=10.0, entry_date=DATES[0],
                             mark=10.0, strategy_ref=REF),))
    return bs.simulate_plan(
        plan, _scenario_path(config, prices, capacities), account=account,
        spec=config.scenario, owner=REF,
        execution=ScenarioExecution(risk_caps=RiskCaps.teaching_fixture()))


def _compare(outcome, engine_snaps):
    assert len(outcome.timeline) == len(engine_snaps) == 5
    for timeline, snap in zip(outcome.timeline, engine_snaps):
        step = snap["step"]
        assert timeline["at"] == snap["at"], step
        assert timeline["cash"] == pytest.approx(snap["cash"]), step
        assert dict(timeline["positions"]) == {
            code: qty for code, qty in snap["positions"].items() if qty > 0
        }, step
        assert {code: qty for code, qty in timeline["sellable"].items()
                if qty > 0} == {code: qty for code, qty in
                                snap["sellable"].items() if qty > 0}, step
        scenario_res = sorted(
            (item["code"], item["side"], item["qty"], round(item["amount"], 6),
             item["status"]) for item in timeline["reservations"])
        assert scenario_res == snap["reservations"], step
        scenario_fills = sorted(
            (fill["side"], fill["code"], fill["qty"], round(fill["price"], 9),
             round(fill["fee"], 9)) for fill in timeline["fills"])
        assert scenario_fills == snap["fills"], step
        assert timeline["equity"] == pytest.approx(snap["equity"]), step


def _scenario_reasons(outcome, step):
    prefix = f":{step}:"
    reasons = []
    for item in outcome.unfilled:
        if prefix not in item:
            continue
        code, _, rest = item.partition(prefix)
        reasons.append((code, rest.rsplit(":", 1)[-1]))
    return sorted(reasons)


# ─── 逆境序列 1：跳价 + 零容量 + 部分成交 + 共享容量 + 期末触发 ──────────────


def test_parity_gap_zero_capacity_partial_and_last_event_pending(tmp_path):
    prices = (10.0, 12.0, 12.4, 12.6, 9.0)
    capacities = (1_000_000, 0, 600, 1_000_000, 1_000_000)
    config = bs.teaching_fixture()
    plan = bs.PolicyPlan(
        plan_id="c1", plan_kind=bs.PLAN_BREAKOUT, code="600002",
        target_qty=1000, risk_amount=100.0, stop_price=10.0, executable=True,
        legs=(bs.PlanLeg(step=1, action="buy", code="600002",
                         qty_fraction=1.0, reason="entry"),))
    decisions = {}
    decisions.update(_decision(DATES[0], [_buy("600002", 1000, 100.0)]))
    decisions.update(_decision(DATES[4], [_sell("600002", 1000)]))

    outcome = _scenario_run(config, plan, prices, capacities)
    snaps = _engine_run(tmp_path, prices, capacities, decisions)
    _compare(outcome, snaps)

    # 零容量事件：两边都保持未成交并给出同一原因。
    assert outcome.timeline[1]["positions"].get("600002") is None
    assert ("600002", "capacity_zero") in _scenario_reasons(outcome, 2)
    assert ("600002", "capacity_zero") in snaps[1]["reasons"]
    # 部分成交 → 后续事件补齐；期末触发的退出挂单保留、不成交。
    assert outcome.timeline[2]["positions"]["600002"] == 600
    assert outcome.timeline[3]["positions"]["600002"] == 1000
    assert outcome.timeline[4]["positions"]["600002"] == 1000
    assert "600002" in outcome.timeline[4]["policy_state"]["pending_exits"]


# ─── 逆境序列 2：买入后当根不反向激活保护，观察失效后下一事件退出 ───────────


def test_parity_entry_same_bar_no_exit_and_next_event_exit(tmp_path):
    prices = (10.0, 9.0, 12.0, 9.0, 9.0)
    capacities = (1_000_000,) * 5
    config = bs.teaching_fixture()
    plan = bs.PolicyPlan(
        plan_id="c1", plan_kind=bs.PLAN_BREAKOUT, code="600002",
        target_qty=1000, risk_amount=100.0, stop_price=10.0, executable=True,
        legs=(bs.PlanLeg(step=1, action="buy", code="600002",
                         qty_fraction=1.0, reason="entry"),))
    decisions = {}
    decisions.update(_decision(DATES[0], [_buy("600002", 1000, 100.0)]))
    decisions.update(_decision(DATES[3], [_sell("600002", 1000)]))

    outcome = _scenario_run(config, plan, prices, capacities)
    snaps = _engine_run(tmp_path, prices, capacities, decisions)
    _compare(outcome, snaps)

    # step2 成交（9.0 已低于 stop=10）但**当根不反向激活**；step4 再次失效，
    # step5 才执行退出（下一支持事件）。
    assert outcome.timeline[1]["positions"]["600002"] == 1000
    assert not any(item["side"] == "sell"
                   for item in outcome.timeline[1]["fills"])
    assert outcome.timeline[2]["positions"]["600002"] == 1000
    assert outcome.timeline[3]["positions"]["600002"] == 1000
    assert outcome.timeline[4]["positions"].get("600002", 0) == 0
    sell_fills = [item for item in outcome.timeline[4]["fills"]
                  if item["side"] == "sell"]
    assert sell_fills and sell_fills[0]["qty"] == 1000


# ─── 同日 T+1 与跨交易日：同日卖出腿被拒，下一交易日观察后再成交 ────────────


def test_parity_same_day_t1_rejects_and_cross_day_fills(tmp_path):
    prices = (10.0,) * 5
    capacities = (1_000_000,) * 5
    config = bs.teaching_fixture()
    plan = bs.PolicyPlan(
        plan_id="c1", plan_kind=bs.PLAN_PROBE, code="600002",
        target_qty=1000, risk_amount=100.0, executable=True,
        legs=(bs.PlanLeg(step=1, action="buy", code="600002",
                         qty_fraction=1.0, reason="entry"),
              bs.PlanLeg(step=2, action="sell", code="600002",
                         qty_fraction=1.0, reason="same_day"),
              bs.PlanLeg(step=3, action="sell", code="600002",
                         qty_fraction=1.0, reason="next_day")))
    # 账户引擎按决定集撤掉未再声明的买单；计划仍要保留买腿时须再次声明
    # （场景腿是静态计划，因此这里显式重复 buy 意图，保持同一事实）。
    decisions = {}
    decisions.update(_decision(DATES[0], [_buy("600002", 1000, 100.0)]))
    decisions.update(_decision(
        DATES[1], [_buy("600002", 1000, 100.0), _sell("600002", 1000)]))
    # 买腿在 step2 已真实成交（终态），step3 起无需再声明。
    decisions.update(_decision(DATES[2], [_sell("600002", 1000)]))

    outcome = _scenario_run(config, plan, prices, capacities)
    snaps = _engine_run(tmp_path, prices, capacities, decisions)
    _compare(outcome, snaps)

    # step2 成交（trade_date=11-20），同日卖出腿被 T+1 拒绝；step3（11-23）
    # 观察后再 sold，step4 才成交。
    assert outcome.timeline[1]["positions"]["600002"] == 1000
    assert outcome.timeline[2]["positions"]["600002"] == 1000
    assert any("no_sellable" in item for item in outcome.unfilled)
    assert outcome.timeline[3]["positions"].get("600002", 0) == 0
