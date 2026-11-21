"""W4b KR6 指标/冻结实验/生命周期（手算基准 + 本地数据正路）。"""

from __future__ import annotations

import pytest

from dragon_quant.infra import calibration
from dragon_quant.research import bs_advice
from dragon_quant.research.bs_advice import assembly, metrics
from dragon_quant.review_account.strategy.bs_policy import teaching_fixture
from dragon_quant.tests.test_bs_advice import (
    _cash_account, _config, _history, _minute, _readers,
)


def _event(day, equity, *, fees=0.0, qty=0, price=0.0, status="filled"):
    return {
        "trade_date": day,
        "state": {"equity": equity,
                  "risk_book": {"total": 100.0, "unknown": 0.0,
                                "unknown_holdings": 0},
                  "orders": [{"order_id": "o1", "status": status,
                              "qty": 100, "filled_qty": 0,
                              "reserved_risk": 100.0,
                              "reserved_amount": 50.0}]},
        "fills": ([{"fee": fees, "qty": qty, "price": price,
                    "order_id": "o1"}] if qty else []),
        "recovery": {"active": False},
    }


def test_metrics_hand_computed_drawdown_fees_tail():
    events = [_event("2026-11-18", 100000.0),
              _event("2026-11-19", 96000.0, fees=10.0, qty=1000, price=10.0)]
    final = {"equity": 96000.0, "trade_date": "2026-11-19",
             "closed_trades": [{"realized_net": -500.0},
                               {"realized_net": 300.0}],
             "recovery_stage": 0}
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=events, final_state=final,
        config=teaching_fixture())
    assert result["net_return_pct"] == pytest.approx(-4.0)
    assert result["fees"] == 10.0
    assert result["max_drawdown_pct"] == pytest.approx(4.0)
    assert result["max_drawdown_duration_trade_days"] == 2
    assert result["right_tail_max"] == 300.0
    assert result["denominator"]["closed_trades"] == 2


def test_metrics_empty_denominator_is_insufficient_not_zero():
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=[], final_state={"equity": 100000.0,
                                                       "closed_trades": []},
        config=teaching_fixture())
    assert result["effect_status"] == "insufficient_data"
    assert "no_qualified_closed_trades" in result["reasons"]
    assert result["net_return_pct"] == 0.0
    assert (result["denominator"]["closed_trades"]) == 0


def test_metrics_dedupes_pending_orders_and_peak_usage():
    events = [_event(f"2026-11-{18 + i:02d}", 100000.0, status="pending")
              for i in range(5)]
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=events,
        final_state={"equity": 100000.0, "closed_trades": [],
                     "trade_date": "2026-11-22"})
    assert result["unfilled_orders"] == 1
    assert result["order_denominator"] == 1
    assert result["risk_budget_used_peak"] == 100.0
    assert result["cash_reserved_peak"] == 50.0


def test_metrics_drawdown_duration_uses_trade_days_not_event_index():
    events = [_event("2026-11-18", 100000.0) for _ in range(100)]
    events.append(_event("2026-11-19", 90000.0))
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=events,
        final_state={"equity": 90000.0, "closed_trades": [],
                     "trade_date": "2026-11-19"},
        trade_calendar=["2026-11-18", "2026-11-19"])
    assert result["max_drawdown_pct"] == pytest.approx(10.0)
    assert result["max_drawdown_duration_trade_days"] == 2


def test_metrics_uses_initial_equity_not_cash_for_stock_account():
    result = metrics.compute_metrics(
        initial_cash=1000.0, initial_equity=10000.0,
        events=[_event("2026-11-18", 10000.0)],
        final_state={"equity": 10000.0, "closed_trades": [],
                     "trade_date": "2026-11-18"})
    assert result["net_return_pct"] == pytest.approx(0.0)
    assert result["initial_equity"] == 10000.0


def test_time_splits_declares_fixed_parameter_and_purge():
    splits = metrics.time_splits(
        [f"2026-11-{day:02d}" for day in range(1, 11)], purge_days=2)
    assert splits["mode"] == "fixed_parameter_split"
    assert splits["training_performed"] is False
    assert splits["windows"]["train"] and splits["windows"]["test"]


def _run_experiment(tmp_path, monkeypatch, *, cohort_mode="rolling"):
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
    return bs_advice.run_bs_experiment(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, cohort_mode=cohort_mode,
        calendar=days + ["2026-11-18", "2026-11-19"],
        dragons_reader=_readers(days)["dragons_reader"],
        prep_reader=_readers(days)["prep_reader"],
        calendar_reader=_readers(days)["calendar_reader"],
        plan_reader=_readers(days)["plan_reader"])


def test_experiment_compares_bs_and_baseline(tmp_path, monkeypatch):
    result = _run_experiment(tmp_path, monkeypatch)
    assert result["schema_version"] == bs_advice.EXPERIMENT_VERSION
    assert result["data_modes"]["local_only"]["status"] in (
        "completed", "insufficient_data")
    assert result["expected_events"] == result["completed_events"] == 3
    assert result["metrics"]["bs"]["denominator"]["fills"] >= 1
    assert result["metrics"]["comparison"]["same_frozen_pack"] is True
    assert result["data_modes"]["enhanced_frozen"]["status"] == \
        "insufficient_data"
    assert result["metrics"]["bs"]["splits"]["mode"] == \
        "fixed_parameter_split"
    assert result["metrics"]["bs"]["splits"]["by_split"], "分窗样本必须显式"


def test_frozen_pack_digest_includes_seed_state_and_market_override_reuses_pack(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=10.0)
    _minute(db_path, "2026-11-18", start="10:00:00", end="10:01:00", close=10.3)
    readers = _readers(days)

    def payload(mark):
        data = _cash_account(config)
        data["runtime_facts"] = {
            "mark_facts": [{"code": "600001", "price": mark,
                            "source_ts": 1, "available_at": 1,
                            "kind": "quote"}],
            "fund_credits": {"sell-1": {"credited": 10.0, "spent": 0.0}},
        }
        return data

    policy = bs_advice.load_policy_payload(config.to_dict())
    first = bs_advice.build_experiment_pack(
        account=bs_advice.parse_account_file(payload(10.0)), policy=policy,
        date_from="2026-11-18", date_to="2026-11-18", db_path=db_path,
        **readers)
    second = bs_advice.build_experiment_pack(
        account=bs_advice.parse_account_file(payload(11.0)), policy=policy,
        date_from="2026-11-18", date_to="2026-11-18", db_path=db_path,
        **readers)
    assert first["digest"] != second["digest"]
    assert first["inputs"]["events"] == second["inputs"]["events"]


def test_rolling_pool_keeps_later_candidates_without_daily(tmp_path,
                                                           monkeypatch):
    from dragon_quant.research.bs_advice.review import build_review_pack

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    policy = bs_advice.load_policy_payload(config.to_dict())
    account = bs_advice.parse_account_file(_cash_account(config))

    def dragons(date_from, date_to):
        return [{"code": "600001", "trade_date": "2026-11-18"}]

    with pytest.raises(bs_advice.BsReviewError) as excinfo:
        build_review_pack(
            account=account, policy=policy, date_from="2026-11-18",
            date_to="2026-11-18", db_path=tmp_path / "missing.db",
            dragons_reader=dragons,
            prep_reader=lambda before: (None, {"error": None}),
            calendar_reader=lambda as_of: ["2026-11-18"],
            plan_reader=lambda d: None)
    # 候选进入母集/池，但因无任何本地事件显式失败，而不是 no_universe。
    assert excinfo.value.code in ("local_assets_missing",
                                  "local_intraday_events_missing")


