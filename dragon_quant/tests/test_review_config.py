"""M1：复盘分配置不变量（纯单测，无 I/O）。

覆盖产品方案 §8–§10 的配置层约束：

* 六个权重 profile（5 角色 + ``diagnostic_non_mainline``），每行合计 = 100；
* 档位阈值 S/A/B/C/D = 85/75/65/50 与档位基数、角色上限、市场/置信系数；
* ``config_hash`` 稳定、长度 12、任一数字变化必变；
* ``ReviewConfig`` 为 frozen dataclass，配置是唯一真源。
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from dragon_quant.review_account.prep.review import (
    CONFIG,
    CONFIG_VERSION,
    ReviewConfig,
    config_hash,
)

DIMS = ("D1", "D2", "D3", "D4", "D5", "D6")
PROFILES = (
    "leader",
    "vanguard",
    "mainstay",
    "capacity",
    "follower",
    "diagnostic_non_mainline",
)


def test_config_version_is_baseline_v1() -> None:
    assert CONFIG_VERSION == "review_baseline_v1"


def test_config_is_frozen_dataclass() -> None:
    assert isinstance(CONFIG, ReviewConfig)
    with pytest.raises(dataclasses.FrozenInstanceError):
        CONFIG.coverage_min = 0.4  # type: ignore[misc]


def test_six_weight_profiles_exist() -> None:
    assert set(CONFIG.role_weights) == set(PROFILES)
    # diagnostic 必须是独立配置项，不得与 follower 合并
    assert "diagnostic_non_mainline" in CONFIG.role_weights
    assert "follower" in CONFIG.role_weights


def test_every_profile_row_sums_to_100() -> None:
    for profile in PROFILES:
        row = CONFIG.role_weights[profile]
        assert set(row) == set(DIMS), profile
        assert sum(row.values()) == pytest.approx(100.0), profile


def test_role_weights_match_product_matrix() -> None:
    expected = {
        "leader": (30, 20, 20, 20, 5, 5),
        "vanguard": (25, 15, 25, 25, 5, 5),
        "mainstay": (20, 20, 15, 25, 15, 5),
        "capacity": (15, 15, 15, 30, 20, 5),
        "follower": (20, 20, 25, 20, 10, 5),
        "diagnostic_non_mainline": (20, 20, 25, 20, 10, 5),
    }
    for profile, values in expected.items():
        row = CONFIG.role_weights[profile]
        assert tuple(row[dim] for dim in DIMS) == values, profile


def test_sub_weights_sum_to_one_per_dimension() -> None:
    for dim in DIMS:
        weights = CONFIG.scoring_rules[dim]["weights"]
        assert sum(weights.values()) == pytest.approx(1.0), dim


def test_dimension_labels_cover_all_dimensions() -> None:
    assert set(CONFIG.dimension_labels) == set(DIMS)


def test_grade_thresholds_and_base() -> None:
    assert tuple(CONFIG.grade_thresholds) == (85, 75, 65, 50)
    assert CONFIG.grade_base_pct == {"S": 12.0, "A": 8.0, "B": 4.0, "C": 0.0, "D": 0.0}


def test_role_caps_and_market_coefficients() -> None:
    assert CONFIG.role_cap_pct == {
        "leader": 15.0,
        "vanguard": 10.0,
        "mainstay": 12.0,
        "capacity": 12.0,
        "follower": 5.0,
    }
    assert CONFIG.market_state_coef == {
        "strong": 1.0,
        "neutral": 0.8,
        "weak": 0.6,
        "ice": 0.4,
        "unknown": 0.5,
    }


def test_confidence_and_coverage_bands() -> None:
    assert CONFIG.coverage_min == 0.5
    assert CONFIG.confidence_coef_bands == ((0.90, 1.00), (0.75, 0.85), (0.60, 0.70))
    assert CONFIG.source_health_bands == {
        "all": 1.00,
        "degraded_1": 0.85,
        "degraded_2": 0.70,
        "basis_missing": 0.00,
    }


def test_liquidity_thresholds_follow_gates() -> None:
    # G3a < 2 亿、G3b 2–5 亿
    assert CONFIG.liquidity_thresholds == (200_000_000.0, 500_000_000.0)


def test_expectations_cover_all_five_roles() -> None:
    assert set(CONFIG.expectations) == {
        "leader",
        "vanguard",
        "mainstay",
        "capacity",
        "follower",
    }
    leader = CONFIG.expectations["leader"]
    assert leader["gap_low"] == 3.0
    assert leader["gap_high"] == 7.0
    assert leader["ratio_low"] == 1.2
    assert leader["seal_by"] == "10:00"


def test_config_hash_is_stable_and_well_formed() -> None:
    first = config_hash()
    second = config_hash(CONFIG)
    assert first == second
    assert re.fullmatch(r"[0-9a-f]{12}", first)


def test_config_hash_changes_when_a_top_level_number_changes() -> None:
    baseline = config_hash()
    changed = dataclasses.replace(CONFIG, grade_thresholds=(86, 75, 65, 50))
    assert config_hash(changed) != baseline


def test_config_hash_changes_when_a_nested_number_changes() -> None:
    baseline = config_hash()
    weights = {profile: dict(row) for profile, row in CONFIG.role_weights.items()}
    weights["leader"]["D1"] = 31
    weights["leader"]["D6"] = 4
    changed = dataclasses.replace(CONFIG, role_weights=weights)
    assert config_hash(changed) != baseline


def test_config_hash_changes_when_scoring_rule_changes() -> None:
    baseline = config_hash()
    rules = {dim: dict(rule) for dim, rule in CONFIG.scoring_rules.items()}
    rules["D1"] = {**rules["D1"], "height_cap": 7}
    changed = dataclasses.replace(CONFIG, scoring_rules=rules)
    assert config_hash(changed) != baseline
