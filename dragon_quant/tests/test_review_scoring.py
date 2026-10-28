"""M1：六维复盘分纯评分骨架（纯单测，无 I/O）。

覆盖产品方案 §8（D1–D6 子分映射、缺失重算、贡献与置信度）与 §9.1（角色权重）。
所有断言都通过 ``score_candidate`` 的公开返回值或 ``scoring`` 内部纯函数完成。
"""

from __future__ import annotations

import copy
import dataclasses

import pytest

from dragon_quant.review_account.prep.review import CONFIG, score_candidate
from dragon_quant.review_account.prep.review.scoring import (
    grade_for_score,
    rank_drivers_drags,
)

DIMS = ("D1", "D2", "D3", "D4", "D5", "D6")


def _cand(**overrides: object) -> dict:
    base: dict[str, object] = {
        "code": "600001",
        "height": 4,
        "intra_board_rank": 1,
        "board_limit_up_num": 10,
        "mainline_rank": 1,
        "attr_match": 2,
        "mainline_days": 3,
        "first_seal_time": "09:41:00",
        "open_num": 0,
        "seal_amount": 130_000_000.0,
        "float_market_cap": 10_000_000_000.0,
        "seal_ratio": 0.42,
        "main_net_amount": 84_000_000.0,
        "amount": 1_000_000_000.0,
        "turnover_rate": 12.0,
        "distance_high60": 3.0,
        "chip_concentration": 0.12,
        "profit_ratio": 0.05,
        "limit_up_cnt_20d": 2,
        "next_day_up_rate_20d": 0.9,
        "next_day_up_sample": 5,
    }
    base.update(overrides)
    return base


def _ctx(**overrides: object) -> dict:
    base: dict[str, object] = {"role": "leader"}
    base.update(overrides)
    return base


def _only_d1_cand() -> dict:
    """只保留 D1 子项的候选（leader 覆盖率 0.30 < 0.50）。"""

    return _cand(
        mainline_rank=None,
        attr_match=None,
        mainline_days=None,
        first_seal_time=None,
        open_num=None,
        seal_amount=None,
        seal_ratio=None,
        main_net_amount=None,
        amount=None,
        turnover_rate=None,
        distance_high60=None,
        chip_concentration=None,
        profit_ratio=None,
        limit_up_cnt_20d=None,
        next_day_up_rate_20d=None,
    )


def _sub(cand: dict, dim: str, key: str, ctx: dict | None = None) -> float | None:
    result = score_candidate(cand, ctx or _ctx())
    return result["dimensions"][dim]["sub"][key]


# ---------------------------------------------------------------------------
# D1 龙头质量
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("height", "expected"),
    [(0, 0.0), (3, 50.0), (6, 100.0), (9, 100.0)],
)
def test_d1_height_boundaries(height: int, expected: float) -> None:
    assert _sub(_cand(height=height), "D1", "H") == pytest.approx(expected)


@pytest.mark.parametrize(
    ("rank", "count", "expected"),
    [(1, 10, 100.0), (6, 10, 44.4), (10, 10, 0.0), (1, 1, 100.0)],
)
def test_d1_intra_board_rank_boundaries(rank: int, count: int, expected: float) -> None:
    cand = _cand(intra_board_rank=rank, board_limit_up_num=count)
    assert _sub(cand, "D1", "L") == pytest.approx(expected)


@pytest.mark.parametrize(
    ("count", "expected"), [(2, 25.0), (4, 50.0), (8, 100.0), (12, 100.0)]
)
def test_d1_board_leader_breadth_boundaries(count: int, expected: float) -> None:
    cand = _cand(board_limit_up_num=count, intra_board_rank=1)
    assert _sub(cand, "D1", "B") == pytest.approx(expected)