def test_enhanced_pack_mechanism_and_parameter_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=10.0)
    _minute(db_path, "2026-11-18", start="10:00:00", end="10:01:00", close=10.3)
    readers = _readers(days)
    account = bs_advice.parse_account_file(_cash_account(config))
    policy = bs_advice.load_policy_payload(config.to_dict())
    pack = bs_advice.build_experiment_pack(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, **readers)
    result = bs_advice.run_bs_experiment(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, pack=pack,
        enhanced_pack=pack,
        parameter_attempts=[{"profile": "a", "result": 1.0}])
    assert result["data_modes"]["enhanced_frozen"]["status"] == \
        "insufficient_data"
    assert result["data_modes"]["enhanced_frozen"]["reason"] == \
        "enhanced_evidence_not_distinct"
    assert result["parameter_attempts"] == [{"profile": "a", "result": 1.0}]


def test_external_account_policy_cannot_override_frozen_pack(tmp_path,
                                                              monkeypatch):
    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=10.0)
    _minute(db_path, "2026-11-18", start="10:00:00", end="10:01:00", close=10.3)
    readers = _readers(days)
    account = bs_advice.parse_account_file(_cash_account(config))
    policy = bs_advice.load_policy_payload(config.to_dict())
    pack = bs_advice.build_experiment_pack(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, **readers)
    other = bs_advice.parse_account_file(
        {**_cash_account(config), "cash": 60000.0})
    with pytest.raises(bs_advice.BsExperimentError):
        bs_advice.run_bs_experiment(
            account=other, policy=policy, date_from="2026-11-18",
            date_to="2026-11-18", db_path=db_path, pack=pack)


def test_experiment_lifecycle_persists_running_then_final(tmp_path, monkeypatch):
    from dragon_quant.tests.support import patch_storage_dbs

    with patch_storage_dbs(str(tmp_path / "dragon.db"),
                           str(tmp_path / "assets.db")):
        experiment_id = bs_advice.save_experiment_run(
            request={"date_from": "2026-11-18"}, expected_events=3)
        running = bs_advice.show_bs_experiment(experiment_id)
        assert running["status"] == "running"
        bs_advice.finish_experiment_run(
            experiment_id, status="completed",
            report={"status": "completed", "expected_events": 3,
                    "completed_events": 3})
        done = bs_advice.show_bs_experiment(experiment_id)
        assert done["status"] == "completed"
        assert done["report"]["completed_events"] == 3
        page = bs_advice.list_bs_experiments()
        assert any(row["id"] == experiment_id for row in page["items"])


def test_fixed_and_rolling_cohort_are_explicit(tmp_path, monkeypatch):
    rolling = _run_experiment(tmp_path, monkeypatch, cohort_mode="rolling")
    fixed = _run_experiment(tmp_path, monkeypatch, cohort_mode="fixed")
    assert rolling["cohort_mode"] == "rolling"
    assert fixed["cohort_mode"] == "fixed"


def test_frozen_payload_roundtrip_replay_and_tamper_rejected(tmp_path,
                                                             monkeypatch):
    from dragon_quant.research.bs_advice import freeze

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
    pack = bs_advice.build_experiment_pack(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, **_readers(days))
    frozen = freeze.freeze_experiment_inputs(
        account=account, policy=policy, pack=pack)
    loaded = freeze.load_experiment_inputs(frozen)
    assert loaded.digest == frozen["digest"]
    assert loaded.policy.content_hash == policy.content_hash
    assert loaded.account.account.cash == account.account.cash
    assert len(loaded.events) == len(pack["events"])
    assert loaded.events[0].rows == pack["events"][0].rows
    assert loaded.events[0].candidates[0].code == \
        pack["events"][0].candidates[0].code
    first = freeze.replay_frozen(frozen)
    second = freeze.replay_frozen(frozen)
    assert first["fees"] == second["fees"]
    assert first["final_state"] == second["final_state"]
    tampered = dict(frozen)
    tampered["account"] = {**dict(frozen["account"]),
                           "fund_credits": {"x": {"credited": 1.0}}}
    with pytest.raises(freeze.FreezeError):
        freeze.load_experiment_inputs(tampered)
    bad_version = dict(frozen, impl_version="future/999")
    with pytest.raises(freeze.FreezeError):
        freeze.load_experiment_inputs(bad_version)
    tampered_policy = dict(frozen, policy={**dict(frozen["policy"]),
                                           "profile_version": "x"})
    with pytest.raises(freeze.FreezeError):
        freeze.load_experiment_inputs(tampered_policy)
    tampered_event = dict(frozen)
    events = [dict(item) for item in frozen["events"]]
    events[0] = {**events[0], "timestamp": int(events[0]["timestamp"]) + 1}
    tampered_event["events"] = events
    with pytest.raises(freeze.FreezeError):
        freeze.load_experiment_inputs(tampered_event)


def test_default_repositories_keep_prep_only_code_without_daily(tmp_path,
                                                               monkeypatch):
    from dragon_quant.infra.assets import db as assets_db
    from dragon_quant.scan.storage import dragons as dragon_store
    from dragon_quant.tests.support import patch_storage_dbs
    from dragon_quant.tests.test_bs_advice import _history, _minute

    monkeypatch.setattr("dragon_quant.infra.assets.db.ASSETS_DB_PATH",
                        tmp_path / "assets.db")
    monkeypatch.setattr("dragon_quant.scan.storage.research_reads.paths.DB_PATH",
                        tmp_path / "dragon.db")
    with patch_storage_dbs(str(tmp_path / "dragon.db"),
                           str(tmp_path / "assets.db")):
        _history(tmp_path / "assets.db")
        _minute(tmp_path / "assets.db", "2026-11-18", start="09:59:00",
                end="10:00:00", close=10.0)
        _minute(tmp_path / "assets.db", "2026-11-18", start="10:00:00",
                end="10:01:00", close=10.3)
        dragon_store.save_dragons("2026-11-17", [{
            "code": "600001", "name": "龙一", "rank": 1,
            "composite_score": 90.0, "is_true_dragon": True,
            "scan_id": "scan-1"}], version="v2")
        assets_db.save_prep_snapshot("2026-11-17", {
            "review_candidates": [{"code": "600002", "name": "仅 prep",
                                   "trade_date": "2026-11-17",
                                   "eligibility": "eligible",
                                   "position_pct": 5.0}],
            "market": {"state": "strong", "coeff": 1.0, "partial": False,
                       "missing_inputs": []}}, source="v2")
        config = _config()
        policy = bs_advice.load_policy_payload(config.to_dict())
        account = bs_advice.parse_account_file(_cash_account(config))
        pack = bs_advice.build_experiment_pack(
            account=account, policy=policy, date_from="2026-11-18",
            date_to="2026-11-18", db_path=tmp_path / "assets.db")
    codes = {item["code"] for day in pack["decision_universes"]
             for item in day["items"]}
    assert "600002" in codes, "prep-only/no-daily 候选必须进入逐决策母集"
    assert "600002" in pack["pack"]["pool_codes"]


