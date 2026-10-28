"""M2：市场七子分与四态 + unknown（纯函数单测，无 I/O）。

覆盖产品方案 §5：

* 七子分分段线性插值（锚点 / 中点 / 两端 clamp）；
* ``LU`` / ``LD`` 为判定基座，任一缺失 → ``score=null``、``state=unknown``；
* 其余子分缺失 → 当前可用权重内归一化，标 ``partial`` + ``missing_inputs``；
* 四态阈值边界（70/50/30）与仓位系数；
* 数据只来自 ``assets`` / ``prev``，不改输入。
"""

from __future__ import annotations

import copy

import pytest

from dragon_quant.review_account.prep.review import CONFIG
from dragon_quant.review_account.prep.review.market import (
    compute_market_state,
    market_state_for_score,
    market_sub_score,
)


def _assets(**overrides: object) -> dict:
    base: dict = {
        "ths_limit_up_pool": {
            "total": 63,
            "rows": [
                {"code": "600001", "high_days": "4天4板", "open_num": 0},
                {"code": "600002", "high_days": "2天2板", "open_num": 1},
                {"code": "600003", "high_days": "3天3板", "open_num": 0},
                {"code": "600004", "high_days": "1天1板", "open_num": 0},
            ],
        },
        "market_breadth": {
            "up_cnt": 3000,
            "down_cnt": 1500,
            "limit_down": ["600901", "600902"],
            "broken": ["600801", "600802", "600803"],
            "total_amount": 9_200e8,
        },
        "fg_boards": {
            "boards": {
                "昨日涨停": {
                    "rows": [
                        {"close": 11.0, "pre_close": 10.0},
                        {"close": 10.2, "pre_close": 10.0},
                    ]
                },
                "昨日跌停": {"rows": []},
                "昨日上榜": {"rows": []},
            }
        },
        "ths_block_top": {
            "rows": [
                {
                    "name": "机器人",
                    "limit_up_num": 11,
                    "high_num": 4,
                    "continuous_plate_num": 3,
                    "stock_list": [],
                }
            ]
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 分段插值
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        # LU：15→0；80→90；120→100，两端 clamp
        ("LU", 15, 0.0),
        ("LU", 80, 90.0),
        ("LU", 120, 100.0),
        ("LU", 10, 0.0),
        ("LU", 130, 100.0),
        ("LU", 47.5, 45.0),
        # LD：0→100；5→90；10→70；30→20；50→0
        ("LD", 0, 100.0),
        ("LD", 5, 90.0),
        ("LD", 10, 70.0),
        ("LD", 30, 20.0),
        ("LD", 50, 0.0),
        ("LD", 60, 0.0),
        # PRE：-5→0；-2→30；0→55；+2→80；+5→100
        ("PRE", -5, 0.0),
        ("PRE", -2, 30.0),
        ("PRE", 0, 55.0),
        ("PRE", 2, 80.0),
        ("PRE", 5, 100.0),
        ("PRE", 6, 100.0),
        # PRO：0.05→10；0.15→50；0.25→80；0.40→100
        ("PRO", 0.05, 10.0),
        ("PRO", 0.15, 50.0),
        ("PRO", 0.25, 80.0),
        ("PRO", 0.40, 100.0),
        ("PRO", 0.01, 10.0),
        ("PRO", 0.50, 100.0),
        # HGT：2→20；3→50；4→70；6→90；8→100
        ("HGT", 2, 20.0),
        ("HGT", 3, 50.0),
        ("HGT", 4, 70.0),
        ("HGT", 6, 90.0),
        ("HGT", 8, 100.0),
        ("HGT", 1, 20.0),
        ("HGT", 9, 100.0),
        # BRK：0.10→100；0.20→75；0.30→45；0.45→10；0.60→0
        ("BRK", 0.10, 100.0),
        ("BRK", 0.20, 75.0),
        ("BRK", 0.30, 45.0),
        ("BRK", 0.45, 10.0),
        ("BRK", 0.60, 0.0),
        ("BRK", 0.05, 100.0),
        ("BRK", 0.70, 0.0),
        # LIQ：5000→10；7000→40；10000→75；15000→100
        ("LIQ", 5000, 10.0),
        ("LIQ", 7000, 40.0),
        ("LIQ", 10000, 75.0),
        ("LIQ", 15000, 100.0),
        ("LIQ", 4000, 10.0),
        ("LIQ", 20000, 100.0),
        ("LIQ", 8500, 57.5),
    ],
)
def test_market_sub_score_anchors_and_interpolation(key: str, value: float, expected: float) -> None:
    assert market_sub_score(key, value) == pytest.approx(expected, abs=1e-9)


