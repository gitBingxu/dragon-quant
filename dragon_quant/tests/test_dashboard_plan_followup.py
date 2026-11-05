"""复盘「被否股后续涨跌」投影单测（方案 §5 / §9.1，TDD 先 RED）。

覆盖：
- ``prep_effect.resolve_plan_date``：plan_review 优先 / previous_snapshot 兜底 / 都无；
- ``prep_effect.run_plan_followup``：正向 1/3/5 日净收益与 summary/spread、缺日 K、
  计划日快照缺失 / 版本不支持 / 无 review_candidates、单 horizon、未来日未到；
- ``prep_view._build_verification`` 空仓验证规则表分支。

全部为纯函数：fake store / fake bars，不打开任何数据库、不触网。
"""

from __future__ import annotations

import pytest

from web_ui.backend.presenters import prep_effect, prep_view

TRADE_DATE = "2026-09-11"
PLAN_DATE = "2026-09-10"
BAR_DATES = ["2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16",
             "2026-09-17", "2026-09-18"]


# ─── fakes ─────────────────────────────────────────────────────────────────


class FakeStore:
    """``build_store("prep")`` 的鸭子类型：``load`` 返回 ``(payload, meta)``。"""

    def __init__(self, days: dict[str, dict]):
        self.days = days
        self.loaded: list[str] = []

    def snapshot_dates(self, date_from: str, date_to: str) -> list[str]:
        return sorted(day for day in self.days if date_from <= day <= date_to)

    def load(self, day: str):
        self.loaded.append(day)
        return self.days[day], {"created_at": f"{day} 16:45:00"}


def _plan_payload(*, version: str = "0.8", candidates: list | None = None) -> dict:
    payload = {
        "trade_date": PLAN_DATE,
        "version": version,
        "review_candidates": candidates if candidates is not None else [
            {"code": "600001", "name": "龙头A", "review_score": 88.0,
             "review_grade": "强", "position_pct": 20,
             "eligibility": "eligible"},
            {"code": "600002", "name": "跟风B", "review_score": 62.5,
             "review_grade": "一般", "position_pct": 0},
        ],
    }
    return payload


def _bars(opens: dict[str, list[float]], dates: list[str] = BAR_DATES) -> dict:
    rows = {
        code: [
            {"date": day, "open": price, "high": price, "low": price,
             "close": price, "volume": 1000.0, "amount": 500_000_000.0}
            for day, price in zip(dates, prices)
        ]
        for code, prices in opens.items()
    }
    return {"rows": rows, "truncated": False,
            "bar_dates": list(dates), "codes": sorted(rows)}


def _loader(bars: dict):
    calls: list[tuple] = []

    def load(codes, date_from, date_to):
        calls.append((list(codes), date_from, date_to))
        return bars

    load.calls = calls
    return load


def _run(days, bars, *, horizons=None):
    return prep_effect.run_plan_followup(
        trade_date=TRADE_DATE,
        current={"plan_review": {"plan_date": PLAN_DATE}},
        prev_meta={"trade_date": PLAN_DATE, "error": None},
        store=FakeStore(days),
        load_bars=_loader(bars),
        horizons=horizons,
    )


# ─── resolve_plan_date ─────────────────────────────────────────────────────


def test_resolve_plan_date_prefers_plan_review() -> None:
    resolved = prep_effect.resolve_plan_date(
        plan_review={"plan_date": PLAN_DATE},
        prev_meta={"trade_date": PLAN_DATE},
    )
    assert resolved["plan_date"] == PLAN_DATE
    assert resolved["plan_date_source"] == "plan_review"
    assert resolved["warnings"] == []


def test_resolve_plan_date_falls_back_to_previous_snapshot() -> None:
    resolved = prep_effect.resolve_plan_date(
        plan_review={}, prev_meta={"trade_date": PLAN_DATE})
    assert resolved["plan_date"] == PLAN_DATE
    assert resolved["plan_date_source"] == "previous_snapshot"
    assert any("plan_date_fallback" in item for item in resolved["warnings"])


