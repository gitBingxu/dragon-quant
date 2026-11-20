"""BS 账户文件、只读建议与 review-account 重放（W4a，隔离临时库）。"""

from __future__ import annotations

import math

import pytest

from dragon_quant.infra.assets import observations
from dragon_quant.research import bs_advice
from dragon_quant.review_account.strategy.bs_policy import teaching_fixture
from dragon_quant.infra.calibration import (
    PRODUCTION_CALIBRATIONS, binding_for,
)
from dragon_quant.workspace import snapshots

CUTOFF = "2026-11-18T16:00:00+08:00"


def _bars(db_path, days):
    for day, (close, volume) in days.items():
        observations.append_bar(
            trade_date=day, market="SH", code="600001", period="daily",
            interval_start=f"{day}T09:30:00+08:00",
            interval_end=f"{day}T15:00:00+08:00",
            open=close, high=close, low=close, close=close, volume=volume,
            amount=close * volume, source="tdx",
            observed_at=f"{day}T15:05:00+08:00",
            available_at=f"{day}T15:05:00+08:00", completeness="final",
            time_semantics="verified", unit_semantics="verified",
            adjustment="none", db_path=db_path,
            calibration=binding_for(
                PRODUCTION_CALIBRATIONS[0].calibration_id,
                calibrated_at=f"{day}T15:05:00+08:00", source="tdx",
                period="daily"))


def _minute(db_path, day, *, start: str, end: str, close: float,
            volume: float = 1_000_000, lag_seconds: int = 30) -> None:
    from datetime import datetime, timedelta
    end_moment = datetime.fromisoformat(f"{day}T{end}+08:00")
    available = end_moment + timedelta(seconds=lag_seconds)
    observations.append_bar(
        trade_date=day, market="SH", code="600001", period="1min",
        interval_start=f"{day}T{start}+08:00",
        interval_end=f"{day}T{end}+08:00",
        open=close, high=close, low=close, close=close, volume=volume,
        amount=close * volume, source="tdx",
        observed_at=available.isoformat(), available_at=available.isoformat(),
        completeness="final", time_semantics="verified",
        unit_semantics="verified", adjustment="none", db_path=db_path,
        calibration=binding_for(
            PRODUCTION_CALIBRATIONS[0].calibration_id,
            calibrated_at=available.isoformat(), source="tdx", period="1min"))


def _history(db_path):
    """写一段已成熟日 K：多数窗口上行 + 最近 5 日低波动，供共同场景。"""
    from datetime import date, timedelta

    days: list[str] = []
    cursor = date(2026, 10, 20)
    while cursor <= date(2026, 11, 17):
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    rise_days = max(1, len(days) - 5)
    closes = [9.4 + (1.2 * index / max(1, rise_days - 1))
              for index in range(rise_days)]
    closes.extend([10.1, 10.1, 10.1, 10.1, 10.1])
    for index, day in enumerate(days):
        close = round(closes[min(index, len(closes) - 1)], 4)
        high = 10.15 if day == "2026-11-17" else close
        observations.append_bar(
            trade_date=day, market="SH", code="600001", period="daily",
            interval_start=f"{day}T09:30:00+08:00",
            interval_end=f"{day}T15:00:00+08:00",
            open=close, high=high, low=close, close=close, volume=1_000_000,
            amount=close * 1_000_000, source="tdx",
            observed_at=f"{day}T15:05:00+08:00",
            available_at=f"{day}T15:05:00+08:00", completeness="final",
            time_semantics="verified", unit_semantics="verified",
            adjustment="none", db_path=db_path,
            calibration=binding_for(
                PRODUCTION_CALIBRATIONS[0].calibration_id,
                calibrated_at=f"{day}T15:05:00+08:00", source="tdx",
                period="daily"))
    return days


def _readers(days):
    def dragons(date_from, date_to):
        return [{"code": "600001", "name": "龙一", "trade_date": days[-1],
                 "scan_id": "scan-1", "is_true_dragon": True}]

    def prep(before_date):
        return ({"review_candidates": [{"code": "600001", "name": "龙一",
                                        "trade_date": days[-1],
                                        "eligibility": "eligible",
                                        "position_pct": 5.0,
                                        "reason_code": None}],
                 "market": {"state": "strong", "score": 80.0, "coeff": 1.0,
                            "partial": False, "missing_inputs": [],
                            "evidence": ["test"]}},
                {"trade_date": days[-1],
                 "created_at": f"{days[-1]}T16:50:00+08:00", "error": None})

    def calendar(as_of):
        return list(days)

    def plan(trade_date):
        return None

    return {"dragons_reader": dragons, "prep_reader": prep,
            "calendar_reader": calendar, "plan_reader": plan}