def test_market_sub_score_missing_value_is_none() -> None:
    assert market_sub_score("LU", None) is None


def test_market_sub_score_unknown_key_fails_loud() -> None:
    with pytest.raises(ValueError):
        market_sub_score("NOPE", 1.0)


# ---------------------------------------------------------------------------
# 完整市场状态
# ---------------------------------------------------------------------------


def test_compute_market_state_full_fixture() -> None:
    result = compute_market_state(_assets(), None)

    assert result["sub_scores"]["LU"] == pytest.approx(66.5, abs=0.05)
    assert result["sub_scores"]["LD"] == pytest.approx(96.0, abs=0.05)
    assert result["sub_scores"]["PRE"] == pytest.approx(100.0)
    assert result["sub_scores"]["PRO"] == pytest.approx(100.0)
    assert result["sub_scores"]["HGT"] == pytest.approx(70.0)
    assert result["sub_scores"]["BRK"] == pytest.approx(100.0)
    assert result["sub_scores"]["LIQ"] == pytest.approx(65.7, abs=0.05)

    assert result["score"] == pytest.approx(85.5, abs=0.05)
    assert result["state"] == "strong"
    assert result["coeff"] == 1.0
    assert result["partial"] is False
    assert result["missing_inputs"] == []
    assert result["unknown_reason"] is None

    assert result["inputs"]["limit_up_cnt"] == 63
    assert result["inputs"]["limit_down_cnt"] == 2
    assert result["inputs"]["max_board_height"] == 4
    assert result["inputs"]["total_amount"] == pytest.approx(9_200e8)
    assert result["inputs"]["broken_rate"] == pytest.approx(3 / 66, abs=1e-3)
    assert result["inputs"]["yesterday_premium_pct"] == pytest.approx(6.0)
    assert result["inputs"]["promotion_rate"] == pytest.approx(1.5)

    assert any("涨停 63 家" in line and "跌停 2 家" in line for line in result["evidence"])
    assert any("炸板率" in line for line in result["evidence"])
    assert any("最高 4 板" in line for line in result["evidence"])


def test_lu_missing_makes_market_unknown_not_neutral() -> None:
    result = compute_market_state(_assets(ths_limit_up_pool={}), None)

    assert result["score"] is None
    assert result["state"] == "unknown"
    assert result["coeff"] == 0.5
    assert result["partial"] is True
    assert result["sub_scores"]["LU"] is None
    assert "LU" in result["missing_inputs"]
    assert "limit_up_cnt_missing" in (result["unknown_reason"] or "")


def test_ld_missing_makes_market_unknown_not_neutral() -> None:
    breadth = _assets()["market_breadth"]
    breadth.pop("limit_down")
    result = compute_market_state(_assets(market_breadth=breadth), None)

    assert result["score"] is None
    assert result["state"] == "unknown"
    assert result["sub_scores"]["LD"] is None
    assert "limit_down_cnt_missing" in (result["unknown_reason"] or "")


def test_both_basis_missing_lists_both_reasons() -> None:
    breadth = _assets()["market_breadth"]
    breadth.pop("limit_down")
    result = compute_market_state(
        _assets(ths_limit_up_pool={}, market_breadth=breadth), None
    )

    assert result["state"] == "unknown"
    assert "LU" in result["missing_inputs"]
    assert "LD" in result["missing_inputs"]
    assert "limit_up_cnt_missing" in (result["unknown_reason"] or "")
    assert "limit_down_cnt_missing" in (result["unknown_reason"] or "")


