"""
资金承接性 scorer（权重 25%）

核心问题：市场恐慌时，其他板块的资金是不是虹吸到了这个板块？
"""

from typing import Optional
from dragon_quant.models.types import ScoreResult, KBar
from dragon_quant.cache.data_cache import DataCache


def score(code: str, cache: DataCache, primary_sector: str = "",
          all_sector_codes: Optional[list[str]] = None,
          sector_name_map: Optional[dict[str, str]] = None) -> ScoreResult:
    """
    Args:
        code: 股票代码
        cache: 共享数据缓存
        primary_sector: 候选股所属主板块代码
        all_sector_codes: 全部已加载板块代码列表（默认从缓存推断）
    Returns:
        ScoreResult(dim="absorption", score, weight=0.25, details)
    """
    if not primary_sector:
        return ScoreResult(
            dim="absorption", score=50.0, weight=0.25,
            details={"fallback": True, "reason": "未指定主板块"}
        )

    # 加载目标板块 5 分 K
    target_klines: list[KBar] = cache.get(f"kline:5min:sector:{primary_sector}") or []
    if len(target_klines) < 6:
        return ScoreResult(
            dim="absorption", score=50.0, weight=0.25,
            details={"fallback": True, "reason": "目标板块5分K不足"}
        )

    # 加载其他板块 5 分 K
    if all_sector_codes is None:
        all_sector_codes = _discover_sector_codes(cache)
    other_klines_map: dict[str, list[KBar]] = {}
    for s_code in all_sector_codes:
        if s_code == primary_sector:
            continue
        klines = cache.get(f"kline:5min:sector:{s_code}")
        if klines and len(klines) >= 6:
            other_klines_map[s_code] = klines

    if not other_klines_map:
        return ScoreResult(
            dim="absorption", score=50.0, weight=0.25,
            details={"fallback": True, "reason": "无其他板块5分K数据"}
        )

    # ─── Step 1: 滑动窗口检测虹吸事件 ───
    events = _detect_events(target_klines, other_klines_map, sector_name_map or {})

    if not events:
        return ScoreResult(
            dim="absorption", score=50.0, weight=0.25,
            details={
                "event_count": 0,
                "fallback_reason": "暂无显著的跨板块资金虹吸信号"
            }
        )

    # ─── Step 2 & 3: 打分 + 汇总 ───
    event_scores = []
    best_event = None
    scored_events = []
    for evt in events:
        es = _score_event(evt)
        event_scores.append(es)
        scored_evt = {**evt, "score": round(es, 2)}
        scored_events.append(scored_evt)
        if best_event is None or es > best_event["score"]:
            best_event = scored_evt

    best_score = max(event_scores)
    multi_bonus = min((len(events) - 1) * 5, 15)
    final_score = min(best_score + multi_bonus, 100)

    return ScoreResult(
        dim="absorption",
        score=round(final_score, 2),
        weight=0.25,
        details={
            "event_count": len(events),
            "best_event": best_event,
            "all_events": sorted(scored_events, key=lambda e: e["score"], reverse=True)[:3],
            "best_event_score": round(best_score, 2),
            "multi_event_bonus": round(multi_bonus, 2),
        }
    )


# ─── 事件检测 ───

