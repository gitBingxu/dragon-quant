"""建仓资格闸门（责任链，顺序即优先级，命中即短路）。

产品方案 §10.1 的唯一实现。输入只有普通 ``dict`` / ``list`` / 标量，输出普通
``dict``；本模块**无 I/O**：不读文件 / 数据库 / 环境变量 / 时钟，不发请求。

设计模式：

* **责任链（Chain of Responsibility）** —— G0 → G7 依次判定，命中即短路；
* **策略（Strategy）** —— 风险 / 流动性 / 一字板系数由 :class:`ReviewConfig`
  提供，闸门只决定「能否继续」，不改公式。

语义边界：

* ``gate_status="unknown"`` → 下游仓位 ``null``（数据/置信不足，不能伪造 0）；
* ``gate_status="blocked"`` → 下游仓位 ``0``（明确不参与）；
* 硬风险（G1）先于任何分数/档位判定，高分解除不了硬风险（AC-P4）；
* ``role=null`` 的 ``diagnostic_non_mainline`` 不获得 follower 角色或仓位上限
  （AC-P18）：G4 仍按 ``mainline_hit=false`` 判 0。
"""

from __future__ import annotations

from typing import Any, Mapping

from .config import CONFIG, ReviewConfig

GATE_PASS = "pass"
GATE_BLOCKED = "blocked"
GATE_UNKNOWN = "unknown"

ELIGIBILITY_ELIGIBLE = "eligible"
ELIGIBILITY_NOT_QUALIFIED = "not_qualified"
ELIGIBILITY_UNKNOWN = "unknown"

GATE_ORDER = (
    "G0_data",
    "G1_hard_risk",
    "G2_suspended",
    "G3a_liquidity",
    "G3b_liquidity",
    "G4_mainline",
    "G5_score",
    "G6_grade",
    "G7_confidence",
)

# 闸门 reason_code 全集（Agent 枚举的唯一来源，禁止在展示层另造一套）。
REASON_DATA_INSUFFICIENT = "data_insufficient"
REASON_HARD_RISK = "hard_risk"
REASON_SUSPENDED = "suspended"
REASON_ILLIQUID = "illiquid"
REASON_NOT_MAINLINE = "not_mainline"
REASON_SCORE_BELOW_FLOOR = "score_below_floor"
REASON_GRADE_NO_POSITION = "grade_no_position"
REASON_CONFIDENCE_INSUFFICIENT = "confidence_insufficient"
REASON_POSITION_BELOW_MIN_UNIT = "position_below_min_unit"
REASON_COMPUTE_ERROR = "compute_error"

REASON_CODES = (
    REASON_DATA_INSUFFICIENT,
    REASON_HARD_RISK,
    REASON_SUSPENDED,
    REASON_ILLIQUID,
    REASON_NOT_MAINLINE,
    REASON_SCORE_BELOW_FLOOR,
    REASON_GRADE_NO_POSITION,
    REASON_CONFIDENCE_INSUFFICIENT,
    REASON_POSITION_BELOW_MIN_UNIT,
    REASON_COMPUTE_ERROR,
)

REASON_LABELS = {
    REASON_DATA_INSUFFICIENT: "数据不足",
    REASON_HARD_RISK: "硬风险",
    REASON_SUSPENDED: "停牌",
    REASON_ILLIQUID: "流动性不足",
    REASON_NOT_MAINLINE: "非主线",
    REASON_SCORE_BELOW_FLOOR: "分数不足",
    REASON_GRADE_NO_POSITION: "档位无仓位",
    REASON_CONFIDENCE_INSUFFICIENT: "置信不足",
    REASON_POSITION_BELOW_MIN_UNIT: "低于最小单位",
    REASON_COMPUTE_ERROR: "计算异常",
}

_RISK_LEVELS = ("none", "soft", "hard", "unknown")
_ONE_WORD_MARK = "一字"
_GRADE_NO_BASE = ("C", "D")


# ---------------------------------------------------------------------------
# 标量工具
# ---------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    return None


def _risk_level(cand: Mapping[str, Any]) -> str:
    """风险等级；**缺少风险输入时视为 unknown，绝不当作无风险**（E16）。"""

    risk = cand.get("risk")
    level = risk.get("level") if isinstance(risk, Mapping) else cand.get("risk_level")
    return level if level in _RISK_LEVELS else "unknown"


def _mainline_hit(cand: Mapping[str, Any]) -> bool:
    explicit = cand.get("mainline_hit")
    if isinstance(explicit, bool):
        return explicit
    return bool(cand.get("role")) and isinstance(cand.get("mainline"), Mapping)


def _is_one_word(limit_up_type: Any) -> bool:
    return isinstance(limit_up_type, str) and _ONE_WORD_MARK in limit_up_type


def _trace_entry(
    gate: str, evaluated: bool, passed: bool | None,
    reason_code: str | None = None, detail: str | None = None,
) -> dict:
    return {
        "gate": gate,
        "evaluated": evaluated,
        "passed": passed,
        "reason_code": reason_code,
        "detail": detail,
    }