def _cash_account(config):
    return {"schema_version": 1, "currency": "CNY", "shares_unit": "share",
            "as_of": "2026-11-17T16:00:00+08:00", "cash": 50000.0,
            "positions": []}


def _config():
    return teaching_fixture()


def _ref(config):
    return snapshots.builtin_ref(
        strategy_id="builtin:bs_account_decision",
        params={"bs_policy": config.to_dict()})


def _account_payload(config=None, *, cash=50000.0, mark=9.0):
    ref = _ref(config or _config())
    return {
        "schema_version": 1, "currency": "CNY", "shares_unit": "share",
        "as_of": CUTOFF, "cash": cash,
        "positions": [{
            "code": "600001", "mark": mark,
            "mark_source": {"price": mark, "source": "tdx",
                            "available_at": "2026-11-18T15:05:00+08:00"},
            "batches": [{
                "batch_id": "orig-b1", "qty": 1000, "sellable_qty": 1000,
                "entry_date": "2026-11-17", "cost_price": 10.0,
                "original_qty": 1000, "initial_risk": 300.0,
                "plan_id": "p1", "family_id": "f1", "strategy_ref": ref}]}],
    }


def test_account_file_preserves_original_batches_and_flags_unknown_cash():
    config = _config()
    snapshot = bs_advice.parse_account_file(_account_payload(config))
    batch = snapshot.account.positions[0].batches[0]
    assert batch.batch_id == "orig-b1"
    assert batch.family_id == "f1" and batch.initial_risk == 300.0
    unknown = bs_advice.parse_account_file(_account_payload(config, cash=None))
    assert unknown.account.cash is None
    assert any(item["reason"] == "account_cash_unknown"
               for item in unknown.restrictions)


def test_account_file_rejects_nan_wrong_currency_unit_and_future():
    config = _config()
    with pytest.raises(bs_advice.AccountFileError):
        bs_advice.parse_account_file(
            {**_account_payload(config), "currency": "USD"})
    with pytest.raises(bs_advice.AccountFileError):
        bs_advice.parse_account_file(
            {**_account_payload(config), "shares_unit": "手"})
    with pytest.raises(bs_advice.AccountFileError):
        bs_advice.parse_account_file(
            {**_account_payload(config), "cash": math.nan})
    future = _account_payload(config)
    future["positions"][0]["mark_source"]["available_at"] = \
        "2026-11-20T15:05:00+08:00"
    with pytest.raises(bs_advice.AccountFileError):
        bs_advice.parse_account_file(future, cutoff=CUTOFF)


def test_policy_ref_conflict_is_explicit():
    config = _config()
    payload = {**_account_payload(config),
               "policy_ref": {"content_hash": "deadbeef"}}
    with pytest.raises(bs_advice.AccountFileError):
        bs_advice.parse_account_file(
            payload, policy_hash=config.content_hash)


def test_incomplete_batch_is_restricted_not_fabricated():
    config = _config()
    payload = _account_payload(config)
    payload["positions"][0]["batches"][0].pop("cost_price")
    snapshot = bs_advice.parse_account_file(payload)
    assert snapshot.account.positions == ()
    assert any(item["reason"] == "account_batch_incomplete"
               for item in snapshot.restrictions)


def test_build_advice_with_config_uses_evidence_and_policy(tmp_path, monkeypatch):
    from dragon_quant.infra import calibration
    from dragon_quant.research.bs_advice import assembly

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    _bars(db_path, {"2026-11-18": (9.0, 1_000_000)})
    advice = bs_advice.build_advice(
        account_payload=_account_payload(config), policy_payload=config.to_dict(),
        cutoff=CUTOFF, db_path=db_path)
    assert advice["decision"] is not None
    assert advice["protections"], "价格/灾难保护应由真实政策核产出"
    assert advice["capabilities"]["usable_capabilities"] == ["daily_bars"]
    assert advice["batches"][0]["orig_batch_id"] == "orig-b1"
    assert advice["persisted"] is False and advice["orders_placed"] is False


def test_policy_loader_freezes_normalized_content_and_rejects_invalid():
    config = _config()
    loaded = bs_advice.load_policy_payload(config.to_dict())
    assert loaded.content_hash == config.content_hash
    assert loaded.fixture is True
    assert loaded.normalized["policy_id"] == config.policy_id
    broken = config.to_dict()
    broken["preference"]["rho"] = 2.0
    with pytest.raises(bs_advice.PolicyFileError):
        bs_advice.load_policy_payload(broken)


