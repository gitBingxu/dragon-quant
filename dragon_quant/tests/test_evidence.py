"""W1 证据层：PIT 选择、冻结重放、完整性、校准门禁与能力缺口。

覆盖 main 复现的 E1/E2/E3：
- E1 深只读、反序列化重算 usable/指纹、篡改（时间/价格/质量/usable/hash/schema）拒绝；
- E2 总 quality 不能绕过单位/内容校准；竞价虚拟末笔不能冒充终态；
- E3 能力检查母集来自冻结请求，截断/未冻结范围/请求外范围不会意外 usable。
"""

from __future__ import annotations

import copy
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import patch

import pytest

from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.assets import observations as store
from dragon_quant.review_account import evidence
from dragon_quant.tests.support import patch_storage_dbs

NOW = "2026-11-18T09:30:00+08:00"
LATER = "2026-11-18T10:00:00+08:00"
EVENING = "2026-11-18T16:00:00+08:00"
MINUTE_END = "2026-11-18T09:31:00+08:00"

AUCTION_ITEMS = {"items": [{"time": "09:20:00", "price": 10.0,
                           "matched": 100, "unmatched": 50}]}
AUCTION_UNITS = {"price": "verified", "matched": "verified",
                 "unmatched": "verified"}
FINAL_PAYLOAD = {"price": 10.0, "amount": 1000.0, "matched": 100,
                 "unmatched": 50, "terminal_source": "exchange_final"}
FINAL_UNITS = {"price": "verified", "amount": "verified",
               "matched": "verified"}
MEMBERSHIP_PAYLOAD = {"codes": ["600001", "600002"],
                      "page_completeness": "complete"}

# pytest fixtures 明确 fixture=True，并记录依据；生产默认清单不使用这些条目。
VERIFIED_AUCTION = evidence.SourceSemantics(
    source="tdx", kind="auction_path", time_semantics="verified",
    unit_semantics="verified", content_semantics="verified",
    quality="semantics_verified", version="fixture-1",
    evidence="pytest fixture: timestamps+units+fields cross-checked",
    fixture=True)
VERIFIED_FINAL = evidence.SourceSemantics(
    source="tdx", kind="auction_final", time_semantics="verified",
    unit_semantics="verified", content_semantics="verified",
    quality="semantics_verified", version="fixture-1",
    evidence="pytest fixture: exchange final terminal source",
    fixture=True)
VERIFIED_MINUTE = evidence.SourceSemantics(
    source="tdx", kind="1min", time_semantics="verified",
    unit_semantics="verified", content_semantics="verified",
    quality="semantics_verified", version="fixture-1",
    evidence="pytest fixture: right-edge+units verified", fixture=True)
VERIFIED_DAILY = evidence.SourceSemantics(
    source="tdx", kind="daily", time_semantics="verified",
    unit_semantics="verified", content_semantics="verified",
    quality="semantics_verified", version="fixture-1",
    evidence="pytest fixture: unadjusted daily", fixture=True)
VERIFIED_MEMBERSHIP = evidence.SourceSemantics(
    source="tdx", kind="sector_membership", time_semantics="verified",
    unit_semantics="unverified", content_semantics="verified",
    quality="semantics_verified", version="fixture-1",
    evidence="pytest fixture: complete membership page", fixture=True)


