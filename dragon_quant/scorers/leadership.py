"""
领涨性 scorer（权重 25%）

核心问题：不看涨停日，平时这票在同行业里是不是领跑的？
新增：个股 5 分 K vs 板块 5 分 K 的 lead-lag 检测（先于板块拉伸）
"""

import statistics
from dragon_quant.models.types import ScoreResult, KBar, StockInfo
from dragon_quant.cache.data_cache import DataCache


def score(code: str, cache: DataCache, primary_sector: str = "") -> ScoreResult:
    """
    Args:
        code: 股票代码
        cache: 共享数据缓存
        primary_sector: 候选股主板块代码
    Returns:
        ScoreResult(dim="leadership", score, weight=0.25, details)
    """
    # 加载数据
    components: list[StockInfo] = cache.get(f"sector:components:{primary_sector}") or []
    all_quotes = cache.get("quotes:batch") or []
    stock_klines: list[KBar] = cache.get(f"kline:day:{code}") or []
    stock_1min: list[KBar] = cache.get(f"kline:1min:{code}") or []
    sector_5min: list[KBar] = cache.get(f"kline:5min:sector:{primary_sector}") or []

    if not components:
        return ScoreResult(
            dim="leadership", score=50.0, weight=0.25,
            details={"fallback": True, "reason": "无板块成分股数据"}
        )

    # 构建 quote_map（二次查表用）
    quote_map = {q.code: q for q in all_quotes} if all_quotes else {}

    # ─── Part 1: 当日真实排名 ───
    pct_rank, intraday_score, all_pcts = _intraday_ranking(code, components, quote_map)

    if not all_pcts:
        return ScoreResult(
            dim="leadership", score=50.0, weight=0.25,
            details={"fallback": True, "reason": "无法获取成分股行情"}
        )

    # ─── Part 2: 行业统计 ───
    median_pct = statistics.median(all_pcts)
    try:
        pct_std = statistics.stdev(all_pcts)
    except statistics.StatisticsError:
        pct_std = 0.01
    pct_std = max(pct_std, 0.01)

    # ─── Part 3: 历史 5 日非涨停日估算 ───
    # 找近 5 个非涨停日（pct < 9.9%）
    non_limit_days = []
    for bar in stock_klines:
        if len(non_limit_days) >= 5:
            break
        if bar.pct < 9.9:
            non_limit_days.append(bar)

    estimated_ranks = []
    for bar in non_limit_days:
        z = (bar.pct - median_pct) / pct_std
        estimated_ranks.append(_normal_cdf_approx(z))

    avg_estimated_rank = (sum(estimated_ranks) / len(estimated_ranks)
                          if estimated_ranks else pct_rank)

    # ─── Part 4: 加权平均 ───
    avg_rank = pct_rank * 0.6 + avg_estimated_rank * 0.4

    # ─── Part 5: 偏离度加分（优先用东财数据，与排名一致） ───
    target_comp = next((c for c in components if c.code == code), None)
    latest_pct = target_comp.pct if target_comp else 0.0
    if latest_pct == 0.0 and code in quote_map:
        latest_pct = quote_map[code].pct
    deviation = latest_pct - median_pct
    deviation_bonus = max(min(deviation / pct_std * 10, 20), 0)

    # ─── Part 6: Lead-Lag 检测 ───
    lead_lag_bonus = _lead_lag_score(stock_1min, sector_5min)

    # ─── 最终得分 ───
    final_score = max(min((1 - avg_rank) * 100 + deviation_bonus + lead_lag_bonus, 100), 0)

    return ScoreResult(
        dim="leadership",
        score=round(final_score, 2),
        weight=0.25,
        details={
            "intraday_rank": int(pct_rank * len(components)) + 1,
            "total_components": len(components),
            "intraday_percentile": round(1 - pct_rank, 4),
            "intraday_score": round(intraday_score, 2),
            "avg_historical_rank_pct": round(avg_estimated_rank, 4),
            "weighted_avg_rank": round(avg_rank, 4),
            "deviation": round(deviation, 4),
            "deviation_bonus": round(deviation_bonus, 2),
            "lead_lag_bonus": round(lead_lag_bonus, 2),
            "sector_median_pct": round(median_pct, 2),
            "sector_std": round(pct_std, 2),
            "historical_days": len(non_limit_days),
        }
    )


