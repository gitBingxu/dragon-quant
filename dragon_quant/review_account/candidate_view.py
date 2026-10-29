"""0.8 候选兼容投影（Adapter，纯函数，无 I/O）。

``review_candidates`` 是唯一权威（六维评分 + 闸门 + 仓位）；本模块把它适配成
旧字段契约的 ``candidates[]`` 视图，让 CLI / factor / buy / auction 的旧消费方
无需立刻改结构，同时保证：

* 旧五维分、旧身份（``is_true_dragon``）、旧 verdict 不进入生产排序；
* ``candidates[]`` 的 code 集合 = ``review_candidates`` ∪ 被 ``review_max_codes``
  截断的 raw 代码；
* 0.8 下游可买集合恒等于 ``review_eligible``（``eligibility=="eligible"`` 且
  ``position_pct>0``），不叠加任何旧门槛。

位置：放在 ``review_account`` 顶层（不在 ``prep`` 子包），避免 ``market`` /
``buy`` 导入本模块时被动加载 prep 包代理与 provider。
"""

from __future__ import annotations

from typing import Any, Mapping

from dragon_quant.review_account.verdicts import (
    VERDICT_IGNORE,
    VERDICT_LABELS,
    VERDICT_LIGHT,
    VERDICT_STRONG,
    VERDICT_UNRATED,
)

REVIEW_POOL_TRUNCATED = "review_pool_truncated"
REVIEW_UNAVAILABLE = "review_unavailable"
REVIEW_POOL_MARK = True

_STRONG_GRADES = ("S", "A")
_ROLE_LABELS = {
    "leader": "龙头",
    "vanguard": "先锋",
    "mainstay": "中军",
    "capacity": "容量",
    "follower": "跟风",
}


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    return None


