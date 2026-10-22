"""近三日龙头的逐只参与判定（纯函数 + 有序规则链）。

设计（见 archived_md/prep/2026-10-20-prep-macro-candidate-verdict-design.md）：

- ``compute_candidates`` 为每只候选写 ``cand["facts"]``（今日池/资金/公告事实）；
  本模块只读 facts，不猜缺失的顶层字段。
- ``in_pool_today`` 与候选入选日解耦：T-2 入选、T 日再涨停的龙回头同样可 strong。
- 忽略原因走有责任链（数据不足 → HARD 公告 → 高位炸板 → 非涨停+流出 →
  退出梯队 → 综合分不足 → 兜底），命中即短路。
- 逐只 try/except：单只字段畸形 → ``unrated``，候选保留，绝不清空整份列表。
"""

from __future__ import annotations

import math
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

# 阈值集中调参。50 分与 review_account.min_score 默认值同源，但语义独立
# （展示评级 vs 交易过滤），改动需同步评估。
STRONG_SCORE_MIN = 75.0
STRONG_MAX_OPEN_NUM = 1
LIGHT_SCORE_MIN = 50.0
CROSS_DAY_LIGHT_MIN_PCT = 3.0
IGNORE_FLOW_RATIO = -0.05
IGNORE_DROP_PCT = 3.0
IGNORE_BROKEN_OPEN_MIN = 3
IGNORE_BROKEN_HEIGHT_MIN = 3
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

_TOP_BOARD_LIMIT = 3
_LIANBAN_RE = re.compile(r"(\d+)天(\d+)板")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_BOARD_SUFFIXES = ("概念", "板块", "指数", "行业")


def _clean(value) -> str:
    return _CONTROL_RE.sub("", str(value if value is not None else "")).strip()


def _num(value) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _shorten(text, limit: int = 40) -> str:
    value = _clean(text)
    return value if len(value) <= limit else value[: max(limit - 1, 0)] + "…"


def _fmt_money(value) -> str:
    number = _num(value)
    if number is None:
        return "—"
    amount = abs(number)
    if amount >= 1e8:
        return f"{amount / 1e8:.2f} 亿"
    if amount >= 1e4:
        return f"{amount / 1e4:.2f} 万"
    return f"{amount:.0f} 元"


def _board_height(high_days) -> Optional[int]:
    if not high_days:
        return None
    match = _LIANBAN_RE.search(str(high_days))
    return int(match.group(2)) if match else None


def _norm_board(text) -> str:
    value = _clean(text).replace("　", "")
    for suffix in _BOARD_SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix):
            value = value[: -len(suffix)]
    return value


def _reason_tokens(reason_type) -> list[str]:
    if not reason_type:
        return []
    return [_norm_board(part) for part in re.split(r"[+、,，]", str(reason_type))
            if _norm_board(part)]


def _mainline(cand: dict, main_boards: list,
              reason_type=None) -> tuple:
    names: list[str] = []
    for board in (main_boards if isinstance(main_boards, list) else []):
        if isinstance(board, dict) and board.get("board_name"):
            names.append(_norm_board(board["board_name"]))
        if len(names) >= _TOP_BOARD_LIMIT:
            break
    if not names:
        return (None, None, None)
    tokens = [_norm_board(c) for c in (cand.get("concepts") or [])
              if isinstance(c, str)]
    tokens.extend(_reason_tokens(
        reason_type if reason_type is not None else cand.get("reason_type")))
    tokens = [t for t in tokens if t]
    for idx, name in enumerate(names, 1):
        if not name:
            continue
        for token in tokens:
            if token == name or (len(name) >= 2 and name in token) or \
               (len(token) >= 2 and token in name):
                return (True, name, idx)
    return (False, None, None)


def find_risk_announcement(announcements, trade_date: str,
                           available: bool) -> Optional[dict]:
    """返回 {level, title} 或 None；available=False 表示公告资产降级（未知）。"""
    if not available or not isinstance(announcements, list):
        return None
    cutoff = None
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


def _risk_rank(candidate: dict) -> tuple:
    return (1 if candidate.get("level") == "hard" else 0,
            candidate.get("_day") or datetime.min.date())