def test_resolve_plan_date_none_without_plan_review() -> None:
    resolved = prep_effect.resolve_plan_date(
        plan_review=None, prev_meta={"trade_date": PLAN_DATE})
    assert resolved["plan_date"] is None
    assert resolved["plan_date_source"] is None
    assert resolved["warnings"] == []


def test_resolve_plan_date_warns_on_mismatch() -> None:
    resolved = prep_effect.resolve_plan_date(
        plan_review={"plan_date": "2026-09-09"},
        prev_meta={"trade_date": PLAN_DATE},
    )
    assert resolved["plan_date"] == "2026-09-09"
    assert resolved["plan_date_source"] == "plan_review"
    assert any("plan_date_mismatch" in item for item in resolved["warnings"])


def test_resolve_plan_date_ignores_invalid_plan_date() -> None:
    resolved = prep_effect.resolve_plan_date(
        plan_review={"plan_date": "not-a-date"},
        prev_meta={"trade_date": PLAN_DATE},
    )
    assert resolved["plan_date"] == PLAN_DATE
    assert resolved["plan_date_source"] == "previous_snapshot"


# ─── run_plan_followup 正向 ────────────────────────────────────────────────


def test_followup_ok_path_with_1_3_5_net_returns() -> None:
    bars = _bars({
        "600001": [10.1, 10.2, 10.3, 10.4, 10.5, 10.6],
        "600002": [10.0] * 6,
    })
    result = _run({PLAN_DATE: _plan_payload()}, bars)

    assert result["status"] == "ok"
    assert result["reason"] is None
    assert result["trade_date"] == TRADE_DATE
    assert result["plan_date"] == PLAN_DATE
    assert result["plan_date_source"] == "plan_review"
    assert result["data_source"] == "local_stock_daily"
    assert result["cost_bps"] == 13.0
    assert result["horizons"] == [1, 3, 5]

    rows = {row["code"]: row for row in result["rows"]}
    assert [row["code"] for row in result["rows"]] == ["600001", "600002"]
    recommended = rows["600001"]
    assert recommended["group"] == "recommended"
    assert recommended["name"] == "龙头A"
    assert recommended["score"] == 88.0
    assert recommended["level"] == "强"
    assert recommended["horizons"]["1"]["entry_date"] == "2026-09-11"
    assert recommended["horizons"]["1"]["exit_date"] == "2026-09-14"
    assert recommended["horizons"]["1"]["net_return_pct"] == pytest.approx(
        0.8601, abs=1e-3)
    assert recommended["horizons"]["1"]["outcome"] == "up"
    assert recommended["horizons"]["3"]["net_return_pct"] == pytest.approx(
        2.8403, abs=1e-3)
    assert recommended["horizons"]["3"]["exit_date"] == "2026-09-16"
    assert recommended["horizons"]["5"]["net_return_pct"] == pytest.approx(
        4.8205, abs=1e-3)
    assert recommended["horizons"]["5"]["exit_date"] == "2026-09-18"

    rejected = rows["600002"]
    assert rejected["group"] == "not_recommended"
    for horizon in ("1", "3", "5"):
        cell = rejected["horizons"][horizon]
        assert cell["net_return_pct"] == pytest.approx(-0.13, abs=1e-6)
        assert cell["outcome"] == "down"
        assert cell["exclusion_reason"] is None

    horizon_1 = result["summary"][0]
    assert horizon_1["horizon"] == 1
    groups = {group["group"]: group for group in horizon_1["groups"]}
    assert groups["recommended"]["valid"] == 1
    assert groups["recommended"]["mean_net_return_pct"] == pytest.approx(
        0.8601, abs=1e-3)
    assert groups["not_recommended"]["mean_net_return_pct"] == pytest.approx(
        -0.13, abs=1e-6)
    assert groups["data_insufficient"]["valid"] == 0
    assert groups["data_insufficient"]["mean_net_return_pct"] is None
    assert horizon_1["spread"] == pytest.approx(0.9901, abs=1e-3)

    coverage = result["local_coverage"]
    assert coverage["plan_codes"] == 2
    assert coverage["loaded_codes"] == 2
    assert coverage["codes_with_bars"] == 2
    assert coverage["bar_dates"] == len(BAR_DATES)
    assert coverage["truncated"] is False

    assert any("13bp" in caveat for caveat in result["caveats"])
    assert any("T+3/T+5" in caveat for caveat in result["caveats"])
    assert any("calendar_fallback" in warning for warning in result["warnings"])


