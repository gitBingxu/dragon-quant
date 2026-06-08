"""
抗跌性 scorer（权重 15%）

核心问题：大盘跳水时，这只票是跟跌还是硬扛？
"""

from __future__ import annotations

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
    stock_1min: list[KBar] = cache.get(f"kline:1min:{code}") or []

    if not stock_klines or not market_klines:
        return ScoreResult(
            dim="anti_drop", score=50.0, weight=0.15,
            details={"fallback": True, "reason": "缺少K线数据"}
        )

    # 统一日K排序：全局约定为“升序（旧→新，最新在后）”
    stock_klines = _ensure_asc(stock_klines)
    market_klines = _ensure_asc(market_klines)

    stock_dates, stock_date_to_idx, stock_date_to_bar = _build_date_maps(stock_klines)
    market_dates, market_date_to_idx, market_date_to_bar = _build_date_maps(market_klines)

    latest_market_date = market_dates[-1] if market_dates else ""

    # ─── Step 1: 识别跳水日（以大盘为准，按 date 对齐到个股） ───
    plunge_days = []  # list[dict]
    for i_m, mbar in enumerate(market_klines):
        if mbar.pct < -0.7:  # 跳水阈值
            date_str = _to_date_str(mbar.timestamp)
            if date_str not in stock_date_to_idx:
                # 个股停牌/缺失该日数据，直接跳过
                continue
            s_idx = stock_date_to_idx[date_str]
            sbar = stock_klines[s_idx]
            plunge_days.append({
                "date": date_str,
                "m_idx": i_m,
                "s_idx": s_idx,
                "market_pct": mbar.pct,
                "stock_pct": sbar.pct,
            })

    if not plunge_days:
        return ScoreResult(
            dim="anti_drop", score=50.0, weight=0.15,
            details={"plunge_days": [], "reason": "近30日无可对齐跳水日"}
        )

    # ─── Step 2: 对每个跳水日三维评估 ───
    day_scores = []
    details_list = []

    for pd in plunge_days:
        date_str = pd["date"]
        s_idx = pd["s_idx"]
        m_idx = pd["m_idx"]
        market_pct = float(pd["market_pct"])

        sbar = stock_klines[s_idx]

        # (a) 相对回撤强度 40%
        relative_score = _relative_retreat(sbar, market_pct)

        # (b) 日内承接强度 30%
        prev_sbar = stock_klines[s_idx - 1] if s_idx - 1 >= 0 else None
        prev_close = prev_sbar.close if prev_sbar else 0.0

        intraday_source = "day"
        intraday_1min_metrics = None

        # 仅当跳水日为最新交易日时，minute 数据才有意义（雪球 minute.json 只返回当日）
        if date_str == latest_market_date and stock_1min and prev_close > 0:
            intraday_source = "1min"
            intraday_score, intraday_1min_metrics = _intraday_hold_1min(stock_1min, prev_close)
        else:
            intraday_score = _intraday_hold(sbar, prev_close)

        # (c) 反弹弹性 30%
        rebound_score, rebound_note = _rebound_by_date(
            stock_date_to_bar=stock_date_to_bar,
            market_dates=market_dates,
            market_date_to_idx=market_date_to_idx,
            market_date_to_bar=market_date_to_bar,
            plunge_date=date_str,
        )

        day_score = relative_score * 0.4 + intraday_score * 0.3 + rebound_score * 0.3
        day_scores.append(day_score)
        details_list.append({
            "date": date_str,
            "market_pct": round(market_pct, 2),
            "stock_pct": round(sbar.pct, 2),
            "relative_retreat": round(relative_score, 2),
            "intraday_hold": round(intraday_score, 2),
            "intraday_source": intraday_source,
            "intraday_1min_metrics": intraday_1min_metrics,
            "rebound": round(rebound_score, 2),
            "rebound_note": rebound_note,
            "day_score": round(day_score, 2),
        })

    # ─── Step 3: 多日汇总 ───
    final_score = sum(day_scores) / len(day_scores)

    # 连续暴跌加成
    consecutive_bonus = _consecutive_plunge_bonus_by_date(
        plunge_days=plunge_days,
        stock_date_to_bar=stock_date_to_bar,
        market_date_to_bar=market_date_to_bar,
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


def _to_date_str(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")


def _ensure_asc(klines: list[KBar]) -> list[KBar]:
    """确保按 timestamp 升序（旧→新）。"""
    if len(klines) <= 1:
        return klines
    # 常见情况：已经升序；否则排序
    is_asc = True
    prev_ts = klines[0].timestamp
    for b in klines[1:]:
        if b.timestamp < prev_ts:
            is_asc = False
            break
        prev_ts = b.timestamp
    if is_asc:
        return klines
    return sorted(klines, key=lambda b: b.timestamp)


def _build_date_maps(klines: list[KBar]) -> tuple[list[str], dict[str, int], dict[str, KBar]]:
    dates: list[str] = []
    date_to_idx: dict[str, int] = {}
    date_to_bar: dict[str, KBar] = {}
    for i, bar in enumerate(klines):
        d = _to_date_str(bar.timestamp)
        dates.append(d)
        date_to_idx[d] = i
        date_to_bar[d] = bar
    return dates, date_to_idx, date_to_bar


# ─── 子维度计算 ───


def _relative_retreat(sbar: KBar, market_pct: float) -> float:
    stock_return = sbar.pct
    excess_return = stock_return - market_pct

    if stock_return > 0:
        return 100.0
    elif excess_return > 0:
        return 60.0 + excess_return / abs(market_pct) * 40.0
    elif stock_return > -2.0:
        return 30.0
    else:
        return 0.0


def _intraday_hold(sbar: KBar, prev_close: float) -> float:
    open_px = sbar.open
    close_px = sbar.close
    high_px = sbar.high
    low_px = sbar.low

    if high_px == low_px:
        return 50.0

    entity_low = min(open_px, close_px)
    lower_shadow_pct = (entity_low - low_px) / (high_px - low_px)

    close_pos = (close_px - open_px) / (high_px - low_px)

    if prev_close == 0:
        max_drop_pct = 0.0
    else:
        max_drop_pct = (low_px - prev_close) / prev_close

    penalty = min(abs(max_drop_pct) / 0.05, 1.0)

    support_score = (lower_shadow_pct * 0.6 + close_pos * 0.4) * 100
    support_score = support_score * (1 - penalty * 0.3)

    return max(min(support_score, 100), 0)


def _intraday_hold_1min(minute_klines: list[KBar], prev_close: float) -> tuple[float, dict]:
    """用当日 1min 分时增强“日内承接”。

    仅适用于“跳水日=当日”，因为 minute.json 只返回当日数据。

    Returns:
        (score, metrics)
    """
    if not minute_klines or prev_close <= 0:
        return 50.0, {"fallback": True, "reason": "minute 空或 prev_close 无效"}

    day_low = min(b.low for b in minute_klines)
    day_close = minute_klines[-1].close

    # 最大回撤（负数），绝对值越小越好
    max_dd = (day_low - prev_close) / prev_close

    # 回补比例：从最低点回补到收盘的程度（0~1+），越大越好
    denom = max(prev_close - day_low, 1e-9)
    recovery = (day_close - day_low) / denom
    recovery = max(0.0, recovery)

    # 映射到分数：
    # - max_dd 以 -5% 作为惩罚封顶
    penalty = min(abs(max_dd) / 0.05, 1.0)
    base = 80.0 * min(recovery, 1.0)  # 回补满额给 80
    score = base * (1 - 0.3 * penalty) + 20.0 * (1 - penalty)  # 最大 100
    score = max(min(score, 100.0), 0.0)

    metrics = {
        "max_dd": round(max_dd, 6),
        "recovery": round(recovery, 6),
        "day_low": day_low,
        "day_close": day_close,
        "prev_close": prev_close,
        "penalty": round(penalty, 6),
    }
    return score, metrics


def _rebound(stock_klines: list[KBar], market_klines: list[KBar],
             s_idx: int, m_idx: int) -> float:
    """旧实现（保留用于兼容老测试/外部引用）。

    注意：该实现依赖特定索引方向，已不再被 score() 使用。
    """
    if s_idx == 0 or m_idx == 0:
        return 50.0

    t1_stock_pct = stock_klines[s_idx - 1].pct
    t1_market_pct = market_klines[m_idx - 1].pct
    alpha = t1_stock_pct - t1_market_pct

    if t1_stock_pct > 0 and alpha > 0:
        return min(alpha / 0.03 * 100, 100)
    elif t1_stock_pct > 0 and t1_market_pct <= 0:
        return 100.0
    elif t1_stock_pct < 0 and t1_market_pct < 0:
        return max(0.0, (1 - abs(alpha) / 0.03) * 100)
    else:
        return 0.0


def _rebound_by_date(*,
                     stock_date_to_bar: dict[str, KBar],
                     market_dates: list[str],
                     market_date_to_idx: dict[str, int],
                     market_date_to_bar: dict[str, KBar],
                     plunge_date: str) -> tuple[float, str]:
    """反弹弹性（按日期对齐）：跳水日次日表现。

    Returns:
        (score, note)
    """
    if plunge_date not in market_date_to_idx:
        return 50.0, "market_date_missing"

    m_idx = market_date_to_idx[plunge_date]
    if m_idx + 1 >= len(market_dates):
        return 50.0, "no_next_market_day"

    next_date = market_dates[m_idx + 1]
    if next_date not in stock_date_to_bar:
        return 50.0, "missing_stock_next_day"

    t1_stock_pct = stock_date_to_bar[next_date].pct
    t1_market_pct = market_date_to_bar[next_date].pct
    alpha = t1_stock_pct - t1_market_pct

    # 优先判断“独涨”场景：个股涨而大盘不涨（<=0）
    if t1_stock_pct > 0 and t1_market_pct <= 0:
        return 100.0, "stock_up_market_not_up"

    # 一起涨且跑赢（alpha>0）
    if t1_stock_pct > 0 and t1_market_pct > 0 and alpha > 0:
        return min(alpha / 0.03 * 100, 100), "both_up_stock_outperform"
    if t1_stock_pct < 0 and t1_market_pct < 0:
        return max(0.0, (1 - abs(alpha) / 0.03) * 100), "both_down_relative_hold"
    return 0.0, "stock_down_market_up"


def _consecutive_plunge_bonus(stock_klines: list[KBar], market_klines: list[KBar],
                               plunge_days: list) -> float:
    """旧实现（保留用于兼容老测试/外部引用）。

    注意：该实现存在 stock/market 索引错配风险，已不再被 score() 使用。
    """
    if len(plunge_days) < 2:
        return 0.0

    plunge_indices = sorted([p[0] for p in plunge_days], reverse=True)
    consecutive_segment = [plunge_indices[0]]
    for i in range(1, len(plunge_indices)):
        if plunge_indices[i] == consecutive_segment[-1] + 1:
            consecutive_segment.append(plunge_indices[i])
        else:
            break

    if len(consecutive_segment) < 2:
        return 0.0

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


def _consecutive_plunge_bonus_by_date(*,
                                     plunge_days: list[dict],
                                     stock_date_to_bar: dict[str, KBar],
                                     market_date_to_bar: dict[str, KBar]) -> float:
    """连续 ≥2 个跳水日：按 market m_idx 连续段，按 date 对齐累计跌幅。

    缺失交易日（停牌/数据缺失）会在 plunge_days 构建阶段被过滤，从而自然“断段”。
    """
    if len(plunge_days) < 2:
        return 0.0

    # 按 market 索引从近到远
    sorted_days = sorted(plunge_days, key=lambda d: d["m_idx"], reverse=True)

    segment = [sorted_days[0]]
    for d in sorted_days[1:]:
        if d["m_idx"] == segment[-1]["m_idx"] - 1:
            segment.append(d)
        else:
            break

    if len(segment) < 2:
        return 0.0

    stock_cum = 1.0
    market_cum = 1.0
    for d in segment:
        date_str = d["date"]
        sbar = stock_date_to_bar.get(date_str)
        mbar = market_date_to_bar.get(date_str)
        if not sbar or not mbar:
            # 理论上不会发生（plunge_days 已过滤），防御一下
            return 0.0
        stock_cum *= (1 + sbar.pct / 100)
        market_cum *= (1 + mbar.pct / 100)

    stock_drop = 1 - stock_cum
    market_drop = 1 - market_cum
    if stock_drop < market_drop * 0.5:
        return 10.0
    return 0.0