# ---------------------------------------------------------------------------
# D2 主线地位
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rank", "expected"),
    [(1, 100.0), (2, 80.0), (3, 65.0), (4, 40.0), (5, 40.0), (0, 0.0), (6, 0.0)],
)
def test_d2_mainline_rank_boundaries(rank: int, expected: float) -> None:
    assert _sub(_cand(mainline_rank=rank), "D2", "M1") == pytest.approx(expected)


@pytest.mark.parametrize(
    ("attr_match", "expected"), [(0, 0.0), (1, 50.0), (2, 100.0), (3, 100.0)]
)
def test_d2_attr_match_boundaries(attr_match: int, expected: float) -> None:
    assert _sub(_cand(attr_match=attr_match), "D2", "M2") == pytest.approx(expected)


@pytest.mark.parametrize(
    ("days", "expected"), [(0, 0.0), (1, 33.3), (3, 100.0), (5, 100.0)]
)
def test_d2_mainline_days_boundaries(days: int, expected: float) -> None:
    assert _sub(_cand(mainline_days=days), "D2", "M3") == pytest.approx(expected)


# ---------------------------------------------------------------------------
# D3 封板质量
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seal_time", "expected"),
    [
        ("09:30:00", 100.0),
        ("09:35:00", 100.0),
        ("09:36:00", 85.0),
        ("10:00:00", 85.0),
        ("10:01:00", 65.0),
        ("11:30:00", 65.0),
        ("12:00:00", 65.0),  # 午间不应有首封，按上午收盘桶处理
        ("13:00:00", 45.0),
        ("14:00:00", 45.0),
        ("14:01:00", 25.0),
    ],
)
def test_d3_first_seal_time_boundaries(seal_time: str, expected: float) -> None:
    assert _sub(_cand(first_seal_time=seal_time), "D3", "T") == pytest.approx(expected)


@pytest.mark.parametrize(
    ("open_num", "expected"), [(0, 100.0), (1, 75.0), (2, 50.0), (3, 20.0), (5, 20.0)]
)
def test_d3_open_num_boundaries(open_num: int, expected: float) -> None:
    assert _sub(_cand(open_num=open_num), "D3", "O") == pytest.approx(expected)


@pytest.mark.parametrize(
    ("seal_amount", "expected"), [(50_000_000.0, 50.0), (100_000_000.0, 100.0)]
)
def test_d3_seal_amount_to_float_cap_boundaries(
    seal_amount: float, expected: float
) -> None:
    cand = _cand(seal_amount=seal_amount, float_market_cap=10_000_000_000.0)
    assert _sub(cand, "D3", "S") == pytest.approx(expected)


def test_d3_seal_ratio_boundaries() -> None:
    assert _sub(_cand(seal_ratio=0.25), "D3", "R") == pytest.approx(50.0)
    assert _sub(_cand(seal_ratio=0.50), "D3", "R") == pytest.approx(100.0)
    assert _sub(_cand(seal_ratio=0.80), "D3", "R") == pytest.approx(100.0)


def test_d3_seal_ratio_can_be_derived_from_amounts() -> None:
    cand = _cand(seal_ratio=None, seal_amount=130_000_000.0, amount=1_000_000_000.0)
    assert _sub(cand, "D3", "R") == pytest.approx(26.0)


# ---------------------------------------------------------------------------
# D4 资金与流动性
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("main_net", "expected"),
    [(-100_000_000.0, 0.0), (0.0, 50.0), (100_000_000.0, 100.0), (-300_000_000.0, 0.0)],
)
def test_d4_main_net_ratio_boundaries(main_net: float, expected: float) -> None:
    cand = _cand(main_net_amount=main_net, amount=1_000_000_000.0)
    assert _sub(cand, "D4", "F") == pytest.approx(expected)


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(400_000_000.0, 20.0), (1_000_000_000.0, 50.0), (2_000_000_000.0, 100.0)],
)
def test_d4_amount_boundaries(amount: float, expected: float) -> None:
    assert _sub(_cand(amount=amount), "D4", "V") == pytest.approx(expected)