def test_strict_advice_refuses_insufficient_evidence(tmp_path):
    config = _config()
    with pytest.raises(bs_advice.BsAdviceError) as excinfo:
        bs_advice.build_advice(
            account_payload=_account_payload(config),
            policy_payload=config.to_dict(), cutoff=CUTOFF,
            db_path=tmp_path / "missing.db", strict=True)
    assert excinfo.value.exit_code == 1


def test_build_advice_without_config_keeps_known_protection(tmp_path):
    db_path = tmp_path / "assets.db"
    _bars(db_path, {"2026-11-18": (9.0, 1_000_000)})
    advice = bs_advice.build_advice(
        account_payload=_account_payload(), policy_payload=None,
        cutoff=CUTOFF, db_path=db_path)
    assert advice["status"] == "restricted"
    assert "policy_config_missing" in advice["missing_capabilities"]
    assert advice["decision"] is None
    assert advice["protections"], "缺新配置不得取消冻结保护"


def test_strict_advice_refuses_missing_config(tmp_path):
    db_path = tmp_path / "assets.db"
    _bars(db_path, {"2026-11-18": (9.0, 1_000_000)})
    with pytest.raises(bs_advice.BsAdviceError) as excinfo:
        bs_advice.build_advice(
            account_payload=_account_payload(), policy_payload=None,
            cutoff=CUTOFF, db_path=db_path, strict=True)
    assert excinfo.value.exit_code == 2


def test_ledger_identity_includes_account_capital_and_events():
    from dragon_quant.review_account.strategy.bs_policy import AccountView
    from dragon_quant.review_account.strategy.bs_policy.scenario_execution import (
        seed_ledger_state,
    )

    low = seed_ledger_state(AccountView(cash=10000.0), asof="2026-11-18")
    high = seed_ledger_state(AccountView(cash=20000.0), asof="2026-11-18")
    base = dict(strategy_ref={"strategy_id": "builtin:bs_account_decision"},
                config_hash="h", snapshot_id="s", date_from="2026-11-18",
                date_to="2026-11-18", events=())
    first = bs_advice.run_ledger_id(cash=10000.0, seed_state=low, **base)
    again = bs_advice.run_ledger_id(cash=10000.0, seed_state=low, **base)
    assert first == again
    other = bs_advice.run_ledger_id(cash=20000.0, seed_state=high, **base)
    assert first != other
    # seed 运行事实/政策状态/资金归属必须进入身份（main 反例）。
    marked = seed_ledger_state(AccountView(cash=10000.0), asof="2026-11-18")
    marked.marks["600001"] = 11.0
    marked.policy_states = [{"code": "600001", "adds_done": 1}]
    marked.fund_credits = {"sell-1": {"credited": 10.0, "spent": 0.0}}
    changed = bs_advice.run_ledger_id(cash=10000.0, seed_state=marked, **base)
    assert changed != first


def test_persisted_run_exposes_lifecycle_status(tmp_path, monkeypatch):
    from dragon_quant.infra import calibration
    from dragon_quant.research.bs_advice import assembly

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=10.0)
    _minute(db_path, "2026-11-18", start="10:00:00", end="10:01:00", close=10.3)
    account = bs_advice.parse_account_file(_cash_account(config))
    policy = bs_advice.load_policy_payload(config.to_dict())
    result = bs_advice.run_bs_review(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, persist=True,
        calendar=days + ["2026-11-18", "2026-11-19"], **_readers(days))
    status = result["run_status"]
    assert status["status"] == "completed"
    assert status["expected_events"] == status["completed_events"] == 2
    readback = bs_advice.read_run(result["ledger_id"])
    assert readback["run_status"]["status"] == "completed"
    # 同输入再运行：幂等且完成水位不倒退。
    again = bs_advice.run_bs_review(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, persist=True,
        calendar=days + ["2026-11-18", "2026-11-19"], **_readers(days))
    assert again["run_status"]["completed_events"] == 2


def test_naive_time_and_unknown_pending_field_are_explicit_errors():
    config = _config()
    naive = _cash_account(config)
    naive["as_of"] = "2026-11-18T16:00:00"
    with pytest.raises(bs_advice.AccountFileError):
        bs_advice.parse_account_file(naive)
    bad = _account_payload(config)
    bad["pending_orders"] = [{"order_id": "o1", "code": "600001",
                              "side": "buy", "qty": 100, "status": "reserved",
                              "created_event": 1, "unknown_field": 1}]
    with pytest.raises(bs_advice.AccountFileError):
        bs_advice.parse_account_file(bad)


