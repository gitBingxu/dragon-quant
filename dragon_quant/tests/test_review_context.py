"""M4：``strategy_context`` 全候选投影与人读复盘摘要。

覆盖产品方案 §13 / 技术方案 §15.1：

* ``strategy_context`` 是 ``review`` 的投影，数值零漂移（AC-P13）；
* ``positions`` 收录**全部**详细池标的（含 ``position=0/null`` 与原因）；
* 枚举只含技术方案 §5 规定的动作值，不泄漏字符串 ``"null"``；
* ``format_review_lines`` 只负责新段，展示 ``review_score``、``eligibility``
  与「单票建议仓位」。
"""

from __future__ import annotations

import json

from dragon_quant.review_account.advice import format_review_lines
from dragon_quant.review_account.prep.review.config import CONFIG
from dragon_quant.review_account.prep.review.context import (
    build_context,
    prep_frozen_at,
)


def _candidate(code: str, **overrides) -> dict:
    base = {
        "code": code,
        "name": f"样例{code[-1]}",
        "role": "leader",
        "weight_profile": "leader",
        "mainline": {"board_name": "机器人", "rank": 1, "state": "strengthening"},
        "review_score": 86.6,
        "review_grade": "S",
        "confidence": 0.92,
        "dimensions": {"D1": {"score": 91.2, "weight": 0.30, "sub": {"H": 66.7}}},
        "contributions": [{"dim": "D1", "weight_norm": 0.30,
                           "dim_score": 91.2, "points": 27.36}],
        "missing_dims": [],
        "eligibility": "eligible",
        "position_pct": 12.0,
        "position_basis": [{"factor": "grade_base", "value": 12.0}],
        "reason_code": None,
        "gate_trace": [{"gate": "G0_data", "evaluated": True,
                        "passed": True, "reason_code": None, "detail": None}],
        "expectations": {"gap_low": 3.0, "gap_high": 7.0,
                         "ratio_low": 1.2, "seal_by": "10:00"},
        "warnings": [],
    }
    base.update(overrides)
    return base


def _review() -> dict:
    return {
        "trade_date": "2026-10-28",
        "frozen_at": prep_frozen_at("2026-10-28"),
        "config_version": "review_baseline_v1",
        "config_hash": "0123456789ab",
        "market": {
            "state": "strong",
            "score": 71.0,
            "coeff": 1.0,
            "evidence": ["涨停 63 家 / 跌停 7 家"],
            "unknown_reason": None,
        },
        "mainlines": [
            {
                "board_name": "机器人",
                "state": "strengthening",
                "mainline_score": 91.7,
                "role_map": {"leader": "600001"},
                "evidence": ["涨停 11 家"],
                "unknown_reason": None,
            }
        ],
        "anchors": {
            "positive": [{"code": "600001", "reason": "市场最高 4 板"}],
            "negative": [],
            "risk": {"limit_down_cnt": 7, "broken_rate": 0.221,
                     "height_d3_change": None},
        },
        "portfolio": {"sum_pct": 12.0, "warning": None},
        "degraded": ["mainline_unavailable"],
        "review_candidates": [
            _candidate("600001"),
            _candidate("600009", role=None, weight_profile="diagnostic_non_mainline",
                       mainline=None, review_score=60.5, review_grade="C",
                       confidence=0.72, eligibility="not_qualified",
                       position_pct=0.0, reason_code="not_mainline",
                       expectations=None),
            _candidate("600010", role=None, weight_profile="diagnostic_non_mainline",
                       mainline=None, review_score=None, review_grade=None,
                       confidence=0.0, eligibility="unknown",
                       position_pct=None, reason_code="data_insufficient",
                       expectations=None),
        ],
    }


