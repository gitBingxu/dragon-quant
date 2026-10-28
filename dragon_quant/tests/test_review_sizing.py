"""M4：单票建议仓位公式与组合边界。

覆盖产品方案 §10.2 / §10.3：

* 角色上限、市场/置信/风险/流动性/一字板系数；
* 三态 ``null`` / ``0`` / ``>0``（AC-P3）；
* 向下取整到 0.5%，低于 0.5% 归 0；
* ``diagnostic_non_mainline`` 不获得 follower 上限（AC-P18）；
* 组合和 > 100% 告警（AC-P15）。
"""

from __future__ import annotations

from dragon_quant.review_account.prep.review.config import CONFIG
from dragon_quant.review_account.prep.review.sizing import (
    portfolio_summary,
    size_position,
)


def _scored(**overrides) -> dict:
    base = {
        "code": "600001",
        "role": "leader",
        "review_grade": "S",
        "confidence": 1.0,
        "gate_status": "pass",
        "reason_code": None,
        "risk_coef": 1.0,
        "liq_coef": 1.0,
        "one_word_coef": 1.0,
        "warnings": [],
    }
    base.update(overrides)
    return base


def _market(state: str = "strong") -> dict:
    return {"state": state}


# ─── 公式与系数 ─────────────────────────────────────────────────────────────


def test_leader_s_strong_full_conf_is_capped_by_role() -> None:
    result = size_position(_scored(role="leader", review_grade="S"), _market(), CONFIG)
    assert result["eligibility"] == "eligible"
    assert result["position_pct"] == 12.0
    factors = {item["factor"]: item["value"] for item in result["position_basis"]}
    assert factors["grade_base"] == 12.0
    assert factors["role_cap"] == 15.0


def test_vanguard_a_neutral_confidence_band_floors_to_half_unit() -> None:
    # 8 × 0.8 × 0.85 = 5.44 → 5.0
    result = size_position(
        _scored(role="vanguard", review_grade="A", confidence=0.80),
        _market("neutral"), CONFIG)
    assert result["position_pct"] == 5.0


def test_follower_b_neutral_soft_weak_liquidity_is_half_percent() -> None:
    # 4 × 0.8 × 0.70 × 0.5 × 0.5 = 0.56 → 0.5
    result = size_position(
        _scored(role="follower", review_grade="B", confidence=0.70,
                risk_coef=0.5, liq_coef=0.5),
        _market("neutral"), CONFIG)
    assert result["position_pct"] == 0.5


def test_below_min_unit_rounds_to_zero_with_reason() -> None:
    # 4 × 0.8 × 0.70 × 0.5 × 0.5 × 0.5 = 0.28 → 0
    result = size_position(
        _scored(role="follower", review_grade="B", confidence=0.70,
                risk_coef=0.5, liq_coef=0.5, one_word_coef=0.5),
        _market("neutral"), CONFIG)
    assert result["position_pct"] == 0.0
    assert result["eligibility"] == "not_qualified"
    assert result["reason_code"] == "position_below_min_unit"


def test_follower_role_cap_limits_high_grade() -> None:
    # A 档基数 8 > follower 上限 5
    result = size_position(
        _scored(role="follower", review_grade="A"), _market(), CONFIG)
    assert result["position_pct"] == 5.0


def test_capacity_role_cap_is_twelve() -> None:
    result = size_position(
        _scored(role="capacity", review_grade="A"), _market(), CONFIG)
    assert result["position_pct"] == 8.0


def test_market_state_coefficients_come_from_config() -> None:
    expected = {"strong": 12.0, "neutral": 9.5, "weak": 7.0,
                "ice": 4.5, "unknown": 6.0}
    # 12 × 系数，再向下取整到 0.5
    for state, position in expected.items():
        result = size_position(_scored(), _market(state), CONFIG)
        assert result["position_pct"] == position, state


def test_confidence_bands() -> None:
    assert size_position(
        _scored(confidence=0.95), _market(), CONFIG)["position_pct"] == 12.0
    assert size_position(
        _scored(confidence=0.80), _market(), CONFIG)["position_pct"] == 10.0
    assert size_position(
        _scored(confidence=0.65), _market(), CONFIG)["position_pct"] == 8.0


def test_one_word_halves_position() -> None:
    result = size_position(
        _scored(one_word_coef=0.5, warnings=["one_word_execution_risk"]),
        _market(), CONFIG)
    assert result["position_pct"] == 6.0


# ─── 三态语义 ───────────────────────────────────────────────────────────────


def test_gate_unknown_returns_null() -> None:
    result = size_position(
        _scored(gate_status="unknown", reason_code="data_insufficient"),
        _market(), CONFIG)
    assert result["position_pct"] is None
    assert result["eligibility"] == "unknown"
    assert result["reason_code"] == "data_insufficient"


def test_gate_blocked_returns_zero() -> None:
    result = size_position(
        _scored(gate_status="blocked", reason_code="hard_risk"),
        _market(), CONFIG)
    assert result["position_pct"] == 0.0
    assert result["eligibility"] == "not_qualified"
    assert result["reason_code"] == "hard_risk"


def test_diagnostic_non_mainline_gets_no_follower_cap() -> None:
    # role=None 时不得借用 follower 上限：即使 A 档也必须是 0。
    result = size_position(
        _scored(role=None, review_grade="A", gate_status="pass"),
        _market(), CONFIG)
    assert result["position_pct"] == 0.0
    assert result["eligibility"] == "not_qualified"
    assert result["reason_code"] == "not_mainline"


# ─── 组合边界 ───────────────────────────────────────────────────────────────


def test_portfolio_sums_positive_positions_and_warns_above_limit() -> None:
    candidates = [
        {"position_pct": 60.0},
        {"position_pct": 50.0},
        {"position_pct": None},
        {"position_pct": 0.0},
    ]
    summary = portfolio_summary(candidates, CONFIG)
    assert summary["sum_pct"] == 110.0
    assert summary["warning"] == "sum_exceeds_100"


def test_portfolio_below_limit_has_no_warning() -> None:
    summary = portfolio_summary(
        [{"position_pct": 12.0}, {"position_pct": 8.0}], CONFIG)
    assert summary["sum_pct"] == 20.0
    assert summary["warning"] is None


def test_portfolio_ignores_unknown_and_zero() -> None:
    summary = portfolio_summary(
        [{"position_pct": None}, {"position_pct": 0.0}], CONFIG)
    assert summary["sum_pct"] == 0.0
    assert summary["warning"] is None