@contextmanager
def _sandbox(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    with patch_storage_dbs(str(tmp_path / "dragon.db"),
                           str(tmp_path / "assets.db")):
        conn = assets_db._connect()
        assets_db._ensure_schema(conn)
        conn.close()
        yield str(tmp_path / "assets.db")


def _obs(payload, *, available_at=NOW, obs_type="auction_path", code="600001",
         source="tdx", quality="semantics_unverified", valid_until=None,
         trade_date="2026-11-18", units=None):
    return store.append_observation(
        trade_date=trade_date, market="sh", code=code, obs_type=obs_type,
        source=source, payload=payload, received_at=available_at,
        available_at=available_at,
        units=dict(units if units is not None else AUCTION_UNITS),
        quality=quality, valid_until=valid_until)


def _bar(interval_end=MINUTE_END, *, available_at=NOW, code="600001",
         close=10.0, time_semantics="unverified", completeness="final",
         period="1min", unit_semantics="verified", calibration=True):
    from dragon_quant.infra.calibration import (
        PRODUCTION_CALIBRATIONS, binding_for,
    )

    bind = (binding_for(PRODUCTION_CALIBRATIONS[0].calibration_id,
                        calibrated_at=available_at, source="tdx",
                        period=period) if calibration else None)
    return store.append_bar(
        trade_date="2026-11-18", market="sh", code=code, period=period,
        interval_start=interval_end, interval_end=interval_end, open=10.0,
        high=10.2, low=9.9, close=close, volume=1.0, amount=10.0,
        source="tdx", observed_at=available_at, available_at=available_at,
        completeness=completeness, time_semantics=time_semantics,
        unit_semantics=unit_semantics, calibration=bind)


def _auction_snapshot(tmp_path, **kwargs):
    with _sandbox(tmp_path):
        _obs(copy.deepcopy(AUCTION_ITEMS), **kwargs)
        return evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["auction_path"],
            calibrations=[VERIFIED_AUCTION])


def _need(capability="auction_path", **kwargs):
    kwargs.setdefault("scope", ("600001",))
    return evidence.EvidenceNeed(rule_id=kwargs.pop("rule_id", "r1"),
                                 capability=capability, **kwargs)


# ─── 基础 PIT / 冻结 ─────────────────────────────────────────────────────────


def test_pit_snapshot_selects_the_last_available_revision_not_the_latest(tmp_path):
    with _sandbox(tmp_path):
        _obs({"items": [{"time": "09:20:00", "price": 10.0,
                         "matched": 1, "unmatched": 1}]})
        _obs({"items": [{"time": "09:21:00", "price": 10.5,
                         "matched": 2, "unmatched": 1}]}, available_at=LATER)
        early = evidence.build_snapshot(
            cutoff="2026-11-18T09:45:00+08:00", codes=["600001"],
            capabilities=["auction_path"], calibrations=[VERIFIED_AUCTION])
        late = evidence.build_snapshot(
            cutoff=EVENING, codes=["600001"], capabilities=["auction_path"],
            calibrations=[VERIFIED_AUCTION])
    assert len(early.facts) == 1
    assert early.facts[0].revision == 1
    assert early.facts[0].payload["items"][0]["price"] == 10.0
    assert late.facts[0].revision == 2
    assert late.facts[0].payload["items"][0]["price"] == 10.5
    with _sandbox(tmp_path):
        again = evidence.build_snapshot(
            cutoff="2026-11-18T09:45:00+08:00", codes=["600001"],
            capabilities=["auction_path"], calibrations=[VERIFIED_AUCTION])
    assert again.snapshot_id == early.snapshot_id


def test_valid_until_expired_fact_is_not_selected(tmp_path):
    with _sandbox(tmp_path):
        _obs(copy.deepcopy(AUCTION_ITEMS), available_at=NOW,
             valid_until="2026-11-18T09:40:00+08:00")
        snapshot = evidence.build_snapshot(
            cutoff=EVENING, codes=["600001"], capabilities=["auction_path"],
            calibrations=[VERIFIED_AUCTION])
    assert snapshot.facts == ()


def test_snapshot_replay_does_not_read_the_active_database(tmp_path):
    with _sandbox(tmp_path):
        _obs(copy.deepcopy(AUCTION_ITEMS))
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["auction_path"],
            calibrations=[VERIFIED_AUCTION])
        frozen = snapshot.to_dict()
        _obs({"items": [{"time": "09:22:00", "price": 11.0,
                         "matched": 3, "unmatched": 1}]},
             available_at="2026-11-18T11:00:00+08:00")
        replayed = evidence.EvidenceSnapshot.from_dict(frozen)
        need = _need()
        with patch.object(evidence.store, "select_observations_asof",
                          side_effect=AssertionError("must not read database")):
            check = evidence.check_need(replayed, need, now=LATER)
    assert replayed.snapshot_id == snapshot.snapshot_id
    assert replayed.to_dict() == frozen
    assert replayed.content_hash == snapshot.content_hash
    assert check.status == "usable"