def test_partial_missing_is_renormalized_within_available_weights() -> None:
    # 去掉 FG T-1 → PRE 与 PRO 都不可得；剩余权重 0.22+0.18+0.10+0.08+0.10 = 0.68
    result = compute_market_state(_assets(fg_boards={}), None)

    assert result["partial"] is True
    assert result["missing_inputs"] == ["PRE", "PRO"]
    assert result["sub_scores"]["PRE"] is None
    assert result["sub_scores"]["PRO"] is None
    # (0.22*66.4615 + 0.18*96 + 0.10*70 + 0.08*100 + 0.10*65.6667) / 0.68
    assert result["score"] == pytest.approx(78.6, abs=0.05)
    assert result["state"] == "strong"


def test_broken_rate_falls_back_to_pool_open_num() -> None:
    breadth = _assets()["market_breadth"]
    breadth.pop("broken")
    result = compute_market_state(_assets(market_breadth=breadth), None)

    # 池内 open_num > 0 的只有 600002 一只 → 1 / (1 + 63)
    assert result["inputs"]["broken_rate"] == pytest.approx(1 / 64, abs=1e-4)
    assert result["sub_scores"]["BRK"] == pytest.approx(100.0)


def test_premium_and_promotion_are_none_when_fg_missing() -> None:
    result = compute_market_state(_assets(fg_boards={}), None)

    assert result["inputs"]["yesterday_premium_pct"] is None
    assert result["inputs"]["promotion_rate"] is None


def test_max_board_height_uses_block_top_with_pool_fallback() -> None:
    # block_top 完全缺失时回退涨停池 high_days（最高 4 板）
    result = compute_market_state(_assets(ths_block_top={}), None)
    assert result["inputs"]["max_board_height"] == 4
    assert result["sub_scores"]["HGT"] == pytest.approx(70.0)


def test_truncated_pool_without_total_is_unknown() -> None:
    pool = _assets()["ths_limit_up_pool"]
    pool.pop("total")
    pool["truncated"] = True

    result = compute_market_state(_assets(ths_limit_up_pool=pool), None)

    assert result["sub_scores"]["LU"] is None
    assert result["state"] == "unknown"


def test_truncated_pool_does_not_fall_back_for_broken_or_promotion() -> None:
    pool = _assets()["ths_limit_up_pool"]
    pool["truncated"] = True
    breadth = _assets()["market_breadth"]
    breadth.pop("broken")

    result = compute_market_state(
        _assets(ths_limit_up_pool=pool, market_breadth=breadth), None
    )

    assert result["sub_scores"]["BRK"] is None
    assert result["inputs"]["promotion_rate"] is None


def test_prev_market_score_change_is_reported() -> None:
    prev = {"review": {"market": {"score": 80.0, "state": "neutral"}}}
    result = compute_market_state(_assets(), prev)

    assert result["prev_score"] == pytest.approx(80.0)
    assert result["prev_state"] == "neutral"
    assert result["score_change"] == pytest.approx(5.5, abs=0.05)
    assert any("环比" in line for line in result["evidence"])


def test_compute_market_state_does_not_mutate_inputs() -> None:
    assets = _assets()
    prev = {"review": {"market": {"score": 80.0, "state": "neutral"}}}
    assets_before = copy.deepcopy(assets)
    prev_before = copy.deepcopy(prev)

    compute_market_state(assets, prev)

    assert assets == assets_before
    assert prev == prev_before


# ---------------------------------------------------------------------------
# 状态阈值
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100.0, "strong"),
        (70.0, "strong"),
        (69.9, "neutral"),
        (50.0, "neutral"),
        (49.9, "weak"),
        (30.0, "weak"),
        (29.9, "ice"),
        (0.0, "ice"),
    ],
)
def test_market_state_threshold_boundaries(score: float, expected: str) -> None:
    assert market_state_for_score(score) == expected


def test_market_state_unknown_for_none() -> None:
    assert market_state_for_score(None) == "unknown"


def test_market_numbers_come_from_config() -> None:
    import dataclasses

    assert sum(CONFIG.weights_market.values()) == pytest.approx(1.0)
    assert set(CONFIG.market_anchors) == set(CONFIG.weights_market)

    flattened = dict(CONFIG.market_anchors)
    flattened["LU"] = ((15.0, 0.0), (80.0, 0.0), (120.0, 0.0))
    cfg = dataclasses.replace(CONFIG, market_anchors=flattened)
    result = compute_market_state(_assets(), None, config=cfg)
    assert result["sub_scores"]["LU"] == 0.0
