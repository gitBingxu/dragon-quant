"""prep / auction 信号语义的**唯一纯适配叶子**（零 I/O、零业务依赖）。

Web presenter、因子层与任何研究入口都调用本模块，避免同一份快照在不同页面
被解释成相反方向（A4/A5/KR3）：

* prep：固定完整 ``review_candidates`` 母集。
  - ``recommended`` = ``eligibility == "eligible"`` 且 ``position_pct > 0``；
  - ``not_recommended`` = ``position_pct == 0``（**不是**看跌预测）；
  - 其余（缺字段 / 无法解析）→ ``data_insufficient``。
* auction：方向拆成 ``bull`` / ``bear`` / ``neutral`` / ``unknown``。
  - 缺 ``unverified`` / ``level`` / ``direction`` 等关键键 → ``unknown``（fail-closed）；
  - ``neutral`` 保留中性，不强塞 ``up`` / ``down``；
  - 只有 ``bull`` / ``bear`` 进入方向命中分母；``is_weak`` 是独立的风险否决轴。

本模块**不得** import ``review_account`` / provider / scoring / storage；只接受
普通 Mapping，返回普通 dict，供任意层复用。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

__all__ = [
    "AUCTION_ACCEPTED_VERSIONS",
    "PREP_ACCEPTED_VERSIONS",
    "auction_expected_direction",
    "auction_signal",
    "prep_expected_direction",
    "prep_group",
]

PREP_ACCEPTED_VERSIONS = ("0.7", "0.8")
AUCTION_ACCEPTED_VERSIONS = ("2",)

_LEVEL_VALUES = ("强", "中", "弱")
_DIRECTION_VALUES = ("bull", "bear", "neutral")


def _text(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _number(value: Any) -> Optional[float]:
    # 只接受真实数值；字符串不猜测（与 decision_review 原 fail-closed 语义一致）。
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def prep_group(candidate: Mapping[str, Any]) -> str:
    """单个 prep 候选 → 固定三组之一（不依赖当日市场建仓日型）。"""
    position = _number(candidate.get("position_pct"))
    if position is None:
        return "data_insufficient"
    if position == 0:
        return "not_recommended"
    if position > 0 and candidate.get("eligibility") == "eligible":
        return "recommended"
    return "data_insufficient"


def prep_expected_direction(group: str) -> Optional[str]:
    """只有「推荐」才有方向预测；不推荐 ≠ 看跌（A4）。"""
    return "up" if group == "recommended" else None


def auction_signal(row: Mapping[str, Any]) -> dict:
    """auction 行 → ``{direction, level, veto, data_available, reason}``。

    方向语义与风险否决分轴；任何关键键缺失都 fail-closed 为 ``unknown``。
    """
    quality = row.get("data_quality")
    status = quality.get("status") if isinstance(quality, Mapping) else None
    level = _text(row.get("level"))
    direction = _text(row.get("direction"))
    strength = _number(row.get("strength"))

    if status == "unavailable":
        return _unknown("data_unavailable", level, bool(row.get("is_weak")))
    # 与 decision_review 一致：缺 unverified 键视为不可评价（fail-closed）。
    if "unverified" not in row:
        return _unknown("unverified_missing", level, bool(row.get("is_weak")))
    if row.get("unverified") is True:
        return _unknown("unverified", level, bool(row.get("is_weak")))
    if level not in _LEVEL_VALUES or strength is None:
        return _unknown("level_or_strength_missing", level,
                        bool(row.get("is_weak")))
    if direction not in _DIRECTION_VALUES:
        # 旧快照可能只有等级；方向未知时仍保留等级效果，但不进方向分母。
        return _unknown("direction_missing", level, bool(row.get("is_weak")))
    return {
        "direction": direction,
        "level": level,
        "veto": row.get("is_weak") is True,
        "data_available": True,
        "reason": None,
    }


def _unknown(reason: str, level: Optional[str], veto: bool) -> dict:
    return {
        "direction": "unknown",
        "level": level,
        "veto": veto,
        "data_available": False,
        "reason": reason,
    }


def auction_expected_direction(direction: str) -> Optional[str]:
    """只有 bull / bear 有方向预期；neutral / unknown 返回 ``None``（A5）。"""
    if direction == "bull":
        return "up"
    if direction == "bear":
        return "down"
    return None
