#!/usr/bin/env python3
"""
带动性分析模块 (Driving Force Analysis)

封板后小弟跟不跟？跟多紧？是带头大哥还是碰巧封了？
"""

from typing import Optional
from dragon_quant.eastmoney_api import parse_board_minutes


def calc_drive_score(
    code: str,
    limit_up_dates: list[dict],
    co_limitup_map: dict[str, list[dict]],
    industry_components: list[dict],
) -> dict:
    """
    计算带动性综合得分。

    参数:
        code: 候选股票代码
        limit_up_dates: [{"date":"...","board_time":"0932","turnover":5.2}, ...]
            近 3 个涨停日的数据
        co_limitup_map: {"2026-04-28": [{同行业其他涨停股}, ...], ...}
        industry_components: 同行业全部成分股当日行情

    返回:
        {"score": 85, "details": {"voice": 90, "follow": 80, "board_leadership": 85, ...}}
    """
    if not limit_up_dates:
        return {"score": 30, "details": {"error": "无涨停日数据"}, "breakdown": {}}

    daily_scores = []
    for ld in limit_up_dates:
        date = ld["date"]
        co_list = co_limitup_map.get(date, [])
        daily = _calc_daily_drive(code, ld, co_list, industry_components)
        daily_scores.append(daily)

    # 取最佳日
    best = max(daily_scores, key=lambda x: x["score"])
    drive_score = best["score"]

    # 连板加分
    max_cons = max(ld.get("consecutive", 1) for ld in limit_up_dates)
    if max_cons >= 2:
        drive_score = min(drive_score + max_cons * 5, 100)

    return {
        "score": round(drive_score, 1),
        "best_day": best,
        "all_days": daily_scores,
        "breakdown": best["breakdown"],
    }


def _calc_daily_drive(
    code: str,
    limit_up: dict,
    co_limitup: list[dict],
    industry_components: list[dict],
) -> dict:
    """计算单个涨停日的带动性得分。"""

    # A. 板块共鸣度 (0.30)
    voice_score = _calc_voice_score(len(co_limitup), len(industry_components))

    # B. 板块跟风力度 (0.30)
    follow_score = _calc_follow_score(industry_components)

    # C. 封板决策力 (0.40)
    board_score = _calc_board_leadership(limit_up, co_limitup)

    daily = voice_score * 0.30 + follow_score * 0.30 + board_score * 0.40

    return {
        "score": round(daily, 1),
        "date": limit_up.get("date", ""),
        "board_time": limit_up.get("board_time", "--"),
        "breakdown": {
            "voice_score": round(voice_score, 1),
            "follow_score": round(follow_score, 1),
            "board_leadership_score": round(board_score, 1),
        },
    }


def _calc_voice_score(co_count: int, industry_size: int) -> float:
    """板块共鸣度：同行业涨停家数占比。>10% 满分。"""
    if industry_size == 0:
        return 50
    pct = co_count / industry_size
    return min(pct / 0.10, 1.0) * 100


def _calc_follow_score(components: list[dict]) -> float:
    """板块跟风力度：涨幅 >3% 的非涨停股占比。>15% 满分。"""
    if not components:
        return 50
    strong = sum(1 for s in components if 3 <= s.get("pct", 0) < 9.9)
    pct = strong / len(components)
    return min(pct / 0.15, 1.0) * 100


def _calc_board_leadership(
    limit_up: dict,
    co_limitup: list[dict],
) -> float:
    """
    封板决策力（v2 增强版）
    四因子：排名 + 绝对时间 + 小弟紧密度 + 一字板惩罚
    """
    board_time = limit_up.get("board_time")
    turnover = limit_up.get("turnover", 0)
    all_boards = [limit_up] + co_limitup

    # 排序
    sorted_boards = sorted(all_boards, key=lambda x: x.get("board_time") or "9999")
    rank = next((i + 1 for i, b in enumerate(sorted_boards) if b is limit_up), len(sorted_boards))
    total = len(sorted_boards)

    # ── C1 排名分 (0.25) ──
    rank_score = max(0, (1 - (rank - 1) / total) * 100)

    # ── C2 绝对时间分 (0.25) ──
    mins = parse_board_minutes(board_time)

    # 市场开盘 9:30 = 570 minutes
    MARKET_OPEN = 9 * 60 + 30  # 570
    if mins is None:
        early_bonus = 100
    elif mins <= MARKET_OPEN + 30:
        early_bonus = 100
    elif mins <= 10 * 60 + 30:
        early_bonus = 70
    elif mins <= 11 * 60 + 30:
        early_bonus = 40
    else:
        early_bonus = 10

    # ── C3 小弟紧密度 (0.50) ──
    if board_time is None:
        # 一字板——小弟时间对比无意义
        gap_score = 50 if not co_limitup else 70
    elif not co_limitup:
        gap_score = 50  # 独苗
    else:
        time_gaps = []
        for other in co_limitup:
            ot = other.get("board_time")
            if ot and ot > board_time:
                o_mins = parse_board_minutes(ot)
                s_mins = parse_board_minutes(board_time)
                if o_mins is not None and s_mins is not None:
                    time_gaps.append(o_mins - s_mins)
        if not time_gaps:
            gap_score = 50
        else:
            avg_gap = sum(time_gaps) / len(time_gaps)
            close_ratio = sum(1 for g in time_gaps if g <= 5) / len(time_gaps)
            if avg_gap <= 5 and close_ratio > 0.5:
                gap_score = 100
            else:
                gap_score = max(0, 100 - avg_gap / 30 * 100)

    # ── C4 一字板惩罚（乘数）──
    if board_time is None or (mins is not None and mins < MARKET_OPEN + 5):
        word_penalty = 0.85 if turnover < 1.0 else 1.0
    else:
        word_penalty = 1.0

    return (rank_score * 0.25 + early_bonus * 0.25 + gap_score * 0.50) * word_penalty
