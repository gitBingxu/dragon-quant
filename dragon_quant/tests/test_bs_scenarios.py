"""W4b 前置：历史场景跨代码交集/成熟度与固定 cohort 语义。"""

from __future__ import annotations

from datetime import date, timedelta

from dragon_quant.infra.assets import observations
from dragon_quant.infra.calibration import PRODUCTION_CALIBRATIONS, binding_for
from dragon_quant.research.bs_advice import assembly
from dragon_quant.research.bs_advice.scenarios import build_historical_scenarios
from dragon_quant.research.bs_advice.universe import assemble_universe
from dragon_quant.review_account.strategy.bs_policy import teaching_fixture

BINDING_ID = PRODUCTION_CALIBRATIONS[0].calibration_id


def _daily(db, code: str, day: str, close: float):
    ts = f"{day}T15:05:00+08:00"
    observations.append_bar(
        trade_date=day, market="SH", code=code, period="daily",
        interval_start=f"{day}T09:30:00+08:00",
        interval_end=f"{day}T15:00:00+08:00",
        open=close, high=close, low=close, close=close, volume=1_000_000,
        amount=close * 1_000_000, source="tdx", observed_at=ts,
        available_at=ts, completeness="final", time_semantics="verified",
        unit_semantics="verified", adjustment="none", db_path=db,
        calibration=binding_for(BINDING_ID, calibrated_at=ts, source="tdx",
                                period="daily"))


def _universe(codes):
    return assemble_universe(
        as_of="2026-11-20", dragons_reader=lambda a, b: [
            {"code": code} for code in codes],
        prep_reader=lambda before: (None, {"error": None}),
        calendar_reader=lambda as_of: [], plan_reader=lambda d: None)


def _days(count, end=date(2026, 11, 19)):
    result, cursor = [], end
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return sorted(result)


def test_unequal_code_history_uses_intersection_or_insufficient(tmp_path,
                                                               monkeypatch):
    from dragon_quant.infra import calibration
    from dragon_quant.research.bs_advice import assembly as snap_assembly

    monkeypatch.setattr(
        snap_assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    db = tmp_path / "assets.db"
    days = _days(8)
    for day in days[:4]:          # 600001 只有更短历史
        _daily(db, "600001", day, 10.0)
    for day in days:
        _daily(db, "600002", day, 11.0)
    snapshot = assembly.assemble_snapshot(
        cutoff="2026-11-20T09:00:00+08:00", codes=["600001", "600002"],
        db_path=db).snapshot
    scenarios = build_historical_scenarios(
        snapshot, _universe(["600001", "600002"]),
        decision_time="2026-11-20T09:00:00+08:00",
        spec=teaching_fixture().scenario)
    # 交集只有 4 天 < horizon=5 → 明确 insufficient，不补零/不猜。
    assert scenarios is None or scenarios.insufficiency


def test_immature_window_ending_after_decision_is_excluded(tmp_path, monkeypatch):
    from dragon_quant.infra import calibration
    from dragon_quant.research.bs_advice import assembly as snap_assembly

    monkeypatch.setattr(
        snap_assembly, "current_tdx_provenance",
        lambda: calibration.PRODUCTION_CALIBRATIONS[0].provenance)
    db = tmp_path / "assets.db"
    days = _days(6)
    for day in days:
        _daily(db, "600001", day, 10.0)
    snapshot = assembly.assemble_snapshot(
        cutoff="2026-11-20T09:00:00+08:00", codes=["600001"],
        db_path=db).snapshot
    spec = teaching_fixture().scenario
    # 决策时点早于最后窗口末日 → 该窗口不成熟，不能进入场景。
    scenarios = build_historical_scenarios(
        snapshot, _universe(["600001"]),
        decision_time=f"{days[-1]}T10:00:00+08:00", spec=spec)
    if scenarios is not None and scenarios.paths:
        for path in scenarios.paths:
            assert path.steps[-1].at < f"{days[-1]}T10:00:00+08:00"