def test_missing_database_and_uninitialized_are_distinct(tmp_path):
    def _no_create(create=True, path=None):
        if not create:
            return None
        raise AssertionError("证据层只读，不得建库")

    with patch("dragon_quant.infra.assets.db._connect", side_effect=_no_create):
        missing = evidence.build_snapshot(cutoff=LATER)
    assert missing.facts == ()
    assert missing.source_manifest["store_state"] == store.MISSING_DATABASE
    assert evidence.source_status(missing)["sources"]["tdx"]["state"] == \
        "no_data_local"

    assets_path = str(tmp_path / "assets.db")
    with patch_storage_dbs(str(tmp_path / "dragon.db"), assets_path):
        conn = sqlite3.connect(assets_path)
        conn.execute("CREATE TABLE prep_assets (trade_date TEXT, "
                     "asset_type TEXT, payload_json TEXT)")
        conn.commit()
        conn.close()
        uninitialized = evidence.build_snapshot(cutoff=LATER)
    assert uninitialized.facts == ()
    assert uninitialized.source_manifest["store_state"] == store.UNINITIALIZED


def test_corrupt_payload_raises_instead_of_looking_empty(tmp_path):
    with _sandbox(tmp_path):
        _obs(copy.deepcopy(AUCTION_ITEMS))
        conn = assets_db._connect()
        conn.execute("UPDATE market_observations SET payload_json = '{bad'")
        conn.commit()
        conn.close()
        with pytest.raises(evidence.EvidenceError):
            evidence.build_snapshot(cutoff=LATER, codes=["600001"])


# ─── E1：深只读与反序列化完整性 ──────────────────────────────────────────────


def test_nested_payload_is_read_only(tmp_path):
    snapshot = _auction_snapshot(tmp_path)
    fact = snapshot.facts[0]
    with pytest.raises(TypeError):
        fact.payload["items"] = []  # type: ignore[index]
    with pytest.raises(TypeError):
        fact.payload["items"][0]["price"] = 999  # type: ignore[index]
    with pytest.raises(TypeError):
        fact.units["price"] = "unverified"  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.source_manifest["cutoff"] = "x"  # type: ignore[index]


def test_exported_dict_mutation_does_not_touch_the_snapshot(tmp_path):
    snapshot = _auction_snapshot(tmp_path)
    exported = snapshot.to_dict()
    exported["facts"][0]["payload"]["items"][0]["price"] = 999
    exported["facts"][0]["usable"] = False
    exported["coverage"]["facts"] = 0
    assert snapshot.facts[0].payload["items"][0]["price"] == 10.0
    assert snapshot.facts[0].usable is True
    assert snapshot.coverage["facts"] == 1
    assert snapshot.content_hash == snapshot.content_hash
    replayed = evidence.EvidenceSnapshot.from_dict(snapshot.to_dict())
    assert replayed.content_hash == snapshot.content_hash


@pytest.mark.parametrize("mutation", [
    # available_at 推到未来（main 复现的 2099 用例）
    lambda payload: payload["facts"][0].update({"available_at": "2099-01-01T00:00:00+08:00"}),
    # 同语义但对 usable 布尔说不
    lambda payload: payload["facts"][0].update({"usable": False}),
    # 价格被改
    lambda payload: payload["facts"][0]["payload"]["items"][0].update({"price": 999}),
    # 质量/校准被改
    lambda payload: payload["facts"][0].update({"quality": "semantics_unverified"}),
    # 时间语义被改
    lambda payload: payload["facts"][0].update({"time_semantics": "unverified"}),
    # 指纹被改
    lambda payload: payload.update({"content_hash": "0" * 64}),
    # snapshot_id 被改
    lambda payload: payload.update({"snapshot_id": "es_tampered"}),
    # schema 版本被改
    lambda payload: payload.update({"schema_version": "99"}),
])
def test_tampered_snapshot_is_rejected(tmp_path, mutation):
    frozen = _auction_snapshot(tmp_path).to_dict()
    tampered = copy.deepcopy(frozen)
    mutation(tampered)
    with pytest.raises(evidence.EvidenceError):
        evidence.EvidenceSnapshot.from_dict(tampered)


