"""``strategy_context``（sc_v1）：``review`` 的只读投影。

Agent Skill 的唯一输入视图（产品方案 §13.1）。本模块**无 I/O**：不读文件 /
数据库 / 环境变量 / 时钟，不发请求；输入只有 ``review`` 普通 dict 与配置。

不变量：

* 投影**不产生** ``review`` 之外的新数值（数值零漂移，AC-P13）；
* ``positions`` 收录**全部** ``review_candidates``（含 ``position=0/null`` 与
  ``reason_code``），不只给可买清单——否则 Agent 无法解释「为什么不买」；
* 枚举只允许技术方案 §5 的动作值，展示层不得自行扩写；
* 缺失保持真正的 ``None``，绝不写成字符串 ``"null"``。
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from .config import CONFIG, ReviewConfig
from .feedback import STATE_ACTIONS
from .gates import REASON_CODES

CONTEXT_VERSION = "sc_v1"

# prep 的决策阶段时点：盘后数据就绪门禁（A50 期指 16:30 收盘 + 缓冲）。
# 这是「决策阶段」的 canonical 时间，不是实时 wall clock；因此不读 datetime.now，
# 同一 trade_date 永远产生同一 frozen_at，--recompute 两次逐字节一致。
PREP_PHASE_TIME = "16:40:00"
PREP_PHASE_TZ = "+08:00"

DISCLAIMER = "量化为复盘与条件化预案，不构成投资建议；阈值均为待验证基线。"

_STATES = ("strong", "neutral", "weak", "ice", "unknown")
_ROLES = ("leader", "vanguard", "mainstay", "capacity", "follower")
# 技术方案 §5「状态与动作的强制映射」的 allowed_actions 全集。
_ACTIONS = (
    "execute_plan",
    "execute_partial",
    "downgrade_half",
    "wait_pullback",
    "cancel_entry",
    "exit_via_sell_rules",
    "observe_only",
    "wait",
)

_POSITION_KEYS = (
    "code",
    "name",
    "role",
    "weight_profile",
    "review_score",
    "review_grade",
    "confidence",
    "dimensions",
    "contributions",
    "missing_dims",
    "eligibility",
    "position_pct",
    "position_basis",
    "reason_code",
    "gate_trace",
    "warnings",
    "mainline",
)

# T+1 状态条件（产品 §11.3）。只描述判据，不复制任何阈值数字：
# 数字来自 review_candidates[].expectations 与 CONFIG.feedback_rules。
_FEEDBACK_STATES = ("confirmed", "weakened", "invalidated", "unknown")
_FEEDBACK_REQUIRES = {
    "confirmed": (
        "gap>=gap_low",
        "ratio>=ratio_low",
        "sealed_by<=seal_by_or_close_is_strong",
    ),
    "weakened": ("gap<gap_low_or_ratio<ratio_low", "not_invalidated"),
    "invalidated": (
        "gap<gap_low-margin",
        "or_close_pct_below_floor",
        "or_close_below_prev_close",
        "or_broken_count>=min",
        "or_mainline_dropped_out_top3",
    ),
    "unknown": ("auction_or_t1_quote_missing", "or_expectations_missing"),
}
_EXPECTATION_KEYS = ("gap_low", "gap_high", "ratio_low", "seal_by")


def _feedback_plan(candidate: Mapping[str, Any]) -> dict:
    """逐只冻结预期 + 状态条件 + 动作枚举（Agent 只读视图）。

    ``expected`` 直接复制 T 日 ``review_candidates[].expectations``，因此 T+1
    读取的是冻结值而非重算值；无角色（诊断标的）显式标 ``expectations`` 缺失。
    """

    expected = candidate.get("expectations")
    base = {
        "code": candidate.get("code"),
        "role": candidate.get("role"),
        "position_pct": candidate.get("position_pct"),
        "expected": None,
        "conditions": [],
        "allowed_actions": list(STATE_ACTIONS["unknown"]),
        "unknown_reason": "expectations_unavailable",
    }
    if not isinstance(expected, Mapping):
        return base
    frozen = {key: expected.get(key) for key in _EXPECTATION_KEYS}
    return {
        **base,
        "expected": frozen,
        "conditions": [
            {
                "state": state,
                "requires": list(_FEEDBACK_REQUIRES[state]),
                "allowed_actions": list(STATE_ACTIONS[state]),
            }
            for state in _FEEDBACK_STATES
        ],
        "allowed_actions": list(STATE_ACTIONS["confirmed"]),
        "unknown_reason": None,
    }


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    return None


def prep_frozen_at(trade_date: Any) -> str | None:
    """复盘阶段冻结时点（确定性）：``<trade_date>T16:40:00+08:00``。

    由调用方注入 ``build_context``；含义是「盘后决策阶段时点」，不是实时 wall
    clock。合法日期缺失时返回 ``None``，绝不回退到当前时间。
    """

    if not isinstance(trade_date, str) or not trade_date:
        return None
    return f"{trade_date}T{PREP_PHASE_TIME}{PREP_PHASE_TZ}"


def _position(candidate: Mapping[str, Any]) -> dict:
    """逐只投影；只挑白名单字段，保证不引入 review 之外的新数值。

    ``deepcopy`` 让 context 与 review_candidates 互不共享可变对象：下游改投影
    不会污染快照里的评分/闸门证据。
    """

    return {key: copy.deepcopy(candidate.get(key)) for key in _POSITION_KEYS}


def _confidence_summary(candidates: list[dict]) -> dict:
    values = [
        value
        for value in (_num(item.get("confidence")) for item in candidates)
        if value is not None
    ]
    if not values:
        return {"min": None, "max": None, "avg": None}
    return {
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "avg": round(sum(values) / len(values), 2),
    }


def _data_quality(review: Mapping[str, Any], candidates: list[dict]) -> dict:
    missing = sorted(
        str(item.get("code"))
        for item in candidates
        if item.get("code") and item.get("review_score") is None
    )
    return {
        "degraded": _list(review.get("degraded")),
        "missing": missing,
        "confidence": _confidence_summary(candidates),
    }


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def build_context(review: dict, config: ReviewConfig = CONFIG) -> dict:
    """把 ``review`` 投影为 Agent 可消费的 ``strategy_context``（纯函数）。

    ``review`` 可同时携带 ``review_candidates``（编排层的完整 payload）；
    也接受缺省该键的 ``review``（此时 ``positions=[]``）。
    ``frozen_at`` 必须由调用方注入：它是「盘后决策阶段时点」
    （见 :func:`prep_frozen_at`），不是实时 wall clock；纯函数不读时钟。
    """

    _ = config  # 投影不新增数字；配置保留在签名里供未来版本化扩展开关
    source = review if isinstance(review, Mapping) else {}
    market = _as_dict(source.get("market"))
    anchors = _as_dict(source.get("anchors"))
    candidates = [
        item for item in _list(source.get("review_candidates"))
        if isinstance(item, Mapping)
    ]

    mainlines = [
        {
            "board_name": item.get("board_name"),
            "state": item.get("state"),
            "mainline_score": item.get("mainline_score"),
            "role_map": dict(item.get("role_map") or {})
            if isinstance(item.get("role_map"), Mapping) else {},
            "evidence": _list(item.get("evidence")),
            "unknown_reason": item.get("unknown_reason"),
        }
        for item in _list(source.get("mainlines"))
        if isinstance(item, Mapping)
    ]

    return {
        "context_version": CONTEXT_VERSION,
        "trade_date": source.get("trade_date"),
        "frozen_at": source.get("frozen_at"),
        "market": {
            "state": market.get("state"),
            "score": market.get("score"),
            "coeff": market.get("coeff"),
            "anchors": dict(_as_dict(anchors.get("risk"))),
            "evidence": _list(market.get("evidence")),
            "unknown_reason": market.get("unknown_reason"),
        },
        "mainlines": mainlines,
        "positions": [_position(item) for item in candidates],
        # T 日冻结的 T+1 预期与四态条件（产品 §11.1 / §13.1）；T+1 只读。
        "feedback_plans": [_feedback_plan(item) for item in candidates],
        "portfolio": dict(_as_dict(source.get("portfolio"))),
        "data_quality": _data_quality(source, candidates),
        "enums": {
            "state": list(_STATES),
            "role": list(_ROLES),
            "action": list(_ACTIONS),
            "reason_code": list(REASON_CODES),
        },
        "disclaimer": DISCLAIMER,
    }


__all__ = [
    "CONTEXT_VERSION",
    "DISCLAIMER",
    "PREP_PHASE_TIME",
    "PREP_PHASE_TZ",
    "build_context",
    "prep_frozen_at",
]