@pytest.mark.parametrize(
    ("turnover", "expected"),
    [
        (1.9, 20.0),
        (2.0, 50.0),
        (3.5, 75.0),
        (5.0, 100.0),
        (12.0, 100.0),
        (20.0, 100.0),
        (27.5, 75.0),
        (35.0, 50.0),
        (40.0, 20.0),
    ],
)
def test_d4_turnover_health_boundaries(turnover: float, expected: float) -> None:
    assert _sub(_cand(turnover_rate=turnover), "D4", "Q") == pytest.approx(expected)


# ---------------------------------------------------------------------------
# D5 筹码 / 位置
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("drawdown", "expected"),
    [(0.0, 100.0), (5.0, 100.0), (12.5, 80.0), (20.0, 60.0), (30.0, 45.0), (40.0, 30.0), (50.0, 10.0)],
)
def test_d5_distance_high60_boundaries(drawdown: float, expected: float) -> None:
    assert _sub(_cand(distance_high60=drawdown), "D5", "P") == pytest.approx(expected)


@pytest.mark.parametrize(
    ("concentration", "expected"),
    [
        (0.05, 100.0),
        (0.10, 100.0),
        (0.15, 80.0),
        (0.20, 60.0),
        (0.25, 40.0),  # 产品 §8.1 未写 20–30%，按连续插值 60→20 补齐
        (0.30, 20.0),
        (0.40, 20.0),
    ],
)
def test_d5_chip_concentration_boundaries(
    concentration: float, expected: float
) -> None:
    assert _sub(_cand(chip_concentration=concentration), "D5", "C") == pytest.approx(
        expected
    )


@pytest.mark.parametrize(
    ("profit", "expected"),
    [
        (-0.05, 60.0),
        (0.0, 100.0),
        (0.10, 100.0),
        (0.175, 65.0),
        (0.25, 50.0),
        (0.325, 35.0),  # 产品 §8.1 未写 25–40%，按连续插值 50→20 补齐
        (0.40, 20.0),
        (0.50, 20.0),
    ],
)
def test_d5_profit_ratio_boundaries(profit: float, expected: float) -> None:
    assert _sub(_cand(profit_ratio=profit), "D5", "X") == pytest.approx(expected)


# ---------------------------------------------------------------------------
# D6 持续性 / 股性
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, 0.0), (1, 30.0), (2, 60.0), (3, 60.0), (4, 85.0), (5, 85.0), (6, 100.0), (9, 100.0)],
)
def test_d6_limit_up_count_boundaries(count: int, expected: float) -> None:
    assert _sub(_cand(limit_up_cnt_20d=count), "D6", "S1") == pytest.approx(expected)


def test_d6_next_day_up_rate_and_sample_floor() -> None:
    # 只有 sample 显式存在且 >= 3 才计算 S2
    assert _sub(_cand(next_day_up_rate_20d=0.5, next_day_up_sample=5), "D6", "S2") == pytest.approx(50.0)
    assert _sub(_cand(next_day_up_rate_20d=0.9, next_day_up_sample=3), "D6", "S2") == pytest.approx(90.0)
    # sample 缺失 / < 3 → null（未知不猜，缺 sample 不得偷得分）
    assert _sub(_cand(next_day_up_rate_20d=0.9, next_day_up_sample=None), "D6", "S2") is None
    assert _sub(_cand(next_day_up_rate_20d=0.9, next_day_up_sample=2), "D6", "S2") is None


def test_d6_missing_sample_does_not_steal_score() -> None:
    result = score_candidate(_cand(next_day_up_rate_20d=0.9, next_day_up_sample=None), _ctx())
    # S2 缺失后 D6 只剩 S1（权重 0.5），D6 = S1 = 60，而不是 0.5*60+0.5*90
    assert result["dimensions"]["D6"]["score"] == pytest.approx(60.0)
    assert result["dimensions"]["D6"]["sub"]["S2"] is None


