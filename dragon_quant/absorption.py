#!/usr/bin/env python3
"""
资金承接性分析模块 (Capital Absorption Analysis)

别的板块跳水时，目标板块反而被资金持续拉升到收盘——跨板块资金虹吸。
"""


def calc_absorption_score(
    target_sector_code: str,
    all_sectors_5min: dict[str, list[dict]],
) -> dict:
    """
    计算资金承接性综合得分。

    参数:
        target_sector_code: 目标板块编码（如 "BK0429"）
        all_sectors_5min: {"BK0429": [{5minK线}, ...], "BK0431": [...], ...}

    返回:
        {"score": 75, "events": [...], "breakdown": {...}}
    """
    events = _detect_capital_flight_events(all_sectors_5min, target_sector_code)

    if not events:
        return {
            "score": 50,
            "events": [],
            "breakdown": {"note": "近期无明显的跨板块资金虹吸事件，中性评分 50"},
        }

    event_scores = []
    for e in events:
        s = _score_single_event(e)
        event_scores.append(s)

    best = max(event_scores)
    multi_bonus = min((len(events) - 1) * 5, 15)
    score = min(best + multi_bonus, 100)

    return {
        "score": round(score, 1),
        "event_count": len(events),
        "best_event": events[event_scores.index(best)] if events else None,
        "all_event_scores": [round(s, 1) for s in event_scores],
        "breakdown": {
            "event_count": len(events),
            "best_event_score": round(best, 1),
            "multi_event_bonus": multi_bonus,
        },
    }


def _detect_capital_flight_events(
    all_sectors: dict[str, list[dict]],
    target_code: str,
) -> list[dict]:
    """扫描全天 5 分钟 K 线，找到资金逃逸→虹吸事件窗口。"""
    if target_code not in all_sectors:
        return []

    target_bars = all_sectors[target_code]
    n_bars = len(target_bars)
    events = []

    for t in range(n_bars - 6):
        window = range(t, t + 6)

        # ① 至少 2 个其他板块窗口内跌超 1%
        dropping = []
        for sc, bars in all_sectors.items():
            if sc == target_code:
                continue
            if len(bars) <= t + 5:
                continue
            try:
                wr = (bars[t + 5]["close"] / bars[t]["open"] - 1) * 100
            except (KeyError, ZeroDivisionError):
                continue
            if wr < -1.0:
                dropping.append((sc, wr))

        if len(dropping) < 2:
            continue

        # ② 目标板块窗口内涨 > 0.3%
        try:
            twr = (target_bars[t + 5]["close"] / target_bars[t]["open"] - 1) * 100
        except (KeyError, ZeroDivisionError):
            continue
        if twr < 0.3:
            continue

        # ③ 方向一致性：至少 4/6 根阳线
        up_bars = 0
        for i in range(6):
            try:
                if target_bars[t + i]["close"] > target_bars[t + i]["open"]:
                    up_bars += 1
            except KeyError:
                pass
        if up_bars < 4:
            continue

        # ④ 到尾盘不跳水：回撤 < 30% 窗口涨幅
        try:
            peak = max(b["close"] for b in target_bars[t:t + 6])
            final = target_bars[-1]["close"]
            window_low = target_bars[t]["open"]
        except (KeyError, IndexError):
            continue

        if peak <= window_low:
            continue

        retrace = (peak - final) / (peak - window_low)
        if retrace > 0.3:
            continue

        events.append({
            "time": t * 5,                     # 触发时间（分钟偏移）
            "dropping_count": len(dropping),
            "dropping_avg": sum(d[1] for d in dropping) / len(dropping),
            "dropping_sectors": [d[0] for d in dropping[:5]],
            "target_rise": round(twr, 2),
            "retrace_pct": round(retrace, 4),
            "up_bars": up_bars,
        })

    return events


def _score_single_event(e: dict) -> float:
    """对单个虹吸事件打分。"""

    # A. 虹吸强度 (0.40)
    flight_intensity = abs(e["dropping_avg"]) * e["dropping_count"]
    intensity_score = min(e["target_rise"] / (flight_intensity + 0.001) * 100, 100)

    # B. 广度 (0.20)
    breadth_score = min(e["dropping_count"] / 10, 1.0) * 100

    # C. 持续性 (0.40)
    sustain_score = (1 - min(e["retrace_pct"], 1.0)) * 100

    return intensity_score * 0.40 + breadth_score * 0.20 + sustain_score * 0.40
