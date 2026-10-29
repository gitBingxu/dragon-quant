"""M2：主线 N/H/C/S、排名与五态（纯函数单测，无 I/O）。

覆盖产品方案 §6：

* ``mainline_score = 0.35N + 0.25H + 0.20C + 0.20S`` 与四个封顶；
* 取前 3 为主线、第 4–5 为 watchlist；并列按（涨停家数 desc、板块名升序）；
* 与 T-1 前 5 对比得 ``new/strengthening/stable/weakening``；无 T-1 → ``unknown``；
* 成员缺失仍展示分数，角色不可判（``unknown_reason``）。
"""

from __future__ import annotations

import copy
import dataclasses

import pytest

from dragon_quant.review_account.prep.review import CONFIG
from dragon_quant.review_account.prep.review.mainline import (
    compute_mainlines,
    mainline_score_for,
)


def _board(
    name: str,
    limit_up_num: int | None = 11,
    high_num: int | None = 4,
    continuous: int | None = 3,
    members: list[str] | None = None,
    **overrides: object,
) -> dict:
    row: dict = {
        "name": name,
        "limit_up_num": limit_up_num,
        "high_num": high_num,
        "continuous_plate_num": continuous,
        "stock_list": [{"code": code} for code in (members or [])],
    }
    row.update(overrides)
    return row


def _assets(rows: list[dict] | None = None, total: int | None = 63, pool_rows: list[dict] | None = None) -> dict:
    pool: dict = {"rows": pool_rows or []}
    if total is not None:
        pool["total"] = total
    return {
        "ths_limit_up_pool": pool,
        "ths_block_top": {"rows": rows or []},
    }


def _default_rows() -> list[dict]:
    return [
        _board("机器人", 11, 4, 3),
        _board("算力", 8, 3, 2),
        _board("消费电子", 5, 2, 1),
        _board("医药", 4, 2, 1),
        _board("地产", 3, 1, 0),
        _board("化工", 2, 1, 0),
    ]


# ---------------------------------------------------------------------------
# 主线分公式
# ---------------------------------------------------------------------------


def test_mainline_score_formula() -> None:
    result = mainline_score_for(11, 4, 3, 11 / 63)

    assert result["sub_scores"]["N"] == pytest.approx(100.0)
    assert result["sub_scores"]["H"] == pytest.approx(66.7, abs=0.05)
    assert result["sub_scores"]["C"] == pytest.approx(100.0)
    assert result["sub_scores"]["S"] == pytest.approx(100.0)
    assert result["score"] == pytest.approx(91.7, abs=0.05)
    assert result["partial"] is False


def test_mainline_caps_are_applied() -> None:
    assert mainline_score_for(30, 10, 9, 0.50)["score"] == pytest.approx(100.0)
    assert mainline_score_for(30, 10, 9, 0.50)["sub_scores"] == {
        "N": 100.0,
        "H": 100.0,
        "C": 100.0,
        "S": 100.0,
    }


def test_mainline_missing_component_is_renormalized_not_zero_filled() -> None:
    # 集中度缺失 → 在 N/H/C 的 0.80 权重内重算
    result = mainline_score_for(11, 4, 3, None)

    assert result["partial"] is True
    assert result["sub_scores"]["S"] is None
    assert result["score"] == pytest.approx(89.6, abs=0.05)
    assert result["score"] is not None


def test_mainline_explicit_zero_components_are_not_missing() -> None:
    result = mainline_score_for(0, 0, 0, 0.0)

    assert result["partial"] is False
    assert result["score"] == 0.0


def test_mainline_all_components_missing_yields_null_score() -> None:
    result = mainline_score_for(None, None, None, None)

    assert result["score"] is None
    assert result["partial"] is True


# ---------------------------------------------------------------------------
# 排名 / watchlist
# ---------------------------------------------------------------------------


def test_mainlines_ranked_and_watchlist_marks() -> None:
    result = compute_mainlines(_assets(_default_rows()), None)

    assert len(result) == 5  # 第 6 名不进入前 5
    assert [item["board_name"] for item in result] == ["机器人", "算力", "消费电子", "医药", "地产"]
    assert [item["rank"] for item in result] == [1, 2, 3, 4, 5]
    assert [item["is_mainline"] for item in result] == [True, True, True, False, False]
    assert [item["watchlist"] for item in result] == [False, False, False, True, True]
    assert result[0]["mainline_score"] == pytest.approx(91.7, abs=0.05)
    assert result[0]["concentration"] == pytest.approx(11 / 63, abs=1e-6)