# ---------------------------------------------------------------------------
# 缺失语义：子分归一、维度剔除、coverage 门槛
# ---------------------------------------------------------------------------


def test_missing_subfactor_is_renormalized_not_zero_filled() -> None:
    # D6 只剩 S1（权重 0.5），D6 = S1 而不是 0.5*S1
    result = score_candidate(_cand(next_day_up_rate_20d=None), _ctx())
    assert result["dimensions"]["D6"]["score"] == pytest.approx(60.0)


def test_dimension_becomes_null_when_remaining_sub_weight_below_half() -> None:
    # D3 缺 T/O，剩余权重 0.25+0.15=0.40 < 0.5 → D3 null
    result = score_candidate(_cand(first_seal_time=None, open_num=None), _ctx())
    assert result["dimensions"]["D3"]["score"] is None
    assert "D3" in result["missing_dims"]


def test_missing_subfactor_with_enough_weight_is_renormalized() -> None:
    # D1 缺 H，剩余 0.35+0.25=0.60 ≥ 0.5 → D1 = (0.35L+0.25B)/0.60 = 100
    result = score_candidate(_cand(height=None), _ctx())
    assert result["dimensions"]["D1"]["score"] == pytest.approx(100.0)


def test_missing_dimension_is_excluded_and_coverage_recomputed() -> None:
    cand = _cand(
        distance_high60=None,
        chip_concentration=None,
        profit_ratio=None,
    )
    result = score_candidate(cand, _ctx())
    assert result["dimensions"]["D5"]["score"] is None
    assert result["missing_dims"] == ["D5"]
    assert result["coverage"] == pytest.approx(0.95)
    # 加权和 (84.08) / 0.95 = 88.505 → 88.5
    assert result["review_score"] == pytest.approx(88.5)


def test_coverage_below_half_yields_null_score_and_grade() -> None:
    # 只保留 D1（leader 权重 0.30）
    result = score_candidate(_only_d1_cand(), _ctx())
    assert result["coverage"] == pytest.approx(0.30)
    assert result["review_score"] is None
    assert result["review_grade"] is None
    assert result["contributions"] == []
    assert result["top_drivers"] == []
    assert result["top_drags"] == []


def test_coverage_exactly_half_is_still_scored() -> None:
    cand = _cand(
        first_seal_time=None,
        open_num=None,
        seal_amount=None,
        seal_ratio=None,
        main_net_amount=None,
        amount=None,
        turnover_rate=None,
        distance_high60=None,
        chip_concentration=None,
        profit_ratio=None,
        limit_up_cnt_20d=None,
        next_day_up_rate_20d=None,
    )
    result = score_candidate(cand, _ctx())
    assert result["coverage"] == pytest.approx(0.50)
    assert result["review_score"] == pytest.approx(92.0)
    assert result["review_grade"] == "S"


# ---------------------------------------------------------------------------
# 总分、贡献、评级
# ---------------------------------------------------------------------------


def test_full_candidate_total_score_and_structure() -> None:
    result = score_candidate(_cand(), _ctx())
    assert result["coverage"] == pytest.approx(1.0)
    assert result["review_score"] == pytest.approx(89.0)
    assert result["review_grade"] == "S"
    assert result["missing_dims"] == []
    assert result["weight_profile"] == "leader"
    assert result["confidence"] == pytest.approx(1.0)
    assert set(result) == {
        "dimensions",
        "contributions",
        "top_drivers",
        "top_drags",
        "coverage",
        "confidence",
        "review_score",
        "review_grade",
        "missing_dims",
        "weight_profile",
    }
    assert set(result["dimensions"]) == set(DIMS)
    assert result["dimensions"]["D1"]["weight"] == pytest.approx(0.30)
    assert result["dimensions"]["D1"]["sub"]["H"] == pytest.approx(66.7)
    assert result["dimensions"]["D3"]["sub"]["S_raw"] == pytest.approx(0.013)


