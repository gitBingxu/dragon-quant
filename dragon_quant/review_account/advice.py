"""复盘结论的中文展示层（纯函数，无 I/O）。

CLI 默认输出的 4 行摘要由此组装；快照本身不持久化展示文本，保证
「快照 = 资产的纯函数」与文案演化解耦。设计见
``docs/solutions/2026-10-20-prep-3day-window-design.md``。
"""

from __future__ import annotations

import re
from typing import Optional

# C0/C1 控制字符：外部板块名等文本进入人读终端前统一清洗
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# 降级 code → 中文标签（未知 code 原样展示）
DEGRADED_LABELS = {
    "dragons": "龙头数据缺失",
    "dragons_partial": "近三日龙头窗口缺日",
    "dragons_window_short": "交易日历不足三日",
    "ths_limit_up_pool": "THS 涨停数据缺失",
    "ths_block_top": "题材主线数据缺失",
    "market_breadth": "市场宽度缺失",
    "fg_boards": "FG 板块数据缺失",
    "fg_boards_partial": "FG 板块部分缺失",
    "fund_flow": "主力净额缺失",
    "cninfo_announcements": "公告数据缺失",
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


def build_advice_reason(sentiment: dict, main_boards: list,
                        trade_date: Optional[str] = None) -> str:
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
    reason = build_advice_reason(sentiment, boards, payload.get("trade_date"))
    return [
        f"是否建议买入：{format_buy_flag(pct)}",
        f"建议仓位：{format_position(pct)}",
        f"建议等级：{advice_level(pct)}",
        f"买入理由：{reason}",
    ]