def test_experiment_flow_same_input_idempotent_and_different_seed_isolated(
        tmp_path, monkeypatch):
    from dragon_quant.research.bs_advice import service
    from dragon_quant.tests.support import patch_storage_dbs

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=10.0)
    _minute(db_path, "2026-11-18", start="10:00:00", end="10:01:00", close=10.3)
    _minute(db_path, "2026-11-18", start="10:01:00", end="10:02:00", close=10.05)
    readers = _readers(days)
    policy = bs_advice.load_policy_payload(config.to_dict())
    account = bs_advice.parse_account_file(_cash_account(config))
    lock = tmp_path / "locks" / "bs.lock"
    with patch_storage_dbs(str(tmp_path / "dragon.db"), str(db_path)):
        first = service.run_experiment_flow(
            account=account, policy=policy, date_from="2026-11-18",
            date_to="2026-11-18", confirm=True, lock_path=lock,
            db_path=db_path, **readers)
        second = service.run_experiment_flow(
            account=account, policy=policy, date_from="2026-11-18",
            date_to="2026-11-18", confirm=True, lock_path=lock,
            db_path=db_path, **readers)
        assert first["id"] == second["id"]
        assert first["pack_digest"] == second["pack_digest"]
        other_account = bs_advice.parse_account_file(
            {**_cash_account(config), "cash": 60000.0})
        third = service.run_experiment_flow(
            account=other_account, policy=policy, date_from="2026-11-18",
            date_to="2026-11-18", confirm=True, lock_path=lock,
            db_path=db_path, **readers)
        assert third["id"] != first["id"]


def test_variants_do_not_read_sources_after_freeze(tmp_path, monkeypatch):
    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=10.0)
    _minute(db_path, "2026-11-18", start="10:00:00", end="10:01:00", close=10.3)
    readers = _readers(days)
    calls = {"n": 0}

    def dragons(date_from, date_to):
        calls["n"] += 1
        return readers["dragons_reader"](date_from, date_to)

    readers["dragons_reader"] = dragons
    account = bs_advice.parse_account_file(_cash_account(config))
    policy = bs_advice.load_policy_payload(config.to_dict())
    pack = bs_advice.build_experiment_pack(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, **readers)
    calls["n"] = 0
    bs_advice.run_bs_experiment(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, pack=pack,
        dragons_reader=readers["dragons_reader"])
    assert calls["n"] == 0, "variant 执行不得再读源"


def test_freeze_candidate_confirmations_and_strict_invalid_inputs(tmp_path,
                                                                  monkeypatch):
    from dragon_quant.research.bs_advice import freeze
    from dragon_quant.review_account.strategy.bs_policy import (
        CandidateView, EvidenceConfirmation,
    )

    confirm = EvidenceConfirmation(
        rule_id="confirm:600001", evidence_ids=("obs-1",),
        available_at="2026-11-18T10:00:00+08:00", version="v1",
        snapshot_id="snap-1")
    candidate = CandidateView(
        code="600001", eligible=False, tradable=False,
        confirmations=(confirm,), entry_version="v1")
    decoded = freeze._candidate(candidate.to_dict())
    assert decoded.confirmations == (confirm,)
    assert decoded.eligible is False
    with pytest.raises(freeze.FreezeError):
        freeze._candidate({**candidate.to_dict(), "eligible": "false"})

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=10.0)
    account = bs_advice.parse_account_file(_cash_account(config))
    policy = bs_advice.load_policy_payload(config.to_dict())
    pack = bs_advice.build_experiment_pack(
        account=account, policy=policy, date_from="2026-11-18",
        date_to="2026-11-18", db_path=db_path, **_readers(days))
    frozen = pack["freeze"]
    for mutate in (
        lambda p: p.update({"calendar": ["not-a-date"]}),
        lambda p: p.update({"date_from": "bad-date"}),
        lambda p: p["cost"].update({"commission_rate": -1}),
        lambda p: p.update({"ref": {}}),
    ):
        bad = {**frozen, "events": list(frozen["events"]),
               "cost": dict(frozen["cost"]), "ref": dict(frozen["ref"])}
        mutate(bad)
        bad["digest"] = freeze.hash_payload(
            {k: v for k, v in bad.items() if k != "digest"})
        with pytest.raises(freeze.FreezeError):
            freeze.load_experiment_inputs(bad)


def test_resume_old_failed_experiment_without_source(tmp_path, monkeypatch):
    from dragon_quant.research.bs_advice import service
    from dragon_quant.review_account import storage
    from dragon_quant.tests.support import patch_storage_dbs

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=10.0)
    _minute(db_path, "2026-11-18", start="10:00:00", end="10:01:00", close=10.3)
    _minute(db_path, "2026-11-18", start="10:01:00", end="10:02:00", close=10.05)
    readers = _readers(days)
    policy = bs_advice.load_policy_payload(config.to_dict())
    account = bs_advice.parse_account_file(_cash_account(config))
    lock = tmp_path / "locks" / "bs.lock"
    with patch_storage_dbs(str(tmp_path / "dragon.db"), str(db_path)):
        first = service.run_experiment_flow(
            account=account, policy=policy, date_from="2026-11-18",
            date_to="2026-11-18", confirm=True, lock_path=lock,
            db_path=db_path, calendar=days + ["2026-11-18", "2026-11-19"],
            **readers)
        storage.update_review_account_experiment(
            first["id"], status="failed",
            report={"status": "failed", "expected_events": 0,
                    "completed_events": 0})
        resumed = service.resume_experiment_flow(
            experiment_id=first["id"], lock_path=lock)
        shown = bs_advice.show_bs_experiment(first["id"])
        full = bs_advice.show_bs_experiment(first["id"], include_details=True)
    assert resumed["status"] == "completed"
    assert resumed["expected_events"] == resumed["completed_events"]
    assert resumed["metrics"]["bs"]["denominator"]["fills"] >= 1
    assert resumed["metrics"]["baseline_no_new_risk"] is not None
    assert "bs" in resumed["variants"] and "baseline_no_new_risk" in resumed["variants"]
    assert "variants" not in shown["report"]
    assert "variants" in full["report"]


def test_finish_save_failure_leaves_resumable_run(tmp_path, monkeypatch):
    from dragon_quant.research.bs_advice import service
    from dragon_quant.tests.support import patch_storage_dbs

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=10.0)
    _minute(db_path, "2026-11-18", start="10:00:00", end="10:01:00", close=10.3)
    _minute(db_path, "2026-11-18", start="10:01:00", end="10:02:00", close=10.05)
    readers = _readers(days)
    policy = bs_advice.load_policy_payload(config.to_dict())
    account = bs_advice.parse_account_file(_cash_account(config))
    lock = tmp_path / "locks" / "bs.lock"
    with patch_storage_dbs(str(tmp_path / "dragon.db"), str(db_path)):
        with pytest.raises(RuntimeError):
            with monkeypatch.context() as patch:
                patch.setattr(service, "finish_experiment_run",
                              lambda *a, **k: (_ for _ in ()).throw(
                                  RuntimeError("report save failed")))
                service.run_experiment_flow(
                    account=account, policy=policy, date_from="2026-11-18",
                    date_to="2026-11-18", confirm=True, lock_path=lock,
                    db_path=db_path,
                    calendar=days + ["2026-11-18", "2026-11-19"],
                    **readers)
        page = bs_advice.list_bs_experiments(include_details=True)
        pending = [row for row in page["items"]
                   if row["status"] in ("running", "failed")]
        assert pending
        resumed = service.resume_experiment_flow(
            experiment_id=pending[0]["id"], lock_path=lock)
    assert resumed["status"] == "completed"
    assert resumed["expected_events"] == resumed["completed_events"]


