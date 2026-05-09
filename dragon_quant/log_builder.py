#!/usr/bin/env python3
"""
四维日志构建模块 — 生成叙事文本

所有函数均为纯数据→文本转换，不做 API 调用。
每个函数返回一个叙事字符串，供 caller 嵌入排版。
"""

from typing import Optional


# ─── 带动性叙事 ──────────────────────────────────────────

def build_drive_logs(code: str, name: str, drive_result: dict,
                     stock_5min: list[dict],
                     companions: list[dict],
                     sector_5min: list[dict],
                     industry_name: str,
                     date: str) -> str:
    """生成带动性叙事文本。"""
    best = drive_result.get("best_day", {}) or {}
    bk = best.get("breakdown", {}) or {}
    score = drive_result.get("score", 0)
    board_time = best.get("board_time", "")
    voice = bk.get("voice_score", 0)
    follow = bk.get("follow_score", 0)
    board_lead = bk.get("board_leadership_score", 0)

    scores_part = f"板块共鸣{voice:.0f}/跟风{follow:.0f}/决策力{board_lead:.0f}"

    if not stock_5min or not sector_5min:
        if score >= 85:
            return f"{scores_part}，板块共振强劲"
        elif score >= 70:
            return f"{scores_part}，有带动效应"
        return scores_part

    # 板块开盘
    sec_open = _sector_opening(sector_5min)

    # 封板方式
    bt = _time_to_index(board_time)
    if bt is None and board_time in ("", "-", "9999", None):
        board_desc = f"{name}强势一字封板"
    elif bt is not None:
        hh = board_time[:2]
        mm = board_time[2:4]
        board_desc = f"{hh}:{mm}{name}封板"
    else:
        board_desc = f"{name}封板"

    # 板块封板后走势
    trend = _sector_trend(sector_5min, board_time)

    # 收盘状态
    close_s = _close_state(sector_5min)

    d = date[-5:] if len(date) >= 5 else date
    industry_name = industry_name or "该"

    return f"{scores_part}，{industry_name}板块{d}{sec_open}，{board_desc}{trend}{close_s}"


def _sector_opening(kline: list[dict]) -> str:
    """板块开盘方向：低开/高开/平开"""
    if len(kline) < 1:
        return ""
    first = kline[0]
    o, c = first.get("open", 0), first.get("close", 0)
    if o <= 0:
        return ""
    chg = (c - o) / o * 100
    if chg < -0.3:
        return "低开"
    elif chg > 0.3:
        return "高开"
    return "平开"


def _sector_trend(kline: list[dict], board_time: str) -> str:
    """板块封板后走势"""
    tb = _time_to_index(board_time)
    if tb is None or tb >= len(kline):
        return "带动板块"
    post = kline[tb:]
    if len(post) < 2:
        return "带动板块"
    start_p = post[0].get("open", 0)
    if start_p <= 0:
        return "带动板块"
    peak = max(b.get("high", 0) for b in post)
    final = post[-1].get("close", 0)
    peak_chg = (peak - start_p) / start_p * 100
    final_chg = (final - start_p) / start_p * 100
    if peak_chg >= 1.5:
        return "带动板块持续走强"
    elif peak_chg >= 0.3:
        return "带动板块小幅走高"
    return "带动板块"


def _close_state(kline: list[dict]) -> str:
    """收盘状态：未跳水/尾盘回落"""
    if len(kline) < 6:
        return "，持续到收盘"
    recent = kline[-6:]
    start_p = recent[0].get("open", 0)
    final = recent[-1].get("close", 0)
    if start_p <= 0:
        return "，持续到收盘"
    retrace = (final - start_p) / start_p * 100
    if retrace < -0.3:
        return "，尾盘有所回落"
    return "，持续到收盘板块未跳水"


# ─── 抗跌性叙事 ──────────────────────────────────────────

