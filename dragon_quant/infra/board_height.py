"""``high_num`` 唯一共享纯解析器（方案 §5.5）。

纯函数、零 I/O、零项目内依赖（只 import 标准库）：``market`` / ``board_rank`` /
``pool`` / ``orchestrator`` / ``prep.compute`` 与 ``web_ui`` 展示层共用同一实现，
禁止各自再写一份。放在 ``infra`` 叶子而非 ``review_account.prep.review``：后者包
``__init__`` 会 eager 加载 provider 模块，``web_ui`` 引用纯解析器时不应把 provider
带进 Web 进程（方案 §2.7）。

事实口径（main 阶段 2 验收）：
  - 未打包值 ``0 < v <= 0xFFFF`` → 原值，行为逐字不变；
  - 打包值 ``v > 0xFFFF`` → ``days = v >> 16``、``boards = v & 0xFFFF``；
    校验 ``1 <= boards <= days <= 99``，不满足回退文本，仍无则 ``None``；
  - 文本兜底 ``high`` 优先、``high_days`` 次之（与 ``board_rank`` 既有顺序一致）。

**不猜测**打包顺序（不用 ``min(hi, lo)``），也绝不返回巨数。
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

MAX_PACKED = 0xFFFF
# 单条主线/市场最高板的天花板：超过 99 板在 A 股语境内不可信。
MAX_PLAUSIBLE_HEIGHT = 99

_BOARD_TEXT_RE = re.compile(r"(\d+)\s*天\s*(\d+)\s*板")

__all__ = [
    "MAX_PACKED",
    "MAX_PLAUSIBLE_HEIGHT",
    "board_height_for_display",
    "decode_packed_height",
    "needs_recompute",
    "parse_board_height",
    "parse_board_text",
]


def _numeric(value: Any) -> float | None:
    """只接受有限数值 / 可转数值的字符串；``bool`` 一律视为缺失。

    与 ``review.market._num`` 同规则（合并既有 5 个调用点的共同能力）。
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return None
    return number if math.isfinite(number) else None


def parse_board_text(value: Any) -> int | None:
    """``"N天M板"`` → ``M``；无法解析返回 ``None``（不猜）。"""

    match = _BOARD_TEXT_RE.search(str(value or ""))
    return int(match.group(2)) if match else None


def decode_packed_height(value: Any) -> int | None:
    """解码 THS 打包值：``hi = 天数``、``lo = 板数``；不合法返回 ``None``。"""

    number = _numeric(value)
    if number is None or number <= MAX_PACKED:
        return None
    whole = int(number)
    if whole != number:            # 打包值必须是整数
        return None
    days = whole >> 16
    boards = whole & 0xFFFF
    if 1 <= boards <= days <= MAX_PLAUSIBLE_HEIGHT:
        return boards
    return None


def parse_board_height(row: Mapping[str, Any] | Any) -> int | None:
    """行 → 展示/评分用板数；优先级：未打包数值 → 打包解码 → 文本 → ``None``。"""

    source = row if isinstance(row, Mapping) else {}
    value = _numeric(source.get("high_num"))
    if value is not None and value > 0:
        if value <= MAX_PACKED:
            return int(value)
        decoded = decode_packed_height(value)
        if decoded is not None:
            return decoded
    # 文本兜底：``high`` 优先、``high_days`` 次之。
    for key in ("high", "high_days"):
        height = parse_board_text(source.get(key))
        if height is not None:
            return height
    return None


def board_height_for_display(stored: Any) -> int | None:
    """已存 ``high_num`` → 展示板数（打包值解码，未打包原值）。"""

    value = _numeric(stored)
    if value is None or value <= 0:
        return None
    if value <= MAX_PACKED:
        return int(value)
    return decode_packed_height(value)


def needs_recompute(stored: Any) -> bool:
    """已存 ``high_num`` 是否为打包值（旧快照评分需重算，不自动修复）。"""

    value = _numeric(stored)
    return value is not None and value > MAX_PACKED