def build_facts(cand: dict, main_boards: list, trade_date: str) -> dict:
    """把候选归一为评级事实。

    今日事实优先读 ``cand['facts']``；``code/name/composite_score/rank/concepts``
    是候选身份/排序字段，允许读顶层；不再用顶层今日事实字段猜值。
    """
    raw = cand.get("facts")
    facts = dict(raw) if isinstance(raw, dict) else {}

    def pick(name, legacy=None):
        if name in facts:
            return facts.get(name)
        return cand.get(legacy or name)

    out = {
        "code": cand.get("code"),
        "name": cand.get("name"),
        "trade_date": cand.get("trade_date") or trade_date,
        "composite_score": _num(cand.get("composite_score")),
        "rank": cand.get("rank"),
        "concepts": [c for c in (cand.get("concepts") or []) if isinstance(c, str)],
        # 今日池题材：facts 唯一来源；入选日 reason_type 仅作旧资产回退
        "reason_type": facts.get("reason_type_today") or cand.get("reason_type"),
        # 兼容手工/测试构造的候选；正常 prepare/--recompute 路径 facts 必含这些键
        "pool_available": bool(pick("pool_available", "pool_same_day")),
        "in_pool_today": bool(pick("in_pool_today", "in_pool")),
        "t_pct": _num(pick("t_pct")),
        "t_turnover": _num(pick("t_turnover")),
        "t_amount": _num(pick("t_amount")),
        "main_net": _num(pick("main_net_amount", "main_net_amount")),
        "flow_available": bool(pick("flow_available")),
        "announcements_available": bool(pick("announcements_available")),
        "announcement_count": pick("announcement_count") or 0,
        "open_num": _num(pick("open_num")),
        "high_days": pick("high_days"),
        "limit_up_type": pick("limit_up_type"),
        "first_limit_up_time": pick("first_limit_up_time"),
        "limit_up_suc_rate": _num(pick("limit_up_suc_rate")),
    }
    out["board_height"] = _board_height(out["high_days"])
    ratio = None
    if out["main_net"] is not None and out["t_amount"] is not None \
            and out["t_amount"] > 0:
        ratio = out["main_net"] / out["t_amount"]
    out["flow_ratio"] = ratio

    risk = facts.get("risk_announcement")
    if not isinstance(risk, dict):
        risk = find_risk_announcement(
            cand.get("announcements"), trade_date,
            out["announcements_available"])
    out["risk_announcement"] = risk if isinstance(risk, dict) else None

    hit, name, rank = _mainline(cand, main_boards, out["reason_type"])
    out["mainline_hit"], out["mainline_name"], out["mainline_rank"] = hit, name, rank
    return out


def _data_insufficient_reason(facts: dict) -> Optional[str]:
    if facts.get("in_pool_today") or facts.get("t_pct") is not None:
        return None
    if facts.get("pool_available"):
        return "今日行情缺失，且未进入涨停池，无法判定"
    return "今日行情与涨停池数据均缺失，无法判定参与价值"


def _ignore_reason(facts: dict) -> Optional[str]:
    risk = facts.get("risk_announcement")
    if isinstance(risk, dict) and risk.get("level") == "hard":
        title = risk.get("title") or "公告"
        return f"近 {ANNOUNCEMENT_RISK_DAYS} 日公告「{title}」，按公告风险规则忽略"

    height = facts.get("board_height")
    open_num = facts.get("open_num")
    if (facts.get("in_pool_today") and height is not None
            and height >= IGNORE_BROKEN_HEIGHT_MIN
            and open_num is not None and open_num >= IGNORE_BROKEN_OPEN_MIN):
        return (f"高位 {facts.get('high_days') or f'{int(height)}板'} "
                f"今日炸板 {int(open_num)} 次，封板承接转弱")

    main_net = facts.get("main_net")
    t_pct = facts.get("t_pct")
    ratio = facts.get("flow_ratio")
    if (not facts.get("in_pool_today") and t_pct is not None
            and t_pct < IGNORE_DROP_PCT and main_net is not None
            and main_net < 0 and ratio is not None
            and ratio <= IGNORE_FLOW_RATIO):
        return (f"今日涨幅 {t_pct:+.2f}%、主力净流出 {_fmt_money(main_net)}"
                f"（占成交额 {ratio * 100:.1f}%），资金离场")

    # 非涨停但涨幅仍 ≥3%：资金明显流出也给显式技术性归因，避免落到「强度不足」兜底
    if (not facts.get("in_pool_today") and t_pct is not None
            and t_pct >= IGNORE_DROP_PCT and main_net is not None
            and main_net < 0 and ratio is not None
            and ratio <= IGNORE_FLOW_RATIO):
        return (f"今日涨幅 {t_pct:+.2f}%、主力净流出 {_fmt_money(main_net)}"
                f"（占成交额 {ratio * 100:.1f}%），资金离场")

    if (facts.get("pool_available") and not facts.get("in_pool_today")
            and t_pct is not None and t_pct < IGNORE_DROP_PCT):
        return (f"今日涨幅 {t_pct:+.2f}%，已退出涨停梯队"
                f"（入选日 {facts.get('trade_date') or '—'}）")

    score = facts.get("composite_score")
    if score is not None and score < LIGHT_SCORE_MIN:
        return f"五维综合 {score:.1f} 低于参与阈值 50"
    return None


