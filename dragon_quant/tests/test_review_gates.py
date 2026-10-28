"""M4：建仓资格闸门（责任链，G0–G7）。

覆盖产品方案 §10.1：

* 顺序即优先级，命中即短路；硬风险不可被高分解除（AC-P4）；
* ``null``（数据不足 / 置信不足）与 ``0``（明确不参与）语义可区分（AC-P3）；
* ``diagnostic_non_mainline`` 不获得 follower 角色或仓位上限（AC-P18）。
"""

from __future__ import annotations

from dragon_quant.review_account.prep.review.config import CONFIG
from dragon_quant.review_account.prep.review.gates import (
    GATE_BLOCKED,
    GATE_PASS,
    GATE_UNKNOWN,
    evaluate_gates,
)


def _cand(**overrides) -> dict:
    base = {
        "code": "600001",
        "role": "leader",
        "mainline_hit": True,
        "review_score": 86.6,
        "review_grade": "S",
        "confidence": 0.92,
        "coverage": 1.0,
        "pool_available": True,
        "t_pct": 9.98,
        "amount": 1_500_000_000.0,
        "suspended": False,
        "limit_up_type": "换手板",
        "risk": {"level": "none"},
    }
    base.update(overrides)
    return base


def _trace(gates: dict) -> dict[str, dict]:
    return {item["gate"]: item for item in gates["gate_trace"]}


def test_all_gates_pass_keeps_trace_in_order() -> None:
    gates = evaluate_gates(_cand(), {}, CONFIG)
    assert gates["gate_status"] == GATE_PASS
    assert gates["reason_code"] is None
    assert [item["gate"] for item in gates["gate_trace"]] == [
        "G0_data", "G1_hard_risk", "G2_suspended", "G3a_liquidity",
        "G3b_liquidity", "G4_mainline", "G5_score", "G6_grade", "G7_confidence",
    ]
    assert all(item["evaluated"] for item in gates["gate_trace"])
    assert all(item["passed"] for item in gates["gate_trace"])


# ─── G0 数据不足 ────────────────────────────────────────────────────────────


def test_g0_pool_missing_and_no_t_pct_is_unknown_not_zero() -> None:
    gates = evaluate_gates(_cand(pool_available=False, t_pct=None), {}, CONFIG)
    assert gates["gate_status"] == GATE_UNKNOWN
    assert gates["reason_code"] == "data_insufficient"
    assert _trace(gates)["G0_data"]["passed"] is False
    assert _trace(gates)["G1_hard_risk"]["evaluated"] is False


def test_g0_coverage_below_floor_is_unknown() -> None:
    gates = evaluate_gates(_cand(coverage=0.4), {}, CONFIG)
    assert gates["gate_status"] == GATE_UNKNOWN
    assert gates["reason_code"] == "data_insufficient"


def test_g0_null_score_is_unknown() -> None:
    gates = evaluate_gates(
        _cand(review_score=None, review_grade=None, coverage=1.0), {}, CONFIG)
    assert gates["gate_status"] == GATE_UNKNOWN
    assert gates["reason_code"] == "data_insufficient"


# ─── G1 硬风险 ──────────────────────────────────────────────────────────────


def test_g1_hard_risk_blocks_even_with_extreme_score() -> None:
    gates = evaluate_gates(
        _cand(review_score=95.0, risk={"level": "hard", "title": "立案调查"}), {}, CONFIG)
    assert gates["gate_status"] == GATE_BLOCKED
    assert gates["reason_code"] == "hard_risk"
    trace = _trace(gates)
    assert trace["G1_hard_risk"]["passed"] is False
    # 硬闸门先于仓位/后续闸门：命中即短路
    assert trace["G2_suspended"]["evaluated"] is False
    assert trace["G4_mainline"]["evaluated"] is False


def test_g1_soft_risk_discounts_but_continues() -> None:
    gates = evaluate_gates(_cand(risk={"level": "soft"}), {}, CONFIG)
    assert gates["gate_status"] == GATE_PASS
    assert gates["risk_coef"] == 0.5


