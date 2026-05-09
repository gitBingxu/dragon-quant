#!/usr/bin/env python3
"""
领涨性分析模块 (Leadership Analysis)

平时（非涨停日）这只票在同行业里排第几？
"""


def calc_leading_score(
    stock_kline: list[dict],
    industry_components: list[dict],
    limit_up_dates: list[str],
    code: str = "",
) -> dict:
    """
    计算领涨性综合得分。

    参数:
        stock_kline: 个股近 15 个交易日日K线
        industry_components: 某日行业成分股行情（取最近一个交易日）
        limit_up_dates: 涨停日期列表
        code: 股票代码，用于在成分股列表中定位真实排名

    返回:
        {"score": 72, "breakdown": {"avg_pct_rank": 0.28, "deviation_bonus": 5}}
    """
    if not industry_components:
        return {"score": 50, "breakdown": {"error": "无行业数据"}, "fallback": True}

    # Step 1: 单日真实排名（基于当日成分股行情）
    sorted_comp = sorted(industry_components, key=lambda x: x.get("pct", 0), reverse=True)
    total = len(sorted_comp)

    # 找到个股在行业中的真实排名
    rank = None
    if code:
        for i, s in enumerate(sorted_comp):
            if s["code"] == code:
                rank = i + 1
                break

    if rank is None:
        rank = total // 2

    pct_rank = rank / total

    # Step 2: 计算行业成分股的涨幅分布，用于更准确的估算
    all_pcts = [s.get("pct", 0) for s in industry_components]
    median_pct = _median(all_pcts)
    pct_std = _std(all_pcts) if len(all_pcts) > 1 else 1.0

    # Step 3: 近 5 个非涨停日的估算排名
    non_limit_dates = []
    for k in stock_kline:
        if k["date"] not in limit_up_dates:
            non_limit_dates.append(k)

    recent = non_limit_dates[-5:] if len(non_limit_dates) >= 5 else non_limit_dates

    pct_ranks = []
    for k in recent:
        # 使用正态分布估算其分位
        deviation = k["pct"] - median_pct
        if abs(pct_std) < 0.01:
            est_rank = 0.5
        else:
            # 标准化后估算分位
            z_score = deviation / pct_std
            # 简单的正态分布CDF近似
            if z_score < -3:
                est_rank = 0.99
            elif z_score < -1:
                est_rank = 0.84 + (z_score + 1) * 0.16
            elif z_score < 0:
                est_rank = 0.5 + z_score * 0.34
            elif z_score < 1:
                est_rank = 0.5 - z_score * 0.34
            elif z_score < 3:
                est_rank = 0.16 - (z_score - 1) * 0.16
            else:
                est_rank = 0.01
        
        est_rank = max(0.01, min(0.99, est_rank))
        pct_ranks.append(est_rank)

    # 结合当日真实排名和历史估算排名
    if pct_ranks:
        # 当日真实排名权重更高
        avg_rank = (pct_rank * 0.6 + sum(pct_ranks) / len(pct_ranks) * 0.4)
    else:
        avg_rank = pct_rank

    leading = (1 - avg_rank) * 100

    # Step 4: 偏离度加分（基于标准差）
    last_k = stock_kline[-1] if stock_kline else {"pct": median_pct}
    deviation = last_k["pct"] - median_pct
    if pct_std > 0:
        deviation_bonus = max(0, min(deviation / pct_std * 10, 20))
    else:
        deviation_bonus = max(0, min(deviation * 5, 20))

    return {
        "score": round(min(leading + deviation_bonus, 100), 1),
        "breakdown": {
            "avg_pct_rank": round(avg_rank, 3),
            "today_rank": pct_rank,
            "base_score": round(leading, 1),
            "deviation_bonus": round(deviation_bonus, 1),
            "industry_size": total,
            "industry_median_pct": round(median_pct, 2),
            "industry_std": round(pct_std, 2),
        },
    }


def _median(values: list[float]) -> float:
    if not values:
        return 0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _std(values: list[float]) -> float:
    """计算标准差"""
    if len(values) <= 1:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return variance ** 0.5
