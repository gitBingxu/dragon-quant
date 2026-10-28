"""M5：T+1 反馈纯函数与计划复盘（无 I/O）。

覆盖产品 §11–§12 / 技术 §5、§7、§9.2：

* 四态判定 confirmed / invalidated / weakened / unknown 的条件顺序；
* ``weakened → confirmed`` 回踩确认只允许一次，且必须留痕；
* 冻结预期来自 ``prev_plan.feedback_plans``，**不得**在 T+1 重算；
* 只用 ``prev_plan.positions`` 的代码，忽略当日竞价池里的其它代码；
* 竞价缺失/未验证或 T+1 行情缺失 → ``unknown``，绝不当作 confirmed；
* ``plan_review`` 四类归因（market / pattern / execution / data）与不编造成交。
"""

from __future__ import annotations

import pytest

from dragon_quant.review_account.prep.review.feedback import (
    compute_feedback,
    compute_plan_review,
)

T = "2026-10-28"
PREV = "2026-10-27"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _expected(**overrides) -> dict:
    base = {"gap_low": 3.0, "gap_high": 7.0, "ratio_low": 1.2, "seal_by": "10:00"}
    base.update(overrides)
    return base


def _position(code: str, **overrides) -> dict:
    base = {
        "code": code,
        "name": f"股票{code[-1]}",
        "role": "leader",
        "review_grade": "S",
        "position_pct": 12.0,
        "eligibility": "eligible",
        "reason_code": None,
    }
    base.update(overrides)
    return base


def _prev_plan(codes=("600001",), expected=None, market_state="strong") -> dict:
    expectation = expected or _expected()
    return {
        "source_trade_date": PREV,
        "context_version": "sc_v1",
        "context_hash": "0123456789ab",
        "market_state": market_state,
        "positions": [_position(code) for code in codes],
        "feedback_plans": [
            {"code": code, "role": "leader", "expected": expectation,
             "position_pct": 12.0}
            for code in codes
        ],
        "degraded": [],
    }


def _auction_row(code: str, *, price=10.45, amount=1.3e7, prev_amounts=(1.0e7,),
                 verified=True, source="auction_assets", reason=None) -> dict:
    return {
        "code": code,
        "price": price,
        "amount": amount,
        "matched": 1_000_000,
        "unmatched": 0,
        "source": source,
        "verified": verified,
        "prev_amounts": list(prev_amounts),
        "unknown_reason": reason,
    }


def _auction(*rows: dict) -> dict:
    return {
        "as_of": T,
        "rows_by_code": {row["code"]: row for row in rows},
        "sources": sorted({row["source"] for row in rows}),
        "backfill": {"attempted": 0, "ok": 0, "failed": []},
        "unverified_count": sum(1 for row in rows if not row["verified"]),
    }


def _daily_row(date: str, *, open_=10.0, close=10.0, high=None, low=None,
               volume=1_000_000) -> dict:
    return {
        "date": date,
        "open": open_,
        "high": close if high is None else high,
        "low": close if low is None else low,
        "close": close,
        "volume": volume,
        "amount": 1e7,
        "turnover": 1.0,
    }


def _daily(code: str = "600001", *, prev_close=10.0, t1_open=10.2,
           t1_close=10.9, t1_high=None) -> dict:
    return {
        "as_of": T,
        "trade_date": T,
        "rows": {
            code: [
                _daily_row(PREV, open_=9.9, close=prev_close, high=prev_close,
                           low=prev_close - 0.1),
                _daily_row(T, open_=t1_open, close=t1_close,
                           high=t1_high if t1_high is not None else t1_close,
                           low=min(t1_open, t1_close)),
            ]
        },
    }


def _intraday(code: str = "600001", bars=()) -> dict:
    return {
        "as_of": T,
        "trade_date": T,
        "rows": {code: [{"time": moment, "close": close, "volume": 1000.0}
                        for moment, close in bars]},
        "checkpoint": ["09:25", "10:00"],
    }


def _item(feedback: dict, code: str = "600001") -> dict:
    return next(item for item in feedback["items"] if item["code"] == code)


# ---------------------------------------------------------------------------
# 四态判定
# ---------------------------------------------------------------------------