def test_universe_marks_incomplete_calendar_and_old_prep_as_missing():
    one_day = bs_advice.assemble_universe(
        as_of="2026-11-18",
        dragons_reader=lambda a, b: [{"code": "600001",
                                      "trade_date": "2026-11-17"}],
        prep_reader=lambda before: (
            {"review_candidates": [{"code": "600001", "eligibility": "eligible",
                                    "position_pct": 5.0}],
             "market": {"state": "strong"}},
            {"trade_date": "2026-11-10",
             "created_at": "2026-11-10T16:50:00+08:00", "error": None}),
        calendar_reader=lambda as_of: ["2026-11-17"],
        plan_reader=lambda d: None)
    assert one_day.coverage["calendar_complete"] is False
    reasons = {gap["reason"] for gap in one_day.gaps}
    assert "calendar_incomplete" in reasons
    assert "prep_not_previous_trading_day" in reasons
    assert one_day.market_context == {}
    assert all("prep" not in item.sources for item in one_day.items), \
        "更旧 prep 不得被当作 T-1 母集"


def test_strict_advice_refuses_incomplete_calendar(tmp_path, monkeypatch):
    from dragon_quant.infra import calibration
    from dragon_quant.research.bs_advice import assembly

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    _bars(db_path, {"2026-11-18": (9.0, 1_000_000)})
    with pytest.raises(bs_advice.BsAdviceError) as excinfo:
        bs_advice.build_advice(
            account_payload=_cash_account(config),
            policy_payload=config.to_dict(),
            cutoff="2026-11-18T16:00:00+08:00", db_path=db_path, strict=True,
            dragons_reader=lambda a, b: [{"code": "600001"}],
            prep_reader=lambda before: (None, {"error": None}),
            calendar_reader=lambda as_of: ["2026-11-18"],
            plan_reader=lambda d: None)
    assert excinfo.value.exit_code == 1


def test_market_coeff_zero_or_missing_denies_new_risk():
    def universe(market):
        return bs_advice.assemble_universe(
            as_of="2026-11-18",
            dragons_reader=lambda a, b: [{"code": "600001"}],
            prep_reader=lambda before: (
                {"review_candidates": [], "market": market},
                {"trade_date": "2026-11-17",
                 "created_at": "2026-11-17T16:50:00+08:00", "error": None}),
            calendar_reader=lambda as_of: ["2026-11-17"],
            plan_reader=lambda d: None)

    zero = bs_advice.market_from_universe(
        universe({"state": "strong", "coeff": 0.0}),
        cutoff="2026-11-18T10:00:00+08:00")
    assert zero.new_risk_allowed is False
    assert "market_coeff_zero" in zero.reasons
    missing = bs_advice.market_from_universe(
        universe({"state": "strong"}),
        cutoff="2026-11-18T10:00:00+08:00")
    assert missing.new_risk_allowed is None
    deny = bs_advice.market_from_universe(
        universe({"state": "strong", "coeff": 1.0, "new_risk_allowed": False}),
        cutoff="2026-11-18T10:00:00+08:00")
    assert deny.new_risk_allowed is False


def test_market_requires_prep_context_not_mere_stock_data(tmp_path, monkeypatch):
    from dragon_quant.infra import calibration
    from dragon_quant.research.bs_advice import assembly

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=10.0)
    _minute(db_path, "2026-11-18", start="10:00:00", end="10:01:00", close=10.3)
    readers = _readers(days)
    readers["prep_reader"] = lambda before: (None, {"error": None})
    advice = bs_advice.build_advice(
        account_payload=_cash_account(config), policy_payload=config.to_dict(),
        cutoff="2026-11-18T10:02:00+08:00", db_path=db_path, **readers)
    assert advice["market"]["new_risk_allowed"] is None
    assert advice["market"]["regime"] == "unknown"
    assert not any(item["action"] == "buy" for item in advice["intents"]), \
        "仅有个股数据不得自动解锁新增风险"