def test_future_fact_cannot_bypass_checks_through_a_hand_built_object(tmp_path):
    snapshot = _auction_snapshot(tmp_path)
    future = replace(snapshot.facts[0],
                     available_at="2099-01-01T00:00:00+08:00", usable=True)
    assert future.usable is True  # 语义组件本身是 verified
    hand_built = evidence.EvidenceSnapshot(
        snapshot_id="hand-built", cutoff=snapshot.cutoff, facts=(future,),
        request=snapshot.request, source_manifest=snapshot.source_manifest,
        coverage=snapshot.coverage, content_hash=snapshot.content_hash)
    check = evidence.check_need(hand_built, _need(scope=("*",)), now=LATER)
    assert check.status == "future"
    assert check.usable is False


def test_snapshot_hash_covers_calibration_content_not_only_version(tmp_path):
    with _sandbox(tmp_path):
        _obs(copy.deepcopy(AUCTION_ITEMS))
        first = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["auction_path"],
            calibrations=[VERIFIED_AUCTION])
        altered = replace(VERIFIED_AUCTION,
                          evidence="pytest fixture: different verified basis")
        second = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["auction_path"],
            calibrations=[altered])
    assert first.snapshot_id != second.snapshot_id
    assert first.content_hash != second.content_hash


def test_snapshot_hash_covers_request_and_coverage(tmp_path):
    with _sandbox(tmp_path):
        _obs(copy.deepcopy(AUCTION_ITEMS))
        one = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["auction_path"],
            calibrations=[VERIFIED_AUCTION])
        two = evidence.build_snapshot(
            cutoff=LATER, codes=["600001", "600002"],
            capabilities=["auction_path"], calibrations=[VERIFIED_AUCTION])
    assert one.snapshot_id != two.snapshot_id


# ─── E2：单位/内容语义不能被总 quality 绕开 ─────────────────────────────────


def test_total_quality_verified_cannot_bypass_missing_unit_semantics(tmp_path):
    with _sandbox(tmp_path):
        # 行内单位语义未核实：即使来源绑定 verified 也不能单边升级。
        _bar(unit_semantics="unverified")
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["intraday_bars"])
    assert snapshot.facts[0].quality == "semantics_verified"
    assert snapshot.facts[0].unit_semantics == "unverified"
    assert snapshot.facts[0].usable is False
    check = evidence.check_need(
        snapshot, _need("intraday_bars", period="1min",
                        expected_intervals=(MINUTE_END,)), now=LATER)
    assert check.status == "unverified"


def test_missing_or_nan_required_fields_are_not_usable(tmp_path):
    with _sandbox(tmp_path):
        _bar(time_semantics="verified")
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["intraday_bars"],
            calibrations=[VERIFIED_MINUTE])
    fact = snapshot.facts[0]
    assert fact.usable is True
    missing_open = replace(fact, payload={**dict(fact.payload), "open": None})
    assert missing_open.usable is False
    nan_close = replace(fact, payload={**dict(fact.payload),
                                       "close": float("nan")})
    assert nan_close.usable is False
    inconsistent = replace(fact, payload={**dict(fact.payload), "high": 1.0})
    assert inconsistent.usable is False


def test_virtual_last_tick_cannot_pose_as_auction_final(tmp_path):
    virtual = {**FINAL_PAYLOAD, "terminal_source": "last_tick_virtual_match"}
    with _sandbox(tmp_path):
        _obs(virtual, obs_type="auction_final", units=FINAL_UNITS)
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["auction_final"],
            calibrations=[VERIFIED_FINAL])
    assert snapshot.facts[0].usable is False
    check = evidence.check_need(
        snapshot, _need("auction_final", affected_actions=("open",)),
        now=LATER)
    assert check.status == "unverified"

    with _sandbox(tmp_path / "final"):
        _obs(dict(FINAL_PAYLOAD), obs_type="auction_final", units=FINAL_UNITS)
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["auction_final"],
            calibrations=[VERIFIED_FINAL])
    assert snapshot.facts[0].usable is True
    assert evidence.check_need(
        snapshot, _need("auction_final"), now=LATER).status == "usable"