def test_flat_curve_has_zero_drawdown_duration_even_with_full_calendar():
    events = [_event(f"2026-11-{day:02d}", 100000.0) for day in range(17, 21)]
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=events,
        final_state={"equity": 100000.0, "closed_trades": [],
                     "trade_date": "2026-11-20"},
        trade_calendar=["2026-11-17", "2026-11-18", "2026-11-19",
                        "2026-11-20"])
    assert result["max_drawdown_pct"] == 0.0
    assert result["max_drawdown_duration_trade_days"] == 0


def test_explicit_unknown_initial_equity_is_unknown_not_cash():
    result = metrics.compute_metrics(
        initial_cash=1000.0, initial_equity=None,
        events=[_event("2026-11-18", 10000.0)],
        final_state={"equity": 10000.0, "closed_trades": [],
                     "trade_date": "2026-11-18"})
    assert result["initial_equity"] is None
    assert result["net_return_pct"] is None
    assert result["engineering_status"] == "insufficient_data"
    assert "initial_equity_unknown" in result["reasons"]


def test_first_event_loss_counts_from_initial_equity():
    result = metrics.compute_metrics(
        initial_cash=1000.0, initial_equity=10000.0,
        events=[_event("2026-11-18", 9000.0)],
        final_state={"equity": 9000.0, "closed_trades": [],
                     "trade_date": "2026-11-18"},
        trade_calendar=["2026-11-18"])
    assert result["max_drawdown_pct"] == pytest.approx(10.0)
    assert result["max_drawdown_duration_trade_days"] == 1


# ─── C2：占用/滑点/尾部/恢复/分窗/成熟度语义 ──────────────────────────────


def _ms(day, hour=15):
    from datetime import datetime, timedelta, timezone

    month, date = 11, int(day.split("-")[2])
    return int(datetime(2026, month, date, hour, tzinfo=timezone(
        timedelta(hours=8))).timestamp() * 1000)


def _order(order_id, *, status="pending", risk=100.0, cash=50.0,
           filled=0, qty=100):
    return {"order_id": order_id, "status": status, "reserved_risk": risk,
            "reserved_amount": cash, "filled_qty": filled, "qty": qty}


def test_order_exposure_peak_end_time_weighted_and_partial_cancel_denominator():
    events = [
        {"trade_date": "2026-11-18",
         "state": {"equity": 100000.0, "cash": 90000.0,
                   "batches": [{"qty": 1000, "cost_price": 10.0}],
                   "risk_book": {"total": 300.0, "unknown": 0.0,
                                 "unknown_holdings": 0},
                   "orders": [_order("a", risk=300.0, cash=1000.0),
                              _order("b", status="cancelled", risk=500.0,
                                     cash=2000.0)]},
         "fills": [], "recovery": {"active": False}},
        {"trade_date": "2026-11-19",
         "state": {"equity": 99000.0, "cash": 89000.0,
                   "batches": [{"qty": 1000, "cost_price": 10.0}],
                   "risk_book": {"total": 100.0, "unknown": 0.0,
                                 "unknown_holdings": 0},
                   "orders": [_order("a", status="partially_filled", risk=100.0,
                                     cash=500.0, filled=50)]},
         "fills": [], "recovery": {"active": False}},
        {"trade_date": "2026-11-20",
         "state": {"equity": 99000.0, "cash": 89000.0, "batches": [],
                   "risk_book": {"total": 0.0, "unknown": 0.0,
                                 "unknown_holdings": 0},
                   "orders": []},
         "fills": [], "recovery": {"active": False}},
    ]
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=events,
        final_state={"equity": 99000.0, "trade_date": "2026-11-20",
                     "closed_trades": []},
        trade_calendar=["2026-11-18", "2026-11-19", "2026-11-20"])
    assert result["order_denominator"] == 2, "取消订单仍保留分母"
    assert result["order_status_counts"] == {"cancelled": 1,
                                             "partially_filled": 1}
    assert result["risk_budget_used_peak"] == 300.0
    assert result["risk_budget_used_end"] == 0.0
    assert result["risk_budget_used_time_weighted"] == pytest.approx(
        (300.0 + 100.0 + 0.0) / 3)
    assert result["funding"]["position_cost_value_end"] == 0.0
    assert result["funding"]["idle_cash_end"] == 89000.0
    assert result["unfilled_orders"] == 2, "取消与部分成交都算未成交"
    assert result["unfilled_partial_orders"] == 1
    assert result["cancelled_orders"] == 1
    assert result["unfilled_qty"] == 150
    assert result["unfilled_quantity_ratio"] == pytest.approx(0.75)


def test_slippage_decomposed_per_side_from_frozen_reference_price():
    from dragon_quant.review_account.strategy.bs_policy import BSPolicyConfig

    payload = teaching_fixture().to_dict()
    payload["scenario"]["buy_slippage"] = 0.002
    payload["scenario"]["sell_slippage"] = 0.004
    config = BSPolicyConfig.from_dict(payload)
    events = [{
        "trade_date": "2026-11-18", "timestamp": 1,
        "state": {"equity": 100000.0, "orders": []},
        "rows": {"600001": {"execution_price": 10.0}},
        "fills": [
            {"side": "buy", "code": "600001", "price": 10.02, "qty": 100,
             "fee": 5.0},
            {"side": "sell", "code": "600001", "price": 9.98, "qty": 100,
             "fee": 6.0}],
        "recovery": {"active": False},
    }]
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=events,
        final_state={"equity": 100000.0, "closed_trades": [],
                     "trade_date": "2026-11-18"}, config=config)
    slip = result["slippage"]
    assert slip["basis"] == "realized_from_frozen_reference_price"
    assert slip["realized"]["buy"]["cost"] == pytest.approx(2.0)
    assert slip["realized"]["sell"]["cost"] == pytest.approx(2.0)
    assert slip["model_estimate"]["buy"] == pytest.approx(1002 * 0.002)
    assert slip["model_estimate"]["sell"] == pytest.approx(998 * 0.004)
    assert slip["model_estimate"]["buy"] != slip["model_estimate"]["sell"]


def test_slippage_without_reference_is_null_model_estimate_only():
    events = [{
        "trade_date": "2026-11-18", "timestamp": 1,
        "state": {"equity": 100000.0, "orders": []},
        "rows": {},
        "fills": [{"side": "buy", "code": "600001", "price": 10.02,
                   "qty": 100, "fee": 5.0}],
        "recovery": {"active": False},
    }]
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=events,
        final_state={"equity": 100000.0, "closed_trades": [],
                     "trade_date": "2026-11-18"})
    assert result["slippage"]["realized"] is None
    assert result["slippage"]["basis"] == "model_estimate_only"
    assert result["slippage"]["missing_reference_fills"] == 1