def test_contributions_sum_equals_review_score() -> None:
    result = score_candidate(_cand(), _ctx())
    total_points = sum(item["points"] for item in result["contributions"])
    assert round(total_points, 1) == result["review_score"]
    assert total_points == pytest.approx(result["review_score"], abs=0.06)
    assert [item["dim"] for item in result["contributions"]] == list(DIMS)
    # weight_norm 之和 = 1，points = weight_norm * dim_score
    assert sum(item["weight_norm"] for item in result["contributions"]) == pytest.approx(1.0)
    for item in result["contributions"]:
        assert item["points"] == pytest.approx(
            item["weight_norm"] * item["dim_score"], abs=0.02
        )
        assert "sub_factors" in item


def test_contributions_sum_equals_score_with_missing_dimension() -> None:
    cand = _cand(
        distance_high60=None,
        chip_concentration=None,
        profit_ratio=None,
    )
    result = score_candidate(cand, _ctx())
    total_points = sum(item["points"] for item in result["contributions"])
    assert round(total_points, 1) == result["review_score"]


# ---------------------------------------------------------------------------
# top_drivers / top_drags（产品 §8.2）
# ---------------------------------------------------------------------------


def test_top_drivers_and_drags_sorted_by_points() -> None:
    result = score_candidate(_cand(), _ctx())
    # points: D1 26.0 > D2 20.0 > D3 18.47 > D4 15.86 > D5 4.88 > D6 3.75
    assert [item["dim"] for item in result["top_drivers"]] == ["D1", "D2"]
    assert [item["dim"] for item in result["top_drags"]] == ["D6", "D5"]
    assert result["top_drivers"][0]["points"] == pytest.approx(26.0)
    assert set(result["top_drivers"][0]) == {"dim", "points", "reason"}
    assert "龙头质量" in result["top_drivers"][0]["reason"]
    assert "贡献" in result["top_drivers"][0]["reason"]


def test_top_drivers_and_drags_with_missing_dimension() -> None:
    cand = _cand(
        distance_high60=None,
        chip_concentration=None,
        profit_ratio=None,
    )
    result = score_candidate(cand, _ctx())
    assert [item["dim"] for item in result["top_drivers"]] == ["D1", "D2"]
    assert [item["dim"] for item in result["top_drags"]] == ["D6", "D4"]
    assert all(item["dim"] != "D5" for item in result["top_drivers"] + result["top_drags"])


def test_top_drivers_and_drags_empty_when_score_null() -> None:
    result = score_candidate(_only_d1_cand(), _ctx())
    assert result["review_score"] is None
    assert result["top_drivers"] == []
    assert result["top_drags"] == []


def test_rank_drivers_drags_tie_breaks_by_dimension_order() -> None:
    contributions = [
        {"dim": "D1", "points": 18.0, "dim_score": 60.0},
        {"dim": "D2", "points": 18.0, "dim_score": 90.0},
        {"dim": "D3", "points": 20.0, "dim_score": 100.0},
        {"dim": "D4", "points": 10.0, "dim_score": 50.0},
    ]
    drivers, drags = rank_drivers_drags(contributions, CONFIG)
    # drivers: D3 最高；D1/D2 并列 → 按 DIMENSIONS 顺序取 D1
    assert [item["dim"] for item in drivers] == ["D3", "D1"]
    # drags: D4 最低；D1/D2 并列 → 按 DIMENSIONS 顺序取 D1
    assert [item["dim"] for item in drags] == ["D4", "D1"]


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100.0, "S"),
        (85.0, "S"),
        (84.99, "A"),
        (75.0, "A"),
        (74.99, "B"),
        (65.0, "B"),
        (64.99, "C"),
        (50.0, "C"),
        (49.99, "D"),
        (0.0, "D"),
        (None, None),
    ],
)
def test_review_grade_bands(score: float | None, expected: str | None) -> None:
    assert grade_for_score(score, CONFIG) == expected