def test_confirmed_when_gap_ratio_and_first_seal_meet_expectation() -> None:
    feedback = compute_feedback(
        _prev_plan(), _auction(_auction_row("600001")),
        _intraday(bars=[("09:40", 10.6), ("09:45", 11.0)]), _daily(), T)

    item = _item(feedback)
    assert feedback["checkpoint"] == "auction+close"
    assert item["state"] == "confirmed"
    assert item["gap_pct"] == pytest.approx(4.5)
    assert item["auction_ratio"] == pytest.approx(1.3)
    assert item["sealed_by"] == "09:45"
    assert item["allowed_actions"] == ["execute_plan", "execute_partial"]
    assert item["unknown_reason"] is None


def test_confirmed_without_intraday_when_close_is_strong() -> None:
    """5 分钟缺失只降级为 close_only；收盘涨幅达标仍可确认。"""

    feedback = compute_feedback(
        _prev_plan(), _auction(_auction_row("600001")), None, _daily(), T)

    item = _item(feedback)
    assert feedback["checkpoint"] == "close_only"
    assert item["state"] == "confirmed"
    assert item["sealed_by"] is None


def test_invalidated_when_gap_below_expected_low_by_margin() -> None:
    row = _auction_row("600001", price=9.6)          # gap -4% < 3% - 3%
    feedback = compute_feedback(_prev_plan(), _auction(row), None,
                                _daily(t1_open=9.6, t1_close=9.4), T)

    item = _item(feedback)
    assert item["state"] == "invalidated"
    assert item["allowed_actions"] == ["cancel_entry", "exit_via_sell_rules"]


def test_invalidated_when_close_breaks_previous_close() -> None:
    row = _auction_row("600001", price=10.1)         # gap +1%：低于预期但未破坏
    feedback = compute_feedback(
        _prev_plan(), _auction(row), None,
        _daily(t1_open=10.1, t1_close=9.95), T)       # 收盘破昨收

    assert _item(feedback)["state"] == "invalidated"


def test_invalidated_when_price_breaks_limit_twice() -> None:
    row = _auction_row("600001", price=10.2)
    bars = [("09:35", 11.0), ("09:40", 10.8), ("09:45", 11.0), ("09:50", 10.7)]
    feedback = compute_feedback(
        _prev_plan(), _auction(row), _intraday(bars=bars),
        _daily(t1_open=10.3, t1_close=10.6), T)

    item = _item(feedback)
    assert item["state"] == "invalidated"
    assert item["evidence"]["broken_count"] == 2


def test_weakened_when_below_expectation_but_not_broken() -> None:
    row = _auction_row("600001", price=10.1, amount=9.0e6)   # gap 1%，ratio 0.9
    feedback = compute_feedback(
        _prev_plan(), _auction(row), None,
        _daily(t1_open=10.1, t1_close=10.3), T)

    item = _item(feedback)
    assert item["state"] == "weakened"
    assert item["allowed_actions"] == ["downgrade_half", "wait_pullback"]


def test_weakened_to_confirmed_recovery_is_recorded_once() -> None:
    row = _auction_row("600001", price=10.1, amount=1.1e7)   # gap 弱，量能回升
    feedback = compute_feedback(
        _prev_plan(), _auction(row), None,
        _daily(t1_open=10.1, t1_close=10.5), T)               # 重新站上前收

    item = _item(feedback)
    assert item["state"] == "confirmed"
    assert item["transition"] == "weakened->confirmed"
    transitions = [entry for entry in feedback["items"]
                   if entry.get("transition")]
    assert len(transitions) == 1


def test_unknown_when_auction_backfill_failed() -> None:
    row = _auction_row("600001", price=None, amount=None, prev_amounts=(),
                       verified=False, source="missing",
                       reason="auction_backfill_failed")
    feedback = compute_feedback(_prev_plan(), _auction(row), None, _daily(), T)

    item = _item(feedback)
    assert item["state"] == "unknown"
    assert item["unknown_reason"] == "auction_backfill_failed"
    assert item["allowed_actions"] == ["observe_only"]
    assert "auction_backfill_failed" in feedback["degraded"]


def test_unknown_when_t1_quote_is_missing() -> None:
    daily = {"as_of": T, "trade_date": T, "rows": {
        "600001": [_daily_row(PREV, close=10.0)]}}
    feedback = compute_feedback(_prev_plan(), _auction(_auction_row("600001")),
                                None, daily, T)

    item = _item(feedback)
    assert item["state"] == "unknown"
    assert item["unknown_reason"] == "t1_quote_missing"


