"""W5-P 公共事实链：显式 entry_mode probe_then_confirm 的真实 probe→add。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dragon_quant.infra.assets import observations
from dragon_quant.infra.calibration import (
    PRODUCTION_CALIBRATIONS, binding_for,
)
from dragon_quant.research import bs_advice
from dragon_quant.research.bs_advice import assembly, facts, freeze
from dragon_quant.review_account.strategy.bs_policy import teaching_fixture
from dragon_quant.tests.test_bs_advice import (
    _cash_account, _minute, _readers,
)


def _rising_daily(db_path) -> list[str]:
    days: list[str] = []
    cursor = date(2026, 10, 20)
    while cursor <= date(2026, 11, 17):
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    cid = PRODUCTION_CALIBRATIONS[0].calibration_id
    closes = [9.0 + 1.2 * index / max(1, len(days) - 6)
              for index in range(max(1, len(days) - 5))]
    closes.extend([9.0 + 0.1 * index for index in range(5)])
    for index, day in enumerate(days):
        close = round(closes[min(index, len(closes) - 1)], 4)
        stamp = f"{day}T15:05:00+08:00"
        observations.append_bar(
            trade_date=day, market="SH", code="600001", period="daily",
            interval_start=f"{day}T09:30:00+08:00",
            interval_end=f"{day}T15:00:00+08:00", open=close, high=close,
            low=close, close=close, volume=1_000_000,
            amount=close * 1_000_000, source="tdx", observed_at=stamp,
            available_at=stamp, completeness="final",
            time_semantics="verified", unit_semantics="verified",
            adjustment="none", db_path=db_path,
            calibration=binding_for(cid, calibrated_at=stamp, source="tdx",
                                    period="daily"))
    return days


def _public_pack(tmp_path, monkeypatch, *, entry_mode):
    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: PRODUCTION_CALIBRATIONS[0].provenance)
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "assets.db"
    days = _rising_daily(db_path)
    for start, end, close in (("09:59:00", "10:00:00", 9.4),
                              ("10:00:00", "10:01:00", 10.9),
                              ("10:01:00", "10:02:00", 12.0),
                              ("10:02:00", "10:03:00", 12.0)):
        _minute(db_path, "2026-11-18", start=start, end=end, close=close)
    config = teaching_fixture(entry_mode=entry_mode)
    account = bs_advice.parse_account_file(_cash_account(config))
    policy = bs_advice.load_policy_payload(config.to_dict())
    calendar = days + ["2026-11-18", "2026-11-19"]
    pack = bs_advice.build_experiment_pack(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, calendar=calendar,
        **_readers(days))
    return pack, account, policy, db_path, calendar, days


def test_public_probe_then_confirm_reaches_add_via_real_events(tmp_path,
                                                              monkeypatch):
    pack, account, policy, db_path, calendar, days = _public_pack(
        tmp_path, monkeypatch, entry_mode="probe_then_confirm")
    confirms = [
        item for event in pack["events"]
        for candidate in event.candidates
        for item in candidate.confirmations]
    assert confirms, "公共候选必须产出真实 confirm:<code>"
    assert all(item.evidence_ids for item in confirms)
    assert any(candidate.check("confirm:600001") and
               candidate.check("confirm:600001").usable
               for event in pack["events"] for candidate in event.candidates)
    result = bs_advice.run_bs_experiment(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, calendar=calendar,
        pack=pack)
    events = result["variants"]["bs"]["events"]
    plans = [(intent["action"], intent["plan_id"])
             for event in events
             for intent in (event.get("decision_record") or {}).get("intents") or []]
    assert any(plan.startswith("probe:") for _action, plan in plans)
    assert any(plan.startswith("add:") for _action, plan in plans), plans
    fills = [(fill["side"], fill["qty"], fill["order_id"])
             for event in events for fill in event.get("fills") or []]
    assert len(fills) >= 2, fills
    assert fills[0][1] > 0 and fills[1][1] > 0
    # 冻结→解码→重放保持 G3 确认与模式
    loaded = freeze.load_experiment_inputs(pack["freeze"])
    assert loaded.policy.config.entry_mode == "probe_then_confirm"
    assert any(item.rule_id == "confirm:600001"
               for event in loaded.events
               for candidate in event.candidates
               for item in candidate.confirmations)


def test_compete_mode_keeps_existing_selection(tmp_path, monkeypatch):
    pack, account, policy, db_path, calendar, _days = _public_pack(
        tmp_path, monkeypatch, entry_mode="compete")
    result = bs_advice.run_bs_experiment(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, calendar=calendar, pack=pack)
    plans = [(intent["action"], intent["plan_id"])
             for event in result["variants"]["bs"]["events"]
             for intent in (event.get("decision_record") or {}).get("intents") or []]
    assert any(plan.startswith("pullback:") for _action, plan in plans)
    assert result["pack_digest"] != _public_pack(
        tmp_path / "other", monkeypatch, entry_mode="probe_then_confirm")[0]["digest"]


def test_entry_mode_config_strict_and_hash_compatible():
    base = teaching_fixture()
    assert base.entry_mode == "compete"
    assert "entry_mode" not in base.to_dict(), "省略 compete 保持旧 hash"
    probe = teaching_fixture(entry_mode="probe_then_confirm")
    assert probe.to_dict()["entry_mode"] == "probe_then_confirm"
    assert probe.content_hash != base.content_hash
    with pytest.raises(Exception):
        teaching_fixture(entry_mode="truthy")
    from dragon_quant.review_account.strategy.bs_policy import BSPolicyConfig
    assert BSPolicyConfig.from_dict(probe.to_dict()).entry_mode == \
        "probe_then_confirm"


def test_recovery_confirmations_use_real_prep_market_and_execution_support(
        tmp_path, monkeypatch):
    pack, _account, _policy, _db, _calendar, _days = _public_pack(
        tmp_path, monkeypatch, entry_mode="compete")
    event = pack["events"][1]
    snapshot = event.decision_kwargs["snapshot"]
    market = event.decision_kwargs["market"]
    meta = dict(event.decision_kwargs["trend_audit"])  # sanity: facts present
    assert meta
    event_universe_meta = {"trade_date": "2026-11-17",
                           "created_at": "2026-11-17T16:50:00+08:00",
                           "error": None}
    context_data = {"state": "strong", "score": 80.0, "partial": False}
    rows = {"600001": {"execution_price": 10.9, "available_qty": 1000,
                       "available_at": event.timestamp}}
    confirms = facts.recovery_confirmations(
        snapshot, market=market, rows=rows,
        execution_facts={"cost_model_present": True, "fee_fields_valid": True},
        market_meta=event_universe_meta, market_context=context_data)
    by_rule = {item.rule_id: item for item in confirms}
    market_confirm = by_rule["market_regime_verified"]
    assert market_confirm.evidence_ids[0].startswith("prep_market|")
    assert market_confirm.available_at == "2026-11-17T16:50:00+08:00"
    execution = by_rule["execution_quality_ok"]
    assert all("1min" in item for item in execution.evidence_ids)
    assert execution.available_at == "2026-11-18T10:01:30+08:00"
    # 负例：空 rows / 字符串 truthy / 缺费用支持 / 未来 prep。
    def rules(**kwargs):
        return {item.rule_id for item in facts.recovery_confirmations(
            snapshot, market=market,
            market_meta=kwargs.get("meta", event_universe_meta),
            market_context=context_data, rows=kwargs.get("rows", rows),
            execution_facts={"cost_model_present":
                             kwargs.get("cost", True),
                             "fee_fields_valid": True})}

    assert "execution_quality_ok" not in rules(rows={})
    bad_rows = {"600001": {"execution_price": 10.9, "available_qty": 0,
                           "available_at": event.timestamp, "tradable": "True"}}
    assert "execution_quality_ok" not in rules(rows=bad_rows)
    assert "execution_quality_ok" not in rules(cost=False)
    shifted = dict(rows["600001"], available_at=event.timestamp - 3_600_000)
    assert "execution_quality_ok" not in rules(rows={"600001": shifted})
    extra = dict(rows, **{"600002": {"execution_price": 3.0,
                                     "available_qty": 100,
                                     "available_at": event.timestamp}})
    assert "execution_quality_ok" not in rules(rows=extra)
    future = dict(event_universe_meta,
                  created_at="2026-11-18T11:00:00+08:00")
    assert "market_regime_verified" not in rules(meta=future)


def test_entry_mode_filter_blocks_duplicate_probe_and_full_entries():
    from types import SimpleNamespace

    from dragon_quant.review_account.strategy.bs_policy.base import (
        PLAN_ADD, PLAN_PROBE, PLAN_PULLBACK, PLAN_SWITCH,
    )
    from dragon_quant.review_account.strategy.bs_policy.entry_mode import (
        apply_entry_mode,
    )
    from dragon_quant.review_account.strategy.bs_policy.plans import PolicyPlan

    state = SimpleNamespace(code="600001", owner={},
                            probe_order_id="ord-1", probe_filled_qty=0,
                            family_id="f1")
    context = SimpleNamespace(active_owner={}, policy_states=(state,))
    config = SimpleNamespace(entry_mode="probe_then_confirm")
    plans = [
        PolicyPlan(plan_id="probe", plan_kind=PLAN_PROBE, code="600001",
                   executable=True),
        PolicyPlan(plan_id="pull", plan_kind=PLAN_PULLBACK, code="600001",
                   executable=True),
        PolicyPlan(plan_id="add", plan_kind=PLAN_ADD, code="600001",
                   executable=True),
        PolicyPlan(plan_id="switch", plan_kind=PLAN_SWITCH, code="600002",
                   executable=True),
    ]
    filtered, gates = apply_entry_mode(plans, context, config)
    by_id = {plan.plan_id: plan for plan in filtered}
    assert by_id["probe"].executable is False
    assert "entry_mode_probe_already_exists" in         by_id["probe"].blocked_reasons
    assert by_id["pull"].executable is False
    assert by_id["add"].executable is True
    assert by_id["switch"].executable is False
    assert len(gates) == 3


def test_market_facts_theme_union_and_pressure_conservative():
    from types import SimpleNamespace

    class Snapshot:
        facts = ()

    position = SimpleNamespace(
        code="600001", mark=None,
        batches=(SimpleNamespace(batch_id="b1", entry_date="2026-11-17"),
                 SimpleNamespace(batch_id="b2", entry_date="2026-11-17")))
    account = SimpleNamespace(positions=(position,))
    checkpoints = {
        "b1": {"theme_ids": ["AI"], "pressure_pct": 0.1},
        "b2": {"theme_ids": ["BANK"], "pressure_pct": 0.3},
    }
    result = facts.market_facts(Snapshot(), account, trade_date="2026-11-18",
                                calendar=(), checkpoints=checkpoints)
    assert set(result["themes"]["600001"]) == {"AI", "BANK"}
    assert result["pressures"]["600001"] == 0.3
    assert result["trends"]["600001"] == "unknown"
