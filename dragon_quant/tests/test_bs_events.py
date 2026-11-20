"""严格 PIT 执行事件（A2）：盘后日K/未来可得/未校准行不得成交。"""

from __future__ import annotations

import pytest

from dragon_quant.infra.assets import observations
from dragon_quant.infra.calibration import (
    PRODUCTION_CALIBRATIONS, UNVERIFIED, VERIFIED, binding_for,
)
from dragon_quant.research.bs_advice import assembly, events
from dragon_quant.research.bs_advice.universe import assemble_universe


def _universe(day="2026-11-18"):
    return assemble_universe(
        as_of=day, dragons_reader=lambda a, b: [{"code": "600001"}],
        prep_reader=lambda before: (None, {"error": None}),
        calendar_reader=lambda as_of: [], plan_reader=lambda d: None)


def _snapshot_for(tmp_path, monkeypatch, *, cutoff="2026-11-18T10:05:00+08:00"):
    from dragon_quant.infra import calibration

    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    return assembly.assemble_snapshot(
        cutoff=cutoff, codes=["600001"], db_path=tmp_path / "assets.db").snapshot


def _binding(period: str, calibrated_at: str):
    return binding_for(PRODUCTION_CALIBRATIONS[0].calibration_id,
                       calibrated_at=calibrated_at, source="tdx",
                       period=period)


def _bar(db_path, *, period="1min", start="09:59:00", end="10:00:00",
         close=10.0, available="2026-11-18T10:00:30+08:00",
         received=None, time_semantics=VERIFIED,
         unit_semantics=VERIFIED, volume=1_000_000,
         calibration=True, valid_content=True):
    high = close if valid_content else close - 1.0
    observations.append_bar(
        trade_date="2026-11-18", market="SH", code="600001", period=period,
        interval_start=f"2026-11-18T{start}+08:00",
        interval_end=f"2026-11-18T{end}+08:00",
        open=close, high=high, low=close, close=close, volume=volume,
        amount=close * volume, source="tdx", observed_at=received or available,
        available_at=available, completeness="final",
        time_semantics=time_semantics, unit_semantics=unit_semantics,
        adjustment="none", db_path=db_path,
        calibration=(_binding(period, received or available)
                     if calibration else None))


def _events(tmp_path, monkeypatch, *, cutoff="2026-11-18T10:05:00+08:00"):
    return events.build_intraday_events(
        codes=["600001"], universe=_universe(),
        base_snapshot=_snapshot_for(tmp_path, monkeypatch, cutoff=cutoff),
        date_from="2026-11-18", date_to="2026-11-18", cutoff=cutoff,
        db_path=tmp_path / "assets.db")


def test_after_hours_daily_bar_cannot_become_execution_event(tmp_path, monkeypatch):
    db = tmp_path / "assets.db"
    # 盘后发布的日K（15:05 可得）不是会话内成交事件。
    observations.append_bar(
        trade_date="2026-11-18", market="SH", code="600001", period="daily",
        interval_start="2026-11-18T09:30:00+08:00",
        interval_end="2026-11-18T15:00:00+08:00",
        open=9.0, high=9.1, low=8.9, close=9.0, volume=1_000_000,
        amount=9_000_000, source="tdx",
        observed_at="2026-11-18T15:05:00+08:00",
        available_at="2026-11-18T15:05:00+08:00", completeness="final",
        time_semantics=VERIFIED, unit_semantics=VERIFIED,
        adjustment="none", db_path=db,
        calibration=_binding("daily", "2026-11-18T15:05:00+08:00"))
    result = _events(tmp_path, monkeypatch)
    assert result.events == ()
    assert any(gap["reason"] in ("after_hours", "time_unverified")
               for gap in result.gaps) or result.coverage["bars_read"] == 0


def test_future_available_and_unverified_rows_are_not_executed(tmp_path, monkeypatch):
    db = tmp_path / "assets.db"
    _bar(db, start="09:59:00", end="10:00:00", close=10.0,
         available="2026-11-18T10:00:30+08:00")
    _bar(db, start="10:00:00", end="10:01:00", close=10.1,
         available="2026-11-18T10:20:00+08:00")  # 晚于 cutoff/事件
    _bar(db, start="10:01:00", end="10:02:00", close=10.2,
         time_semantics=UNVERIFIED)  # 未校准原行
    result = _events(tmp_path, monkeypatch)
    assert len(result.events) == 1
    assert result.events[0].rows["600001"]["execution_price"] == 10.0
    assert result.coverage["rejected_bars"] >= 1