def _finish(
    trace: list[dict], status: str, reason_code: str | None,
    *, risk_level: str, risk_coef: float, liq_coef: float,
    one_word_coef: float, warnings: list[str],
) -> dict:
    """补齐短路后的未评估闸门，返回完整闸门结果。"""

    seen = {item["gate"] for item in trace}
    for gate in GATE_ORDER:
        if gate not in seen:
            trace.append(_trace_entry(gate, False, None))
    trace.sort(key=lambda item: GATE_ORDER.index(item["gate"]))
    return {
        "gate_status": status,
        "reason_code": reason_code,
        "risk_level": risk_level,
        "risk_coef": risk_coef,
        "liq_coef": liq_coef,
        "one_word_coef": one_word_coef,
        "warnings": warnings,
        "gate_trace": trace,
    }


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def evaluate_gates(
    cand: dict, ctx: dict | None = None, config: ReviewConfig = CONFIG
) -> dict:
    """建仓资格闸门（纯函数）。

    ``cand`` 约定字段（缺失即 unknown，不填中性值）：

    ============== ==========================================================
    字段            含义
    ============== ==========================================================
    ``role``       角色；``None`` = 非主线（diagnostic profile，不获角色上限）
    ``mainline_hit`` 是否属于当日主线（缺省时由 role + mainline 推导）
    ``review_score`` / ``review_grade`` / ``confidence`` / ``coverage``
    ``pool_available`` / ``t_pct``   G0 数据基座
    ``amount``      T 日成交额（元，G3）
    ``suspended``   T 日/T+1 公告停牌（G2）
    ``limit_up_type`` 含「一字」→ 执行风险
    ``risk``        ``{"level": "none"|"soft"|"hard"|"unknown"}``
    ============== ==========================================================

    ``ctx`` 只作上下文留痕（如 ``trade_date`` / ``market``），不参与判定。
    返回 ``gate_status`` / ``reason_code`` / 各系数 / ``warnings`` / ``gate_trace``。
    """

    source = cand if isinstance(cand, Mapping) else {}
    _ = ctx  # 闸门是候选级纯判定；ctx 保留给调用方传市场/日期血缘
    risk_level = _risk_level(source)
    risk_coef = float(config.risk_coef.get(risk_level, 0.0))
    one_word_coef = (
        float(config.one_word_coef) if _is_one_word(source.get("limit_up_type")) else 1.0
    )
    warnings: list[str] = []
    if one_word_coef != 1.0:
        warnings.append("one_word_execution_risk")

    trace: list[dict] = []

    # G0 数据不足：涨停池不可用且无 T 日涨幅，或覆盖率不足，或分数不可用。
    score = _num(source.get("review_score"))
    coverage = _num(source.get("coverage"))
    pool_available = source.get("pool_available") is True
    t_pct = _num(source.get("t_pct"))
    data_ok = pool_available or t_pct is not None
    coverage_ok = coverage is not None and coverage >= config.coverage_min
    g0_pass = data_ok and coverage_ok and score is not None
    trace.append(_trace_entry(
        "G0_data", True, g0_pass,
        None if g0_pass else REASON_DATA_INSUFFICIENT,
        None if g0_pass else (
            "pool_missing_and_no_t_pct" if not data_ok
            else "coverage_below_floor" if not coverage_ok
            else "review_score_unavailable"
        ),
    ))
    if not g0_pass:
        return _finish(
            trace, GATE_UNKNOWN, REASON_DATA_INSUFFICIENT,
            risk_level=risk_level, risk_coef=risk_coef, liq_coef=1.0,
            one_word_coef=one_word_coef, warnings=warnings,
        )

    # G1 硬风险 / 风险未知（未知 ≠ 无风险）。
    if risk_level == "hard":
        trace.append(_trace_entry("G1_hard_risk", True, False, REASON_HARD_RISK,
                                  "hard_risk_announcement"))
        return _finish(
            trace, GATE_BLOCKED, REASON_HARD_RISK,
            risk_level=risk_level, risk_coef=risk_coef, liq_coef=1.0,
            one_word_coef=one_word_coef, warnings=warnings,
        )
    if risk_level == "unknown":
        trace.append(_trace_entry("G1_hard_risk", True, False,
                                  REASON_DATA_INSUFFICIENT,
                                  "announcement_risk_unknown"))
        return _finish(
            trace, GATE_UNKNOWN, REASON_DATA_INSUFFICIENT,
            risk_level=risk_level, risk_coef=risk_coef, liq_coef=1.0,
            one_word_coef=one_word_coef, warnings=warnings,
        )
    trace.append(_trace_entry("G1_hard_risk", True, True, None,
                              f"risk_level={risk_level}"))

    # G2 不可交易。
    if source.get("suspended") is True:
        trace.append(_trace_entry("G2_suspended", True, False, REASON_SUSPENDED,
                                  "suspended"))
        return _finish(
            trace, GATE_BLOCKED, REASON_SUSPENDED,
            risk_level=risk_level, risk_coef=risk_coef, liq_coef=1.0,
            one_word_coef=one_word_coef, warnings=warnings,
        )
    trace.append(_trace_entry("G2_suspended", True, True))

    # G3a / G3b 流动性：缺失为 unknown（不伪造「充足」）。
    amount = _num(source.get("amount"))
    liq_low, liq_high = config.liquidity_thresholds
    if amount is None:
        trace.append(_trace_entry("G3a_liquidity", True, False,
                                  REASON_DATA_INSUFFICIENT, "liquidity_unknown"))
        return _finish(
            trace, GATE_UNKNOWN, REASON_DATA_INSUFFICIENT,
            risk_level=risk_level, risk_coef=risk_coef, liq_coef=1.0,
            one_word_coef=one_word_coef, warnings=warnings,
        )
    if amount < liq_low:
        trace.append(_trace_entry("G3a_liquidity", True, False, REASON_ILLIQUID,
                                  f"amount={amount:.0f}"))
        return _finish(
            trace, GATE_BLOCKED, REASON_ILLIQUID,
            risk_level=risk_level, risk_coef=risk_coef, liq_coef=0.0,
            one_word_coef=one_word_coef, warnings=warnings,
        )
    liq_coef = 1.0 if amount >= liq_high else 0.5
    trace.append(_trace_entry("G3a_liquidity", True, True))
    trace.append(_trace_entry("G3b_liquidity", True, True, None,
                              f"liq_coef={liq_coef}"))

    # G4 非主线：role=null / 跟风但不在任何主线 → 明确不参与。
    hit = _mainline_hit(source)
    if not hit:
        trace.append(_trace_entry("G4_mainline", True, False, REASON_NOT_MAINLINE,
                                  "mainline_hit=false"))
        return _finish(
            trace, GATE_BLOCKED, REASON_NOT_MAINLINE,
            risk_level=risk_level, risk_coef=risk_coef, liq_coef=liq_coef,
            one_word_coef=one_word_coef, warnings=warnings,
        )
    trace.append(_trace_entry("G4_mainline", True, True))

    # G5 分数不足（D 档）。
    floor = float(config.grade_thresholds[3])
    if score < floor:
        trace.append(_trace_entry("G5_score", True, False, REASON_SCORE_BELOW_FLOOR,
                                  f"review_score={score:.1f}"))
        return _finish(
            trace, GATE_BLOCKED, REASON_SCORE_BELOW_FLOOR,
            risk_level=risk_level, risk_coef=risk_coef, liq_coef=liq_coef,
            one_word_coef=one_word_coef, warnings=warnings,
        )
    trace.append(_trace_entry("G5_score", True, True))

    # G6 无仓位基数（C / D）。
    grade = source.get("review_grade")
    if not isinstance(grade, str) or not grade:
        trace.append(_trace_entry("G6_grade", True, False,
                                  REASON_DATA_INSUFFICIENT, "review_grade_missing"))
        return _finish(
            trace, GATE_UNKNOWN, REASON_DATA_INSUFFICIENT,
            risk_level=risk_level, risk_coef=risk_coef, liq_coef=liq_coef,
            one_word_coef=one_word_coef, warnings=warnings,
        )
    if grade in _GRADE_NO_BASE:
        trace.append(_trace_entry("G6_grade", True, False, REASON_GRADE_NO_POSITION,
                                  f"review_grade={grade}"))
        return _finish(
            trace, GATE_BLOCKED, REASON_GRADE_NO_POSITION,
            risk_level=risk_level, risk_coef=risk_coef, liq_coef=liq_coef,
            one_word_coef=one_word_coef, warnings=warnings,
        )
    trace.append(_trace_entry("G6_grade", True, True))

    # G7 置信不足：低于最低置信档位 → null（不是 0）。
    confidence = _num(source.get("confidence"))
    confidence_floor = float(config.confidence_coef_bands[-1][0])
    if confidence is None or confidence < confidence_floor:
        trace.append(_trace_entry("G7_confidence", True, False,
                                  REASON_CONFIDENCE_INSUFFICIENT,
                                  f"confidence={confidence}"))
        return _finish(
            trace, GATE_UNKNOWN, REASON_CONFIDENCE_INSUFFICIENT,
            risk_level=risk_level, risk_coef=risk_coef, liq_coef=liq_coef,
            one_word_coef=one_word_coef, warnings=warnings,
        )
    trace.append(_trace_entry("G7_confidence", True, True))

    return _finish(
        trace, GATE_PASS, None,
        risk_level=risk_level, risk_coef=risk_coef, liq_coef=liq_coef,
        one_word_coef=one_word_coef, warnings=warnings,
    )


__all__ = [
    "ELIGIBILITY_ELIGIBLE",
    "ELIGIBILITY_NOT_QUALIFIED",
    "ELIGIBILITY_UNKNOWN",
    "GATE_BLOCKED",
    "GATE_ORDER",
    "GATE_PASS",
    "GATE_UNKNOWN",
    "REASON_CODES",
    "REASON_LABELS",
    "evaluate_gates",
]