def _is_strong(facts: dict) -> bool:
    score = facts.get("composite_score")
    if not facts.get("in_pool_today") or score is None \
            or score < STRONG_SCORE_MIN:
        return False
    open_num = facts.get("open_num")
    if open_num is None or open_num > STRONG_MAX_OPEN_NUM:
        return False
    main_net = facts.get("main_net")
    if main_net is None or main_net <= 0:
        return False
    if not facts.get("flow_available") or not facts.get("announcements_available"):
        return False
    risk = facts.get("risk_announcement")
    if isinstance(risk, dict) and risk.get("level") in ("hard", "soft"):
        return False
    if facts.get("mainline_hit") is False:
        return False
    return True


def _is_light(facts: dict) -> bool:
    score = facts.get("composite_score")
    if score is None or score < LIGHT_SCORE_MIN:
        return False
    if facts.get("in_pool_today"):
        return True
    t_pct = facts.get("t_pct")
    if t_pct is None or t_pct < CROSS_DAY_LIGHT_MIN_PCT:
        return False
    main_net = facts.get("main_net")
    return main_net is None or main_net >= 0


def _decision(verdict: str, *, dimensions=None,
              ignore_reason: Optional[str] = None) -> dict:
    return {
        "verdict": verdict,
        "verdict_label": VERDICT_LABELS.get(verdict, verdict),
        "dimensions": list(dimensions or []),
        "ignore_reason": ignore_reason,
    }


def build_dimensions(facts: dict) -> list[str]:
    """生成 2-3 行中文维度明细；可选字段缺失时省略片段，不输出 None。"""
    score = facts.get("composite_score")
    quality = f"质量维度：五维综合 {score:.1f}"
    rank = facts.get("rank")
    if isinstance(rank, (int, float)):
        quality += f"（排名 {int(rank)}）"
    concepts = facts.get("concepts") or []
    if concepts:
        quality += f"；题材：{'、'.join(_clean(c) for c in concepts[:2])}"
        if facts.get("mainline_hit") is True:
            quality += f"（今日主线第 {facts.get('mainline_rank')}）"
        elif facts.get("mainline_hit") is False:
            quality += "（非今日主线）"
    if not facts.get("flow_available") or not facts.get("announcements_available"):
        quality += "；数据缺失，评级降档"
    risk = facts.get("risk_announcement")
    if isinstance(risk, dict) and risk.get("level") == "soft" and risk.get("title"):
        quality += f"；公告：{risk['title']}（风险关注）"

    if facts.get("in_pool_today"):
        seal = f"涨停维度：{facts.get('limit_up_type') or '涨停'}"
        high_days = facts.get("high_days")
        if high_days:
            seal += f" {high_days}"
        first = facts.get("first_limit_up_time")
        if first:
            seal += f"，首封 {first}"
        open_num = facts.get("open_num")
        if open_num is not None:
            seal += f"，炸板 {int(open_num)} 次"
        rate = facts.get("limit_up_suc_rate")
        if rate is not None:
            seal += f"，封板成功率 {rate * 100:.0f}%"
    else:
        t_pct = facts.get("t_pct")
        seal = (f"今日维度：涨幅 {t_pct:+.2f}%" if t_pct is not None
                else "今日维度：行情缺失")
        if facts.get("pool_available"):
            seal += f"，未进入涨停池（入选日 {facts.get('trade_date') or '—'}）"
        else:
            seal += "，涨停池数据缺失（无法确认封板质量）"

    main_net = facts.get("main_net")
    if main_net is None:
        flow = "资金维度：主力净额缺失（数据降级）"
    else:
        direction = "流入" if main_net >= 0 else "流出"
        flow = f"资金维度：主力净{direction} {_fmt_money(main_net)}"
        ratio = facts.get("flow_ratio")
        if ratio is not None:
            flow += f"（占成交额 {ratio * 100:.1f}%）"
    return [quality, seal, flow]


def evaluate_one(facts: dict) -> dict:
    insufficient = _data_insufficient_reason(facts)
    if insufficient:
        return _decision(VERDICT_IGNORE, ignore_reason=insufficient)
    reason = _ignore_reason(facts)
    if reason:
        return _decision(VERDICT_IGNORE, ignore_reason=reason)
    if _is_strong(facts):
        return _decision(VERDICT_STRONG, dimensions=build_dimensions(facts))
    if _is_light(facts):
        return _decision(VERDICT_LIGHT, dimensions=build_dimensions(facts))
    return _decision(VERDICT_IGNORE, ignore_reason="今日强度不足，未达参与阈值")


def evaluate_candidates(candidates: list, main_boards: list,
                        trade_date: str) -> list:
    """逐只评级；单只异常 → unrated，候选保留，不清空整份列表。"""
    out: list[dict] = []
    for cand in (candidates if isinstance(candidates, list) else []):
        if not isinstance(cand, dict):
            continue
        item = dict(cand)
        try:
            facts = build_facts(cand, main_boards, trade_date)
            item.update(evaluate_one(facts))
        except Exception:
            item.update(_decision(VERDICT_UNRATED,
                                  ignore_reason="评级字段异常"))
        out.append(item)
    return out
