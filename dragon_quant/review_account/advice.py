"""复盘结论的中文展示层（纯函数，无 I/O）。

CLI 默认输出的 4 行摘要由此组装；快照本身不持久化展示文本，保证
「快照 = 资产的纯函数」与文案演化解耦。设计见
``archived_md/prep/2026-10-20-prep-3day-window-design.md``。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from dragon_quant.providers.macro import MACRO_INDEX_KEYS
from dragon_quant.review_account.verdicts import (
    VERDICT_IGNORE, VERDICT_LABELS, VERDICT_LIGHT, VERDICT_STRONG,
)

# C0/C1 控制字符：外部板块名等文本进入人读终端前统一清洗
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# 降级 code → 中文标签（未知 code 原样展示）
DEGRADED_LABELS = {
    "dragons": "龙头数据缺失",
    "dragons_partial": "龙头窗口缺日",
    "dragons_window_short": "交易日历不足以填满窗口",
    "dragons_window_stale": "龙头窗口口径落后（非最新窗口长度）",
    "dragons_mirror_missing_day": "龙头镜像缺日（源库有行而镜像缺行）",
    "dragons_window_widened_dependents_stale": "窗口加宽后资金/公告覆盖不足",
    "ths_limit_up_pool": "THS 涨停数据缺失",
    "ths_block_top": "题材主线数据缺失",
    "market_breadth": "市场宽度缺失",
    "fg_boards": "FG 板块数据缺失",
    "fg_boards_partial": "FG 板块部分缺失",
    "fund_flow": "主力净额缺失",
    "cninfo_announcements": "公告数据缺失",
    "macro_global": "宏观数据缺失",
    "macro_partial": "宏观部分品种缺失",
    "macro_same_day_missing": "外围期指同日数据缺失",
    "position_input_fg_t1": "涨停/跌停采用 FG T-1 口径",
    "compute_error": "复盘计算异常",
}


def _clean(value) -> str:
    """转字符串并去除 C0/C1 控制字符。"""
    return _CONTROL_RE.sub("", str(value))


def _number(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def advice_level(pct) -> str:
    """建议仓位档位 → 展示等级（D8 锁定映射，区间写法为防御式实现）。"""
    value = _number(pct)
    if value is None:
        return "不建议买入（数据不足）"
    if value >= 0.8:
        return "强烈买入"
    if value >= 0.5:
        return "一般"
    if value > 0:
        return "轻仓博弈"
    return "不建议买入"


def should_buy(pct) -> Optional[bool]:
    """是否建议买入：None 表示数据不足（三态），0 表示明确不建议。"""
    value = _number(pct)
    if value is None:
        return None
    return value > 0


def format_buy_flag(pct) -> str:
    flag = should_buy(pct)
    if flag is None:
        return "无法判断（数据不足）"
    return "是" if flag else "否"


def format_position(pct) -> str:
    value = _number(pct)
    if value is None:
        return "—"
    return f"{round(value * 100)}%"


_SESSION_SUFFIX = {
    "same_day": "（同日收盘）",
    "previous_session": "（前一交易日收盘）",
}


def build_macro_fragments(macro, trade_date: Optional[str] = None) -> list[str]:
    """宏观快照 → 「外围/美债/黄金」中文片段（纯函数）。

    外围只展示 ``MACRO_INDEX_KEYS``（日经/A50 期指）；session 为指数组聚合口径：
    ``same_day`` / ``previous_session`` / ``mixed``（mixed 时逐品种标收盘日）。
    """
    if not isinstance(macro, dict) or not macro.get("available"):
        return []
    instruments = macro.get("instruments")
    if not isinstance(instruments, dict):
        return []
    parts: list[str] = []

    session = macro.get("session")
    indices: list[str] = []
    for key in MACRO_INDEX_KEYS:
        rec = instruments.get(key)
        if not isinstance(rec, dict):
            continue
        pct = _number(rec.get("pct"))
        if pct is None:
            continue
        text = f"{_clean(rec.get('label') or key)} {pct:+.2f}%"
        if session == "mixed":
            day = rec.get("session_date")
            if isinstance(day, str) and len(day) >= 10:
                text += f"（{_clean(day[:10])} 收盘）"
        indices.append(text)
    if indices:
        suffix = "" if session == "mixed" else _SESSION_SUFFIX.get(session or "", "")
        text = "外围：" + "、".join(indices) + suffix
        stale_days: list[int] = []
        for key in MACRO_INDEX_KEYS:
            rec = instruments.get(key)
            if not isinstance(rec, dict) or not rec.get("stale"):
                continue
            session_date = rec.get("session_date")
            try:
                stale_days.append((datetime.fromisoformat(trade_date).date()
                                   - datetime.fromisoformat(str(session_date)[:10]).date()).days)
            except (TypeError, ValueError):
                continue
        if stale_days:
            text += f"（数据较旧，{max(stale_days)} 天前）"
        parts.append(text)

    ust = instruments.get("ust10y")
    if isinstance(ust, dict):
        price = _number(ust.get("price"))
        if price is not None:
            text = f"美债10Y {price:.2f}%"
            bp = _number(ust.get("chg_bp"))
            if bp is not None:
                text += f"（较前值 {bp:+.0f}bp"
                if ust.get("chg_bp_approx"):
                    text += "，近似"
                text += "）"
            parts.append(text)

    gold = instruments.get("gold")
    if isinstance(gold, dict):
        pct = _number(gold.get("pct"))
        if pct is not None:
            parts.append(f"{_clean(gold.get('label') or '黄金')} {pct:+.2f}%")
    return parts


def build_advice_reason(sentiment: dict, main_boards: list,
                        trade_date: Optional[str] = None,
                        macro=None) -> str:
    """由情绪因子 + 主线组装人类可读理由（纯函数）。

    因子为 None 时跳过对应片段；结论句按 ``position_advice`` 档位生成；
    ``degraded`` 信息并入理由末尾，不再单独走 stderr。
    """
    s = sentiment if isinstance(sentiment, dict) else {}
    position_input = s.get("position_input")
    position_input = position_input if isinstance(position_input, dict) else {}

    fragments: list[str] = []

    limit_up = _number(s.get("limit_up_cnt"))
    if limit_up is not None:
        src = "（FG T-1 口径）" if position_input.get("limit_up_cnt") == "fg_t1" else ""
        fragments.append(f"涨停 {int(limit_up)} 家{src}")

    limit_down = _number(s.get("limit_down_cnt"))
    if limit_down is not None:
        src = "（FG T-1 口径）" if position_input.get("limit_down_cnt") == "fg_t1" else ""
        fragments.append(f"跌停 {int(limit_down)} 家{src}")

    broken_rate = _number(s.get("broken_rate"))
    if broken_rate is not None:
        fragments.append(f"炸板率 {broken_rate * 100:.1f}%")

    premium = _number(s.get("yesterday_premium_pct"))
    if premium is not None:
        fragments.append(f"昨日涨停溢价 {premium:+.2f}%")

    promotion = _number(s.get("promotion_rate"))
    if promotion is not None:
        fragments.append(f"晋级率 {promotion * 100:.1f}%")

    height = _number(s.get("max_board_height"))
    if height is not None:
        fragments.append(f"最高 {int(height)} 板")

    names = [
        _clean(board.get("board_name"))
        for board in (main_boards if isinstance(main_boards, list) else [])
        if isinstance(board, dict) and board.get("board_name")
    ]
    names = [name for name in names if name][:3]
    if names:
        fragments.append("主线：" + "、".join(names))

    factor_text = "；".join(fragments) if fragments else "暂无可用情绪因子"
    prefix_parts = []
    if trade_date:
        prefix_parts.append(f"复盘日 {_clean(trade_date)}")
    prefix_parts.extend(build_macro_fragments(macro, trade_date))
    prefix_parts.append(factor_text)

    conclusion = _conclusion(s)

    degraded = s.get("degraded")
    degraded = degraded if isinstance(degraded, list) else []
    labels = [
        DEGRADED_LABELS.get(label, label)
        for label in (_clean(item) for item in degraded)
        if label
    ]
    if labels:
        conclusion += f"数据降级：{'、'.join(labels)}，结论可信度下降。"

    return f"{'；'.join(prefix_parts)}。{conclusion}"


def _conclusion(sentiment: dict) -> str:
    advice = sentiment.get("position_advice")
    pct = _number(sentiment.get("position_pct"))
    if advice == "重仓":
        return "涨停 ≥80 家且跌停 ≤5 家，情绪强势，建议重仓。"
    if advice == "空仓":
        return "跌停 ≥30 家，风险极高，建议空仓。"
    if advice == "轻仓":
        return "跌停 ≥10 家或涨停 ≤15 家，情绪偏弱，建议轻仓。"
    if advice == "正常":
        if pct is None:
            return "涨跌停家数处中性区间。"
        return f"涨跌停家数处中性区间，建议仓位 {round(pct * 100)}%。"
    if pct is None:
        return "情绪数据不足，无法给出买入建议。"
    return "情绪口径异常，暂不给出买入建议。"


def format_advice_lines(result: dict) -> list[str]:
    """把复盘结果格式化为 CLI 默认的 4 行中文摘要。"""
    payload = result if isinstance(result, dict) else {}
    sentiment = payload.get("sentiment")
    sentiment = sentiment if isinstance(sentiment, dict) else {}
    pct = sentiment.get("position_pct")
    boards = payload.get("main_boards")
    boards = boards if isinstance(boards, list) else []
    reason = build_advice_reason(sentiment, boards, payload.get("trade_date"),
                                 payload.get("macro"))
    return [
        f"是否建议买入：{format_buy_flag(pct)}",
        f"建议仓位：{format_position(pct)}",
        f"建议等级：{advice_level(pct)}",
        f"买入理由：{reason}",
    ]


def _score_key(candidate: dict) -> float:
    score = _number(candidate.get("composite_score"))
    return -(score if score is not None else 0.0)


def _rank_key(candidate: dict) -> int:
    rank = candidate.get("rank")
    return rank if isinstance(rank, int) else 999999


def format_candidate_lines(result: dict) -> list[str]:
    """逐只标的清单：✅ 买入在前（strong→light），忽略在后一句原因，未评级最后。"""
    payload = result if isinstance(result, dict) else {}
    raw = payload.get("candidates")
    candidates = [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []
    if not candidates:
        return ["无参与复盘标的"]

    buys, ignores, unrated = [], [], []
    for cand in candidates:
        verdict = cand.get("verdict")
        if verdict in (VERDICT_STRONG, VERDICT_LIGHT):
            buys.append(cand)
        elif verdict == VERDICT_IGNORE:
            ignores.append(cand)
        else:
            unrated.append(cand)

    buys.sort(key=lambda c: (0 if c.get("verdict") == VERDICT_STRONG else 1,
                             _score_key(c), _rank_key(c),
                             _clean(c.get("code") or "")))
    ignores.sort(key=lambda c: (_score_key(c), _clean(c.get("code") or "")))
    unrated.sort(key=lambda c: (_score_key(c), _rank_key(c),
                                _clean(c.get("code") or "")))

    lines: list[str] = []
    for cand in buys:
        name = _clean(cand.get("name") or cand.get("code") or "—")
        label = cand.get("verdict_label") or VERDICT_LABELS.get(cand.get("verdict"), "")
        lines.append(f"✅ {name}-{label}")
        dims = cand.get("dimensions")
        if isinstance(dims, list):
            for dim in dims:
                lines.append(f"- {_clean(dim)}")
        lines.append("")

    if ignores:
        if lines and lines[-1] != "":
            lines.append("")
        for cand in ignores:
            name = _clean(cand.get("name") or cand.get("code") or "—")
            reason = _clean(cand.get("ignore_reason") or "未达参与阈值")
            lines.append(f"{name}-忽略：{reason}")

    if unrated:
        if lines and lines[-1] != "":
            lines.append("")
        for cand in unrated:
            name = _clean(cand.get("name") or cand.get("code") or "—")
            reason = _clean(cand.get("ignore_reason") or "评级字段缺失")
            lines.append(f"{name}-未评级：{reason}")

    while lines and lines[-1] == "":
        lines.pop()
    return lines or ["无参与复盘标的"]
