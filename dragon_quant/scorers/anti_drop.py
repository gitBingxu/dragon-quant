"""
抗跌性 scorer（权重 15%）

核心问题：大盘跳水时，这只票是跟跌还是硬扛？
"""

from datetime import datetime
from typing import Optional
from dragon_quant.models.types import ScoreResult, KBar
from dragon_quant.cache.data_cache import DataCache


def score(code: str, cache: DataCache) -> ScoreResult:
    """
    Args:
        code: 股票代码
        cache: 共享数据缓存
    Returns:
        ScoreResult(dim="anti_drop", score, weight=0.15, details)
    """
    # 加载数据
    stock_klines: list[KBar] = cache.get(f"kline:day:{code}") or []
    market_klines: list[KBar] = cache.get(f"kline:day:000001") or []
    stock_5min: list[KBar] = cache.get(f"kline:5min:{code}") or []

    if not stock_klines or not market_klines:
        return ScoreResult(
            dim="anti_drop", score=50.0, weight=0.15,
            details={"fallback": True, "reason": "缺少K线数据"}
        )

    # ─── Step 1: 识别跳水日 ───
    plunge_days = []  # [(index_in_stock, index_in_market, date, market_pct), ...]
    for i_m, mbar in enumerate(market_klines):
        if mbar.pct < -0.7:  # 跳水阈值
            ts = datetime.fromtimestamp(mbar.timestamp / 1000)
            # 找对应的个股 K 线（同日）
            s_idx = _find_matching_index(stock_klines, mbar.timestamp)
            if s_idx is not None:
                plunge_days.append((s_idx, i_m, ts.strftime("%Y-%m-%d"), mbar.pct))

    if not plunge_days:
        return ScoreResult(
            dim="anti_drop", score=50.0, weight=0.15,
            details={"plunge_days": [], "reason": "近30日无跳水日"}
        )

    # ─── Step 2: 对每个跳水日三维评估 ───
    day_scores = []
    details_list = []

    for s_idx, m_idx, date_str, market_pct in plunge_days:
        sbar = stock_klines[s_idx]

        # (a) 相对回撤强度 40%
        relative_score = _relative_retreat(sbar, market_pct)

        # (b) 日内承接强度 30%
        prev_sbar = stock_klines[s_idx + 1] if s_idx + 1 < len(stock_klines) else None
        prev_close = prev_sbar.close if prev_sbar else 0.0
        intraday_score = _intraday_hold(sbar, prev_close)

        # (c) 反弹弹性 30%
        rebound_score = _rebound(stock_klines, market_klines, s_idx, m_idx)

        day_score = relative_score * 0.4 + intraday_score * 0.3 + rebound_score * 0.3
        day_scores.append(day_score)
        details_list.append({
            "date": date_str,
            "market_pct": round(market_pct, 2),
            "stock_pct": round(sbar.pct, 2),
            "relative_retreat": round(relative_score, 2),
            "intraday_hold": round(intraday_score, 2),
            "rebound": round(rebound_score, 2),
            "day_score": round(day_score, 2),
        })

    # ─── Step 3: 多日汇总 ───
    final_score = sum(day_scores) / len(day_scores)

    # 连续暴跌加成
    consecutive_bonus = _consecutive_plunge_bonus(
        stock_klines, market_klines, plunge_days
    )
    final_score = min(final_score + consecutive_bonus, 100)

    return ScoreResult(
        dim="anti_drop",
        score=round(final_score, 2),
        weight=0.15,
        details={
            "plunge_days": [d["date"] for d in details_list],
            "plunge_day_scores": [d["day_score"] for d in details_list],
            "day_details": details_list,
            "consecutive_plunge_bonus": consecutive_bonus,
        }
    )


def _find_matching_index(klines: list[KBar], target_ts: int) -> Optional[int]:
    """按日期匹配 K 线索引（忽略时分秒，只看年月日）"""
    target_date = datetime.fromtimestamp(target_ts / 1000).date()
    for i, bar in enumerate(klines):
        bar_date = datetime.fromtimestamp(bar.timestamp / 1000).date()
        if bar_date == target_date:
            return i
    return None


# ─── 子维度计算 ───


def _rebound(stock_klines: list[KBar], market_klines: list[KBar],
             s_idx: int, m_idx: int) -> float:
    """反弹弹性 — 跳水日次日表现"""
    if s_idx == 0 or m_idx == 0:
        return 50.0  # 跳水日是最近一天，无次日数据

    t1_stock_pct = stock_klines[s_idx - 1].pct
    t1_market_pct = market_klines[m_idx - 1].pct
    alpha = t1_stock_pct - t1_market_pct

    if t1_stock_pct > 0 and alpha > 0:
        return min(alpha / 0.03 * 100, 100)  # 一起涨但跑赢
    elif t1_stock_pct > 0 and t1_market_pct <= 0:
        return 100.0  # 独涨最强
    elif t1_stock_pct < 0 and t1_market_pct < 0:
        return max(0.0, (1 - abs(alpha) / 0.03) * 100)  # 一起跌但抗跌
    else:
        return 0.0  # 个股跌大盘涨


def _consecutive_plunge_bonus(stock_klines: list[KBar], market_klines: list[KBar],
                               plunge_days: list) -> float:
    """连续 ≥2 个跳水日，期间个股跌幅 < 大盘跌幅 × 0.5 → +10 分"""
    if len(plunge_days) < 2:
        return 0.0

    # 取最近一次连续暴跌段
    plunge_indices = sorted([p[0] for p in plunge_days], reverse=True)
    consecutive_segment = [plunge_indices[0]]
    for i in range(1, len(plunge_indices)):
        if plunge_indices[i] == consecutive_segment[-1] + 1:
            consecutive_segment.append(plunge_indices[i])
        else:
            break

    if len(consecutive_segment) < 2:
        return 0.0

    # 期间累计涨跌幅
    first_idx = consecutive_segment[-1]
    last_idx = consecutive_segment[0]
    stock_cum = 1.0
    market_cum = 1.0
    for i in range(last_idx, first_idx + 1):
        stock_cum *= (1 + stock_klines[i].pct / 100)
        market_cum *= (1 + market_klines[i].pct / 100)

    stock_drop = 1 - stock_cum
    market_drop = 1 - market_cum

    if stock_drop < market_drop * 0.5:
        return 10.0
    return 0.0