def test_tie_sort_uses_limit_up_num_desc_then_name_asc() -> None:
    rows = [
        _board("A", 11, 4, 3),
        _board("B", 12, 4, 3),
    ]
    result = compute_mainlines(_assets(rows), None)
    assert [item["board_name"] for item in result] == ["B", "A"]

    rows = [
        _board("B", 11, 4, 3),
        _board("A", 11, 4, 3),
    ]
    result = compute_mainlines(_assets(rows), None)
    assert [item["board_name"] for item in result] == ["A", "B"]


# ---------------------------------------------------------------------------
# T-1 五态
# ---------------------------------------------------------------------------


def _prev(mainlines: list[dict] | None) -> dict:
    return {"review": {"mainlines": mainlines or []}}


def test_no_prev_snapshot_marks_all_unknown() -> None:
    result = compute_mainlines(_assets(_default_rows()), None)
    assert {item["state"] for item in result} == {"unknown"}


def test_empty_prev_mainlines_marks_all_unknown() -> None:
    result = compute_mainlines(_assets(_default_rows()), _prev([]))
    assert {item["state"] for item in result} == {"unknown"}


def test_stable_when_rank_and_score_within_band() -> None:
    prev = _prev(
        [
            {"board_name": "机器人", "rank": 1, "mainline_score": 91.7},
            {"board_name": "算力", "rank": 2, "mainline_score": 70.8},
        ]
    )
    result = {item["board_name"]: item for item in compute_mainlines(_assets(_default_rows()), prev)}
    assert result["机器人"]["state"] == "stable"
    assert result["算力"]["state"] == "stable"


def test_new_when_absent_from_prev_top5_and_enters_top3() -> None:
    prev = _prev([{"board_name": "算力", "rank": 1, "mainline_score": 70.8}])
    result = {item["board_name"]: item for item in compute_mainlines(_assets(_default_rows()), prev)}
    assert result["机器人"]["state"] == "new"


def test_strengthening_by_rank_rise() -> None:
    prev = _prev(
        [
            {"board_name": "机器人", "rank": 3, "mainline_score": 91.7},
            {"board_name": "算力", "rank": 1, "mainline_score": 70.8},
        ]
    )
    result = {item["board_name"]: item for item in compute_mainlines(_assets(_default_rows()), prev)}
    assert result["机器人"]["state"] == "strengthening"


def test_strengthening_by_score_jump() -> None:
    prev = _prev(
        [
            {"board_name": "机器人", "rank": 1, "mainline_score": 80.0},
            {"board_name": "算力", "rank": 2, "mainline_score": 70.8},
        ]
    )
    result = {item["board_name"]: item for item in compute_mainlines(_assets(_default_rows()), prev)}
    assert result["机器人"]["state"] == "strengthening"


def test_rank_up_but_score_drop_is_weakening() -> None:
    # 算力：T-1 第 3 → T 日第 2（排名 +1），但分数 95.0 → 70.8（-24.2）
    prev = _prev(
        [
            {"board_name": "机器人", "rank": 1, "mainline_score": 91.7},
            {"board_name": "消费电子", "rank": 2, "mainline_score": 43.1},
            {"board_name": "算力", "rank": 3, "mainline_score": 95.0},
        ]
    )
    result = {item["board_name"]: item for item in compute_mainlines(_assets(_default_rows()), prev)}
    assert result["算力"]["state"] == "weakening"


def test_rank_drop_but_score_jump_is_weakening() -> None:
    # 消费电子：T-1 第 1 → T 日第 3（排名 -2），但分数 20.0 → 43.1（+23.1）
    prev = _prev(
        [
            {"board_name": "消费电子", "rank": 1, "mainline_score": 20.0},
            {"board_name": "机器人", "rank": 2, "mainline_score": 91.7},
            {"board_name": "算力", "rank": 3, "mainline_score": 70.8},
        ]
    )
    result = {item["board_name"]: item for item in compute_mainlines(_assets(_default_rows()), prev)}
    assert result["消费电子"]["state"] == "weakening"


def test_normal_strengthening_is_not_classified_as_weakening() -> None:
    # 算力：排名 +1 且分数 +5（未触发任何负向条件）→ strengthening
    prev = _prev(
        [
            {"board_name": "机器人", "rank": 1, "mainline_score": 91.7},
            {"board_name": "消费电子", "rank": 2, "mainline_score": 43.1},
            {"board_name": "算力", "rank": 3, "mainline_score": 65.8},
        ]
    )
    result = {item["board_name"]: item for item in compute_mainlines(_assets(_default_rows()), prev)}
    assert result["算力"]["state"] == "strengthening"


