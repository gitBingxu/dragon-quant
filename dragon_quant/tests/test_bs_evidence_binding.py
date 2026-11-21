"""W4b 前置：prep 接收时刻时区 + 观测行级校准绑定（严格证据）。"""

from __future__ import annotations

from contextlib import contextmanager
from dragon_quant.infra.assets import db as assets_db, observations
from dragon_quant.review_account import evidence
from dragon_quant.review_account.evidence import QUALITY_VERIFIED
from dragon_quant.review_account.evidence.schema import VERIFIED
from dragon_quant.research.bs_advice import market
from dragon_quant.research.bs_advice.universe import assemble_universe
from dragon_quant.tests.support import patch_storage_dbs


@contextmanager
def _env(tmp_path):
    with patch_storage_dbs(str(tmp_path / "dragon.db"),
                           str(tmp_path / "assets.db")):
        yield


def _quote(calibration=None):
    return observations.append_observation(
        trade_date="2026-11-18", market="SH", code="SH000001",
        obs_type="quote", source="tdx", payload={"price": 3200.0},
        received_at="2026-11-18T10:00:00+08:00",
        available_at="2026-11-18T10:00:00+08:00",
        units={"price": "verified"}, quality=QUALITY_VERIFIED,
        calibration=calibration)


def _verified_quote():
    return evidence.SourceSemantics(
        source="tdx", kind="quote", time_semantics=VERIFIED,
        unit_semantics=VERIFIED, content_semantics=VERIFIED,
        quality=QUALITY_VERIFIED, evidence="fixture: known quote semantics")


def test_prep_snapshot_created_at_is_timezone_aware(tmp_path):
    with _env(tmp_path):
        assets_db.save_prep_snapshot("2026-11-17", {"market": {"state": "strong"}})
        payload, meta = assets_db.get_prep_snapshot(
            "2026-11-17", with_meta=True)
    assert payload and meta["created_at"].endswith("+08:00")


def test_strict_observation_without_row_binding_is_unverified(tmp_path):
    with _env(tmp_path):
        _quote()
        loose = evidence.build_snapshot(
            cutoff="2026-11-18T10:05:00+08:00", codes=["SH000001"],
            capabilities=["market_context"], calibrations=[_verified_quote()])
        strict = evidence.build_snapshot(
            cutoff="2026-11-18T10:05:00+08:00", codes=["SH000001"],
            capabilities=["market_context"], calibrations=[_verified_quote()],
            strict_calibration=True)
    assert loose.facts[0].usable is True
    assert strict.facts[0].usable is False
    assert strict.facts[0].unit_semantics == evidence.UNVERIFIED


def test_unknown_observation_binding_never_fabricates_verified(tmp_path):
    with _env(tmp_path):
        _quote(calibration={"calibration_id": "not-a-production-spec",
                            "source": "tdx", "period": "quote",
                            "calibrated_at": "2026-11-18T10:00:00+08:00"})
        snapshot = evidence.build_snapshot(
            cutoff="2026-11-18T10:05:00+08:00", codes=["SH000001"],
            capabilities=["market_context"], calibrations=[_verified_quote()],
            strict_calibration=True)
    assert snapshot.facts[0].usable is False


def test_future_market_deny_or_zero_is_unknown_not_leaked():
    def universe(market, created_at):
        return assemble_universe(
            as_of="2026-11-18",
            dragons_reader=lambda a, b: [],
            prep_reader=lambda before: (
                {"review_candidates": [], "market": market},
                {"trade_date": "2026-11-17", "created_at": created_at,
                 "error": None}),
            calendar_reader=lambda as_of: ["2026-11-17"],
            plan_reader=lambda d: None)

    future = "2026-11-20T16:00:00+08:00"
    cutoff = "2026-11-19T10:00:00+08:00"
    denied = market.market_from_universe(
        universe({"state": "strong", "coeff": 1.0,
                  "new_risk_allowed": False}, future), cutoff=cutoff)
    assert denied.new_risk_allowed is None
    assert "market_context_after_cutoff" in denied.reasons
    zero = market.market_from_universe(
        universe({"state": "strong", "coeff": 0.0}, future), cutoff=cutoff)
    assert zero.new_risk_allowed is None


def test_market_rejects_naive_prep_created_at():
    def universe(created_at):
        return assemble_universe(
            as_of="2026-11-18",
            dragons_reader=lambda a, b: [],
            prep_reader=lambda before: (
                {"review_candidates": [{"code": "600001",
                                        "eligibility": "eligible",
                                        "position_pct": 5.0}],
                 "market": {"state": "strong", "coeff": 1.0}},
                {"trade_date": "2026-11-17", "created_at": created_at,
                 "error": None}),
            calendar_reader=lambda as_of: ["2026-11-17"],
            plan_reader=lambda d: None)

    naive = market.market_from_universe(
        universe("2026-11-17 16:50:00"),
        cutoff="2026-11-18T10:00:00+08:00")
    assert naive.new_risk_allowed is None
    assert "market_received_at_missing" in naive.reasons
    aware = market.market_from_universe(
        universe("2026-11-17T16:50:00+08:00"),
        cutoff="2026-11-18T10:00:00+08:00")
    assert aware.new_risk_allowed is True
