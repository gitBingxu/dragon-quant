"""0.8 兼容投影：``candidates[]`` = ``review_candidates[]`` 的同结果视图。

覆盖技术方案 §4.5：字段映射 / 缺失语义 / 截断兜底 / review 不可用，
以及 buy 前置闸门依赖的 ``review_pool`` / ``review_eligible`` 标记。
"""

from __future__ import annotations

from dragon_quant.review_account.candidate_view import (
    is_review_eligible,
    is_review_pool,
    project_candidates,
    review_candidate_view,
)

DAY = "2026-10-29"


def _review(code="600001", *, name="龙头A", score=75.0, grade="A",
            eligibility="eligible", position=4.0, reason_code=None,
            gate_trace=None):
    return {
        "code": code,
        "name": name,
        "trade_date": DAY,
        "review_score": score,
        "review_grade": grade,
        "eligibility": eligibility,
        "position_pct": position,
        "reason_code": reason_code,
        "gate_trace": gate_trace or [],
        "role": "leader",
        "mainline": {"board_name": "机器人", "rank": 1, "state": "strengthening"},
        "warnings": [],
    }


def _raw(code="600001", **overrides):
    base = {
        "code": code,
        "name": "原始名",
        "trade_date": DAY,
        "composite_score": 88.0,
        "is_true_dragon": True,
        "concepts": ["机器人"],
        "main_net_amount": 1.2e7,
        "announcements": [{"title": "公告", "date": "2026-10-28"}],
        "facts": {
            "in_pool_today": True,
            "risk_announcement": {"level": "hard", "title": "立案调查公告"},
        },
    }
    base.update(overrides)
    return base


def test_projection_maps_review_fields_and_keeps_raw_facts():
    view = review_candidate_view(_review(), raw=_raw(), rank=1)

    assert view["code"] == "600001"
    assert view["name"] == "原始名"              # raw 补齐优先
    assert view["composite_score"] == 75.0        # = review_score
    assert view["rank"] == 1
    assert view["verdict"] == "strong"            # S/A ∧ position>0
    assert view["verdict_label"] == "强烈建议"
    assert view["is_true_dragon"] is None         # 旧身份绝不复活
    assert view["review_pool"] is True
    assert view["review_eligible"] is True
    assert view["facts"] == _raw()["facts"]
    assert view["concepts"] == ["机器人"]
    assert view["main_net_amount"] == 1.2e7
    assert view["announcements"] == _raw()["announcements"]
    assert is_review_pool(view) and is_review_eligible(view)


def test_verdict_mapping_is_deterministic():
    assert review_candidate_view(
        _review(grade="S", position=5.0), raw=_raw(), rank=1)["verdict"] == "strong"
    assert review_candidate_view(
        _review(grade="A", position=5.0), raw=_raw(), rank=2)["verdict"] == "strong"
    assert review_candidate_view(
        _review(grade="B", position=5.0), raw=_raw(), rank=3)["verdict"] == "light"
    assert review_candidate_view(
        _review(grade="B", position=0.0, eligibility="not_qualified",
                reason_code="hard_risk"), raw=_raw(), rank=4)["verdict"] == "ignore"
    assert review_candidate_view(
        _review(grade=None, score=None, position=None, eligibility="unknown",
                reason_code="data_insufficient"),
        raw=_raw(), rank=5)["verdict"] == "unrated"


def test_eligible_but_zero_position_is_not_buyable():
    view = review_candidate_view(
        _review(position=0.0, grade="B", reason_code="position_below_min_unit"),
        raw=_raw(), rank=1)
    assert view["review_eligible"] is False
    assert is_review_eligible(view) is False
    assert "低于最小单位" in view["ignore_reason"]


def test_ignore_reason_uses_reason_label_and_failed_gate_detail():
    trace = [
        {"gate": "G0_data", "evaluated": True, "passed": True,
         "reason_code": None, "detail": None},
        {"gate": "G3a_liquidity", "evaluated": True, "passed": False,
         "reason_code": "illiquid", "detail": "amount=1000"},
    ]
    view = review_candidate_view(
        _review(position=0.0, grade="C", eligibility="not_qualified",
                reason_code="illiquid", gate_trace=trace),
        raw=_raw(), rank=1)
    assert "流动性不足" in view["ignore_reason"]
    assert "amount=1000" in view["ignore_reason"]


def test_hard_risk_ignore_reason_prepends_announcement_title():
    view = review_candidate_view(
        _review(position=0.0, grade="C", eligibility="not_qualified",
                reason_code="hard_risk"),
        raw=_raw(), rank=1)
    assert "立案调查公告" in view["ignore_reason"]
    assert "硬风险" in view["ignore_reason"]


def test_review_only_code_uses_review_name_and_never_fabricates_facts():
    view = review_candidate_view(_review(code="600999", name="新龙"), raw=None, rank=7)
    assert view["name"] == "新龙"
    assert view["facts"] is None
    assert view["concepts"] is None
    assert view["main_net_amount"] is None
    assert view["announcements"] is None


def test_project_candidates_is_review_pool_plus_truncated_raw():
    review = [_review(code="600001", score=80.0),
              _review(code="600002", name="二龙", score=70.0, grade="C",
                      eligibility="not_qualified", position=0.0,
                      reason_code="not_mainline")]
    raw = [_raw(code="600001"), _raw(code="600003", name="截断龙")]
    out = project_candidates(review, raw, DAY)

    assert [item["code"] for item in out] == ["600001", "600002", "600003"]
    assert [item["rank"] for item in out] == [1, 2, None]
    truncated = out[2]
    assert truncated["verdict"] == "unrated"
    assert truncated["composite_score"] is None
    assert truncated["position_pct"] is None
    assert truncated["ignore_reason"] == "review_pool_truncated"
    assert truncated["review_pool"] is True
    assert truncated["review_eligible"] is False
    assert truncated["name"] == "截断龙"
    assert out[1]["verdict"] == "ignore"


def test_project_candidates_marks_review_unavailable_without_fallback():
    out = project_candidates([], [_raw(code="600001")], DAY, review_available=False)
    assert len(out) == 1
    assert out[0]["verdict"] == "unrated"
    assert out[0]["composite_score"] is None
    assert out[0]["ignore_reason"] == "review_unavailable"
    assert out[0]["review_eligible"] is False


def test_projection_never_raises_on_malformed_entry():
    out = project_candidates([{"code": "600001", "position_pct": "bad",
                               "eligibility": "eligible",
                               "gate_trace": "not-a-list"}],
                             [{"code": "600001"}], DAY)
    assert out[0]["code"] == "600001"
    assert out[0]["verdict"] == "unrated"
    assert out[0]["composite_score"] is None