def _list(value: Any) -> list:
    return list(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict:
    return value if isinstance(value, Mapping) else {}


def _verdict(review_candidate: Mapping[str, Any]) -> str:
    grade = review_candidate.get("review_grade")
    position = _num(review_candidate.get("position_pct"))
    if position is None:
        return VERDICT_UNRATED
    if position <= 0:
        return VERDICT_IGNORE
    return VERDICT_STRONG if grade in _STRONG_GRADES else VERDICT_LIGHT


def _reason_label(reason_code: Any) -> str | None:
    if not isinstance(reason_code, str) or not reason_code:
        return None
    # lazy import：本模块会被 buy/auction 导入，模块级 import gates 会把整个
    # prep.review 子包（含配置/评分）提前拉起，保持导入轻量。
    from dragon_quant.review_account.prep.review.gates import REASON_LABELS

    return REASON_LABELS.get(reason_code, reason_code)


def _first_failed_gate_detail(gate_trace: Any) -> str | None:
    for entry in _list(gate_trace):
        if not isinstance(entry, Mapping):
            continue
        if entry.get("passed") is False and isinstance(entry.get("detail"), str):
            detail = entry["detail"].strip()
            if detail:
                return detail
    return None


def _hard_risk_title(raw: Mapping[str, Any] | None) -> str | None:
    facts = _dict(_dict(raw).get("facts"))
    risk = _dict(facts.get("risk_announcement"))
    if risk.get("level") != "hard":
        return None
    title = risk.get("title")
    return title if isinstance(title, str) and title else None


def _ignore_reason(review_candidate: Mapping[str, Any],
                   raw: Mapping[str, Any] | None) -> str | None:
    reason_code = review_candidate.get("reason_code")
    parts: list[str] = []
    title = _hard_risk_title(raw)
    if title:
        parts.append(f"公告「{title}」")
    label = _reason_label(reason_code)
    if label:
        parts.append(label)
    detail = _first_failed_gate_detail(review_candidate.get("gate_trace"))
    if detail:
        parts.append(detail)
    return "：".join(parts) if parts else None


def _dimensions(review_candidate: Mapping[str, Any]) -> list[str]:
    """2-3 行展示明细；仅展示，不参与任何计算。"""
    score = _num(review_candidate.get("review_score"))
    grade = review_candidate.get("review_grade")
    grade_text = grade if isinstance(grade, str) and grade else "—"
    lines = [
        "复盘分 " + ("—" if score is None else f"{score:.1f}") + f" / {grade_text}"
    ]
    role = review_candidate.get("role")
    role_text = _ROLE_LABELS.get(role, "非主线") if isinstance(role, str) else "非主线"
    position = _num(review_candidate.get("position_pct"))
    position_text = "null" if position is None else (
        "0" if position <= 0 else f"{position:.1f}%")
    lines.append(f"{role_text} / 仓位 {position_text}")
    mainline = _dict(review_candidate.get("mainline"))
    board = mainline.get("board_name")
    if isinstance(board, str) and board:
        rank = mainline.get("rank")
        lines.append(f"主线 {board}" + (f" 第{rank}" if isinstance(rank, int) else ""))
    else:
        lines.append("主线 —")
    return lines


def review_candidate_view(review_candidate: dict, *,
                          raw: dict | None = None,
                          rank: int | None = None) -> dict:
    """把单只 ``review_candidate`` 投影为旧字段兼容视图（不抛异常）。

    ``raw`` 为同 code 的原始候选（已投影的 ``candidates[]`` 行或 compute 段
    raw 行），只用于补齐 name / facts / concepts / 资金与公告；缺失保持 ``None``。
    """

    source = review_candidate if isinstance(review_candidate, Mapping) else {}
    raw_row = raw if isinstance(raw, Mapping) else None
    code = source.get("code")
    name = (raw_row or {}).get("name") or source.get("name")
    trade_date = (raw_row or {}).get("trade_date") or source.get("trade_date")
    verdict = _verdict(source)
    review_eligible = (
        source.get("eligibility") == "eligible"
        and (_num(source.get("position_pct")) or 0) > 0
    )
    try:
        ignore_reason = _ignore_reason(source, raw_row)
    except Exception:  # 投影绝不抛异常：展示原因降级为 reason_code 原文
        reason = source.get("reason_code")
        ignore_reason = reason if isinstance(reason, str) else None
    try:
        dimensions = _dimensions(source)
    except Exception:
        dimensions = []

    return {
        "code": code,
        "name": name,
        "trade_date": trade_date,
        # 原始事实/血缘：只来自 raw，缺失保持 None（不伪造）
        "facts": raw_row.get("facts") if raw_row is not None else None,
        "concepts": raw_row.get("concepts") if raw_row is not None else None,
        "main_net_amount": (raw_row.get("main_net_amount")
                            if raw_row is not None else None),
        "announcements": (raw_row.get("announcements")
                          if raw_row is not None else None),
        # 旧字段：同一份 review 结果的确定性映射
        "composite_score": _num(source.get("review_score")),
        "rank": rank if isinstance(rank, int) else None,
        "verdict": verdict,
        "verdict_label": VERDICT_LABELS.get(verdict, verdict),
        "ignore_reason": ignore_reason,
        "dimensions": dimensions,
        "position_pct": _num(source.get("position_pct")),
        "is_true_dragon": None,
        "review_pool": REVIEW_POOL_MARK,
        "review_eligible": review_eligible,
    }


def _raw_view(raw: Mapping[str, Any], trade_date: str, *,
              reason: str, dimensions: list[str]) -> dict:
    """raw-only 行（截断 / review 不可用）的兼容视图。"""
    return {
        "code": raw.get("code"),
        "name": raw.get("name"),
        "trade_date": raw.get("trade_date") or trade_date,
        "facts": raw.get("facts"),
        "concepts": raw.get("concepts"),
        "main_net_amount": raw.get("main_net_amount"),
        "announcements": raw.get("announcements"),
        "composite_score": None,
        "rank": None,
        "verdict": VERDICT_UNRATED,
        "verdict_label": VERDICT_LABELS[VERDICT_UNRATED],
        "ignore_reason": reason,
        "dimensions": dimensions,
        "position_pct": None,
        "is_true_dragon": None,
        "review_pool": REVIEW_POOL_MARK,
        "review_eligible": False,
    }


def project_candidates(review_candidates: list[dict],
                       raw_candidates: list[dict],
                       trade_date: str,
                       *,
                       review_available: bool = True) -> list[dict]:
    """``review_candidates`` 为主集合，raw 仅补齐事实 / 截断兜底。

    * review 代码按 review 排序逐只投影（1-based rank）；
    * raw 中已入池代码只作增强，不重复追加；
    * raw 中未入池代码：
      - ``review_available=True`` → ``unrated/review_pool_truncated``；
      - ``review_available=False``（disabled/compute_error）→ ``review_unavailable``。
    """

    raw_by_code: dict[str, dict] = {}
    for item in _list(raw_candidates):
        if isinstance(item, Mapping) and isinstance(item.get("code"), str) \
                and item["code"]:
            raw_by_code.setdefault(item["code"], dict(item))

    out: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(_list(review_candidates), start=1):
        if not isinstance(item, Mapping):
            continue
        code = item.get("code")
        if not isinstance(code, str) or not code or code in seen:
            continue
        seen.add(code)
        out.append(review_candidate_view(
            dict(item), raw=raw_by_code.get(code), rank=index))

    reason = REVIEW_UNAVAILABLE if not review_available else REVIEW_POOL_TRUNCATED
    dimensions = [] if not review_available else ["未进入复盘详细池"]
    for code, raw in raw_by_code.items():
        if code in seen:
            continue
        seen.add(code)
        out.append(_raw_view(raw, trade_date, reason=reason,
                             dimensions=list(dimensions)))
    return out


def is_review_pool(candidate: dict) -> bool:
    """该行是否来自 0.8 review 兼容视图（来源标记，不代表可买）。"""
    return isinstance(candidate, Mapping) and candidate.get("review_pool") is True


def is_review_eligible(candidate: dict) -> bool:
    """唯一可买标记：``review_pool`` 且 ``review_eligible`` 为 True。"""
    return is_review_pool(candidate) and candidate.get("review_eligible") is True


__all__ = [
    "REVIEW_POOL_TRUNCATED",
    "REVIEW_UNAVAILABLE",
    "is_review_eligible",
    "is_review_pool",
    "project_candidates",
    "review_candidate_view",
]