def test_g1_unknown_risk_is_unknown_not_no_risk() -> None:
    gates = evaluate_gates(_cand(risk={"level": "unknown"}), {}, CONFIG)
    assert gates["gate_status"] == GATE_UNKNOWN
    assert gates["reason_code"] == "data_insufficient"
    assert _trace(gates)["G1_hard_risk"]["passed"] is False


# ─── G2 / G3 可交易与流动性 ─────────────────────────────────────────────────


def test_g2_suspended_is_zero() -> None:
    gates = evaluate_gates(_cand(suspended=True), {}, CONFIG)
    assert gates["gate_status"] == GATE_BLOCKED
    assert gates["reason_code"] == "suspended"


def test_g3a_illiquid_is_zero() -> None:
    gates = evaluate_gates(_cand(amount=199_999_999.0), {}, CONFIG)
    assert gates["gate_status"] == GATE_BLOCKED
    assert gates["reason_code"] == "illiquid"


def test_g3a_missing_amount_is_unknown_not_zero() -> None:
    gates = evaluate_gates(_cand(amount=None), {}, CONFIG)
    assert gates["gate_status"] == GATE_UNKNOWN
    assert gates["reason_code"] == "data_insufficient"
    assert _trace(gates)["G3a_liquidity"]["passed"] is False


def test_g3b_weak_liquidity_halves_and_continues() -> None:
    gates = evaluate_gates(_cand(amount=300_000_000.0), {}, CONFIG)
    assert gates["gate_status"] == GATE_PASS
    assert gates["liq_coef"] == 0.5
    assert _trace(gates)["G3b_liquidity"]["detail"] == "liq_coef=0.5"


def test_g3b_full_liquidity_keeps_one() -> None:
    gates = evaluate_gates(_cand(amount=500_000_000.0), {}, CONFIG)
    assert gates["gate_status"] == GATE_PASS
    assert gates["liq_coef"] == 1.0


# ─── G4 非主线（diagnostic 不获得角色 / 上限）──────────────────────────────


def test_g4_not_mainline_blocks_even_with_high_score() -> None:
    gates = evaluate_gates(
        _cand(role=None, mainline_hit=False, review_score=95.0,
              review_grade="S"), {}, CONFIG)
    assert gates["gate_status"] == GATE_BLOCKED
    assert gates["reason_code"] == "not_mainline"
    assert _trace(gates)["G5_score"]["evaluated"] is False


# ─── G5 / G6 分数与档位 ─────────────────────────────────────────────────────


def test_g5_score_below_floor_is_zero() -> None:
    gates = evaluate_gates(_cand(review_score=49.9, review_grade="D"), {}, CONFIG)
    assert gates["gate_status"] == GATE_BLOCKED
    assert gates["reason_code"] == "score_below_floor"


def test_g6_grade_c_has_no_position_base() -> None:
    gates = evaluate_gates(_cand(review_score=70.0, review_grade="C"), {}, CONFIG)
    assert gates["gate_status"] == GATE_BLOCKED
    assert gates["reason_code"] == "grade_no_position"


# ─── G7 置信不足 ────────────────────────────────────────────────────────────


def test_g7_low_confidence_is_null_not_zero() -> None:
    gates = evaluate_gates(
        _cand(review_score=80.0, review_grade="A", confidence=0.59), {}, CONFIG)
    assert gates["gate_status"] == GATE_UNKNOWN
    assert gates["reason_code"] == "confidence_insufficient"


# ─── 一字板执行风险 ─────────────────────────────────────────────────────────


def test_one_word_board_halves_position_and_warns() -> None:
    gates = evaluate_gates(_cand(limit_up_type="一字板"), {}, CONFIG)
    assert gates["gate_status"] == GATE_PASS
    assert gates["one_word_coef"] == 0.5
    assert "one_word_execution_risk" in gates["warnings"]