def build_anti_drop_logs(anti_drop_result: dict) -> str:
    """生成抗跌性叙事文本。"""
    daily = anti_drop_result.get("daily_scores", [])
    dc = anti_drop_result.get("drop_days_count", 0)
    ads = anti_drop_result.get("score", 50)

    # 整体评价
    if ads >= 70:
        overall = f"近{dc}次跳水表现坚挺"
    elif ads >= 40:
        overall = f"近{dc}次跳水抗跌一般"
    elif dc > 0:
        overall = f"近{dc}次跳水偏弱，警惕系统性风险"
    else:
        return "近期无跳水日，抗跌性待验证"

    # 最近一个跳水日详情
    if not daily:
        return overall

    last = daily[-1]
    d = last.get("date", "")[-5:]
    mpct = last.get("market_pct", 0)
    rel = last.get("rel_score", 0)

    if mpct < -2:
        market_desc = "大盘跳水"
    elif mpct < -0.7:
        market_desc = "大盘偏弱"
    else:
        market_desc = f"大盘{mpct:+.1f}%"

    if rel >= 80:
        stock_desc = "逆势抗跌"
    elif rel >= 50:
        stock_desc = "小幅跟跌"
    else:
        stock_desc = "并未强势上涨或横盘，而是比大盘跌的更多"

    return f"{overall}，{d}{market_desc}，{stock_desc}"


# ─── 领涨性叙事 ──────────────────────────────────────────

def build_leadership_logs(leading_result: dict) -> str:
    """生成领涨性叙事文本。"""
    bk = leading_result.get("breakdown", {}) or {}
    rank_pct = bk.get("avg_pct_rank", 0.5)
    median = bk.get("industry_median_pct", 0)
    size = bk.get("industry_size", 0)

    if size > 0:
        rank_pos = int(rank_pct * size)
        return f"行业排名前{rank_pct*100:.0f}%({rank_pos}/{size})，跑赢中位数{median:+.1f}%"
    return f"行业排名前{rank_pct*100:.0f}%，跑赢中位数{median:+.1f}%"


# ─── 资金承接性叙事 ────────────────────────────────────────

def build_absorption_logs(absorption_result: dict,
                          code_to_name: dict[str, str],
                          stock_5min: list[dict],
                          sector_5min: list[dict],
                          date: str,
                          board_time: str,
                          name: str,
                          industry_name: str) -> str:
    """生成资金承接性叙事文本。"""
    events = absorption_result.get("events") or []
    best = absorption_result.get("best_event")

    if not events:
        return "暂无显著跨板块虹吸信号"

    if not best or not stock_5min:
        return f"发现{len(events)}次跨板块虹吸事件"

    # 股票开盘情况
    opening = _stock_opening(stock_5min)
    early = _stock_early(stock_5min)

    # 事件详情
    t = best.get("time", 0)
    h = 9 + (t + 30) // 60
    m = (t + 30) % 60
    time_str = f"{h:02d}:{m:02d}"

    dropping = best.get("dropping_sectors", [])
    dropping_names = [code_to_name.get(s, s) for s in dropping[:2]]
    names_str = "、".join(dropping_names) if dropping_names else "其他板块"

    d = date[-5:] if len(date) >= 5 else date
    ind = industry_name or "该"

    return (f"{d}{time_str}{name}{opening}{early}，{time_str}左右"
            f"{names_str}板块突然跳水，资金出逃，"
            f"此时{name}强势封板，带领{ind}板块迅速翻红并持续走强")


def _stock_opening(kline: list[dict]) -> str:
    """股票开盘：高开/低开"""
    if not kline:
        return ""
    f = kline[0]
    o, c = f.get("open", 0), f.get("close", 0)
    if o <= 0:
        return ""
    chg = (c - o) / o * 100
    if chg < -0.3:
        return "低开"
    elif chg > 0.3:
        return "高开"
    return "平开"


def _stock_early(kline: list[dict]) -> str:
    """股票早盘走势"""
    if len(kline) < 2:
        return ""
    window = kline[:min(4, len(kline))]
    first = kline[0].get("close", 0)
    low = min(b.get("low", 0) for b in window)
    if first <= 0 or low <= 0:
        return ""
    chg = (low - first) / first * 100
    if chg < -1.0:
        return "迅速下杀"
    elif chg < -0.3:
        return "小幅下探"
    return ""


# ─── 工具函数 ────────────────────────────────────────────

def _fmt_time(t: str) -> str:
    """0935 → 09:35"""
    if len(t) >= 4:
        return f"{t[:2]}:{t[2:4]}"
    return t


def _time_to_index(board_time: str) -> Optional[int]:
    """封板时间 HHMM → 5分钟K线下标。0935=1, 0940=2, ..."""
    if not board_time or board_time in ("", "-", "9999"):
        return None
    try:
        h = int(board_time[:2])
        m = int(board_time[2:4])
        minutes = h * 60 + m - 570  # 570 = 9*60+30
        if minutes < 0:
            return 0
        idx = minutes // 5
        return max(0, idx)
    except (ValueError, IndexError):
        return None