def test_followup_bar_window_is_anchored_on_eval_date() -> None:
    bars = _bars({"600001": [10.0] * 6})
    loader = _loader(bars)
    prep_effect.run_plan_followup(
        trade_date=TRADE_DATE,
        current={"plan_review": {"plan_date": PLAN_DATE}},
        prev_meta={"trade_date": PLAN_DATE, "error": None},
        store=FakeStore({PLAN_DATE: _plan_payload(candidates=[
            {"code": "600001", "position_pct": 20, "eligibility": "eligible"}])}),
        load_bars=loader,
    )
    assert loader.calls[0][1] == "2026-05-14"
    assert loader.calls[0][2] == "2026-10-11"


# ─── run_plan_followup 空态 ────────────────────────────────────────────────


def test_followup_missing_bars_keeps_rows_with_missing_bar() -> None:
    empty = {"rows": {}, "truncated": False, "bar_dates": [], "codes": []}
    result = _run({PLAN_DATE: _plan_payload()}, empty)

    assert result["status"] == "insufficient_data"
    assert result["reason"] == "local_bars_missing"
    assert len(result["rows"]) == 2
    for row in result["rows"]:
        for horizon in ("1", "3", "5"):
            cell = row["horizons"][horizon]
            assert cell["net_return_pct"] is None
            assert cell["exclusion_reason"] == "missing_bar"
    groups = {group["group"]: group for group in result["summary"][0]["groups"]}
    assert groups["recommended"]["valid"] == 0
    assert groups["recommended"]["excluded"] == 1
    assert groups["not_recommended"]["excluded"] == 1
    assert result["local_coverage"]["loaded_codes"] == 0


def test_followup_plan_snapshot_missing() -> None:
    result = _run({}, _bars({"600001": [10.0] * 6}))
    assert result["status"] == "insufficient_data"
    assert result["reason"] == "plan_snapshot_missing"
    assert result["rows"] == []


def test_followup_plan_snapshot_unsupported() -> None:
    result = _run({PLAN_DATE: _plan_payload(version="0.6")},
                  _bars({"600001": [10.0] * 6}))
    assert result["status"] == "insufficient_data"
    assert result["reason"] == "plan_snapshot_unsupported"
    assert any("prep --recompute" in caveat for caveat in result["caveats"])


def test_followup_plan_samples_missing() -> None:
    result = _run({PLAN_DATE: _plan_payload(candidates=[])},
                  _bars({"600001": [10.0] * 6}))
    assert result["status"] == "insufficient_data"
    assert result["reason"] == "plan_samples_missing"
    assert result["rows"] == []


def test_followup_empty_plan_payload_is_corrupt() -> None:
    class EmptyPayloadStore(FakeStore):
        def load(self, day: str):
            return None, {"created_at": f"{day} 16:45:00"}

    result = prep_effect.run_plan_followup(
        trade_date=TRADE_DATE,
        current={"plan_review": {"plan_date": PLAN_DATE}},
        prev_meta={"trade_date": PLAN_DATE, "error": None},
        store=EmptyPayloadStore({PLAN_DATE: {}}),
        load_bars=_loader(_bars({"600001": [10.0] * 6})),
    )
    assert result["status"] == "insufficient_data"
    assert result["reason"] == "plan_snapshot_corrupt"
    assert result["rows"] == []


def test_followup_single_horizon_only_returns_requested_key() -> None:
    bars = _bars({"600001": [10.1, 10.2, 10.3, 10.4, 10.5, 10.6],
                  "600002": [10.0] * 6})
    result = _run({PLAN_DATE: _plan_payload()}, bars, horizons=[1])
    assert result["horizons"] == [1]
    assert {item["horizon"] for item in result["summary"]} == {1}
    for row in result["rows"]:
        assert set(row["horizons"]) == {"1"}