# ─── 当日排名 ───

def _intraday_ranking(code: str, components: list[StockInfo],
                      quote_map: dict) -> tuple[float, float, list[float]]:
    """
    Returns: (pct_rank, intraday_score, all_pcts)
    """
    # 收集成分股 pct（优先用 StockInfo.pct，fallback 用 quote_map）
    stock_pcts = []  # [(code, pct), ...]
    for comp in components:
        pct_val = comp.pct
        if pct_val == 0.0 and comp.code in quote_map:
            pct_val = quote_map[comp.code].pct
        stock_pcts.append((comp.code, pct_val))

    all_pcts = [p for _, p in stock_pcts]

    # 按 pct 降序排列
    sorted_stocks = sorted(stock_pcts, key=lambda x: -x[1])
    total = len(sorted_stocks)

    # 找目标股的排名
    rank = total  # 默认垫底
    for i, (c, _) in enumerate(sorted_stocks):
        if c == code:
            rank = i + 1
            break

    pct_rank = rank / total  # 0.01 = 最好，0.99 = 最差
    intraday_score = (1 - pct_rank) * 100

    return pct_rank, intraday_score, all_pcts


# ─── 正态 CDF 近似 ───

def _normal_cdf_approx(z: float) -> float:
    """分段线性近似标准正态 CDF，返回 estimated_rank (0.01=头部, 0.99=垫底)

    原理: rank = 1.0 - Φ(z)，用分段线性近似 Φ(z):
      Φ(0)=0.5  Φ(1)=0.84  Φ(2)=0.98  Φ(3)=0.999
      Φ(-1)=0.16  Φ(-2)=0.02  Φ(-3)=0.001
    """
    if z > 3:
        return 0.01
    elif z > 1:
        return 0.16 - (z - 1) * 0.075
    elif z > -1:
        return 0.50 - z * 0.34
    elif z > -3:
        return 0.84 - (z + 1) * 0.075
    else:
        return 0.99


# ─── Lead-Lag 检测 ───

def _lead_lag_score(stock_1min: list[KBar], sector_5min: list[KBar]) -> float:
    """检测个股是否先于板块拉伸。返回 0-20 的加分。"""
    if not stock_1min or not sector_5min:
        return 0.0

    stock_5min = _aggregate_1min_to_5min(stock_1min)

    min_len = min(len(stock_5min), len(sector_5min))
    stock_bars = stock_5min[:min_len]
    sector_bars = sector_5min[:min_len]

    lead_count = 0
    total_windows = 0

    for i in range(min_len - 6):
        s_ret = _bar_return(stock_bars, i)
        sec_ret = _bar_return(sector_bars, i)

        if s_ret > 0.005 and (s_ret - sec_ret) > 0.003:
            total_windows += 1
            for j in range(i + 1, min(i + 7, min_len)):
                sec_follow = _bar_return(sector_bars, j)
                if sec_follow > 0.003:
                    lead_count += 1
                    break

    if total_windows == 0:
        return 0.0

    lead_ratio = lead_count / total_windows
    return lead_ratio * 20.0


def _aggregate_1min_to_5min(bars: list[KBar]) -> list[KBar]:
    """每 5 根 1 分 bar 合成 1 根 5 分 bar"""
    result = []
    for i in range(0, len(bars), 5):
        chunk = bars[i:i + 5]
        if len(chunk) < 3:
            continue
        result.append(KBar(
            timestamp=chunk[0].timestamp,
            open=chunk[0].open,
            close=chunk[-1].close,
            high=max(b.high for b in chunk),
            low=min(b.low for b in chunk),
            volume=sum(b.volume for b in chunk),
            amount=sum(b.amount for b in chunk),
            chg=chunk[-1].chg,
            pct=chunk[-1].pct,
            turnover=0,
        ))
    return result


def _bar_return(klines: list[KBar], idx: int) -> float:
    """单根 bar 涨跌幅"""
    bar = klines[idx]
    if bar.open == 0:
        return 0.0
    return (bar.close - bar.open) / bar.open
