"""M2：五角色责任链、跨板块唯一主归属与未知处理（纯函数单测，无 I/O）。

覆盖产品方案 §7：

* 责任链固定 ``leader → vanguard → mainstay → capacity → follower``，命中即停；
* 全首板 → 第 1 名仍判龙头但 ``role_confidence="low"``、``role_basis="first_board_earliest"``；
* 先锋要求「最早非一字」且首封时间可判，时间缺失即跳过；
* 板块成员缺失 → 角色 ``null`` + ``unknown_reason``；非主线同样 ``null`` 且走诊断 profile；
* 跨板块按「涨停原因 token 匹配数 → 主线分」选唯一主归属，其余进 ``also_in_boards``。
"""

from __future__ import annotations

import copy

from dragon_quant.review_account.prep.review import CONFIG
from dragon_quant.review_account.prep.review.config import DIAGNOSTIC_PROFILE, weight_profile_for
from dragon_quant.review_account.prep.review.roles import assign_roles


def _row(
    code: str,
    *,
    height: int | None = 1,
    first_seal_time: str | None = "10:00:00",
    amount: float | None = 1e9,
    float_market_cap: float | None = 1e10,
    limit_up_type: str | None = "换手板",
    reason_type: str | None = "机器人",
) -> dict:
    return {
        "code": code,
        "height": height,
        "first_seal_time": first_seal_time,
        "amount": amount,
        "float_market_cap": float_market_cap,
        "limit_up_type": limit_up_type,
        "reason_type": reason_type,
    }


def _board(
    name: str = "机器人",
    *,
    members: list[str] | None = None,
    rank: int = 1,
    score: float = 90.0,
    state: str = "strengthening",
    continuous: int = 3,
    **overrides: object,
) -> dict:
    board: dict = {
        "board_name": name,
        "rank": rank,
        "mainline_score": score,
        "state": state,
        "limit_up_num": len(members or []),
        "high_num": 4,
        "continuous_plate_num": continuous,
        "members": list(members or []),
    }
    board.update(overrides)
    return board


def _five_role_fixture() -> tuple[list[dict], list[dict]]:
    pool = [
        _row("600001", height=4, first_seal_time="09:40:00", amount=2e9),
        _row("600002", height=1, first_seal_time="09:33:00", amount=1e9),
        _row("600003", height=2, first_seal_time="10:00:00", amount=0.8e9, float_market_cap=25e9),
        _row("600004", height=2, first_seal_time="10:30:00", amount=1.2e9),
        _row("600005", height=1, first_seal_time="14:00:00", amount=0.5e9),
    ]
    blocks = [_board(members=[row["code"] for row in pool])]
    return pool, blocks


# ---------------------------------------------------------------------------
# 责任链
# ---------------------------------------------------------------------------


def test_five_roles_follow_chain_order() -> None:
    pool, blocks = _five_role_fixture()
    result = assign_roles(pool, blocks, CONFIG)

    assert result["600001"]["role"] == "leader"
    assert result["600002"]["role"] == "vanguard"
    assert result["600003"]["role"] == "mainstay"
    assert result["600004"]["role"] == "capacity"
    assert result["600005"]["role"] == "follower"

    assert result["600001"]["role_confidence"] == "high"
    assert result["600003"]["role_basis"].startswith("流通市值")
    assert result["600004"]["role_basis"].startswith("成交额")
    assert result["600005"]["role_confidence"] == "low"


def test_role_competitors_record_other_eligible_roles() -> None:
    pool, blocks = _five_role_fixture()
    result = assign_roles(pool, blocks, CONFIG)

    # 先锋 600002 同时满足容量核心（成交额 ≥ 10 亿且高度 ≤ 2），但责任链已命中先锋
    assert "capacity" in result["600002"]["role_competitors"]
    # 龙头 600001 高度 4，不满足容量核心
    assert "capacity" not in result["600001"]["role_competitors"]


def test_leader_with_earliest_seal_records_vanguard_competitor() -> None:
    pool = [
        _row("600001", height=3, first_seal_time="09:31:00"),
        _row("600002", height=1, first_seal_time="09:40:00", amount=0.5e9),
    ]
    result = assign_roles(pool, [_board(members=["600001", "600002"])], CONFIG)

    assert result["600001"]["role"] == "leader"
    assert result["600002"]["role"] == "vanguard"
    # 龙头本身也是最早非一字：作为「若不当龙头则可能为先锋」的证据记录
    assert "vanguard" in result["600001"]["role_competitors"]