def test_followup_future_horizon_is_insufficient_horizon() -> None:
    dates = ["2026-09-11", "2026-09-14"]
    bars = _bars({"600001": [10.1, 10.2], "600002": [10.0, 10.0]}, dates)
    result = _run({PLAN_DATE: _plan_payload()}, bars)
    rows = {row["code"]: row for row in result["rows"]}
    assert rows["600001"]["horizons"]["1"]["net_return_pct"] == pytest.approx(
        0.8601, abs=1e-3)
    for horizon in ("3", "5"):
        assert rows["600001"]["horizons"][horizon]["net_return_pct"] is None
        assert (rows["600001"]["horizons"][horizon]["exclusion_reason"]
                == "insufficient_horizon")
    # 至少一个样本有净收益 → 端点 ok，缺格仍待评估。
    assert result["status"] == "ok"
    assert result["reason"] is None


# ─── prep_view 空仓验证规则表 ─────────────────────────────────────────────


def _market_snapshot(*, score=None, state=None, pct=None,
                     lu=None, ld=None, broken=None, premium=None,
                     sentiment_score=None) -> dict:
    snapshot: dict = {"sentiment": {"position_pct": pct}}
    market: dict = {}
    if score is not None:
        market["score"] = score
    if state is not None:
        market["state"] = state
    inputs = {}
    if lu is not None:
        inputs["limit_up_cnt"] = lu
    if ld is not None:
        inputs["limit_down_cnt"] = ld
    if broken is not None:
        inputs["broken_rate"] = broken
    if premium is not None:
        inputs["yesterday_premium_pct"] = premium
    if inputs:
        market["inputs"] = inputs
    if market:
        snapshot["review"] = {"market": market}
    if sentiment_score is not None:
        snapshot["sentiment"]["temperature_score"] = sentiment_score
    return snapshot


def _verification(current, prev, *, prev_meta=None, trade_date=TRADE_DATE):
    return prep_view._build_verification(
        trade_date=trade_date,
        current=current,
        prev=prev,
        prev_meta=prev_meta if prev_meta is not None
        else {"trade_date": "2026-09-10"},
    )


def test_verification_current_snapshot_missing() -> None:
    result = _verification(None, _market_snapshot(score=70.0, state="neutral",
                                                  pct=0.0))
    assert result["conclusion"] == "not_evaluable"
    assert result["reason"] == "current_snapshot_missing"
    assert result["advice"] == "empty"


def test_verification_no_prev_snapshot() -> None:
    result = _verification(
        _market_snapshot(score=80.0, state="strong", pct=0.5), None)
    assert result["conclusion"] == "not_evaluable"
    assert result["reason"] == "no_prev_snapshot"


def test_verification_prev_position_missing() -> None:
    result = _verification(
        _market_snapshot(score=80.0, state="strong", pct=0.5),
        _market_snapshot(score=70.0, state="neutral"))
    assert result["conclusion"] == "not_evaluable"
    assert result["reason"] == "prev_position_missing"


def test_verification_temperature_basis_mismatch() -> None:
    prev = _market_snapshot(score=70.0, state="neutral", pct=0.0)
    prev.pop("review")
    prev["sentiment"]["temperature_score"] = 70.0
    current = _market_snapshot(score=80.0, state="strong", pct=0.5)
    result = _verification(current, prev)
    assert result["conclusion"] == "not_evaluable"
    assert result["reason"] == "temperature_basis_mismatch"
    assert result["evidence"]["score_change"] is None


def test_verification_empty_correct_on_weak_state() -> None:
    result = _verification(
        _market_snapshot(score=60.0, state="weak", pct=0.5, lu=30, ld=40),
        _market_snapshot(score=70.0, state="neutral", pct=0.0),
    )
    assert result["conclusion"] == "correct"
    assert result["advice"] == "empty"
    assert result["evidence"]["score_change"] == -10.0
    assert result["evidence"]["prev_state"] == "neutral"
    assert result["evidence"]["current_state"] == "weak"


