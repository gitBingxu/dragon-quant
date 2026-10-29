"""旧候选评级常量与共享风险工具（旧推荐规则链已退役）。

历史背景：``evaluate_candidates`` / ``evaluate_one`` 等五维推荐规则链已在
2026-10-30 的「prep 新旧算法统一」中退出生产（唯一权威是 ``review_candidates``）。
本模块只保留：

* 四档 verdict 常量与中文标签（0.8 兼容投影与展示层使用）；
* ``find_risk_announcement``（新六维闸门 G1 与旧资产降级路径共用的公告风险工具）。

不要在这里重新引入任何「候选范围 / 推荐过滤」逻辑。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

VERDICT_STRONG = "strong"
VERDICT_LIGHT = "light"
VERDICT_IGNORE = "ignore"
VERDICT_UNRATED = "unrated"

VERDICT_LABELS = {
    VERDICT_STRONG: "强烈建议",
    VERDICT_LIGHT: "轻仓博弈",
    VERDICT_IGNORE: "忽略",
    VERDICT_UNRATED: "未评级",
}

ANNOUNCEMENT_RISK_DAYS = 3

ANNOUNCEMENT_HARD_KEYWORDS = (
    "立案", "调查", "行政处罚", "处罚", "警示函", "监管函", "退市", "预亏",
    "预减", "业绩下滑", "减值", "违规", "诉讼", "仲裁", "冻结",
)
ANNOUNCEMENT_SOFT_KEYWORDS = ("减持", "质押", "担保", "澄清", "延期回复", "终止")
ANNOUNCEMENT_NEGATIONS = (
    "终止减持", "减持计划终止", "不减持", "不存在", "未涉及", "不属实", "撤销",
)
# 具体「利好型撤销」只抑制 HARD；通用否定词不抑制 HARD，避免
# 「立案调查暨不存在应披露事项」「冻结及减持计划终止」被整条跳过而漏判风险。
ANNOUNCEMENT_HARD_NEGATIONS = ("撤销退市风险警示", "撤销退市")

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _clean(value) -> str:
    return _CONTROL_RE.sub("", str(value if value is not None else "")).strip()


def _shorten(text, limit: int = 40) -> str:
    value = _clean(text)
    return value if len(value) <= limit else value[: max(limit - 1, 0)] + "…"


def _risk_rank(candidate: dict) -> tuple:
    return (1 if candidate.get("level") == "hard" else 0,
            candidate.get("_day") or datetime.min.date())


def find_risk_announcement(announcements, trade_date: str,
                           available: bool) -> Optional[dict]:
    """返回 {level, title} 或 None；available=False 表示公告资产降级（未知）。"""
    if not available or not isinstance(announcements, list):
        return None
    try:
        cutoff = (datetime.fromisoformat(trade_date)
                  - timedelta(days=ANNOUNCEMENT_RISK_DAYS)).date()
    except (TypeError, ValueError):
        cutoff = None
    best = None
    for ann in announcements:
        if not isinstance(ann, dict):
            continue
        title = _clean(ann.get("title") or "")
        if not title:            # 无标题不猜风险，也不把 None 拼进原因
            continue
        text = f"{title} {_clean(ann.get('type') or '')}"
        # HARD 优先：通用否定词（不存在/未涉及/不属实）不抑制 HARD，
        # 否则「立案调查暨不存在应披露事项」「冻结及减持计划终止」会漏判；
        # 仅「撤销退市风险警示」这类明确利好短语抑制 HARD。
        hard = (any(k in text for k in ANNOUNCEMENT_HARD_KEYWORDS)
                and not any(p in text for p in ANNOUNCEMENT_HARD_NEGATIONS))
        if hard:
            level = "hard"
        elif any(phrase in text for phrase in ANNOUNCEMENT_NEGATIONS):
            # SOFT 主体被否定（终止减持/减持计划终止/不属实等）→ 整条跳过
            continue
        elif any(k in text for k in ANNOUNCEMENT_SOFT_KEYWORDS):
            level = "soft"
        else:
            level = None
        if level is None:
            continue
        raw = str(ann.get("date") or "")[:10]
        day = None
        if raw:
            try:
                day = datetime.fromisoformat(raw).date()
            except ValueError:
                day = None
        # 无法证明在窗口内 → 视为窗口外（未知不伪造风险）
        if day is None:
            continue
        if cutoff is not None and day < cutoff:
            continue
        candidate = {"level": level, "title": _shorten(title, 40),
                     "date": raw or None, "_day": day}
        # HARD 优先于 SOFT；同级别取较新
        if best is None or _risk_rank(candidate) > _risk_rank(best):
            best = candidate
    if best is None:
        return None
    best.pop("_day", None)
    return best


__all__ = [
    "ANNOUNCEMENT_RISK_DAYS",
    "VERDICT_IGNORE",
    "VERDICT_LABELS",
    "VERDICT_LIGHT",
    "VERDICT_STRONG",
    "VERDICT_UNRATED",
    "find_risk_announcement",
]