def test_later_revision_does_not_enter_earlier_cutoff(tmp_path, monkeypatch):
    db = tmp_path / "assets.db"
    _bar(db, start="09:59:00", end="10:00:00", close=10.0,
         available="2026-11-18T10:00:30+08:00")
    # 同一区间后到修订（不同内容/更晚可得）。
    observations.append_bar(
        trade_date="2026-11-18", market="SH", code="600001", period="1min",
        interval_start="2026-11-18T09:59:00+08:00",
        interval_end="2026-11-18T10:00:00+08:00",
        open=9.0, high=9.0, low=9.0, close=9.0, volume=1_000_000,
        amount=9_000_000, source="tdx",
        observed_at="2026-11-18T10:01:30+08:00",
        available_at="2026-11-18T10:01:30+08:00", completeness="final",
        time_semantics=VERIFIED, unit_semantics=VERIFIED,
        adjustment="none", db_path=db)
    early = events.build_intraday_events(
        codes=["600001"], universe=_universe(),
        base_snapshot=_snapshot_for(tmp_path, monkeypatch,
                                    cutoff="2026-11-18T10:00:45+08:00"),
        date_from="2026-11-18", date_to="2026-11-18",
        cutoff="2026-11-18T10:00:45+08:00", db_path=db)
    assert len(early.events) == 1
    assert early.events[0].rows["600001"]["execution_price"] == 10.0


def test_received_after_cutoff_is_not_usable(tmp_path, monkeypatch):
    db = tmp_path / "assets.db"
    _bar(db, start="09:59:00", end="10:00:00", close=10.0,
         available="2026-11-18T10:00:30+08:00",
         received="2026-11-18T10:04:00+08:00")
    result = events.build_intraday_events(
        codes=["600001"], universe=_universe(),
        base_snapshot=_snapshot_for(tmp_path, monkeypatch,
                                    cutoff="2026-11-18T10:02:00+08:00"),
        date_from="2026-11-18", date_to="2026-11-18",
        cutoff="2026-11-18T10:02:00+08:00", db_path=db)
    assert result.events == ()
    assert any(gap["reason"] == "received_after_cutoff" for gap in result.gaps)


def test_invalid_content_or_missing_calibration_is_not_executed(tmp_path, monkeypatch):
    db = tmp_path / "assets.db"
    _bar(db, start="09:59:00", end="10:00:00", close=10.0,
         available="2026-11-18T10:00:30+08:00", valid_content=False)
    _bar(db, start="10:00:00", end="10:01:00", close=10.1,
         available="2026-11-18T10:01:30+08:00", calibration=False)
    result = _events(tmp_path, monkeypatch)
    reasons = {gap["reason"] for gap in result.gaps}
    assert "invalid_bar_content" in reasons
    assert "calibration_unverified" in reasons
    assert result.events == ()


def test_current_install_fingerprint_cannot_backfill_unbound_or_tampered_row(
        tmp_path, monkeypatch):
    db = tmp_path / "assets.db"
    _bar(db, start="09:59:00", end="10:00:00", close=10.0,
         available="2026-11-18T10:00:30+08:00", calibration=False)
    tampered = _binding("1min", "2026-11-18T10:01:30+08:00")
    tampered["converter_sha256"] = {key: "0" * 64 for key in
                                    tampered["converter_sha256"]}
    observations.append_bar(
        trade_date="2026-11-18", market="SH", code="600001", period="1min",
        interval_start="2026-11-18T10:00:00+08:00",
        interval_end="2026-11-18T10:01:00+08:00",
        open=10.1, high=10.1, low=10.1, close=10.1, volume=1_000_000,
        amount=10_100_000, source="tdx",
        observed_at="2026-11-18T10:01:30+08:00",
        available_at="2026-11-18T10:01:30+08:00", completeness="final",
        time_semantics=VERIFIED, unit_semantics=VERIFIED,
        adjustment="none", db_path=db, calibration=tampered)
    snapshot = _snapshot_for(tmp_path, monkeypatch,
                             cutoff="2026-11-18T10:02:00+08:00")
    facts = [fact for fact in snapshot.facts
             if fact.capability == "intraday_bars"]
    assert facts and all(fact.usable is False for fact in facts)


def test_capacity_is_participation_discounted(tmp_path, monkeypatch):
    db = tmp_path / "assets.db"
    _bar(db, close=10.0, volume=1_000_000)
    result = events.build_intraday_events(
        codes=["600001"], universe=_universe(),
        base_snapshot=_snapshot_for(tmp_path, monkeypatch),
        date_from="2026-11-18", date_to="2026-11-18",
        cutoff="2026-11-18T10:05:00+08:00", db_path=db,
        participation=0.1)
    assert result.events[0].rows["600001"]["available_qty"] == 100_000


def test_empty_events_is_explicit_not_daily_fallback(tmp_path, monkeypatch):
    with pytest.raises(observations.ObservationStoreError):
        _events(tmp_path, monkeypatch)