def test_verification_empty_correct_on_limit_down_lead() -> None:
    result = _verification(
        _market_snapshot(score=60.0, state="neutral", pct=0.5, lu=10, ld=10),
        _market_snapshot(score=70.0, state="neutral", pct=0.0),
    )
    assert result["conclusion"] == "correct"


def test_verification_empty_neutral_when_limit_counts_contradict() -> None:
    result = _verification(
        _market_snapshot(score=60.0, state="neutral", pct=0.5, lu=80, ld=3),
        _market_snapshot(score=70.0, state="neutral", pct=0.0),
    )
    assert result["conclusion"] == "neutral"
    assert result["reason"] is None


def test_verification_empty_wrong_on_strong_up() -> None:
    result = _verification(
        _market_snapshot(score=80.0, state="strong", pct=0.5, lu=88, ld=3),
        _market_snapshot(score=70.0, state="neutral", pct=0.0),
    )
    assert result["conclusion"] == "wrong"
    assert result["advice"] == "empty"


def test_verification_empty_neutral_on_up_without_strong() -> None:
    result = _verification(
        _market_snapshot(score=80.0, state="neutral", pct=0.5, lu=88, ld=3),
        _market_snapshot(score=70.0, state="neutral", pct=0.0),
    )
    assert result["conclusion"] == "neutral"


def test_verification_empty_neutral_when_change_zero() -> None:
    result = _verification(
        _market_snapshot(score=70.0, state="neutral", pct=0.5),
        _market_snapshot(score=70.0, state="neutral", pct=0.0),
    )
    assert result["conclusion"] == "neutral"


def test_verification_empty_not_evaluable_when_limit_counts_missing() -> None:
    result = _verification(
        _market_snapshot(score=60.0, state="neutral", pct=0.5),
        _market_snapshot(score=70.0, state="neutral", pct=0.0),
    )
    assert result["conclusion"] == "not_evaluable"
    assert result["reason"] == "limit_counts_missing"


def test_verification_empty_not_evaluable_when_state_missing_on_up() -> None:
    result = _verification(
        _market_snapshot(score=80.0, pct=0.5, lu=88, ld=3),
        _market_snapshot(score=70.0, state="neutral", pct=0.0),
    )
    assert result["conclusion"] == "not_evaluable"
    assert result["reason"] == "state_missing"


def test_verification_hold_correct_on_up_neutral() -> None:
    result = _verification(
        _market_snapshot(score=80.0, state="neutral", pct=0.5),
        _market_snapshot(score=70.0, state="neutral", pct=0.5),
    )
    assert result["conclusion"] == "correct"
    assert result["advice"] == "hold"


def test_verification_hold_wrong_on_down_weak() -> None:
    result = _verification(
        _market_snapshot(score=60.0, state="weak", pct=0.5),
        _market_snapshot(score=70.0, state="neutral", pct=0.5),
    )
    assert result["conclusion"] == "wrong"
    assert result["advice"] == "hold"


def test_verification_hold_not_evaluable_when_state_missing() -> None:
    result = _verification(
        _market_snapshot(score=80.0, pct=0.5),
        _market_snapshot(score=70.0, state="neutral", pct=0.5),
    )
    assert result["conclusion"] == "not_evaluable"
    assert result["reason"] == "state_missing"


def test_verification_hold_neutral_on_up_weak() -> None:
    result = _verification(
        _market_snapshot(score=80.0, state="weak", pct=0.5),
        _market_snapshot(score=70.0, state="neutral", pct=0.5),
    )
    assert result["conclusion"] == "neutral"


def test_review_context_includes_verification_on_missing_plan_too() -> None:
    """空态路径也必须带 verification（结构恒存在）。"""

    current = _market_snapshot(score=80.0, state="strong", pct=0.5)
    context = prep_view.build_review_context(
        trade_date=TRADE_DATE, current=current,
        prev=_market_snapshot(score=70.0, state="neutral", pct=0.5),
        prev_meta={"trade_date": PLAN_DATE, "error": None}, prev_plan=None)
    assert context["plan_effect"]["available"] is False
    verification = context["plan_effect"]["market"]["verification"]
    assert verification["conclusion"] == "correct"
    assert verification["evidence"]["score_change"] == 10.0
