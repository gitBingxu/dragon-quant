"""单票建议仓位公式与组合边界（纯函数）。

产品方案 §10.2 / §10.3 的唯一实现。输入只有普通 ``dict`` / 标量，输出普通
``dict``；本模块**无 I/O**：不读文件 / 数据库 / 环境变量 / 时钟，不发请求。

设计模式：

* **策略（Strategy）** —— 档位基数、角色上限与各系数全部来自
  :class:`ReviewConfig`；换一组配置即换一套仓位口径，公式不变；
* **模板方法（Template Method）** —— 所有分支都收敛到
  ``_finalize``（系数连乘 → 角色封顶 → 向下取整 → 三态输出）。

三态语义：``None``（数据/置信不足）、``0``（明确不参与）、``>0``（可准备建仓）。
单票仓位彼此独立、**不做归一**；组合只汇总与告警，不给出优先买入顺序。
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .config import CONFIG, ReviewConfig
from .gates import (
    ELIGIBILITY_ELIGIBLE,
    ELIGIBILITY_NOT_QUALIFIED,
    ELIGIBILITY_UNKNOWN,
    GATE_BLOCKED,
    GATE_UNKNOWN,
    REASON_DATA_INSUFFICIENT,
    REASON_NOT_MAINLINE,
    REASON_POSITION_BELOW_MIN_UNIT,
)

_SUM_WARNING = "sum_exceeds_100"


# ---------------------------------------------------------------------------
# 标量工具
# ---------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def _confidence_coef(confidence: float | None, config: ReviewConfig) -> float | None:
    """置信度 → 系数；低于最低档位返回 ``None``（由 G7 判 null）。"""

    if confidence is None:
        return None
    for threshold, coef in config.confidence_coef_bands:
        if confidence >= float(threshold):
            return float(coef)
    return None


def _market_coef(market: Mapping[str, Any], config: ReviewConfig) -> float:
    state = market.get("state") if isinstance(market, Mapping) else None
    if not isinstance(state, str) or state not in config.market_state_coef:
        state = "unknown"
    return float(config.market_state_coef[state])


def _role_cap(role: Any, config: ReviewConfig) -> float | None:
    """角色上限；``role=null`` 绝不回退到 follower（AC-P18）。"""

    if not isinstance(role, str) or not role:
        return None
    cap = config.role_cap_pct.get(role)
    return None if cap is None else float(cap)


def _floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    units = math.floor(value / step + 1e-9)
    return round(units * step, 2)


def _finalize(
    *, eligibility: str, position_pct: float | None,
    reason_code: str | None = None, basis: list[dict] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "eligibility": eligibility,
        "position_pct": position_pct,
        "position_basis": basis or [],
        "reason_code": reason_code,
        "warnings": list(warnings or []),
    }


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def size_position(
    scored: dict, market: dict, config: ReviewConfig = CONFIG
) -> dict:
    """单票建议仓位（纯函数）。

    ``scored`` 需同时携带评分字段（``role`` / ``review_grade`` / ``confidence``）
    与闸门结果（``gate_status`` / ``reason_code`` / ``risk_coef`` / ``liq_coef`` /
    ``one_word_coef``）。返回 ``eligibility`` / ``position_pct`` /
    ``position_basis`` / ``reason_code`` / ``warnings``。

    公式（产品 §10.2）::

        raw = grade_base × market_coef × confidence_coef × risk_coef
              × liq_coef × one_word_coef
        position = floor_to_step(min(raw, role_cap))
        若 position < position_min_pct → 0（position_below_min_unit）
    """

    source = scored if isinstance(scored, Mapping) else {}
    market_payload = market if isinstance(market, Mapping) else {}
    warnings = list(source.get("warnings") or [])

    gate_status = source.get("gate_status")
    if gate_status == GATE_UNKNOWN:
        return _finalize(
            eligibility=ELIGIBILITY_UNKNOWN, position_pct=None,
            reason_code=source.get("reason_code"), warnings=warnings)
    if gate_status == GATE_BLOCKED:
        return _finalize(
            eligibility=ELIGIBILITY_NOT_QUALIFIED, position_pct=0.0,
            reason_code=source.get("reason_code"), warnings=warnings)

    role_cap = _role_cap(source.get("role"), config)
    if role_cap is None:
        # diagnostic_non_mainline：不获得任何角色上限，也不借用 follower。
        return _finalize(
            eligibility=ELIGIBILITY_NOT_QUALIFIED, position_pct=0.0,
            reason_code=REASON_NOT_MAINLINE, warnings=warnings)

    grade = source.get("review_grade")
    base = config.grade_base_pct.get(grade) if isinstance(grade, str) else None
    if base is None:
        return _finalize(
            eligibility=ELIGIBILITY_UNKNOWN, position_pct=None,
            reason_code=REASON_DATA_INSUFFICIENT, warnings=warnings)

    confidence_coef = _confidence_coef(_num(source.get("confidence")), config)
    if confidence_coef is None:
        # 防御：正常情况下 G7 已把 < 0.60 判为 unknown。
        return _finalize(
            eligibility=ELIGIBILITY_UNKNOWN, position_pct=None,
            reason_code="confidence_insufficient", warnings=warnings)

    market_coef = _market_coef(market_payload, config)
    risk_coef = _num(source.get("risk_coef"))
    liq_coef = _num(source.get("liq_coef"))
    one_word_coef = _num(source.get("one_word_coef"))
    if risk_coef is None:
        risk_coef = float(config.risk_coef["unknown"])
    if liq_coef is None:
        liq_coef = 1.0
    if one_word_coef is None:
        one_word_coef = 1.0

    raw = (float(base) * market_coef * confidence_coef
           * risk_coef * liq_coef * one_word_coef)
    capped = min(raw, role_cap)
    position = _floor_to_step(capped, float(config.position_step_pct))

    basis = [
        {"factor": "grade_base", "value": float(base)},
        {"factor": "market_coef", "value": market_coef},
        {"factor": "confidence_coef", "value": confidence_coef},
        {"factor": "risk_coef", "value": risk_coef},
        {"factor": "liq_coef", "value": liq_coef},
        {"factor": "one_word_coef", "value": one_word_coef},
        {"factor": "raw_pct", "value": round(raw, 2)},
        {"factor": "role_cap", "value": role_cap},
    ]

    if position < float(config.position_min_pct):
        return _finalize(
            eligibility=ELIGIBILITY_NOT_QUALIFIED, position_pct=0.0,
            reason_code=REASON_POSITION_BELOW_MIN_UNIT, basis=basis,
            warnings=warnings)

    return _finalize(
        eligibility=ELIGIBILITY_ELIGIBLE, position_pct=position,
        basis=basis, warnings=warnings)


def portfolio_summary(
    candidates: Any, config: ReviewConfig = CONFIG
) -> dict:
    """组合边界：只汇总有效单票仓位；``> 100%`` 时给 ``sum_exceeds_100``。

    **不做归一、不排序、不推荐优先买入**：单票仓位彼此独立，加总可能超过 100%。
    ``None``（未知）与 ``0``（明确不参与）都不计入分母。
    """

    rows = candidates if isinstance(candidates, list) else []
    total = 0.0
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        value = _num(item.get("position_pct"))
        if value is not None and value > 0:
            total += value
    total = round(total, 2)
    limit = float(config.portfolio_sum_limit_pct)
    return {
        "sum_pct": total,
        "warning": _SUM_WARNING if total > limit else None,
    }


__all__ = ["portfolio_summary", "size_position"]
