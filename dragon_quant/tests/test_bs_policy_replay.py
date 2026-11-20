"""C3b3 CP：公共 policy replay 与真实引擎逐事件一致。

同一冻结配置/归属/期初账户与事件序列下，公共 replay（纯内存）与
:class:`AccountEngine`（SQLite + CAS）必须给出相同的成交量/价/费、现金、逐批
剩余/可卖、预留/资金 credit、撤单/未成交与恢复阶段；期末新下单保持 pending。
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
from dragon_quant.review_account.strategy.account_runtime import (
    AccountPolicyRuntime,
)
from dragon_quant.review_account.strategy.policy_replay import (
    ReplayEvent, ReplayInput, replay_account_policy,
)
from dragon_quant.tests._bs_policy_support import _scenarios
from dragon_quant.tests.support import patch_storage_dbs

_CST = timezone(timedelta(hours=8))

D1, D2, D3 = "2026-11-18", "2026-11-19", "2026-11-20"
CALENDAR = (D1, D2, D3, "2026-11-23", "2026-11-24", "2026-11-25")
PRICES = {"600001": [10.5, 10.5, 10.5, 9.9, 9.45],
          "600002": [10.5, 10.5, 10.5, 10.6, 12.0]}


def _ms(day, hour=10):
    return int(datetime.strptime(f"{day} {hour:02d}", "%Y-%m-%d %H")
               .replace(tzinfo=_CST).timestamp() * 1000)


T1, T2, T3 = (_ms(day) for day in (D1, D2, D3))


@contextmanager
def _env(tmp_path):
    dragon = str(tmp_path / "dragon.db")
    with patch_storage_dbs(dragon, str(tmp_path / "assets.db")), \
            patch("dragon_quant.infra.storage.paths.DB_PATH", Path(dragon)):
        yield


class _Event:
    def __init__(self, ts, rows, day, **kwargs):
        self.timestamp = ts
        self.trade_date = day
        self.rows = rows
        self.unavailable = {}
        self.last_closes = {}
        self.candidates = []
        self.phase = "bar"
        self.evidence_available = ()
        for key, value in kwargs.items():
            setattr(self, key, value)


def _row(ts, price, *, capacity=10 ** 9):
    return {"execution_price": price, "execution_timestamp": ts,
            "available_at": ts, "available_qty": capacity, "tradable": True}


def _checks(*rules):
    return tuple(CapabilityCheck(rule_id=rule, capability="intraday_bars",
                                 status="usable", protects="new_risk")
                 for rule in rules)


def _candidate(price=10.5, code="600002"):
    return bs.CandidateView(
        code=code, name="候选二", eligible=True, tradable=True, price=price,
        pressure_pct=0.10, rank=1, theme_ids=("T2",),
        structure={"breakout_level": 10.2, "pullback_level": 9.8,
                   "confirm_level": 10.5, "probe_level": 10.5},
        evidence_checks=_checks(f"entry:confirm:{code}",
                                f"entry:breakout:{code}"))


def _ref(config):
    from dragon_quant.workspace import snapshots
    return snapshots.builtin_ref(strategy_id=bs.BS_ACCOUNT_STRATEGY_ID,
                                 params={"bs_policy": config.to_dict()})


def _decision_kwargs(config):
    return {
        "scenarios": _scenarios(config, {"up": PRICES},
                                trade_dates=list(CALENDAR[:5])),
        "market": bs.MarketView(regime="trend_up", new_risk_allowed=True),
        "pressures": {"600001": 0.10, "600002": 0.10},
        "themes": {"600001": ("T1",), "600002": ("T2",)},
        "trends": {"600001": "down", "600002": "up"},
        "held_days": {"600001": 2},
    }


def _replay_events(config):
    kwargs = _decision_kwargs(config)
    return (
        ReplayEvent(timestamp=T1, trade_date=D1,
                    rows={"600001": _row(T1, 10.5),
                          "600002": _row(T1, 10.5)},
                    candidates=(_candidate(),), decision_kwargs=kwargs,
                    decide=True),
        # 父卖全成（老仓闭合），子买本事件仍不成交。
        ReplayEvent(timestamp=T2, trade_date=D2,
                    rows={"600001": _row(T2, 10.5),
                          "600002": _row(T2, 10.5)}),
        # 子买按已实现净卖款成交（本事件新价格更高 → 只买得起部分）。
        ReplayEvent(timestamp=T3, trade_date=D3,
                    rows={"600001": _row(T3, 10.5, capacity=0),
                          "600002": _row(T3, 13.0)}),
    )


def _run_engine(tmp_path, config, name="cp-engine"):
    with _env(tmp_path):
        ledger = AccountLedger(name)
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
            ledger, AccountPolicyRuntime(config, strategy_ref=_ref(config)),
            ExecutionCostModel(), calendar=list(CALENDAR), order_ttl_ms=None,
            risk_caps=RiskCaps.teaching_fixture(),
            max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)
        snapshots = []
        for replay_event in _replay_events(config):
            result = engine.process_event(
                _Event(replay_event.timestamp, replay_event.rows,
                       replay_event.trade_date),
                decide=replay_event.decide,
                candidates=replay_event.candidates,
                decision_kwargs=replay_event.decision_kwargs)
            state = engine.state()
            snapshots.append({
                "fills": [(f["side"], f["code"], f["price"], f["qty"],
                           f["fee"]) for f in result["fill_records"]],
                "canceled": sorted(result["canceled"]),
                "cash": state.cash,
                "remaining": _remaining(state.batches),
                "reservations": sorted(
                    (r.side, r.qty, round(r.amount, 6))
                    for r in state.reservations if r.status == "reserved"),
                "fund_credits": _credits(state.fund_credits),
                "recovery_stage": state.recovery_stage,
                "equity": state.marked_equity(),
                "closed_trades": [(item.get("code"), item.get("realized_net"),
                                   item.get("loss"))
                                  for item in state.closed_trades],
                "consecutive_losses": state.consecutive_losses,
            })
        final = engine.state()
        return snapshots, {
            "remaining": _remaining(final.batches),
            "orders": sorted((o.side, o.status, o.quantity_remaining)
                             for o in final.orders),
            "cash": final.cash,
        }


def _run_replay(config):
    account = bs.AccountView(
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
    result = replay_account_policy(ReplayInput(
        ledger_id="cp-replay", account=account, config=config,
        strategy_ref=_ref(config), events=_replay_events(config),
        calendar=CALENDAR, order_ttl_ms=None,
        risk_caps=RiskCaps.teaching_fixture(),
        max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS))
    snapshots = []
    for record in result.events:
        snapshots.append({
            "fills": [(f["side"], f["code"], f["price"], f["qty"], f["fee"])
                      for f in record["fills"]],
            "canceled": sorted(record["canceled"]),
            "cash": record["state"]["cash"],
            "remaining": _remaining_dict(record["state"]["batches"]),
            "reservations": sorted(
                (item["side"], item["qty"], round(item["amount"], 6))
                for item in record["state"]["reservations"]),
            "fund_credits": _credits(record["state"]["fund_credits"]),
            "recovery_stage": record["state"]["recovery_stage"],
            "equity": record["state"]["equity"],
            "closed_trades": [(item.get("code"), item.get("realized_net"),
                               item.get("loss"))
                              for item in record["state"]["closed_trades"]],
            "consecutive_losses": record["state"]["consecutive_losses"],
        })
    final = result.final_state
    return snapshots, {
        "remaining": _remaining_dict(final["batches"]),
        "orders": sorted((item["side"], item["status"],
                          item["qty"] - item["filled_qty"])
                         for item in final["orders"]),
        "cash": final["cash"],
    }


def _remaining(batches):
    totals: dict[str, int] = {}
    for batch in batches:
        if batch.remaining > 0:
            totals[batch.code] = totals.get(batch.code, 0) + batch.remaining
    return totals


def _remaining_dict(batches) -> dict[str, int]:
    totals: dict[str, int] = {}
    for item in batches:
        totals[item["code"]] = totals.get(item["code"], 0) + item["qty"]
    return totals


def _credits(value):
    # 子单/父单身份由批次 scope 决定；比对真实净到账/消耗值（业务事实）。
    return tuple(sorted(
        (round(item.get("credited", 0.0), 6),
         round(item.get("spent", 0.0), 6))
        for item in (value or {}).values()))


def test_public_replay_matches_engine_event_by_event(tmp_path):
    config = bs.teaching_fixture()
    engine_snaps, engine_final = _run_engine(tmp_path, config)
    replay_snaps, replay_final = _run_replay(config)
    assert len(engine_snaps) == len(replay_snaps) == 3
    for index, (engine, replay) in enumerate(zip(engine_snaps, replay_snaps)):
        assert engine["fills"] == replay["fills"], index
        assert engine["canceled"] == replay["canceled"], index
        assert engine["cash"] == pytest.approx(replay["cash"], abs=1e-6)
        assert engine["remaining"] == replay["remaining"], index
        assert engine["reservations"] == replay["reservations"], index
        assert engine["fund_credits"] == replay["fund_credits"], index
        assert engine["recovery_stage"] == replay["recovery_stage"], index
        assert engine["equity"] == pytest.approx(replay["equity"], abs=1e-6)
        assert engine["closed_trades"] == replay["closed_trades"], index
        assert engine["consecutive_losses"] == replay["consecutive_losses"]
    # 不是全空成交：父卖与子买都真实成交，且子买只按净卖款部分成交。
    all_fills = [fill for snap in replay_snaps for fill in snap["fills"]]
    assert [fill[0] for fill in all_fills] == ["sell", "buy"]
    assert all_fills[1][3] < 900
    assert replay_snaps[1]["closed_trades"]       # 卖出闭合真实记录
    assert replay_snaps[1]["closed_trades"] == engine_snaps[1]["closed_trades"]
    assert engine_final["cash"] == pytest.approx(replay_final["cash"], abs=1e-6)
    assert engine_final["remaining"] == replay_final["remaining"]


def test_replay_final_event_keeps_pending_and_not_all_empty(tmp_path):
    config = bs.teaching_fixture()
    account = bs.AccountView(cash=100_000.0, positions=())
    candidate = _candidate(price=10.0)
    scenarios = _scenarios(config, {"up": {"600002": [10.2, 10.4, 10.6, 10.8, 11.0]}},
                           trade_dates=list(CALENDAR[:5]))
    events = (ReplayEvent(
        timestamp=T1, trade_date=D1, rows={"600002": _row(T1, 10.0)},
        candidates=(candidate,), decide=True,
        decision_kwargs={"scenarios": scenarios,
                         "market": bs.MarketView(
                             regime="trend_up", new_risk_allowed=True),
                         "pressures": {"600002": 0.10},
                         "themes": {"600002": ("T2",)}}),)
    result = replay_account_policy(ReplayInput(
        ledger_id="cp-pending", account=account, config=config,
        strategy_ref=_ref(config), events=events, calendar=CALENDAR,
        order_ttl_ms=None, risk_caps=RiskCaps.teaching_fixture(),
        max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS))
    record = result.events[0]
    assert record["fills"] == []                       # 期末新单不成交
    open_orders = [item for item in result.final_state["orders"]
                   if item["status"] not in ("filled", "cancelled",
                                             "rejected")]
    assert open_orders and open_orders[0]["side"] == "buy"
    assert result.final_state["cash"] == pytest.approx(100_000.0)
