"""``eval_candidate`` 闸门中立：A 口径下不伪造分数。"""

from __future__ import annotations

from dragon_quant.bs_eval.candidates import eval_candidate


ROW = {
    "trade_date": "2026-09-01", "code": "600001", "name": "样本",
    "rank": 1, "composite_score": None, "is_true_dragon": False,
}


def test_candidate_is_gate_neutral_and_does_not_fabricate_scores():
    candidate = eval_candidate(ROW)
    assert candidate["review_pool"] is True
    assert candidate["review_eligible"] is True
    assert candidate["is_true_dragon"] is True
    assert candidate["composite_score"] is None
    assert candidate["code"] == "600001"
    assert candidate["trade_date"] == "2026-09-01"


def test_missing_optional_fields_project_to_empty_name_and_none():
    candidate = eval_candidate({"trade_date": "2026-09-01", "code": "600002"})
    assert candidate["name"] == ""
    assert candidate["rank"] is None
    assert candidate["composite_score"] is None