def test_tail_threshold_from_study_overrides_profile():
    closed = [{"realized_net": -400.0, "closed_event": _ms("2026-11-18")},
              {"realized_net": 300.0, "closed_event": _ms("2026-11-18")}]
    config = teaching_fixture()
    profile = metrics.compute_metrics(
        initial_cash=100000.0, events=[],
        final_state={"equity": 100000.0, "closed_trades": closed},
        config=config)
    study = metrics.compute_metrics(
        initial_cash=100000.0, events=[],
        final_state={"equity": 100000.0, "closed_trades": closed},
        config=config, study={"tail_threshold_pct": 0.1})
    assert profile["tail_threshold_source"] == \
        "profile_invalidation_disaster_stop_pct"
    assert profile["tail_loss_count"] == 0
    assert study["tail_threshold_source"] == "study_explicit"
    assert study["tail_threshold"] == pytest.approx(-100.0)
    assert study["tail_loss_count"] == 1
    assert study["tail_loss_severity"] == -400.0


def test_recovery_segments_same_day_multi_stage_non_overlapping_and_censored():
    events = [
        {"trade_date": "2026-11-18", "state": {"equity": 100.0},
         "recovery": {"active": True, "stage": 1, "current_scale": 0.5}},
        {"trade_date": "2026-11-18", "state": {"equity": 101.0},
         "recovery": {"active": True, "stage": 2, "current_scale": 0.75}},
        {"trade_date": "2026-11-19", "state": {"equity": 104.0},
         "recovery": {"active": True, "stage": 2, "current_scale": 0.75}},
        {"trade_date": "2026-11-20", "state": {"equity": 106.0},
         "recovery": {"active": True, "stage": 3, "current_scale": 1.0}},
    ]
    dates = ["2026-11-18", "2026-11-19", "2026-11-20", "2026-11-21"]
    from dragon_quant.research.bs_advice.metrics_risk import recovery_segments

    segments = recovery_segments(events, dates)
    assert [item["stage"] for item in segments] == [1, 2, 3]
    assert segments[0]["trade_days"] == 1
    assert segments[1]["trade_days"] == 2
    assert segments[-1]["censored"] is True, "窗末仍恢复中必须标记删失"
    assert segments[-1]["scale"] == 1.0


def test_time_splits_label_end_purge_uses_calendar():
    days = [f"2026-11-{day:02d}" for day in range(1, 11)]
    splits = metrics.time_splits(days, horizon_days=2)
    assert splits["purge_basis"] == "label_end_calendar"
    assert splits["purge_days"] == 2
    assert splits["purged_days"]["train"][-1] == "2026-11-04"
    assert splits["purged_days"]["valid"] == []


def test_maturity_excludes_preexisting_and_unmatched_label_end():
    calendar = ["2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"]
    closed = [
        {"realized_net": 1.0, "closed_event": _ms("2026-11-16")},
        {"realized_net": 1.0, "closed_event": _ms("2026-11-18")},
        {"realized_net": 1.0, "closed_event": _ms("2026-11-19")},
        {"realized_net": 1.0, "closed_event": _ms("2026-11-20")},
    ]
    report = metrics.maturity_report(
        closed, calendar, evaluation_cutoff="2026-11-19", horizon_days=1,
        research_start="2026-11-18")
    assert report["preexisting"] == 1
    assert report["matured"] == 2
    assert report["unmatured"] == 1
    assert report["exclusions"]["after_cutoff"] == 1
    assert report["status"] == "insufficient_data"
    assert report["matured_ids"]
    assert all("sample_id" in item for item in report["details"])


def test_maturity_uses_real_datetime_not_truncated_date():
    closed = [{"closed_trade_id": "t1", "realized_net": -999999.0,
               "closed_event": _ms("2026-11-18", hour=14)}]
    report = metrics.maturity_report(
        closed, ["2026-11-18"],
        evaluation_cutoff="2026-11-18T10:00:00+08:00",
        research_start="2026-11-18")
    assert report["matured"] == 0
    assert report["unmatured"] == 1
    assert report["exclusions"]["after_cutoff"] == 1
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=[],
        final_state={"equity": 100000.0, "closed_trades": closed},
        maturity_report_data=report, study={"tail_threshold_pct": 5.0})
    assert result["denominator"]["qualified_closed_trades"] == 0
    assert result["tail_loss_count"] is None
    assert result["tail_loss_severity"] is None
    assert result["effect_status"] == "insufficient_data"


def test_maturity_rejects_naive_or_invalid_cutoff():
    closed = [{"closed_trade_id": "t1", "realized_net": 1.0,
               "closed_event": _ms("2026-11-18", hour=10)}]
    naive = metrics.maturity_report(
        closed, ["2026-11-18"], evaluation_cutoff="2026-11-18T20:00:00",
        research_start="2026-11-18")
    assert naive["cutoff_error"] == "cutoff_naive"
    assert naive["status"] == "insufficient_data"
    invalid = metrics.maturity_report(
        closed, ["2026-11-18"], evaluation_cutoff="not-a-date",
        research_start="2026-11-18")
    assert invalid["cutoff_error"] == "cutoff_invalid"
    assert invalid["status"] == "insufficient_data"


def test_split_opening_equity_uses_prior_day_not_first_fill():
    events = [
        {"trade_date": "2026-11-18", "state": {"equity": 100.0},
         "fills": [], "recovery": {"active": False}},
        {"trade_date": "2026-11-19", "state": {"equity": 90.0},
         "fills": [], "recovery": {"active": False}},
        {"trade_date": "2026-11-20", "state": {"equity": 95.0},
         "fills": [], "recovery": {"active": False}},
    ]
    splits = metrics.time_splits(["2026-11-18", "2026-11-19", "2026-11-20"],
                                 train=0.4, valid=0.3, horizon_days=0)
    splits["purged_days"] = {"test": ["2026-11-20"]}
    breakdown = metrics.split_samples(events, splits, initial_equity=100.0,
                                      calendar=["2026-11-18", "2026-11-19",
                                                "2026-11-20"])
    item = breakdown["test"]
    assert item["opening_equity"] == 90.0
    assert item["net_return_pct"] == pytest.approx((95.0 - 90.0) / 90.0 * 100.0)


def _study_setup(tmp_path, monkeypatch, *, need_storage=False):
    from contextlib import nullcontext

    from dragon_quant.tests.support import patch_storage_dbs

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    days = _history(db_path)
    _minute(db_path, "2026-11-18", start="09:59:00", end="10:00:00", close=10.0)
    _minute(db_path, "2026-11-18", start="10:00:00", end="10:01:00", close=10.3)
    _minute(db_path, "2026-11-18", start="10:01:00", end="10:02:00", close=10.05)
    readers = _readers(days)
    policy = bs_advice.load_policy_payload(config.to_dict())
    account = bs_advice.parse_account_file(_cash_account(config))
    lock = tmp_path / "locks" / "bs.lock"
    storage = (patch_storage_dbs(str(tmp_path / "dragon.db"), str(db_path))
               if need_storage else nullcontext())
    return dict(config=config, db_path=db_path, days=days, readers=readers,
                policy=policy, account=account, lock=lock, storage=storage)