def test_weakening_by_rank_drop() -> None:
    prev = _prev(
        [
            {"board_name": "消费电子", "rank": 1, "mainline_score": 43.1},
            {"board_name": "机器人", "rank": 2, "mainline_score": 91.7},
            {"board_name": "算力", "rank": 3, "mainline_score": 70.8},
        ]
    )
    result = {item["board_name"]: item for item in compute_mainlines(_assets(_default_rows()), prev)}
    assert result["消费电子"]["state"] == "weakening"


def test_weakening_by_score_drop() -> None:
    prev = _prev(
        [
            {"board_name": "机器人", "rank": 1, "mainline_score": 91.7},
            {"board_name": "算力", "rank": 2, "mainline_score": 90.0},
        ]
    )
    result = {item["board_name"]: item for item in compute_mainlines(_assets(_default_rows()), prev)}
    assert result["算力"]["state"] == "weakening"


# ---------------------------------------------------------------------------
# 缺失 / 成员 / role_map
# ---------------------------------------------------------------------------


def test_missing_market_limit_up_count_makes_concentration_unknown() -> None:
    result = compute_mainlines(_assets(_default_rows(), total=None), None)
    robot = result[0]

    assert robot["partial"] is True
    assert robot["concentration"] is None
    assert robot["mainline_score"] == pytest.approx(89.6, abs=0.05)


def test_missing_members_keep_score_but_mark_unknown_reason() -> None:
    rows = [{"name": "机器人", "limit_up_num": 11, "high_num": 4, "continuous_plate_num": 3}]
    result = compute_mainlines(_assets(rows), None)
    robot = result[0]

    assert robot["mainline_score"] == pytest.approx(91.7, abs=0.05)
    assert robot["members_sample"] == []
    assert robot["role_map"] == {}
    assert "board_members_missing" in (robot["unknown_reason"] or "")


def test_members_sample_is_capped_at_ten() -> None:
    codes = [f"6000{index:02d}" for index in range(12)]
    result = compute_mainlines(_assets([_board("机器人", members=codes)]), None)
    assert result[0]["members_sample"] == codes[:10]


def test_role_map_is_attached_for_mainline_boards() -> None:
    pool_rows = [
        {
            "code": "600001",
            "height": 4,
            "first_seal_time": "09:40:00",
            "amount": 2e9,
            "float_market_cap": 5e9,
            "limit_up_type": "换手板",
            "reason_type": "机器人",
        },
        {
            "code": "600002",
            "height": 1,
            "first_seal_time": "09:33:00",
            "amount": 1e9,
            "float_market_cap": 5e9,
            "limit_up_type": "换手板",
            "reason_type": "机器人",
        },
    ]
    rows = [_board("机器人", members=["600001", "600002"])]
    result = compute_mainlines(_assets(rows, pool_rows=pool_rows), None)

    assert result[0]["role_map"]["leader"] == "600001"
    assert result[0]["role_map"]["vanguard"] == "600002"


def test_category_is_passed_through() -> None:
    rows = [_board("机器人", category="gn")]
    result = compute_mainlines(_assets(rows), None)
    assert result[0]["category"] == "gn"


def test_missing_limit_up_num_without_members_is_not_guessed() -> None:
    rows = [{"name": "机器人", "high_num": 4, "continuous_plate_num": 3}]
    result = compute_mainlines(_assets(rows), None)[0]

    assert result["limit_up_num"] is None
    assert result["sub_scores"]["N"] is None
    assert result["partial"] is True


def test_truncated_pool_without_total_does_not_infer_limit_up_num() -> None:
    rows = [
        {
            "name": "机器人",
            "high_num": 4,
            "continuous_plate_num": 3,
            "stock_list": [{"code": "600001"}],
        }
    ]
    assets = _assets(rows, total=None, pool_rows=[{"code": "600001"}])
    assets["ths_limit_up_pool"]["truncated"] = True

    result = compute_mainlines(assets, None)[0]

    assert result["limit_up_num"] is None
    assert result["concentration"] is None
    assert result["sub_scores"]["N"] is None
    assert result["partial"] is True


def test_null_score_board_is_unknown_even_if_absent_from_prev() -> None:
    rows = [{"name": "空板"}]
    prev = _prev([{"board_name": "其他主线", "rank": 1, "mainline_score": 50.0}])

    result = compute_mainlines(_assets(rows), prev)

    assert result[0]["mainline_score"] is None
    assert result[0]["state"] == "unknown"