def _detect_events(target_klines: list[KBar],
                   other_map: dict[str, list[KBar]],
                   sector_name_map: dict[str, str] = None) -> list[dict]:
    """滑动窗口扫描虹吸事件"""
    if sector_name_map is None:
        sector_name_map = {}
    WINDOW = 6  # 30 分钟
    events = []

    # 对齐所有板块的 K 线（按时间戳）
    aligned = _align_klines(target_klines, other_map)
    if aligned is None:
        return []

    target_aligned, others_aligned = aligned
    total_bars = len(target_aligned)

    for i in range(WINDOW - 1, total_bars):
        start = i - WINDOW + 1
        end = i  # inclusive

        # 目标板块窗口涨幅
        target_ret = _window_return(target_aligned, start, end)

        # 条件②：目标板块涨 > 0.3%
        if target_ret <= 0.003:
            continue

        # 条件③：≥4/6 根阳线
        window = target_aligned[start:end + 1]
        yang_count = sum(1 for bar in window if bar.close > bar.open)
        if yang_count < 4:
            continue

        # 条件①：≥2 个其他板块跌 > 1%
        fleeing_sectors = []
        for s_code, s_klines in others_aligned.items():
            if len(s_klines) <= end:
                continue
            s_ret = _window_return(s_klines, start, end)
            if s_ret < -0.01:
                fleeing_sectors.append({
                    "code": s_code,
                    "name": sector_name_map.get(s_code, s_code),
                    "drop_pct": round(s_ret * 100, 2),
                })

        if len(fleeing_sectors) < 2:
            continue

        # 条件④：回撤 < 30% 窗口涨幅
        window_open = target_aligned[start].open
        peak_close = max(bar.close for bar in window)
        final_close = target_aligned[end].close

        if final_close >= peak_close:
            drawdown_ratio = 0.0
        else:
            drawdown_ratio = (peak_close - final_close) / (peak_close - window_open) if peak_close != window_open else 0

        if drawdown_ratio > 0.3:
            continue

        # 出逃板块平均跌幅
        fleeing_avg_drop = sum(f["drop_pct"] for f in fleeing_sectors) / len(fleeing_sectors)

        # 时间戳（从 bar 的 timestamp 换算）
        from datetime import datetime
        start_dt = datetime.fromtimestamp(target_aligned[start].timestamp / 1000)
        end_dt = datetime.fromtimestamp(target_aligned[end].timestamp / 1000)

        events.append({
            "start_bar": start,
            "end_bar": end,
            "start_date": f"{start_dt.month}.{start_dt.day}",
            "start_time": f"{start_dt.hour}:{start_dt.minute:02d}",
            "end_time": f"{end_dt.hour}:{end_dt.minute:02d}",
            "target_pct": round(target_ret * 100, 2),
            "yang_count": yang_count,
            "fleeing_count": len(fleeing_sectors),
            "fleeing_avg_drop": round(fleeing_avg_drop, 2),
            "fleeing_sectors": fleeing_sectors,
            "drawdown_ratio": round(drawdown_ratio, 2),
        })

    return events


def _window_return(klines: list[KBar], start: int, end: int) -> float:
    """窗口涨跌幅"""
    if start == 0:
        prev_close = klines[0].open
    else:
        prev_close = klines[start - 1].close
    if prev_close == 0:
        return 0.0
    return (klines[end].close - prev_close) / prev_close


def _align_klines(target: list[KBar], other_map: dict[str, list[KBar]]):
    """简单对齐：以目标板块的时间轴为基准，截取公共区间"""
    if not target:
        return None

    # 取各板块最小长度
    min_len = len(target)
    for klines in other_map.values():
        min_len = min(min_len, len(klines))

    if min_len < 6:
        return None

    return target[:min_len], {k: v[:min_len] for k, v in other_map.items()}


# ─── 事件打分 ───

def _score_event(evt: dict) -> float:
    """三维评估单个虹吸事件"""
    # (a) 虹吸强度 40%
    flight_intensity = abs(evt["fleeing_avg_drop"]) * evt["fleeing_count"]
    intensity = min(evt["target_pct"] / max(flight_intensity, 0.001) * 100, 100)

    # (b) 广度 20%
    breadth = min(evt["fleeing_count"] / 10, 1.0) * 100

    # (c) 持续性 40%
    sustain = (1 - evt["drawdown_ratio"]) * 100

    return intensity * 0.4 + breadth * 0.2 + sustain * 0.4


# ─── 辅助 ───

def _discover_sector_codes(cache: DataCache) -> list[str]:
    """从缓存中发现已加载的板块代码"""
    snapshot = cache.snapshot()
    codes = set()
    for key in snapshot:
        if key.startswith("kline:5min:sector:"):
            codes.add(key.replace("kline:5min:sector:", ""))
    return list(codes)