def test_study_two_policies_plus_baseline_replay_and_identity(tmp_path,
                                                             monkeypatch):
    from dragon_quant.research.bs_advice import service
    from dragon_quant.review_account.strategy.bs_policy import teaching_fixture

    setup = _study_setup(tmp_path, monkeypatch, need_storage=True)
    variant_a = teaching_fixture(profile_id="variant-a",
                                 profile_version="real-1", fixture=False,
                                 label="variant a")
    variant_b = teaching_fixture(profile_id="variant-b",
                                 profile_version="real-1", fixture=False,
                                 label="variant b")
    study = {
        "schema_version": 1, "study_id": "s1", "evaluation_cutoff": "2026-11-18",
        "tail_threshold_pct": 5.0,
        "variants": [
            {"name": "no_new_risk", "kind": "baseline_no_new_risk"},
            {"name": "a", "kind": "policy", "config": variant_a.to_dict()},
            {"name": "b", "kind": "policy", "config": variant_b.to_dict()},
        ]}
    with setup["storage"]:
        result = service.run_experiment_flow(
            account=setup["account"], policy=setup["policy"],
            date_from="2026-11-18", date_to="2026-11-18", confirm=True,
            lock_path=setup["lock"], db_path=setup["db_path"],
            calendar=setup["days"] + ["2026-11-18", "2026-11-19"],
            study=study, **setup["readers"])
        replay = bs_advice.replay_bs_experiment(result["id"])
        pack_study = bs_advice.build_experiment_pack(
            account=setup["account"], policy=setup["policy"],
            date_from="2026-11-18", date_to="2026-11-18",
            db_path=setup["db_path"], study=study, **setup["readers"])
        pack_other = bs_advice.build_experiment_pack(
            account=setup["account"], policy=setup["policy"],
            date_from="2026-11-18", date_to="2026-11-18",
            db_path=setup["db_path"],
            study={**study, "study_id": "s2"}, **setup["readers"])
    by_variant = result["metrics"]["comparison"]["study"]["by_variant"]
    assert set(by_variant) == {"no_new_risk", "a", "b"}
    assert by_variant["a"]["config_hash"] == variant_a.content_hash
    assert by_variant["a"]["gates"] is not None
    assert by_variant["a"]["funding"] is not None
    assert by_variant["a"]["comparable"] is True
    assert by_variant["no_new_risk"]["kind"] == "baseline_no_new_risk"
    assert result["study_digest"]
    assert result["metrics"]["bs"]["tail_threshold_source"] == "study_explicit"
    assert set(replay["metrics"]["comparison"]["study"]["by_variant"]) == \
        set(by_variant)
    assert replay["study_digest"] == result["study_digest"]
    assert pack_study["digest"] != pack_other["digest"], \
        "研究身份必须包含完整 study，只用 base pack 会碰撞幂等旧结果"


def test_cli_exposes_study_and_enhanced_flags():
    import argparse
    from unittest.mock import patch

    from dragon_quant import cli

    captured = {}
    original = argparse.ArgumentParser.parse_args

    def fake_parse_args(self, args=None, namespace=None):
        captured["parser"] = self
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = fake_parse_args
    try:
        with patch("sys.argv", ["dragon-quant", "-h"]):
            with pytest.raises(SystemExit):
                cli.main()
    finally:
        argparse.ArgumentParser.parse_args = original
    parser = captured["parser"]
    action = parser.parse_args([
        "research", "bs-experiment", "run", "--account-file", "a.json",
        "--bs-policy", "p.json", "--from", "2026-11-18", "--to", "2026-11-18",
        "--study-file", "s.json", "--enhanced-experiment-id", "7"])
    assert action.study_file == "s.json"
    assert action.enhanced_experiment_id == "7"


def test_parse_study_strict_schema_and_digest_stability():
    from dragon_quant.research.bs_advice.study import StudyError, parse_study

    config = teaching_fixture()
    payload = {
        "schema_version": 1, "study_id": "strict",
        "evaluation_cutoff": "2026-11-18", "tail_threshold_pct": 5.0,
        "splits": {"train": 0.5, "valid": 0.25, "horizon_days": 1},
        "variants": [
            {"name": "base", "kind": "baseline_no_new_risk"},
            {"name": "p", "kind": "policy", "config": config.to_dict()}]}
    first = parse_study(payload)
    second = parse_study(payload)
    assert first.digest == second.digest
    assert [variant.name for variant in first.variants] == ["base", "p"]

    for mutate in (
        lambda data: data.update({"unknown": 1}),
        lambda data: data["variants"].append({"name": "p", "kind": "policy",
                                              "config": config.to_dict()}),
        lambda data: data["variants"].append({"name": "x", "kind": "nope"}),
        lambda data: data["variants"].append({"name": "b2",
                                              "kind": "baseline_no_new_risk",
                                              "config": config.to_dict()}),
        lambda data: data.update({"splits": {"train": 2.0}}),
        lambda data: data.update({"splits": {"unknown": 1}}),
        lambda data: data.update({"tail_threshold_pct": True}),
        lambda data: data.update({"variants": [
            {"name": "b", "kind": "baseline_no_new_risk"}]}),
        lambda data: data["variants"].append("not-an-object"),
    ):
        data = {**payload, "variants": [dict(item) for item in payload["variants"]],
                "splits": dict(payload["splits"])}
        mutate(data)
        with pytest.raises(StudyError):
            parse_study(data)


def test_enhanced_distinct_evidence_is_actually_evaluated(tmp_path,
                                                         monkeypatch):
    setup = _study_setup(tmp_path, monkeypatch)
    days = setup["days"] + ["2026-11-18", "2026-11-19"]
    local = bs_advice.build_experiment_pack(
        account=setup["account"], policy=setup["policy"],
        date_from="2026-11-18", date_to="2026-11-18",
        db_path=setup["db_path"], calendar=days, **setup["readers"])
    _minute(setup["db_path"], "2026-11-18", start="10:02:00",
            end="10:03:00", close=10.2)
    enhanced = bs_advice.build_experiment_pack(
        account=setup["account"], policy=setup["policy"],
        date_from="2026-11-18", date_to="2026-11-18",
        db_path=setup["db_path"], calendar=days, **setup["readers"])
    assert enhanced["digest"] != local["digest"]
    assert len(enhanced["events"]) > len(local["events"])
    result = bs_advice.run_bs_experiment(
        account=setup["account"], policy=setup["policy"],
        date_from="2026-11-18", date_to="2026-11-18", pack=local,
        enhanced_pack={"freeze": enhanced["freeze"]})
    assert result["data_modes"]["enhanced_frozen"]["status"] == "evaluated"
    assert result["data_modes"]["enhanced_frozen"]["reason"] is None
    effect = result["metrics"]["capability_effect"]
    assert effect["same_policy"] is True and effect["same_seed"] is True
    assert effect["same_cost"] is True and effect["same_calendar"] is True
    assert effect["same_research_window"] is True
    assert effect["same_resources"] is True and effect["same_universe"] is True
    assert effect["evidence_distinct"] is True
    assert effect["event_count_delta"] > 0
    # 机制可达；未形成合格标签时差值必须 null，不能伪造能力收益。
    assert effect["status"] in ("evaluated", "insufficient_data")
    assert effect["delta_net_return_pct"] is None
    assert result["metrics"]["comparison"]["same_frozen_pack"] is True