def test_compute_mainlines_does_not_mutate_inputs() -> None:
    assets = _assets(_default_rows())
    prev = _prev([{"board_name": "机器人", "rank": 1, "mainline_score": 91.7}])
    assets_before = copy.deepcopy(assets)
    prev_before = copy.deepcopy(prev)

    compute_mainlines(assets, prev)

    assert assets == assets_before
    assert prev == prev_before


def test_mainline_numbers_come_from_config() -> None:
    assert sum(CONFIG.weights_mainline.values()) == pytest.approx(1.0)
    assert set(CONFIG.mainline_caps) == {"limit_up_num", "high_num", "continuous_plate_num", "concentration"}

    weights = dict(CONFIG.weights_mainline)
    weights["N"] = 0.0
    weights["H"] = 0.60
    cfg = dataclasses.replace(CONFIG, weights_mainline=weights)
    result = compute_mainlines(_assets(_default_rows()), None, config=cfg)
    assert result[0]["mainline_score"] != pytest.approx(91.7, abs=0.05)


# ---------------------------------------------------------------------------
# unknown_reason：稳定原因码（禁止无说明的「未知」）
# ---------------------------------------------------------------------------


def _reasons(result: list[dict]) -> dict[str, str]:
    return {item["board_name"]: item.get("unknown_reason") or "" for item in result}


def test_no_prev_marks_prev_snapshot_missing() -> None:
    reasons = _reasons(compute_mainlines(_assets(_default_rows()), None))
    assert all("prev_snapshot_missing" in text for text in reasons.values())


def test_corrupt_prev_marks_prev_snapshot_corrupt() -> None:
    reasons = _reasons(compute_mainlines(
        _assets(_default_rows()), None,
        prev_meta={"error": "JSONDecodeError: bad", "trade_date": None}))
    assert all("prev_snapshot_corrupt" in text for text in reasons.values())


def test_prev_without_mainlines_marks_prev_mainlines_missing() -> None:
    prev = {"review": {"enabled": False}}
    result = compute_mainlines(_assets(_default_rows()), prev)
    assert {item["state"] for item in result} == {"unknown"}
    assert all("prev_mainlines_missing" in text
               for text in _reasons(result).values())


def test_prev_board_without_score_marks_prev_mainline_score_missing() -> None:
    prev = _prev([{"board_name": "机器人", "rank": 1, "mainline_score": None}])
    robot = {item["board_name"]: item
             for item in compute_mainlines(_assets(_default_rows()), prev)}["机器人"]
    assert robot["state"] == "unknown"
    assert "prev_mainline_score_missing" in robot["unknown_reason"]


def test_current_score_missing_uses_mainline_score_missing() -> None:
    rows = [{"name": "空板"}]
    result = compute_mainlines(_assets(rows), None)
    assert "mainline_score_missing" in result[0]["unknown_reason"]
    assert "mainline_score_unavailable" not in result[0]["unknown_reason"]


def test_board_outside_prev_topn_marks_prev_rank_missing() -> None:
    prev = _prev([{"board_name": "算力", "rank": 1, "mainline_score": 70.8}])
    result = {item["board_name"]: item
              for item in compute_mainlines(_assets(_default_rows()), prev)}
    # 第 4 名（watchlist，rank > top_n=3）不在 prev 前 5：无法判定 new
    assert result["医药"]["state"] == "unknown"
    assert "mainline_prev_rank_missing" in result["医药"]["unknown_reason"]


def test_defensive_below_threshold_reason() -> None:
    base = {item["board_name"]: item
            for item in compute_mainlines(_assets(_default_rows()), None)}
    prev = _prev([
        {"board_name": "机器人", "rank": 1, "mainline_score": 91.7},
        {"board_name": "医药", "rank": 2,
         "mainline_score": base["医药"]["mainline_score"]},
    ])
    cfg = dataclasses.replace(
        CONFIG, mainline_state_rank_up=3, mainline_state_rank_down=3)
    result = {item["board_name"]: item
              for item in compute_mainlines(
                  _assets(_default_rows()), prev, config=cfg)}
    # 医药 prev rank 2 → 当前 rank 4（-2，未达本配置的 ±3），分数不变 → 五态兜底 unknown
    assert result["医药"]["state"] == "unknown"
    assert "mainline_change_below_threshold" in result["医药"]["unknown_reason"]