# ---------------------------------------------------------------------------
# 角色 / profile 与纯函数约束
# ---------------------------------------------------------------------------


def test_role_null_uses_diagnostic_profile_explicitly() -> None:
    result = score_candidate(_cand(), {"role": None})
    assert result["weight_profile"] == "diagnostic_non_mainline"
    assert result["dimensions"]["D5"]["weight"] == pytest.approx(0.10)


def test_missing_context_defaults_to_diagnostic_profile() -> None:
    assert score_candidate(_cand(), None)["weight_profile"] == "diagnostic_non_mainline"


def test_unknown_role_fails_loud() -> None:
    with pytest.raises(ValueError):
        score_candidate(_cand(), {"role": "bogus"})


def test_diagnostic_result_ignores_follower_weight_changes() -> None:
    custom = dataclasses.replace(
        CONFIG,
        role_weights={
            **CONFIG.role_weights,
            "follower": {"D1": 100.0, "D2": 0.0, "D3": 0.0, "D4": 0.0, "D5": 0.0, "D6": 0.0},
        },
    )
    diagnostic_before = score_candidate(_cand(), {"role": None}, CONFIG)
    diagnostic_after = score_candidate(_cand(), {"role": None}, custom)
    assert diagnostic_before == diagnostic_after

    follower_before = score_candidate(_cand(), {"role": "follower"}, CONFIG)
    follower_after = score_candidate(_cand(), {"role": "follower"}, custom)
    assert follower_before["review_score"] != follower_after["review_score"]


@pytest.mark.parametrize("role", ["leader", "vanguard", "mainstay", "capacity", "follower"])
def test_each_role_uses_its_own_profile(role: str) -> None:
    result = score_candidate(_cand(), {"role": role})
    assert result["weight_profile"] == role
    assert result["dimensions"]["D1"]["weight"] == pytest.approx(
        CONFIG.role_weights[role]["D1"] / 100.0
    )


@pytest.mark.parametrize("health", [1.0, 0.85, 0.70, 0.0])
def test_confidence_multiplies_coverage_by_source_health(health: float) -> None:
    result = score_candidate(_cand(), _ctx(source_health=health))
    assert result["confidence"] == pytest.approx(round(1.0 * health, 2))


def test_confidence_derives_from_degraded_list() -> None:
    assert score_candidate(_cand(), _ctx(degraded=[]))["confidence"] == pytest.approx(1.0)
    assert score_candidate(_cand(), _ctx(degraded=["fund_flow"]))["confidence"] == pytest.approx(0.85)
    assert score_candidate(_cand(), _ctx(degraded=["fund_flow", "cninfo"]))["confidence"] == pytest.approx(0.70)


def test_confidence_zero_when_limit_up_pool_missing() -> None:
    result = score_candidate(_cand(), _ctx(limit_up_pool_available=False))
    assert result["confidence"] == pytest.approx(0.0)


def test_extreme_composite_score_does_not_change_result() -> None:
    baseline = score_candidate(_cand(), _ctx())
    for value in (0.0, -999.0, 999.0, "not-a-number", None):
        result = score_candidate(_cand(composite_score=value), _ctx())
        assert result == baseline


def test_score_candidate_does_not_mutate_inputs() -> None:
    cand = _cand(composite_score=82.5)
    ctx = _ctx()
    cand_copy = copy.deepcopy(cand)
    ctx_copy = copy.deepcopy(ctx)
    score_candidate(cand, ctx)
    assert cand == cand_copy
    assert ctx == ctx_copy


def test_invalid_field_types_degrade_instead_of_raising() -> None:
    result = score_candidate(
        _cand(turnover_rate="abc", first_seal_time="not-a-time", open_num=-1),
        _ctx(),
    )
    assert result["dimensions"]["D4"]["sub"]["Q"] is None
    assert result["dimensions"]["D3"]["sub"]["T"] is None
    assert result["dimensions"]["D3"]["sub"]["O"] is None