def test_frozen_expectations_are_used_and_not_recomputed_from_config() -> None:
    """冻结 gap_low=1.0：+1.5% 高开应确认，而不是按 CONFIG leader 的 3% 重算。"""

    plan = _prev_plan(expected=_expected(gap_low=1.0))
    row = _auction_row("600001", price=10.15)       # gap +1.5%
    feedback = compute_feedback(
        plan, _auction(row), None,
        _daily(t1_open=10.2, t1_close=10.9), T)

    assert _item(feedback)["state"] == "confirmed"
    assert _item(feedback, "600001")["expectations_source"] == "prev_plan"


def test_feedback_only_uses_prev_plan_codes_not_today_pool() -> None:
    feedback = compute_feedback(
        _prev_plan(), _auction(_auction_row("600001"),
                               _auction_row("600999")),
        None, _daily(), T)

    assert [item["code"] for item in feedback["items"]] == ["600001"]


def test_feedback_empty_without_prev_plan_positions_is_not_an_error() -> None:
    empty = {"source_trade_date": None, "positions": [], "feedback_plans": []}
    feedback = compute_feedback(empty, _auction(), None, _daily(), T)

    assert feedback["items"] == []
    assert feedback["checkpoint"] == "none"
    assert feedback["degraded"] == []


# ---------------------------------------------------------------------------
# plan_review
# ---------------------------------------------------------------------------


def _multi_plan() -> dict:
    codes = ("600001", "600002", "600003", "600004")
    return {
        "source_trade_date": PREV,
        "market_state": "strong",
        "positions": [
            _position("600001", position_pct=12.0),
            _position("600002", position_pct=12.0),
            _position("600003", position_pct=8.0),
            _position("600004", position_pct=4.0, role="follower",
                      review_grade="B"),
        ],
        "feedback_plans": [
            {"code": code, "role": "leader", "expected": _expected(),
             "position_pct": 12.0}
            for code in codes
        ],
        "degraded": [],
    }


def _multi_daily() -> dict:
    return {
        "as_of": T, "trade_date": T,
        "rows": {
            "600001": [_daily_row(PREV, close=10.0),
                       _daily_row(T, open_=10.0, close=9.6)],   # outcome -4%
            "600002": [_daily_row(PREV, close=10.0),
                       _daily_row(T, open_=10.0, close=11.0)],  # outcome +10%
            "600003": [_daily_row(PREV, close=10.0),
                       _daily_row(T, open_=10.0, close=9.9)],   # outcome -1%
            "600004": [_daily_row(PREV, close=10.0),
                       _daily_row(T, open_=10.0, close=9.8)],   # outcome -2%
        },
    }


def _multi_feedback() -> dict:
    return {
        "items": [
            {"code": "600001", "state": "confirmed", "close_pct": -4.0,
             "unknown_reason": None},
            {"code": "600002", "state": "invalidated", "close_pct": 10.0,
             "unknown_reason": None},
            {"code": "600003", "state": "confirmed", "close_pct": 2.0,
             "unknown_reason": None},
            {"code": "600004", "state": "unknown", "close_pct": 5.0,
             "unknown_reason": "auction_backfill_failed"},
        ],
        "degraded": ["auction_backfill_failed"],
    }


def test_plan_review_attributes_market_pattern_execution_and_data() -> None:
    review = compute_plan_review(
        _multi_plan(), _multi_feedback(), None, _multi_daily(), T,
        holdings={"codes": ["600001", "600002", "600004"], "degraded": []},
        current_market={"state": "weak"})

    assert review["plan_date"] == PREV
    assert review["eval_date"] == T
    attribution = review["attribution"]
    assert attribution["market"] == 1
    assert set(review["attribution_evidence"]["pattern"]) == {"600001", "600002"}
    assert review["attribution_evidence"]["execution"] == ["600003"]
    assert review["attribution_evidence"]["data"] == ["600004"]

    items = {item["code"]: item for item in review["items"]}
    assert items["600003"]["planned_action"] == "execute_plan"
    assert items["600003"]["actual_position"] == 0
    assert items["600001"]["actual_position"] is None      # 持仓中，规模未知
    assert items["600001"]["actual_entry_price"] is None   # 绝不编造成交价
    assert items["600003"]["outcome_pct"] == pytest.approx(-1.0)