def test_enhanced_resource_mismatch_and_gap_only_are_negative(tmp_path,
                                                             monkeypatch):
    from dragon_quant.review_account.strategy.event_inputs import hash_payload

    setup = _study_setup(tmp_path, monkeypatch)
    days = setup["days"] + ["2026-11-18", "2026-11-19"]
    local = bs_advice.build_experiment_pack(
        account=setup["account"], policy=setup["policy"],
        date_from="2026-11-18", date_to="2026-11-18",
        db_path=setup["db_path"], calendar=days, **setup["readers"])
    # 同 seed 但研究日历/资源不同 → 不可比。
    other_calendar = bs_advice.build_experiment_pack(
        account=setup["account"], policy=setup["policy"],
        date_from="2026-11-18", date_to="2026-11-18",
        db_path=setup["db_path"], calendar=days + ["2026-11-20"],
        **setup["readers"])
    mismatch = bs_advice.run_bs_experiment(
        account=setup["account"], policy=setup["policy"],
        date_from="2026-11-18", date_to="2026-11-18", pack=local,
        enhanced_pack={"freeze": other_calendar["freeze"]})
    assert mismatch["data_modes"]["enhanced_frozen"]["reason"] == \
        "enhanced_resources_not_comparable"
    assert mismatch["metrics"]["capability_effect"]["status"] == \
        "insufficient_data"
    # 只有 gap 变化（事件/证据清单/母集相同）→ 不算能力证据不同。
    gap_freeze = {key: value for key, value in local["freeze"].items()}
    gap_freeze["execution_gaps"] = [{"code": "600001", "reason": "gap_only"}]
    gap_freeze["digest"] = hash_payload(
        {key: value for key, value in gap_freeze.items() if key != "digest"})
    gap = bs_advice.run_bs_experiment(
        account=setup["account"], policy=setup["policy"],
        date_from="2026-11-18", date_to="2026-11-18", pack=local,
        enhanced_pack={"freeze": gap_freeze})
    assert gap["data_modes"]["enhanced_frozen"]["reason"] == \
        "enhanced_evidence_not_distinct"
    # 同 seed 但资源/日期/母集任一项不同 → 不可比。
    for field, value in (("order_ttl_ms", 1), ("participation", 0.5),
                         ("date_to", "2026-11-19"),
                         ("decision_universes", [])):
        mutated = {key: val for key, val in local["freeze"].items()}
        mutated[field] = value
        mutated["digest"] = hash_payload(
            {key: val for key, val in mutated.items() if key != "digest"})
        result = bs_advice.run_bs_experiment(
            account=setup["account"], policy=setup["policy"],
            date_from="2026-11-18", date_to="2026-11-18", pack=local,
            enhanced_pack={"freeze": mutated})
        assert result["data_modes"]["enhanced_frozen"]["reason"] == \
            "enhanced_resources_not_comparable", field


def test_risk_unknown_holding_is_null_not_zero():
    events = [{
        "trade_date": "2026-11-18", "timestamp": 1,
        "state": {"equity": 100000.0, "cash": 90000.0,
                  "batches": [{"qty": 1000, "cost_price": 10.0,
                               "initial_risk": 0.0, "mark": 10.0,
                               "market_value": 10000.0}],
                  "risk_book": {"total": 0.0, "unknown": 0.0,
                                "unknown_holdings": 1},
                  "orders": []},
        "fills": [], "recovery": {"active": False}}]
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=events,
        final_state={"equity": 100000.0, "closed_trades": [],
                     "trade_date": "2026-11-18"},
        trade_calendar=["2026-11-18"])
    assert result["risk_budget_used_peak"] is None
    assert result["risk_unknown"] is True
    assert result["risk_unknown_holdings"] == 1
    assert result["risk_budget_used_peak_lower_bound"] == 0.0
    assert "risk_unknown" in result["reasons"]
    assert result["funding"]["position_market_value_end"] == 10000.0


def test_risk_snapshot_missing_is_unknown_not_zero():
    result = metrics.compute_metrics(
        initial_cash=100000.0,
        events=[{"trade_date": "2026-11-18", "state": {"equity": 100000.0,
                                                       "orders": []},
                 "fills": [], "recovery": {"active": False}}],
        final_state={"equity": 100000.0, "closed_trades": [],
                     "trade_date": "2026-11-18"},
        trade_calendar=["2026-11-18"])
    assert result["risk_budget_used_peak"] is None
    assert result["risk_unknown"] is True
    assert "risk_snapshot_missing" in result["reasons"]


def test_recovery_active_days_dedupes_same_day_stages():
    events = [
        {"trade_date": "2026-11-18", "timestamp": 1,
         "state": {"equity": 100.0}, "recovery": {"active": True, "stage": 1}},
        {"trade_date": "2026-11-18", "timestamp": 2,
         "state": {"equity": 101.0}, "recovery": {"active": True, "stage": 2}},
        {"trade_date": "2026-11-19", "timestamp": 3,
         "state": {"equity": 102.0}, "recovery": {"active": True, "stage": 2}},
        {"trade_date": "2026-11-19", "timestamp": 4,
         "state": {"equity": 102.0}, "recovery": {"active": False}},
    ]
    result = metrics.compute_metrics(
        initial_cash=100.0, events=events,
        final_state={"equity": 102.0, "closed_trades": [],
                     "trade_date": "2026-11-19"},
        trade_calendar=["2026-11-18", "2026-11-19"])
    assert result["recovery_active_trade_days"] == 2, "同日多阶段只算一天"
    assert result["recovery_stage_trade_day_sum"] == 3
    stages = result["recovery_segments"]
    assert [item["stage"] for item in stages] == [1, 2]
    assert stages[0]["start_ts"] == 1 and stages[0]["end_ts"] == 1
    assert stages[1]["censored"] is False


def test_recovery_exposure_ignores_price_only_move():
    events = [
        {"trade_date": "2026-11-18", "timestamp": 1,
         "state": {"equity": 100000.0,
                   "batches": [{"qty": 1000, "cost_price": 10.0,
                                "mark": 10.0, "market_value": 10000.0}],
                   "risk_book": {"total": 0.0, "unknown_holdings": 0},
                   "orders": []},
         "recovery": {"active": True, "stage": 1}},
        {"trade_date": "2026-11-19", "timestamp": 2,
         "state": {"equity": 110000.0,
                   "batches": [{"qty": 1000, "cost_price": 10.0,
                                "mark": 11.0, "market_value": 11000.0}],
                   "risk_book": {"total": 0.0, "unknown_holdings": 0},
                   "orders": []},
         "recovery": {"active": True, "stage": 1}},
    ]
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=events,
        final_state={"equity": 110000.0, "closed_trades": [],
                     "trade_date": "2026-11-19"},
        trade_calendar=["2026-11-18", "2026-11-19"])
    stage = result["recovery_segments"][0]
    assert stage["equity_return_pct"] == pytest.approx(10.0)
    assert stage["exposure_change_pct"] == pytest.approx(0.0), \
        "同 qty 价格变动只改变权益，不是再暴露"
    assert stage["position_qty_start"] == stage["position_qty_end"] == 1000


def test_recovery_exposure_tracks_real_add():
    events = [
        {"trade_date": "2026-11-18", "timestamp": 1,
         "state": {"equity": 100000.0,
                   "batches": [{"qty": 1000, "cost_price": 10.0,
                                "mark": 10.0, "market_value": 10000.0}],
                   "risk_book": {"total": 0.0, "unknown_holdings": 0},
                   "orders": []},
         "recovery": {"active": True, "stage": 1}},
        {"trade_date": "2026-11-19", "timestamp": 2,
         "state": {"equity": 110000.0,
                   "batches": [{"qty": 1500, "cost_price": 10.0,
                                "mark": 11.0, "market_value": 16500.0}],
                   "risk_book": {"total": 0.0, "unknown_holdings": 0},
                   "orders": []},
         "recovery": {"active": True, "stage": 1}},
    ]
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=events,
        final_state={"equity": 110000.0, "closed_trades": [],
                     "trade_date": "2026-11-19"},
        trade_calendar=["2026-11-18", "2026-11-19"])
    stage = result["recovery_segments"][0]
    assert stage["position_qty_start"] == 1000
    assert stage["position_qty_end"] == 1500
    assert stage["exposure_pct_start"] == pytest.approx(10.0)
    assert stage["exposure_pct_end"] == pytest.approx(15.0)
    assert stage["exposure_change_pct"] == pytest.approx(5.0)