def test_prep_eligibility_uses_exact_enum_and_keeps_denominator():
    days = ["2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16",
            "2026-11-17"]
    candidates = [
        {"code": "600001", "eligibility": "ineligible", "position_pct": 0.1},
        {"code": "600002", "eligibility": "eligible", "position_pct": 0},
        {"code": "600003", "eligibility": "rejected", "position_pct": 5.0},
        {"code": "600004", "eligibility": "eligible", "position_pct": 5.0},
        {"code": "600005", "eligibility": "unknown", "position_pct": None},
    ]
    universe = bs_advice.assemble_universe(
        as_of="2026-11-18",
        dragons_reader=lambda a, b: [{"code": "600001",
                                      "trade_date": "2026-11-17"}],
        prep_reader=lambda before: (
            {"review_candidates": candidates,
             "market": {"state": "strong"}},
            {"trade_date": "2026-11-17",
             "created_at": "2026-11-17T16:50:00+08:00", "error": None}),
        calendar_reader=lambda as_of: days, plan_reader=lambda d: None)
    items = {item.code: item for item in universe.items}
    assert all(code in items for code in
               ("600001", "600002", "600003", "600004", "600005"))
    assert items["600004"].eligible is True
    for code in ("600001", "600002", "600003", "600005"):
        assert items[code].eligible is False
    assert any("prep_eligibility:ineligible"
               in reason for reason in items["600001"].eligibility_reasons)


def test_advice_cash_only_evaluates_exante_universe_and_entry_intent(
        tmp_path, monkeypatch):
    from dragon_quant.infra import calibration
    from dragon_quant.research.bs_advice import assembly

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=10.0)
    _minute(db_path, "2026-11-18", start="10:00:00", end="10:01:00", close=10.3)
    _minute(db_path, "2026-11-18", start="10:01:00", end="10:02:00", close=10.05)
    advice = bs_advice.build_advice(
        account_payload=_cash_account(config), policy_payload=config.to_dict(),
        cutoff="2026-11-18T10:02:00+08:00", db_path=db_path,
        **_readers(days))
    assert advice["universe"]["items"], "事前母集必须来自 dragons/prep/holdings"
    assert advice["candidates"], "现金空仓账户也必须得到候选视图"
    assert advice["candidates"][0]["eligible"] is True
    assert any(intent["action"] == "buy" for intent in advice["intents"]), \
        "合法本地证据正路必须产生可执行入场意图"


def test_account_checkpoint_is_parsed_and_seeded(tmp_path):
    from dragon_quant.review_account.strategy.bs_policy.scenario_execution import (
        seed_ledger_state,
    )

    config = _config()
    payload = _account_payload(config)
    payload["recovery"] = {"active": True, "stage": 1,
                           "trigger_trade_date": "2026-11-10",
                           "trigger_event": 1, "days_since_trigger": 2,
                           "contraction_targets": {"600001": {
                               "batch_ids": ["orig-b1"], "target_qty": 500,
                               "remaining": {"orig-b1": 1000}}},
                           "target_snapshot_trade_date": "2026-11-10"}
    payload["reservations"] = [{"order_id": "resv-1", "code": "600001",
                                "side": "buy", "qty": 100, "price": 9.0,
                                "status": "reserved", "purpose": "entry"}]
    payload["pending_orders"] = [{
        "order_id": "ord-1", "code": "600001", "side": "buy", "qty": 100,
        "status": "reserved", "created_event": 1, "filled_qty": 0,
        "strategy_ref": {}, "plan_id": "p1"}]
    payload["runtime_facts"] = {
        "mark_facts": [{"code": "600001", "price": 9.0, "source_ts": 1,
                        "available_at": 1, "kind": "quote"}],
        "batch_facts": [{"batch_id": "orig-b1", "peak_price": 11.0,
                         "peak_source_ts": 1, "peak_available_at": 1,
                         "took_profit_half": True}],
        "fund_credits": {"sell-1": {"credited": 100.0, "spent": 0.0}},
    }
    snapshot = bs_advice.parse_account_file(payload)
    assert snapshot.recovery.active is True
    assert snapshot.pending_orders[0].order_id == "ord-1"
    assert snapshot.core_reservations[0].order_id == "resv-1"
    state = seed_ledger_state(
        snapshot.account, asof="2026-11-18", recovery=snapshot.recovery,
        policy_states=snapshot.policy_states,
        pending_orders=snapshot.pending_orders,
        reservations=snapshot.core_reservations,
        mark_facts=snapshot.mark_facts, batch_facts=snapshot.batch_facts,
        fund_credits=snapshot.fund_credits)
    assert state.recovery["active"] is True
    assert state.orders[0].order_id == "ord-1"
    assert state.reservations[0].order_id == "resv-1"
    assert state.batch_facts["orig-b1"].peak_price == 11.0
    assert state.batch_facts["orig-b1"].took_profit_half is True
    assert state.fund_credits["sell-1"]["credited"] == 100.0