def test_all_first_board_leader_is_low_confidence() -> None:
    pool = [
        _row("600001", height=1, first_seal_time="10:00:00", amount=0.5e9),
        _row("600002", height=1, first_seal_time="09:35:00"),
        _row("600003", height=1, first_seal_time="09:50:00"),
    ]
    result = assign_roles(pool, [_board(members=["600001", "600002", "600003"])], CONFIG)

    assert result["600002"]["role"] == "leader"
    assert result["600002"]["role_confidence"] == "low"
    assert result["600002"]["role_basis"] == "first_board_earliest"
    assert result["600003"]["role"] == "vanguard"
    assert result["600001"]["role"] == "follower"


def test_vanguard_skips_missing_first_seal_time() -> None:
    pool = [
        _row("600001", height=3, first_seal_time="09:40:00"),
        _row("600002", height=1, first_seal_time=None, amount=0.5e9),
        _row("600003", height=1, first_seal_time="09:35:00"),
    ]
    result = assign_roles(pool, [_board(members=["600001", "600002", "600003"])], CONFIG)

    assert result["600001"]["role"] == "leader"
    assert result["600003"]["role"] == "vanguard"
    assert result["600002"]["role"] == "follower"


def test_vanguard_skips_one_word_limit_up() -> None:
    pool = [
        _row("600001", height=3, first_seal_time="09:40:00"),
        _row("600002", height=2, first_seal_time="09:30:00", amount=0.5e9, limit_up_type="一字板"),
        _row("600003", height=1, first_seal_time="09:35:00"),
    ]
    result = assign_roles(pool, [_board(members=["600001", "600002", "600003"])], CONFIG)

    assert result["600003"]["role"] == "vanguard"
    assert result["600002"]["role"] == "follower"


# ---------------------------------------------------------------------------
# 缺失 / 非主线
# ---------------------------------------------------------------------------


def test_missing_board_members_yields_null_role_and_reason() -> None:
    pool = [_row("600001"), _row("600002")]
    blocks = [{"board_name": "机器人", "rank": 1, "mainline_score": 90.0}]

    result = assign_roles(pool, blocks, CONFIG)

    for code in ("600001", "600002"):
        assert result[code]["role"] is None
        assert result[code]["role_confidence"] == "unknown"
        assert result[code]["unknown_reason"] == "board_members_missing"
        assert result[code]["weight_profile"] == DIAGNOSTIC_PROFILE


def test_no_mainline_available_yields_null_role() -> None:
    result = assign_roles([_row("600001")], [], CONFIG)

    assert result["600001"]["role"] is None
    assert result["600001"]["unknown_reason"] == "no_mainline_available"
    assert result["600001"]["mainline_rank"] is None


def test_non_mainline_uses_diagnostic_profile_not_follower() -> None:
    pool = [_row("600009", reason_type="其他题材")]
    blocks = [_board(members=["600001"])]

    result = assign_roles(pool, blocks, CONFIG)
    record = result["600009"]

    assert record["role"] is None
    assert record["unknown_reason"] == "not_in_mainline"
    assert record["mainline"] is None
    assert record["mainline_rank"] == 0
    assert record["weight_profile"] == DIAGNOSTIC_PROFILE
    assert weight_profile_for(record["role"]) == DIAGNOSTIC_PROFILE
    assert record["role_competitors"] == []


# ---------------------------------------------------------------------------
# 跨板块唯一主归属
# ---------------------------------------------------------------------------


def test_multi_board_attribution_prefers_token_match_then_score() -> None:
    pool = [_row("600001", reason_type="机器人+减速器")]
    blocks = [
        _board("机器人", members=["600001"], rank=1, score=80.0),
        _board("减速器", members=["600001"], rank=2, score=90.0),
    ]

    result = assign_roles(pool, blocks, CONFIG)
    record = result["600001"]

    # token 匹配数相同（各 1）→ 主线分高者胜
    assert record["mainline"]["board_name"] == "减速器"
    assert record["also_in_boards"] == ["机器人"]
    assert record["attr_match"] == 1
    assert "leader@机器人" in record["role_competitors"]


def test_multi_board_attribution_prefers_matching_board_over_higher_score() -> None:
    pool = [_row("600001", reason_type="机器人+减速器")]
    blocks = [
        _board("机器人", members=["600001"], rank=1, score=90.0),
        _board("减速器", members=["600001"], rank=2, score=80.0),
    ]
    result = assign_roles(pool, blocks, CONFIG)
    assert result["600001"]["mainline"]["board_name"] == "机器人"