def test_unfilled_cancelled_order_is_not_zero_ratio():
    events = [{"trade_date": "2026-11-18", "timestamp": 1,
               "state": {"equity": 100000.0,
                         "risk_book": {"total": 0.0, "unknown_holdings": 0},
                         "orders": [{"order_id": "c1", "qty": 100,
                                     "filled_qty": 0, "status": "cancelled",
                                     "reserved_risk": 0.0,
                                     "reserved_amount": 0.0}]},
               "fills": [], "recovery": {"active": False}}]
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=events,
        final_state={"equity": 100000.0, "closed_trades": [],
                     "trade_date": "2026-11-18"},
        trade_calendar=["2026-11-18"])
    assert result["unfilled_orders"] == 1
    assert result["unfilled_ratio"] == 1.0
    assert result["unfilled_quantity_ratio"] == 1.0
    assert result["cancelled_orders"] == 1
    assert result["open_pending_orders"] == 0


def test_caller_maturity_true_cannot_qualify_effect():
    closed = [{"closed_trade_id": "t1", "realized_net": 10.0,
               "closed_event": _ms("2026-11-18", hour=10)}]
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=[],
        final_state={"equity": 100000.0, "closed_trades": closed},
        maturity=True)
    assert result["effect_status"] == "insufficient_data"
    assert "maturity_unverified" in result["reasons"]


def test_alternatives_sign_and_qualification_are_unified():
    closed = [{"closed_trade_id": "t1", "realized_net": 5.0,
               "closed_event": _ms("2026-11-18", hour=10)}]
    report = metrics.maturity_report(
        closed, ["2026-11-18"],
        evaluation_cutoff="2026-11-18T23:59:59+08:00",
        research_start="2026-11-18")
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=[],
        final_state={"equity": 100000.0, "closed_trades": closed},
        maturity_report_data=report,
        alternatives={"alt": {"final_equity": 110000.0,
                              "status": "evaluated", "executable": True,
                              "comparable": True}})
    alt = result["alternatives"]["alt"]
    assert alt["qualified_missed_amount"] == pytest.approx(-10000.0)
    assert alt["descriptive_equity_diff"] == pytest.approx(10000.0)
    # 不可执行/不可比时不留 qualified 数值，仅保留 descriptive。
    blocked = metrics.compute_metrics(
        initial_cash=100000.0, events=[],
        final_state={"equity": 100000.0, "closed_trades": closed},
        maturity_report_data=report,
        alternatives={"alt": {"final_equity": 110000.0,
                              "status": "insufficient_data",
                              "executable": False, "comparable": True}})
    assert blocked["alternatives"]["alt"]["qualified_missed_amount"] is None
    assert blocked["alternatives"]["alt"]["descriptive_equity_diff"] == \
        pytest.approx(10000.0)


def test_enhanced_runs_for_every_explicit_policy(tmp_path, monkeypatch):
    from dragon_quant.review_account.strategy.bs_policy import teaching_fixture

    setup = _study_setup(tmp_path, monkeypatch)
    days = setup["days"] + ["2026-11-18", "2026-11-19"]
    variant_a = teaching_fixture(profile_id="p1", profile_version="r1",
                                 fixture=False)
    variant_b = teaching_fixture(profile_id="p2", profile_version="r1",
                                 fixture=False)
    study = {"schema_version": 1, "study_id": "cap-policies", "variants": [
        {"name": "no_new_risk", "kind": "baseline_no_new_risk"},
        {"name": "p1", "kind": "policy", "config": variant_a.to_dict()},
        {"name": "p2", "kind": "policy", "config": variant_b.to_dict()}]}
    local = bs_advice.build_experiment_pack(
        account=setup["account"], policy=setup["policy"],
        date_from="2026-11-18", date_to="2026-11-18", db_path=setup["db_path"],
        calendar=days, study=study, **setup["readers"])
    _minute(setup["db_path"], "2026-11-18", start="10:02:00",
            end="10:03:00", close=10.2)
    enhanced = bs_advice.build_experiment_pack(
        account=setup["account"], policy=setup["policy"],
        date_from="2026-11-18", date_to="2026-11-18", db_path=setup["db_path"],
        calendar=days, study=study, **setup["readers"])
    result = bs_advice.run_bs_experiment(
        account=setup["account"], policy=setup["policy"],
        date_from="2026-11-18", date_to="2026-11-18", pack=local,
        enhanced_pack={"freeze": enhanced["freeze"]})
    by_variant = result["metrics"]["comparison"]["study"]["by_variant"]
    for name in ("p1", "p2"):
        capability = by_variant[name]["capability"]
        assert capability is not None, f"{name} 必须有能力条件结果"
        assert capability["same_resources"] is True
        assert capability["same_universe"] is True
        assert capability["same_research_window"] is True
        assert capability["evidence_distinct"] is True


def test_unfilled_statuses_full_reject_and_zero_orders():
    events = [{"trade_date": "2026-11-18", "timestamp": 1,
               "state": {"equity": 100000.0,
                         "risk_book": {"total": 0.0, "unknown_holdings": 0},
                         "orders": [
                             {"order_id": "f1", "qty": 100, "filled_qty": 100,
                              "status": "filled", "reserved_risk": 0.0,
                              "reserved_amount": 0.0},
                             {"order_id": "r1", "qty": 100, "filled_qty": 0,
                              "status": "rejected", "reserved_risk": 0.0,
                              "reserved_amount": 0.0,
                              "reason": "insufficient_cash"}]},
               "fills": [], "recovery": {"active": False}}]
    result = metrics.compute_metrics(
        initial_cash=100000.0, events=events,
        final_state={"equity": 100000.0, "closed_trades": [],
                     "trade_date": "2026-11-18"},
        trade_calendar=["2026-11-18"])
    assert result["order_status_counts"] == {"filled": 1, "rejected": 1}
    assert result["unfilled_orders"] == 1
    assert result["unfilled_ratio"] == 0.5
    assert result["rejected_orders"] == 1
    assert result["unfilled_reasons"] == {"insufficient_cash": 1}
    empty = metrics.compute_metrics(
        initial_cash=100000.0,
        events=[{"trade_date": "2026-11-18", "state": {"equity": 100000.0,
                                                       "orders": [],
                                                       "risk_book": {
                                                           "total": 0.0,
                                                           "unknown_holdings": 0}},
                 "fills": [], "recovery": {"active": False}}],
        final_state={"equity": 100000.0, "closed_trades": [],
                     "trade_date": "2026-11-18"},
        trade_calendar=["2026-11-18"])
    assert empty["order_denominator"] == 0
    assert empty["unfilled_orders"] == 0
    assert empty["unfilled_ratio"] is None
