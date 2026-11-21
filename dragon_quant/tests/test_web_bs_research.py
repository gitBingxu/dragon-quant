"""W4b Web：BS 只读建议/实验端点的结构化 payload 与错误语义。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from dragon_quant.review_account.strategy.bs_policy import teaching_fixture
from web_ui.backend.app import create_app
from web_ui.backend.settings import DashboardSettings


def _client(tmp_path):
    return TestClient(create_app(DashboardSettings(
        workspace_path=str(tmp_path / "ws"), workspace_writes_enabled=False)),
        base_url="http://localhost")


def _account():
    return {"schema_version": 1, "currency": "CNY", "shares_unit": "share",
            "as_of": "2026-11-17T16:00:00+08:00", "cash": 50000.0,
            "positions": []}


def test_bs_advice_accepts_structured_payload_without_paths(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/research/bs-advice", json={
        "account": _account(), "policy": teaching_fixture().to_dict(),
        "cutoff": "2026-11-18T10:00:00+08:00"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["persisted"] is False
    assert data["orders_placed"] is False
    assert "policy" in data
    view = response.json()["view"]
    assert view["kind"] == "advice"
    assert view["input"]["orders_placed"] is False
    assert view["preference"]["is_ev"] is False
    assert "missing" in view["capabilities"]
    assert view["denominators"]["universe_codes"] >= 0


def test_bs_advice_rejects_invalid_policy(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/research/bs-advice", json={
        "account": _account(), "policy": {"schema_version": 999},
        "cutoff": "2026-11-18T10:00:00+08:00"})
    assert response.status_code == 400


def test_bs_experiment_requires_confirmation_and_lists_empty(tmp_path):
    client = _client(tmp_path)
    run = client.post("/api/research/bs-experiments", json={
        "account": _account(), "policy": teaching_fixture().to_dict(),
        "date_from": "20261118", "date_to": "20261118", "confirm": False})
    assert run.status_code == 400
    assert run.json()["error"] == "confirmation_required"
    listing = client.get("/api/research/bs-experiments")
    assert listing.status_code == 200
    assert listing.json()["data"] == []
    assert listing.json()["view"]["kind"] == "experiment_list"
    assert listing.json()["view"]["items"] == []


def test_bs_ledger_missing_returns_404(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/research/bs-ledger",
                          params={"ledger_id": "bsrun_missing"})
    assert response.status_code == 404


def test_bs_review_invalid_account_maps_to_http_400(tmp_path):
    client = _client(tmp_path)
    bad = {**_account(), "currency": "USD"}
    response = client.post("/api/research/bs-review", json={
        "account": bad, "policy": teaching_fixture().to_dict(),
        "date_from": "20261118", "date_to": "20261118"})
    assert response.status_code == 400
    assert response.json()["error"] == "account_file_invalid"


def test_bs_experiment_list_rejects_bad_limit(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/research/bs-experiments",
                      params={"limit": 0}).status_code == 400
    assert client.get("/api/research/bs-experiments",
                      params={"limit": 500}).status_code == 400


def test_bs_experiment_accepts_structured_study_and_local_enhanced_id(tmp_path):
    client = _client(tmp_path)
    study = {"schema_version": 1, "study_id": "api-study", "variants": [
        {"name": "base", "kind": "baseline_no_new_risk"},
        {"name": "p", "kind": "policy",
         "config": teaching_fixture().to_dict()}]}
    # 结构化 study 被请求模型接受；未确认仍先触发确认门禁。
    run = client.post("/api/research/bs-experiments", json={
        "account": _account(), "policy": teaching_fixture().to_dict(),
        "date_from": "20261118", "date_to": "20261118", "confirm": False,
        "study": study, "enhanced_experiment_id": 7})
    assert run.status_code == 400
    assert run.json()["error"] == "confirmation_required"
    # 本地已存 ID 不存在 → 显式 404，不读取任意服务器路径。
    missing = client.post("/api/research/bs-experiments", json={
        "account": _account(), "policy": teaching_fixture().to_dict(),
        "date_from": "20261118", "date_to": "20261118", "confirm": True,
        "enhanced_experiment_id": 999})
    assert missing.status_code == 404
    assert missing.json()["error"] == "experiment_not_found"


def _synthetic_report():
    primary = {
        "engineering_status": "completed",
        "effect_status": "insufficient_data",
        "net_return_pct": None,
        "final_equity": 100000.0,
        "max_drawdown_pct": 4.0,
        "max_drawdown_duration_trade_days": 2,
        "tail_loss_count": None,
        "risk_budget_used_peak": None,
        "unfilled_orders": 1,
        "unfilled_ratio": 0.5,
        "qualified_sample_n": 0,
        "definitions": {
            "net_return_pct": {"definition": "净收益定义", "denominator": "期初权益"},
            "tail_loss_count": {"definition": "尾部定义", "denominator": "合格样本"},
        },
        "denominator": {"events": 3, "qualified_closed_trades": 0,
                        "orders": 2},
        "maturity": {"status": "insufficient_data",
                     "evaluation_cutoff": "2026-11-18T23:59:59+08:00",
                     "exclusions": {"after_cutoff": 1}, "blockers": []},
        "splits": {"mode": "fixed_parameter_split", "purge_days": 1,
                   "purge_basis": "label_end_calendar",
                   "training_performed": False, "windows": {"test": ["a", "b"]}},
        "slippage": {"basis": "model_estimate_only"},
        "execution_support": {"status": "capability_missing"},
        "alternatives": {
            "no_new_risk": {"status": "evaluated", "executable": True,
                            "comparable": True, "qualified": False,
                            "qualified_missed_amount": None,
                            "descriptive_equity_diff": 1000.0},
        },
        "recovery_segments": [],
        "notes": ["effect_status_requires_mature_labels"],
    }
    return {
        "id": 7, "status": "completed", "pack_digest": "d" * 64,
        "study_digest": "s" * 64, "metrics_version": "bs_metrics/5",
        "expected_events": 3, "completed_events": 3, "watermark": 3,
        "data_modes": {"enhanced_frozen": {"status": "insufficient_data",
                                         "reason": "enhanced_evrovided"}},
        "metrics": {
            "bs": primary,
            "baseline_no_new_risk": {**primary, "final_equity": 99000.0},
            "enhanced_frozen": None,
            "capability_effect": {"status": "insufficient_data",
                                  "reason": "enhanced_evidence_not_provided"},
            "comparison": {"study": {
                "study_id": "s1", "study_digest": "s" * 64, "spec": {},
                "by_variant": {
                    "base": {"kind": "policy",
                             "engineering_status": "completed",
                             "effect_status": "insufficient_data",
                             "final_equity": 100000.0, "fills": 1,
                             "executable": True, "comparable": True,
                             "qualified": False, "maturity": primary["maturity"],
                             "capability": {"status": "insufficient_data",
                                            "used_added": [], "missing_removed": [],
                                            "evidence_distinct": True}},
                    "no_new_risk": {"kind": "baseline_no_new_risk",
                                    "engineering_status": "completed",
                                    "effect_status": "insufficient_data",
                                    "final_equity": 99000.0, "fills": 0,
                                    "executable": True, "comparable": True,
                                    "qualified": False}},
            }},
        },
    }


def test_presenters_separate_engineering_from_effect_and_units():
    from web_ui.backend.presenters import bs_research_view as view

    experiment = view.present_experiment({"id": 7, "report": _synthetic_report()})
    assert experiment["kind"] == "experiment"
    assert experiment["engineering_status"] == "completed"
    assert experiment["effect_status"] == "insufficient_data"
    assert "证据不足" in experiment["effect_label"]
    rows = {row["key"]: row for row in experiment["metrics"]["primary"]}
    assert rows["net_return_pct"]["display"] == "待证据", "null 不显示为 0"
    assert rows["net_return_pct"]["unit"] == "%"
    assert rows["tail_loss_count"]["sample_n"] == 0
    assert rows["net_return_pct"]["maturity_cutoff"] == \
        "2026-11-18T23:59:59+08:00"
    assert experiment["metrics"]["maturity"]["exclusions"] == \
        {"after_cutoff": 1}
    matrix = experiment["matrix"]["rows"]
    by_name = {row["name"]: row for row in matrix}
    assert by_name["no_new_risk"]["effect_label"].endswith("效果证据不足")
    assert by_name["base"]["capability"]["evidence_distinct"] is True
    alternative = experiment["alternatives"][0]
    assert alternative["qualified_missed_amount"] is None
    assert alternative["label"].startswith("描述性")


def test_present_ledger_and_advice_views_expose_status_and_denominators():
    from web_ui.backend.presenters import bs_research_view as view

    ledger = view.present_ledger({
        "ledger_id": "bsrun_x", "run_status": {
            "status": "failed", "expected_events": 5, "completed_events": 2,
            "watermark": 2, "failure_reason": "boom", "failed_cursor": 2},
        "batches": [], "orders": [], "fills": [],
        "history": {"records": [{"timestamp": 1, "trade_date": "2026-11-18",
                                 "status": "applied", "decided": True,
                                 "decision_record": {"selected_plan_id": "p"},
                                 "execution_record": {"rejected": []},
                                 "state": {"cash": 1.0, "equity": 1.0}}],
                    "next_cursor": "c1", "has_more": True}})
    assert ledger["run"]["is_partial"] is True
    assert ledger["run"]["resume_available"] is True
    assert ledger["events"][0]["selected_plan_id"] == "p"
    assert ledger["page"]["has_more"] is True

    advice = view.present_advice({
        "status": "restricted", "cutoff": "2026-11-18T10:00:00+08:00",
        "strategy_id": "builtin:bs_account_decision",
        "capabilities": {"usable_capabilities": ["daily"],
                         "missing_capabilities": ["intraday_bars"]},
        "restrictions": [{"code": "intraday_bars_missing"}],
        "protections": [{"rule_id": "p1"}], "decision": None,
        "policy": {"provided": True,
                   "normalized": {"preference": {"rho": 0.6}}},
        "universe": {"items": [1, 2]}, "rejected": [],
        "persisted": False, "orders_placed": False})
    assert advice["capabilities"]["usability"] == "受限"
    assert advice["denominators"]["universe_codes"] == 2
    assert advice["preference"]["is_ev"] is False
    assert advice["preference"]["ev_qualified"] is False
    assert advice["restrictions"][0]["reason_label"].startswith("缺少分钟观测")


def test_present_advice_probability_uses_real_build_and_domain_flag(
        tmp_path, monkeypatch):
    from dragon_quant.infra import calibration
    from dragon_quant.research import bs_advice
    from dragon_quant.research.bs_advice import assembly
    from dragon_quant.tests.test_bs_advice import (
        CUTOFF, _account_payload, _bars, _config,
    )
    from web_ui.backend.presenters import bs_research_view as view

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    config = _config()
    db_path = tmp_path / "assets.db"
    _bars(db_path, {"2026-11-18": (9.0, 1_000_000)})
    advice = bs_advice.build_advice(
        account_payload=_account_payload(config),
        policy_payload=config.to_dict(), cutoff=CUTOFF, db_path=db_path)
    probability = ((advice.get("decision") or {}).get("audit") or {}) \
        .get("probability") or {}
    assert probability.get("qualified") is False
    shown = view.present_advice(advice)
    pref = shown["preference"]
    assert pref["ev_qualified"] is False
    assert pref["ev_available"] is False
    assert pref["status_label"] == "不合格"
    assert "不合格" in pref["note"]
    assert any("缺少概率样本" in item for item in pref["reasons_labels"])
    assert "概率样本合格" not in pref["note"]
    assert shown["plans"]["abandoned"] == [] or all(
        item["reason_summary"] != item["code"]
        for item in shown["plans"]["abandoned"])


def test_present_ledger_matches_real_account_ledger_history(tmp_path):
    from dragon_quant.review_account.ledger import AccountLedger
    from dragon_quant.review_account.strategy import bs_policy as bs
    from dragon_quant.tests.test_bs_g3_real_chain import (
        DAY2, DAY4, T2, T4, _add_event, _engine, _env, _probe_event,
        _revoke_decide, _row, _Event, _ref,
    )
    from web_ui.backend.presenters import bs_research_view as view

    with _env(tmp_path):
        config = bs.teaching_fixture()
        ledger = AccountLedger("c3-ledger")
        ledger.create(initial_cash=100_000.0, strategy_ref=_ref(config))
        engine = _engine(ledger, config)
        _probe_event(engine, config)
        engine.process_event(_Event(T2, {"600001": _row(T2, 10.1)}, day=DAY2))
        _add_event(engine, config)
        engine.process_event(
            _Event(T4, {"600001": _row(T4, 10.5, capacity=250)}, day=DAY4))
        _revoke_decide(engine, config, ts=T4 + 60_000, day=DAY4, price=8.0,
                       market_allowed=True, regime="trend_up")
        page = ledger.read_history(limit=50)
        run = ledger.run_status()
        payload = {"ledger_id": "c3-ledger", "run_status": run,
                   "history": page.to_dict(), "batches": ledger.query_batches(),
                   "orders": ledger.query_orders(), "fills": ledger.query_fills()}

    records = page.to_dict()["records"]
    assert records and records[0]["event_time"]
    decided = [item for item in records if item.get("decision")]
    assert decided, "真实 ledger history 必须有 decision"
    shown = view.present_ledger(payload)
    events = shown["events"]
    assert any(event["decided"] and event["selected_plan_id"] is None
               or event["decided"] for event in events)
    assert all(event["event_time"] for event in events)
    assert any(event["fills"] for event in events), "必须有实际成交"
    assert any(event["orders"] for event in events)
    assert any(event["intents"] for event in events)
    assert all(event["state_available"] is False for event in events)
    assert any("初始化/无决策" in event["status"] or event["decided"]
               for event in events)
    # 真实代码不得把股票代码当拒绝原因。
    for event in events:
        for rejected in event["rejected"]:
            assert rejected["reason_label"] != "600001"


def test_present_advice_rejected_reasons_are_labels_not_stock_code():
    from web_ui.backend.presenters import bs_research_view as view

    shown = view.present_advice({
        "status": "restricted", "decision": {
            "selected_plan_id": None,
            "rejected": [{"code": "600001",
                          "reasons": ["history_auction_path_missing",
                                      "evidence_not_usable:entry:auction_confirm:600001:unverified"]}],
            "audit": {"probability": {"qualified": False,
                                      "status": "unqualified",
                                      "reasons": ["probability_missing"]}},
        },
        "policy": {"provided": True},
        "protections": [{"rule_id": "p1", "code": "price_stop"}],
        "restrictions": [{"code": "history_auction_path_missing"}],
        "universe": {"items": [1]}, "candidates": [1]})
    abandoned = shown["plans"]["abandoned"][0]
    assert abandoned["code"] == "600001"
    assert abandoned["reason_summary"] != "600001"
    assert "缺少历史竞价路径" in abandoned["reason_summary"]
    assert any("证据不可用" in item for item in abandoned["reasons_labels"])
    assert shown["restrictions"][0]["reason_label"].startswith("缺少历史竞价路径")
    assert shown["preference"]["reasons_labels"] == ["缺少概率样本"]
    assert shown["preference"]["ev_available"] is False


def test_fixture_root_rejects_repo_and_accepts_tmp(tmp_path):
    from pathlib import Path

    import pytest

    from web_ui.c3_bs_fixture import _temporary_root

    with pytest.raises(SystemExit):
        _temporary_root(Path.cwd())
    target = _temporary_root(tmp_path / "dq-c3-safe")
    assert str(target).startswith(("/private/tmp", "/tmp", "/private/var"))


def test_capability_labels_cover_known_enums_and_ledger_sellable_adapter():
    from web_ui.backend.presenters import bs_research_view as view

    shown = view.present_advice({
        "status": "restricted", "decision": None, "policy": {"provided": True},
        "capabilities": {"missing_capabilities": [
            "account_state", "auction_final", "auction_path",
            "market_context", "probability_sample", "tradability"]},
        "restrictions": [], "universe": {"items": [1]}})
    labels = shown["capabilities"]["missing_labels"]
    assert all("未识别能力" not in item for item in labels)
    assert "账户状态" in labels and "概率样本" in labels

    ledger = view.present_ledger({
        "ledger_id": "x", "run_status": {"status": "completed"},
        "batches": [{"batch_id": "b1", "code": "600001"},
                    {"batch_id": "b2", "code": "600001"}],
        "final_state": {"batches": [{"batch_id": "b1", "sellable": 0}]},
        "orders": [], "fills": [], "history": {"records": []}})
    batches = {item["batch_id"]: item for item in ledger["batches"]}
    assert batches["b1"]["sellable_qty"] == 0, "0 可卖必须显示 0"
    assert "sellable_qty" not in batches["b2"], "未知不猜"