def test_plan_review_marks_execution_data_unavailable_without_fabricating() -> None:
    review = compute_plan_review(
        _multi_plan(), _multi_feedback(), None, _multi_daily(), T,
        holdings=None, current_market=None)

    items = {item["code"]: item for item in review["items"]}
    assert all(item["actual_position"] is None for item in items.values())
    assert all(item["actual_entry_price"] is None for item in items.values())
    assert review["attribution"]["execution"] == 0
    assert "execution_data_unavailable" in review["degraded"]


def test_plan_review_marks_invalidated_zero_plan_as_correct_avoidance() -> None:
    plan = _multi_plan()
    plan["positions"][1]["position_pct"] = 0.0
    plan["positions"][1]["eligibility"] = "not_qualified"
    review = compute_plan_review(
        plan, _multi_feedback(), None, _multi_daily(), T,
        holdings={"codes": [], "degraded": []})

    no_trade = {entry["code"]: entry for entry in review["no_trade"]}
    assert no_trade["600002"]["judgement"] == "correct_avoidance"
    items = {item["code"]: item for item in review["items"]}
    assert items["600002"]["planned_action"] == "cancel_entry"


# ---------------------------------------------------------------------------
# 冻结预期缺失 / 主线破坏 / 竞价额比历史回退（验收必须修改项 1、2）
# ---------------------------------------------------------------------------


def test_incomplete_frozen_expectations_yield_stable_reason() -> None:
    plan = _prev_plan(expected=_expected())
    plan["feedback_plans"][0]["expected"].pop("ratio_low")
    feedback = compute_feedback(
        plan, _auction(_auction_row("600001")), None,
        _daily(t1_open=10.2, t1_close=10.9), T)

    item = _item(feedback)
    assert item["state"] == "unknown"
    assert item["unknown_reason"] == "expectations_incomplete"
    assert item["allowed_actions"] == ["observe_only"]
    assert item["evidence"]["missing_expected_fields"] == ["ratio_low"]
    assert "expectations_incomplete" in feedback["degraded"]


def test_missing_expectations_are_distinct_from_incomplete_ones() -> None:
    plan = {
        "source_trade_date": PREV,
        "positions": [_position("600001", role=None, eligibility="unknown")],
        "feedback_plans": [],
    }
    feedback = compute_feedback(
        plan, _auction(_auction_row("600001")), None,
        _daily(t1_open=10.2, t1_close=10.9), T)

    item = _item(feedback)
    assert item["state"] == "unknown"
    assert item["unknown_reason"] == "expectations_missing"
    assert item["evidence"]["missing_expected_fields"] == []


def test_mainline_dropped_out_of_top3_invalidates_with_evidence() -> None:
    plan = _prev_plan()
    plan["positions"][0]["mainline"] = {"board_name": "机器人", "rank": 1}
    feedback = compute_feedback(
        plan, _auction(_auction_row("600001", price=10.1)), None,
        _daily(t1_open=10.2, t1_close=10.9), T,
        current_mainlines=[{"board_name": "AI算力", "rank": 1}])

    item = _item(feedback)
    assert item["state"] == "invalidated"
    assert item["evidence"]["mainline_dropped"] is True


def test_auction_ratio_uses_history_mean_when_yesterday_is_missing() -> None:
    row = _auction_row("600001", amount=1.0e7,
                       prev_amounts=(None, 1.0e7, 1.2e7))
    feedback = compute_feedback(
        _prev_plan(), _auction(row), None,
        _daily(t1_open=10.2, t1_close=10.9), T)

    item = _item(feedback)
    # 1.0e7 / mean(1.0e7, 1.2e7) = 0.9091：昨日缺失时用历史均值，而非取较远一日。
    assert item["auction_ratio"] == pytest.approx(0.9091, abs=1e-4)
    assert item["state"] == "weakened"


def test_auction_ratio_prefers_yesterday_over_history() -> None:
    row = _auction_row("600001", amount=1.3e7,
                       prev_amounts=(1.0e7, 2.0e7))
    feedback = compute_feedback(
        _prev_plan(), _auction(row), None,
        _daily(t1_open=10.2, t1_close=10.9), T)

    assert _item(feedback)["auction_ratio"] == pytest.approx(1.3)


def test_auction_ratio_none_when_all_history_missing_never_fabricates_one() -> None:
    row = _auction_row("600001", amount=1.3e7, prev_amounts=(None, None))
    feedback = compute_feedback(
        _prev_plan(), _auction(row), None,
        _daily(t1_open=10.2, t1_close=10.9), T)

    item = _item(feedback)
    assert item["auction_ratio"] is None
    assert item["state"] != "confirmed"