def test_multi_board_tie_breaks_by_rank_then_name() -> None:
    pool = [_row("600001", reason_type="机器人+减速器")]
    same_score = [
        _board("机器人", members=["600001"], rank=2, score=90.0),
        _board("减速器", members=["600001"], rank=1, score=90.0),
    ]
    result = assign_roles(pool, same_score, CONFIG)
    assert result["600001"]["mainline"]["board_name"] == "减速器"

    same_rank = [
        _board("机器人", members=["600001"], rank=1, score=90.0),
        _board("减速器", members=["600001"], rank=1, score=90.0),
    ]
    result = assign_roles(pool, same_rank, CONFIG)
    assert result["600001"]["mainline"]["board_name"] == min("机器人", "减速器")


def test_secondary_board_role_conflict_is_recorded() -> None:
    pool = [_row("600001", height=4, first_seal_time="09:40:00")]
    blocks = [
        _board("机器人", members=["600001"], rank=1, score=90.0),
        _board("减速器", members=["600001"], rank=2, score=80.0),
    ]
    # 涨停原因只匹配机器人 → 主归属机器人；减速器中的同只票也记录冲突
    result = assign_roles(pool, blocks, CONFIG)
    assert result["600001"]["mainline"]["board_name"] == "机器人"
    assert "leader@减速器" in result["600001"]["role_competitors"]


# ---------------------------------------------------------------------------
# 阈值边界 / 证据字段
# ---------------------------------------------------------------------------


def test_mainstay_threshold_is_inclusive() -> None:
    pool = [
        _row("600001", height=4, first_seal_time="09:40:00"),
        _row(
            "600002",
            height=2,
            first_seal_time=None,
            amount=5e9,
            float_market_cap=CONFIG.role_mainstay_min_float_cap,
        ),
        _row(
            "600003",
            height=2,
            first_seal_time=None,
            amount=1e9,
            float_market_cap=CONFIG.role_mainstay_min_float_cap - 1,
        ),
    ]
    result = assign_roles(pool, [_board(members=["600001", "600002", "600003"])], CONFIG)

    assert result["600002"]["role"] == "mainstay"
    assert result["600003"]["role"] == "capacity"
    # 中军同时满足容量核心条件 → 竞争者记录
    assert "capacity" in result["600002"]["role_competitors"]


def test_capacity_requires_amount_and_height_cap() -> None:
    pool = [
        _row("600001", height=4, first_seal_time="09:40:00"),
        _row("600002", height=3, first_seal_time=None, amount=5e9),
        _row("600003", height=2, first_seal_time=None, amount=CONFIG.role_capacity_min_amount),
        _row("600004", height=2, first_seal_time=None, amount=CONFIG.role_capacity_min_amount - 1),
    ]
    result = assign_roles(pool, [_board(members=["600001", "600002", "600003", "600004"])], CONFIG)

    assert result["600002"]["role"] == "follower"  # 高度 3 > 2
    assert result["600003"]["role"] == "capacity"
    assert result["600004"]["role"] == "follower"


def test_missing_metrics_do_not_guess_roles() -> None:
    pool = [
        _row("600001", height=None, first_seal_time=None),
        _row("600002", height=None, first_seal_time=None, amount=None, float_market_cap=None),
    ]
    result = assign_roles(pool, [_board(members=["600001", "600002"])], CONFIG)

    # 高度未知不能判龙头；首封时间也未知 → 先锋不可判；两只都只能兜底跟风
    assert result["600001"]["role"] == "follower"
    assert result["600002"]["role"] == "follower"


def test_evidence_fields_feed_scoring_contract() -> None:
    pool, blocks = _five_role_fixture()
    result = assign_roles(pool, blocks, CONFIG)
    leader = result["600001"]

    assert leader["intra_board_rank"] == 1
    assert leader["board_limit_up_num"] == 5
    assert leader["mainline_rank"] == 1
    assert leader["mainline_days"] == 3
    assert leader["attr_match"] == 1
    assert leader["mainline"] == {
        "board_name": "机器人",
        "rank": 1,
        "mainline_score": 90.0,
        "state": "strengthening",
    }
    assert leader["evidence"]["height"] == 4
    assert leader["evidence"]["first_seal_time"] == "09:40:00"


def test_assign_roles_does_not_mutate_inputs() -> None:
    pool, blocks = _five_role_fixture()
    pool_before = copy.deepcopy(pool)
    blocks_before = copy.deepcopy(blocks)

    assign_roles(pool, blocks, CONFIG)

    assert pool == pool_before
    assert blocks == blocks_before