def test_review_uses_intraday_events_and_persists(tmp_path, monkeypatch):
    from dragon_quant.infra import calibration
    from dragon_quant.research.bs_advice import assembly

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=10.0)
    _minute(db_path, "2026-11-18", start="10:00:00", end="10:01:00", close=10.3)
    _minute(db_path, "2026-11-18", start="10:01:00", end="10:02:00", close=10.05)
    account = bs_advice.parse_account_file(_cash_account(config))
    policy = bs_advice.load_policy_payload(config.to_dict())
    result = bs_advice.run_bs_review(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, persist=True,
        calendar=days + ["2026-11-18", "2026-11-19"], **_readers(days))
    assert result["persisted"] is True and result["ledger_id"]
    assert result["fills"], "合法本地分钟正路必须真实产生入场成交"
    assert result["fills"][0]["side"] == "buy"
    readback = bs_advice.read_run(result["ledger_id"])
    assert readback["ledger_id"] == result["ledger_id"]
    assert readback["history"]["records"]


def test_persisted_run_is_idempotent_for_same_inputs(tmp_path, monkeypatch):
    from dragon_quant.infra import calibration
    from dragon_quant.research.bs_advice import assembly

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=9.0)
    _minute(db_path, "2026-11-18", start="10:00:00", end="10:01:00", close=9.1)
    account_payload = _cash_account(config)
    account_payload["pending_orders"] = [{
        "order_id": "cancelled-1", "code": "600001", "side": "buy",
        "qty": 100, "status": "cancelled", "created_event": 1,
        "filled_qty": 0}, {
        "order_id": "partial-1", "code": "600001", "side": "buy",
        "qty": 200, "status": "partially_filled", "created_event": 1,
        "filled_qty": 100, "strategy_ref": {}, "plan_id": "p1"}]
    account = bs_advice.parse_account_file(account_payload)
    policy = bs_advice.load_policy_payload(config.to_dict())
    kwargs = dict(account=account, policy=policy, date_from="2026-11-18",
                  date_to="2026-11-18", db_path=db_path, persist=True,
                  calendar=days + ["2026-11-18", "2026-11-19"],
                  **_readers(days))
    first = bs_advice.run_bs_review(**kwargs)
    second = bs_advice.run_bs_review(**kwargs)
    assert first["ledger_id"] == second["ledger_id"]
    assert first["revision"] == second["revision"]
    cancelled = [order for order in first["orders"]
                 if order["order_id"] == "cancelled-1"]
    assert cancelled and cancelled[0]["status"] == "cancelled"
    assert len(first["orders"]) == len(second["orders"])


def test_review_without_local_assets_is_explicit_error(tmp_path):
    account = bs_advice.parse_account_file(_account_payload())
    policy = bs_advice.load_policy_payload(_config().to_dict())
    with pytest.raises(bs_advice.BsReviewError) as excinfo:
        bs_advice.run_bs_review(
            account=account, policy=policy, date_from="2026-11-18",
            date_to="2026-11-20", db_path=tmp_path / "missing.db")
    assert excinfo.value.code in ("local_assets_missing",
                                  "local_intraday_events_missing")


def test_watermark_crash_then_resume_does_not_lose_progress(tmp_path, monkeypatch):
    from dragon_quant.infra import calibration
    from dragon_quant.research.bs_advice import assembly
    from dragon_quant.review_account.ledger.store import AccountLedger

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=10.0)
    _minute(db_path, "2026-11-18", start="10:00:00", end="10:01:00", close=10.3)
    account = bs_advice.parse_account_file(
        {**_cash_account(config), "cash": 50001.0})
    policy = bs_advice.load_policy_payload(config.to_dict())

    def _crash(*args, **kwargs):
        return None

    with monkeypatch.context() as patch:
        patch.setattr(AccountLedger, "update_run_status", _crash)
        first = bs_advice.run_bs_review(
            account=account, policy=policy, date_from="2026-11-18",
            date_to="2026-11-18", db_path=db_path, persist=True,
            calendar=days + ["2026-11-18", "2026-11-19"], **_readers(days))
    assert first["run_status"]["completed_events"] == 0
    second = bs_advice.run_bs_review(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, persist=True,
        calendar=days + ["2026-11-18", "2026-11-19"], **_readers(days))
    assert second["run_status"]["completed_events"] == 2