def _walk_values(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk_values(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_values(value)
    else:
        yield node


def test_prep_frozen_at_is_non_null_canonical_and_deterministic() -> None:
    frozen = prep_frozen_at("2026-10-28")
    assert frozen == "2026-10-28T16:40:00+08:00"       # 盘后决策阶段时点
    assert prep_frozen_at("2026-10-28") == frozen      # 不读 wall clock
    assert prep_frozen_at(None) is None
    assert prep_frozen_at("") is None


def test_context_keeps_injected_frozen_at_without_reading_clock() -> None:
    review = _review()
    context = build_context(review, CONFIG)
    assert context["frozen_at"] == "2026-10-28T16:40:00+08:00"
    assert context["frozen_at"] == prep_frozen_at(review["trade_date"])


def test_context_is_projection_of_every_review_candidate() -> None:
    review = _review()
    context = build_context(review, CONFIG)

    assert context["context_version"] == "sc_v1"
    assert context["trade_date"] == "2026-10-28"
    assert len(context["positions"]) == len(review["review_candidates"])
    assert [item["code"] for item in context["positions"]] == \
        [item["code"] for item in review["review_candidates"]]

    for position, candidate in zip(context["positions"], review["review_candidates"]):
        for key in ("name", "role", "review_score", "review_grade", "confidence",
                    "eligibility", "position_pct", "reason_code", "dimensions",
                    "contributions", "missing_dims", "gate_trace", "mainline"):
            assert position[key] == candidate[key], (candidate["code"], key)


def test_context_market_and_portfolio_match_review() -> None:
    review = _review()
    context = build_context(review, CONFIG)
    assert context["market"]["state"] == review["market"]["state"]
    assert context["market"]["score"] == review["market"]["score"]
    assert context["market"]["coeff"] == review["market"]["coeff"]
    assert context["market"]["anchors"] == review["anchors"]["risk"]
    assert context["portfolio"] == review["portfolio"]
    assert context["mainlines"] == [
        {key: item[key] for key in
         ("board_name", "state", "mainline_score", "role_map", "evidence",
          "unknown_reason")}
        for item in review["mainlines"]
    ]


def test_context_enums_are_complete_and_bounded() -> None:
    context = build_context(_review(), CONFIG)
    enums = context["enums"]
    assert set(enums) == {"state", "role", "action", "reason_code"}
    assert enums["state"] == ["strong", "neutral", "weak", "ice", "unknown"]
    assert enums["role"] == ["leader", "vanguard", "mainstay", "capacity", "follower"]
    assert enums["action"] == [
        "execute_plan", "execute_partial", "downgrade_half", "wait_pullback",
        "cancel_entry", "exit_via_sell_rules", "observe_only", "wait",
    ]
    assert "data_insufficient" in enums["reason_code"]
    assert "position_below_min_unit" in enums["reason_code"]


def test_context_never_leaks_the_string_null() -> None:
    context = build_context(_review(), CONFIG)
    assert "null" not in {value for value in _walk_values(context)
                          if isinstance(value, str)}
    # 真正的缺失仍是 None，不是 "null" 字符串
    assert context["positions"][2]["position_pct"] is None
    assert context["positions"][2]["review_score"] is None


def test_context_data_quality_summarises_degraded_missing_and_confidence() -> None:
    context = build_context(_review(), CONFIG)
    quality = context["data_quality"]
    assert quality["degraded"] == ["mainline_unavailable"]
    assert quality["missing"] == ["600010"]
    assert quality["confidence"] == {"min": 0.0, "max": 0.92, "avg": 0.55}
    assert "disclaimer" in context and "不构成投资建议" in context["disclaimer"]
    # M5：feedback_plans 逐只覆盖全部 positions，冻结预期来自 expectations。
    plans = context["feedback_plans"]
    assert [plan["code"] for plan in plans] == [
        item["code"] for item in context["positions"]]
    leader = plans[0]
    assert leader["expected"] == {"gap_low": 3.0, "gap_high": 7.0,
                                  "ratio_low": 1.2, "seal_by": "10:00"}
    assert [condition["state"] for condition in leader["conditions"]] == [
        "confirmed", "weakened", "invalidated", "unknown"]
    assert leader["allowed_actions"] == ["execute_plan", "execute_partial"]
    assert plans[1]["expected"] is None
    assert plans[1]["unknown_reason"] == "expectations_unavailable"


def test_context_is_json_serialisable_and_deterministic() -> None:
    first = build_context(_review(), CONFIG)
    second = build_context(json.loads(json.dumps(_review(), ensure_ascii=False)), CONFIG)
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
        json.dumps(second, ensure_ascii=False, sort_keys=True)


# ─── 人读新段 ───────────────────────────────────────────────────────────────


def _result(review: dict | None = None) -> dict:
    payload = {"trade_date": "2026-10-28"}
    if review is not None:
        payload["review"] = {key: value for key, value in review.items()
                             if key != "review_candidates"}
        payload["review_candidates"] = review["review_candidates"]
        payload["strategy_context"] = build_context(review, CONFIG)
    return payload


def test_format_review_lines_shows_score_eligibility_and_position() -> None:
    lines = format_review_lines(_result(_review()))
    text = "\n".join(lines)
    assert lines[0].startswith("复盘摘要：市场 strong（71.0，×1.0）")
    assert "机器人（强化）" in lines[0]
    assert "单票建议仓位" in text
    assert "review_score 86.6" in text
    assert "eligibility=eligible" in text
    # 0 与 null 都要逐只出现并带原因；0 必须显式写「不建议新建仓」
    assert "600009" in text and "不建议新建仓：非主线" in text
    assert "600010" in text and "单票建议仓位 null" in text
    assert "清仓" not in text, "AC-P17：单票 0 不得写成应清仓（持仓交给 sell）"
    assert "数据：1 项降级" in text


def test_format_review_lines_survives_disabled_review_with_one_line() -> None:
    assert format_review_lines(
        {"review": {"enabled": False, "reason": "disabled"}}) == [
            "复盘摘要不可用（disabled）"]


def test_format_review_lines_survives_missing_review_with_one_line() -> None:
    assert format_review_lines({}) == ["复盘摘要不可用（missing）"]


def test_format_review_lines_reports_compute_error_as_one_line() -> None:
    lines = format_review_lines(
        {"review": {"enabled": True, "compute_error": "RuntimeError: boom"}})
    assert lines == ["复盘摘要不可用（compute_error）"]