def test_constructed_0925_constant_cannot_upgrade_to_final(tmp_path):
    fabricated = {"price": 10.0, "amount": 1000.0, "matched": 100,
                  "unmatched": 50, "source_time": "09:25:00"}
    with _sandbox(tmp_path):
        _obs(fabricated, obs_type="auction_final", units=FINAL_UNITS)
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["auction_final"],
            calibrations=[VERIFIED_FINAL])
    assert snapshot.facts[0].usable is False
    assert evidence.check_need(
        snapshot, _need("auction_final"), now=LATER).status == "unverified"


def test_theme_membership_has_no_price_unit_requirement(tmp_path):
    with _sandbox(tmp_path):
        _obs(copy.deepcopy(MEMBERSHIP_PAYLOAD), obs_type="sector_membership",
             code="881101", units={"membership": "code_list"})
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["881101"], capabilities=["theme_membership"],
            calibrations=[VERIFIED_MEMBERSHIP])
    assert snapshot.facts[0].usable is True
    assert evidence.check_need(
        snapshot, _need("theme_membership", scope=("881101",)),
        now=LATER).status == "usable"

    with _sandbox(tmp_path / "partial"):
        payload = {"codes": ["600001"], "page_completeness": "unverified"}
        _obs(payload, obs_type="sector_membership", code="881101",
             units={"membership": "code_list"})
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["881101"], capabilities=["theme_membership"],
            calibrations=[VERIFIED_MEMBERSHIP])
    assert snapshot.facts[0].usable is False


def test_default_calibration_keeps_minute_evidence_unverified(tmp_path):
    with _sandbox(tmp_path):
        _bar(time_semantics="unverified")
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["intraday_bars"])
    need = evidence.EvidenceNeed(
        rule_id="intraday-entry", capability="intraday_bars",
        scope=("600001",), period="1min",
        expected_intervals=(MINUTE_END,), require_verified=True)
    check = evidence.check_need(snapshot, need, now=LATER)
    assert check.status == "unverified"
    assert check.reasons
    assert snapshot.facts[0].usable is False


def test_row_calibration_binding_unlocks_verified_evidence(tmp_path):
    with _sandbox(tmp_path):
        _bar(time_semantics="verified", unit_semantics="verified")
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["intraday_bars"])
    need = evidence.EvidenceNeed(
        rule_id="intraday-entry", capability="intraday_bars",
        scope=("600001",), period="1min",
        expected_intervals=(MINUTE_END,), require_verified=True)
    check = evidence.check_need(snapshot, need, now=LATER)
    assert check.status == "usable"
    assert check.matched_evidence

    missing = evidence.EvidenceNeed(
        rule_id="intraday-entry", capability="intraday_bars",
        scope=("600001",), period="1min",
        expected_intervals=(MINUTE_END, "2026-11-18T09:32:00+08:00"),
        require_verified=True)
    partial = evidence.check_need(snapshot, missing, now=LATER)
    assert partial.status == "partial"
    assert partial.missing_intervals == ("2026-11-18T09:32:00+08:00",)


def test_production_default_semantics_do_not_hard_verify_units_or_content():
    for item in evidence.DEFAULT_SEMANTICS:
        assert item.unit_semantics == evidence.UNVERIFIED
        assert item.content_semantics == evidence.UNVERIFIED
        assert item.fixture is False
        if evidence.VERIFIED in (item.time_semantics,):
            assert item.evidence and item.evidence != evidence.UNVERIFIED


def test_verified_calibration_requires_recorded_basis():
    with pytest.raises(evidence.EvidenceError):
        evidence.SourceSemantics(source="tdx", kind="1min",
                                 time_semantics="verified")
    with pytest.raises(evidence.EvidenceError):
        evidence.SourceSemantics(source="tdx", kind="1min",
                                 unit_semantics="verified",
                                 evidence="unverified")


