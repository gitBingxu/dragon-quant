#!/usr/bin/env python3
"""
抗跌性分析模块 (Anti-Drop Analysis)

大盘跳水时，它是跟着崩还是扛住了？
三个子维度：相对回撤强度、日内承接强度、企稳反弹弹性
"""


def calc_anti_drop_score(
    stock_kline: list[dict],
    market_kline: list[dict],
) -> dict:
    """
    计算抗跌性综合得分。

    参数:
        stock_kline: 个股近 15 个交易日日K线
        market_kline: 大盘指数近 15 个交易日日K线

    返回:
        {"score": 75, "details": {"drop_days": [...], "daily_scores": [...]}, "breakdown": {...}}
    """
    # Step 1: 识别大盘跳水日（跌幅 < -0.7%）
    drop_days = []
    for d in market_kline:
        if d["pct"] < -0.7:
            # 找到对应的个股K线
            stock_day = next((s for s in stock_kline if s["date"] == d["date"]), None)
            if stock_day:
                drop_days.append({
                    "date": d["date"],
                    "market_pct": d["pct"],
                    "stock_kline": stock_day,
                })

    if not drop_days:
        return {
            "score": 50,
            "details": "近期无大盘跳水日，抗跌性待验证",
            "drop_days": [],
            "breakdown": {"note": "无跳水日，中性评分 50"},
        }

    # Step 2: 对每个跳水日打分
    daily_scores = []
    for i, dd in enumerate(drop_days):
        # 反弹：取次日（或后日）数据 — 用日期查找替代列表引用
        cur_idx = next((j for j, k in enumerate(stock_kline) if k["date"] == dd["date"]), -1)
        next_idx = cur_idx + 1
        has_t1 = (cur_idx >= 0 and next_idx < len(stock_kline)
                  and next_idx < len(market_kline))

        # 前一日收盘价
        if cur_idx > 0:
            prev_close = stock_kline[cur_idx - 1]["close"]
        else:
            prev_close = dd["stock_kline"]["open"]

        daily = _score_drop_day(
            stock_day=dd["stock_kline"],
            market_pct=dd["market_pct"],
            stock_t1=stock_kline[next_idx] if has_t1 else None,
            market_t1=market_kline[next_idx] if has_t1 else None,
            prev_close=prev_close,
        )
        daily["date"] = dd["date"]
        daily["market_pct"] = round(dd["market_pct"], 2)
        daily_scores.append(daily)

    # Step 3: 聚合
    avg_score = sum(d["total"] for d in daily_scores) / len(daily_scores)

    # 连跌奖励
    cons_info = _count_consecutive_drops(drop_days)
    consecutive_drops = cons_info["length"]
    conse_start = cons_info["start"]
    if consecutive_drops >= 2:
        first_date = drop_days[conse_start]["date"]
        first_idx = next((j for j, k in enumerate(stock_kline) if k["date"] == first_date), -1)
        if first_idx > 0:
            period_stock = (stock_kline[first_idx + consecutive_drops - 1]["close"]
                            / stock_kline[first_idx - 1]["close"] - 1) * 100
            period_market = sum(d["market_pct"] for d in drop_days[conse_start:conse_start + consecutive_drops])
            if period_stock > period_market * 0.5:
                avg_score = min(avg_score + 10, 100)

    return {
        "score": round(avg_score, 1),
        "drop_days_count": len(drop_days),
        "daily_scores": daily_scores,
        "breakdown": {
            d["date"]: {
                "rel_score": d["rel_score"],
                "support_score": d["support_score"],
                "rebound_score": d["rebound_score"],
                "total": d["total"],
            }
            for d in daily_scores
        },
    }


def _score_drop_day(
    stock_day: dict,
    market_pct: float,
    stock_t1: "dict | None",
    market_t1: "dict | None",
    prev_close: float,
) -> dict:
    """单个跳水日的三维打分。"""

    # A. 相对回撤强度 (0.40)
    stock_return = stock_day["pct"]
    excess_return = stock_return - market_pct

    if stock_return > 0:
        rel_score = 100
    elif excess_return > 0:
        rel_score = 60 + excess_return / abs(market_pct) * 40
    elif stock_return > -2:
        rel_score = 30
    else:
        rel_score = 0

    # B. 日内承接强度 (0.30)
    try:
        o, h, l, c = stock_day["open"], stock_day["high"], stock_day["low"], stock_day["close"]
    except KeyError:
        return {"rel_score": 0, "support_score": 50, "rebound_score": 0, "total": 50}
    if h > l:
        lower_shadow_pct = (min(o, c) - l) / (h - l)
    else:
        lower_shadow_pct = 0

    if c > o:
        close_pos = (c - o) / (h - l + 0.001)
    else:
        close_pos = -(o - c) / (h - l + 0.001)

    max_drop = (l - prev_close) / prev_close if prev_close else 0
    penalty = max(0, min(1, abs(max_drop) / 0.05))

    support_score = (lower_shadow_pct * 0.6 + close_pos * 0.4) * 100
    support_score = support_score * (1 - penalty * 0.3)
    support_score = max(0, min(100, support_score))

    # C. 企稳反弹弹性 (0.30)
    if stock_t1 and market_t1:
        t1_stock = (stock_t1["close"] / stock_day["close"] - 1) * 100

        t1_market_pct = market_t1["pct"]  # t1 日涨跌幅
        t1_stock_pct = stock_t1["pct"]
        alpha = t1_stock_pct - t1_market_pct

        if t1_stock_pct > 0 and alpha > 0:
            rebound_score = min(alpha / 3 * 100, 100)
        elif t1_stock_pct > 0 >= t1_market_pct:
            rebound_score = 100
        elif t1_stock_pct < 0 and t1_market_pct < 0:
            rebound_score = max(0, (1 - abs(alpha) / 3) * 100)
        else:
            rebound_score = 0
    else:
        rebound_score = 50  # 无次日数据

    total = rel_score * 0.40 + support_score * 0.30 + rebound_score * 0.30

    return {
        "rel_score": round(rel_score, 1),
        "support_score": round(support_score, 1),
        "rebound_score": round(rebound_score, 1),
        "total": round(total, 1),
    }


def _count_consecutive_drops(drop_days: list[dict]) -> dict:
    """统计最长连续跳水天数，返回 {"length": N, "start": index_in_drop_days}."""
    if not drop_days:
        return {"length": 0, "start": 0}
    max_len = 1
    max_start = 0
    current_len = 1
    current_start = 0
    for i in range(1, len(drop_days)):
        d1 = drop_days[i - 1]["date"]
        d2 = drop_days[i]["date"]
        try:
            from datetime import datetime
            dt1 = datetime.strptime(d1, "%Y-%m-%d")
            dt2 = datetime.strptime(d2, "%Y-%m-%d")
            diff = (dt2 - dt1).days
            if diff <= 2:
                current_len += 1
            else:
                current_len = 1
                current_start = i
        except ValueError:
            current_len = 1
            current_start = i
        if current_len > max_len:
            max_len = current_len
            max_start = current_start
    return {"length": max_len, "start": max_start}
