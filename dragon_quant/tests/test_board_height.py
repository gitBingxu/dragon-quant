"""``high_num`` 共享纯解析器单测（方案 §5.5 / §9.1，TDD 先 RED）。

事实口径（main 阶段 2 验收）：THS 打包值 `hi = 天数`、`lo = 板数`；
校验 `1 <= boards <= days <= 99`，不满足回退文本，仍无则 ``None``（不猜）。
未打包值（``0 < v <= 0xFFFF``）逐字不变。
"""

from __future__ import annotations

import pytest

from dragon_quant.infra.board_height import (
    MAX_PACKED,
    board_height_for_display,
    decode_packed_height,
    needs_recompute,
    parse_board_height,
    parse_board_text,
)


def row(high_num=None, high=None, high_days=None) -> dict:
    return {"high_num": high_num, "high": high, "high_days": high_days}


# ─── 文本解析 ──────────────────────────────────────────────────────────────


def test_parse_board_text_extracts_board_count():
    assert parse_board_text("5天5板") == 5
    assert parse_board_text("3天2板") == 2
    assert parse_board_text("1天1板") == 1


def test_parse_board_text_missing_or_malformed_returns_none():
    for value in (None, "", "连板", "3天", "板2", 12345, {"x": 1}):
        assert parse_board_text(value) is None


# ─── 未打包（行为不变） ────────────────────────────────────────────────────


def test_unpacked_numeric_wins_even_when_text_disagrees():
    # 现有行为：数值优先，即使与文本不一致。
    assert parse_board_height(row(4, "4天2板")) == 4


def test_unpacked_value_at_boundary_stays_literal():
    assert parse_board_height(row(MAX_PACKED)) == MAX_PACKED


# ─── 打包值（days = v >> 16，boards = v & 0xFFFF）──────────────────────────


def test_packed_0x50005_decodes_to_five_boards():
    assert parse_board_height(row(327685, "5天5板")) == 5
    assert decode_packed_height(327685) == 5


def test_packed_0x30002_decodes_to_two_boards():
    assert parse_board_height(row(196610)) == 2


def test_packed_0x20003_without_text_is_none_not_two():
    # days=2 / boards=3 非法（boards > days）；不得返回 2 或 3。
    assert parse_board_height(row(131075)) is None
    assert decode_packed_height(131075) is None


def test_packed_out_of_range_is_none():
    # days=1 / boards=4464，越界。
    assert parse_board_height(row(70000)) is None


def test_packed_invalid_falls_back_to_text():
    assert parse_board_height(row(131075, "3天2板")) == 2


# ─── 文本兜底与未知 ────────────────────────────────────────────────────────


@pytest.mark.parametrize("high_num", [0, -3, None, "n/a", True])
def test_invalid_numeric_falls_back_to_text(high_num):
    assert parse_board_height(row(high_num, "3天2板")) == 2


def test_text_fallback_prefers_high_over_high_days():
    # 口径统一：high 优先；两者不一致时以 high 为准。
    assert parse_board_height(row(None, "4天1板", "3天2板")) == 1


def test_text_fallback_uses_high_days_when_high_missing():
    assert parse_board_height(row(None, None, "3天2板")) == 2


def test_no_usable_value_returns_none():
    assert parse_board_height(row(None, None, None)) is None
    assert parse_board_height({}) is None


def test_numeric_string_high_num_is_accepted():
    # 汇总既有调用点能力（market/board_rank 原本接受数值字符串）。
    assert parse_board_height(row("4", None)) == 4


# ─── 展示辅助 ──────────────────────────────────────────────────────────────


def test_board_height_for_display_decodes_packed_and_keeps_unpacked():
    assert board_height_for_display(327685) == 5
    assert board_height_for_display(4) == 4
    assert board_height_for_display(0) is None
    assert board_height_for_display(None) is None


def test_needs_recompute_only_for_packed_values():
    assert needs_recompute(327685) is True
    assert needs_recompute(MAX_PACKED + 1) is True
    assert needs_recompute(MAX_PACKED) is False
    assert needs_recompute(4) is False
    assert needs_recompute(None) is False
    assert needs_recompute("4") is False