# ─── E3：冻结请求母集与截断披露 ─────────────────────────────────────────────


def test_missing_requested_code_is_missing_not_usable(tmp_path):
    with _sandbox(tmp_path):
        _obs(copy.deepcopy(AUCTION_ITEMS), code="600001")
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001", "600002"],
            capabilities=["auction_path"], calibrations=[VERIFIED_AUCTION])
    check = evidence.check_need(snapshot, _need(scope=("*",)), now=LATER)
    assert check.status == "missing"
    assert check.missing_scopes == ("600002",)
    assert snapshot.coverage["by_capability"]["auction_path"]["missing_codes"] \
        == ("600002",)


def test_truncated_snapshot_never_reports_full_usable(tmp_path):
    with _sandbox(tmp_path):
        _obs(copy.deepcopy(AUCTION_ITEMS), code="600001")
        _obs(copy.deepcopy(AUCTION_ITEMS), code="600002")
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["auction_path"],
            calibrations=[VERIFIED_AUCTION], fact_limit=1)
    assert snapshot.source_manifest["truncated"] is True
    check = evidence.check_need(snapshot, _need(), now=LATER)
    assert check.status == "partial"
    assert any(reason["reason"] == "coverage_truncated"
               for reason in check.reasons)


def test_unbounded_snapshot_never_reports_full_usable(tmp_path):
    with _sandbox(tmp_path):
        _obs(copy.deepcopy(AUCTION_ITEMS))
        snapshot = evidence.build_snapshot(
            cutoff=LATER, capabilities=["auction_path"],
            calibrations=[VERIFIED_AUCTION])
    assert snapshot.request.all_codes is True
    check = evidence.check_need(snapshot, _need(), now=LATER)
    assert check.status == "partial"
    assert any(reason["reason"] == "coverage_truncated"
               for reason in check.reasons)


def test_scope_outside_frozen_request_is_missing(tmp_path):
    snapshot = _auction_snapshot(tmp_path)
    check = evidence.check_need(snapshot, _need(scope=("600009",)), now=LATER)
    assert check.status == "missing"
    assert "600009" in check.missing_scopes
    assert any(reason["reason"] == "scope_outside_request"
               for reason in check.reasons)


def test_trade_dates_outside_request_are_missing(tmp_path):
    with _sandbox(tmp_path):
        _obs(copy.deepcopy(AUCTION_ITEMS))
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], trade_dates=["2026-11-18"],
            capabilities=["auction_path"], calibrations=[VERIFIED_AUCTION])
    check = evidence.check_need(
        snapshot, _need(trade_dates=("2026-11-17",)), now=LATER)
    assert check.status == "missing"
    assert any(reason["reason"] == "scope_outside_request"
               for reason in check.reasons)


def test_expected_interval_must_match_not_just_any_interval(tmp_path):
    with _sandbox(tmp_path):
        _bar(time_semantics="verified")
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["intraday_bars"])
    need = evidence.EvidenceNeed(
        rule_id="wrong-interval", capability="intraday_bars",
        scope=("600001",), period="1min",
        expected_intervals=("2026-11-18T09:35:00+08:00",))
    check = evidence.check_need(snapshot, need, now=LATER)
    assert check.status == "missing"
    assert check.matched_evidence == ()
    assert check.missing_intervals == ("2026-11-18T09:35:00+08:00",)


def test_future_intervals_are_not_reported_as_missing(tmp_path):
    with _sandbox(tmp_path):
        _bar(time_semantics="unverified")
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["intraday_bars"],
            calibrations=[VERIFIED_MINUTE])
    need = evidence.EvidenceNeed(
        rule_id="later-entry", capability="intraday_bars",
        scope=("600001",), period="1min",
        expected_intervals=("2026-11-18T14:30:00+08:00",))
    check = evidence.check_need(snapshot, need, now=LATER)
    assert check.status == "future"
    assert check.missing_intervals == ()


# ─── 输入/保护分离/来源状态 ─────────────────────────────────────────────────


def test_missing_account_inputs_return_missing_without_fake_account(tmp_path):
    snapshot = _auction_snapshot(tmp_path)
    need = evidence.EvidenceNeed(
        rule_id="add-position", capability="auction_path", scope=("600001",),
        required_inputs=(evidence.INPUT_ACCOUNT_STATE,
                         evidence.INPUT_RISK_BUDGET),
        affected_actions=("increase",))
    check = evidence.check_need(snapshot, need, now=LATER)
    assert check.status == "missing"
    assert set(check.inputs_missing) == {evidence.INPUT_ACCOUNT_STATE,
                                         evidence.INPUT_RISK_BUDGET}
    assert {reason["reason"] for reason in check.reasons} >= {
        "account_state_missing", "risk_budget_missing"}
    assert check.matched_evidence == ()

    supplied = evidence.check_need(
        snapshot, need, now=LATER,
        inputs={evidence.INPUT_ACCOUNT_STATE: True,
                evidence.INPUT_RISK_BUDGET: True})
    assert supplied.status == "usable"


def test_existing_protection_and_new_risk_are_independent(tmp_path):
    with _sandbox(tmp_path):
        _obs(copy.deepcopy(AUCTION_ITEMS))
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"],
            capabilities=["auction_path", "intraday_bars"],
            calibrations=[VERIFIED_AUCTION])
    needs = (
        evidence.EvidenceNeed(rule_id="new-entry", capability="intraday_bars",
                              scope=("600001",), period="1min",
                              expected_intervals=(MINUTE_END,)),
        evidence.EvidenceNeed(rule_id="protect-exit", capability="auction_path",
                              scope=("600001",),
                              protects=evidence.PROTECT_EXISTING,
                              affected_actions=("reduce",)),
    )
    summary = evidence.summarize_checks(
        evidence.check_needs(snapshot, needs, now=LATER))
    assert summary["new_risk_blocked"] is True
    assert summary["existing_protection_blocked"] is False
    assert {item["rule_id"] for item in summary["blocked"]["new_risk"]} == {
        "new-entry"}


def test_source_status_separates_missing_paid_keys(tmp_path):
    snapshot = _auction_snapshot(tmp_path)
    status = evidence.source_status(snapshot)
    assert status["sources"]["meoz"]["state"] == "missing_api_key"
    assert status["sources"]["tushare"]["state"] == "missing_api_key"
    assert status["sources"]["tdx"]["state"] == "ready"
    assert status["store_state"] == store.READY
    assert status["calibrations"]["tdx:auction_path"]["version"] == "fixture-1"


def test_coverage_summary_discloses_limits_and_truncation(tmp_path):
    with _sandbox(tmp_path):
        _obs(copy.deepcopy(AUCTION_ITEMS))
        snapshot = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["auction_path"],
            calibrations=[VERIFIED_AUCTION], fact_limit=1)
    summary = evidence.coverage_summary(snapshot)
    assert summary["truncated"] is True
    assert summary["limits"]["facts"] == 1
    assert summary["by_capability"]["auction_path"]["observed_codes"] == ["600001"]


def test_trade_date_scope_limits_the_snapshot(tmp_path):
    with _sandbox(tmp_path):
        _obs({"items": [{"time": "09:20:00", "price": 10.0, "matched": 1,
                         "unmatched": 1}]})
        _obs({"items": [{"time": "09:20:00", "price": 9.0, "matched": 1,
                         "unmatched": 1}]},
             available_at="2026-11-17T09:30:00+08:00", trade_date="2026-11-17")
        scoped = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], trade_dates=["2026-11-17"],
            capabilities=["auction_path"], calibrations=[VERIFIED_AUCTION])
        both = evidence.build_snapshot(
            cutoff=LATER, codes=["600001"], capabilities=["auction_path"],
            calibrations=[VERIFIED_AUCTION])
    assert [fact.trade_date for fact in scoped.facts] == ["2026-11-17"]
    assert len(both.facts) == 2
