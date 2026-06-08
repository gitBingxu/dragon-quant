"""dragon_quant.scorers.absorption

资金承接性 scorer（权重 25%）

核心问题：市场恐慌时，其他板块的资金是不是虹吸到了这个板块？

本实现的“虹吸事件”判定遵循：
1) 目标板块 30 分钟窗口（6 根 5minK）满足“≥4 阳线 + 涨幅>0.3%”；
2) 其他板块在同窗口或向前平移 5 分钟窗口（-1 根 bar）下跌（阈值 -0.3%）；
3) 继续保留旧版硬条件：
   - 目标窗口回撤比例 <= 0.3
   - 其他板块跳水发生不晚于目标板块首次拉伸，且时间差 <= 10 分钟（同一交易日）
"""

from __future__ import annotations

from typing import Optional
from dragon_quant.models.types import ScoreResult, KBar
from dragon_quant.cache.data_cache import DataCache


WINDOW = 6  # 30 分钟
TARGET_MIN_UP = 0.003  # 0.3%
TARGET_MIN_YANG = 4
DROP_TH = -0.003  # -0.3%
MIN_AFFECTED = 2
MAX_TRADE_DAYS = 5
BUCKET_MS = 300_000  # 5 min


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
            details={"fallback": True, "fallback_reason": "未指定主板块"}
        )

    # 加载目标板块 5 分 K
    target_klines: list[KBar] = cache.get(f"kline:5min:sector:{primary_sector}") or []
    if len(target_klines) < 6:
        return ScoreResult(
            dim="absorption", score=50.0, weight=0.25,
            details={"fallback": True, "fallback_reason": "目标板块5分K不足"}
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
            details={"fallback": True, "fallback_reason": "无其他板块5分K数据"}
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
    events = []

    # 只检查最近 MAX_TRADE_DAYS 个交易日
    last_dates = _last_n_trade_dates(target_klines, MAX_TRADE_DAYS)
    target_klines = _filter_klines_by_dates(target_klines, last_dates)
    if len(target_klines) < WINDOW:
        return []
    filtered_other_map: dict[str, list[KBar]] = {}
    for s_code, klines in other_map.items():
        f = _filter_klines_by_dates(klines, last_dates)
        if len(f) >= WINDOW:
            filtered_other_map[s_code] = f
    if not filtered_other_map:
        return []

    # 对齐所有板块的 K 线（按时间戳）
    aligned = _align_klines(target_klines, filtered_other_map)
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
        if target_ret <= TARGET_MIN_UP:
            continue

        # 条件③：≥4/6 根阳线
        window = target_aligned[start:end + 1]
        yang_count = sum(1 for bar in window if bar.close > bar.open)
        if yang_count < TARGET_MIN_YANG:
            continue

        # 条件①：>= MIN_AFFECTED 个其他板块在同窗口或前移 5min 窗口下跌（<= DROP_TH）
        fleeing_sectors = []
        dive_bars: list[int] = []
        for s_code, s_klines in others_aligned.items():
            # 同窗口
            ret_same = _window_return_opt(s_klines, start, end)
            same_hit = ret_same is not None and ret_same < DROP_TH

            # 前移 5min 窗口（整体前移 1 根 bar）
            ret_lead = None
            lead_hit = False
            if start >= 1:
                ret_lead = _window_return_opt(s_klines, start - 1, end - 1)
                lead_hit = ret_lead is not None and ret_lead < DROP_TH

            if not (same_hit or lead_hit):
                continue

            # 展示字段：优先 same-window（time_diff 更贴近“同步虹吸”）
            if same_hit:
                match_window = "same"
                drop_pct = round(ret_same * 100, 2)
            else:
                match_window = "lead_5m"
                drop_pct = round(ret_lead * 100, 2)

            fleeing_sectors.append({
                "code": s_code,
                "name": sector_name_map.get(s_code, s_code),
                "drop_pct": drop_pct,
                "match_window": match_window,
                "drop_pct_same": round(ret_same * 100, 2) if ret_same is not None else None,
                "drop_pct_lead": round(ret_lead * 100, 2) if ret_lead is not None else None,
            })

            # 因果校验用：取该板块“最早的跳水 bar”作为候选
            db_candidates: list[int] = []
            if same_hit:
                db = _find_first_dive_bar(s_klines, start, end)
                if db is not None:
                    db_candidates.append(db)
            if lead_hit and start >= 1:
                db = _find_first_dive_bar(s_klines, start - 1, end - 1)
                if db is not None:
                    db_candidates.append(db)
            if db_candidates:
                dive_bars.append(min(db_candidates))

        if len(fleeing_sectors) < MIN_AFFECTED:
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

        if not dive_bars:
            continue

        earliest_dive_bar = min(dive_bars)
        rally_bar = _find_first_rally_bar(target_aligned, start, end)
        if rally_bar is None:
            continue

        # 跳水必须在拉伸之前（允许同一根K线）
        if earliest_dive_bar > rally_bar:
            continue

        dive_ts = target_aligned[earliest_dive_bar].timestamp
        rally_ts = target_aligned[rally_bar].timestamp

        from datetime import datetime
        dive_dt = datetime.fromtimestamp(dive_ts / 1000)
        rally_dt = datetime.fromtimestamp(rally_ts / 1000)

        # 必须在同一个交易日
        if dive_dt.date() != rally_dt.date():
            continue

        # 时间相差不能超过 10 分钟
        time_diff_ms = rally_ts - dive_ts
        if time_diff_ms > 600_000:
            continue

        time_diff_min = round(time_diff_ms / 60000, 1)

        events.append({
            "start_bar": start,
            "end_bar": end,
            "dive_time": f"{dive_dt.month}月{dive_dt.day}日 {dive_dt.hour}:{dive_dt.minute:02d}",
            "rally_time": f"{rally_dt.month}月{rally_dt.day}日 {rally_dt.hour}:{rally_dt.minute:02d}",
            "time_diff_min": time_diff_min,
            "target_pct": round(target_ret * 100, 2),
            "yang_count": yang_count,
            "fleeing_count": len(fleeing_sectors),
            "fleeing_avg_drop": round(fleeing_avg_drop, 2),
            "fleeing_sectors": fleeing_sectors,
            "drawdown_ratio": round(drawdown_ratio, 2),
            "drop_th": DROP_TH,
            "sector_universe": len(others_aligned),
            "max_trade_days": MAX_TRADE_DAYS,
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


def _window_return_opt(klines: list[Optional[KBar]], start: int, end: int) -> Optional[float]:
    """窗口涨跌幅（可容忍缺失 bar；缺失则返回 None）"""
    if start < 0 or end < 0 or end >= len(klines) or start > end:
        return None

    if start == 0:
        first = klines[0]
        if first is None:
            return None
        prev_close = first.open
    else:
        prev = klines[start - 1]
        if prev is None:
            return None
        prev_close = prev.close

    last = klines[end]
    if last is None:
        return None

    if prev_close == 0:
        return None
    return (last.close - prev_close) / prev_close


def _align_klines(
    target: list[KBar],
    other_map: dict[str, list[KBar]],
) -> Optional[tuple[list[KBar], dict[str, list[Optional[KBar]]]]]:
    """按 5 分钟 bucket 对齐：以目标板块时间轴为基准。

    说明：
    - 目标板块缺 bar 无法定义窗口，直接剔除。
    - 其他板块允许缺 bar，在窗口计算时按“不可判定”处理。
    """
    if not target:
        return None

    target_bucket_map: dict[int, KBar] = {}
    for bar in target:
        target_bucket_map[bar.timestamp // BUCKET_MS] = bar

    buckets = sorted(target_bucket_map.keys())
    if len(buckets) < WINDOW:
        return None

    target_aligned = [target_bucket_map[b] for b in buckets]

    others_aligned: dict[str, list[Optional[KBar]]] = {}
    for s_code, klines in other_map.items():
        m: dict[int, KBar] = {}
        for bar in klines:
            m[bar.timestamp // BUCKET_MS] = bar
        others_aligned[s_code] = [m.get(b) for b in buckets]

    return target_aligned, others_aligned


def _find_first_dive_bar(sector_klines: list[Optional[KBar]], window_start: int, window_end: int) -> Optional[int]:
    """找到板块首次跳水的K线索引
    条件：10分钟内（2根5分K）累计涨跌幅 < -0.5% 且 当前K为阴线
    """
    for i in range(window_start + 1, window_end + 1):
        if i - 1 < 0 or i >= len(sector_klines):
            continue
        prev = sector_klines[i - 1]
        cur = sector_klines[i]
        if prev is None or cur is None:
            continue
        prev_close = prev.close
        if prev_close == 0:
            continue
        ret_2bar = (cur.close - prev_close) / prev_close
        if ret_2bar < -0.005 and cur.close < cur.open:
            return i
    return None


def _find_first_rally_bar(target_klines, window_start, window_end):
    """找到目标板块在窗口内首次拉伸的K线索引（第一根阳线）"""
    for i in range(window_start, window_end + 1):
        if target_klines[i].close > target_klines[i].open:
            return i
    return None


# ─── 事件打分 ───

def _score_event(evt: dict) -> float:
    """三维评估单个虹吸事件"""
    # (a) 虹吸强度 40%
    flight_intensity = abs(evt["fleeing_avg_drop"]) * evt["fleeing_count"]
    intensity = min(evt["target_pct"] / max(flight_intensity, 0.001) * 100, 100)

    # (b) 广度 20%（占比口径：受影响板块 / 本次参与判定板块数）
    denom = max(int(evt.get("sector_universe") or 10), 10)
    breadth = min(evt["fleeing_count"] / denom, 1.0) * 100

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


def _last_n_trade_dates(klines: list[KBar], n: int) -> set:
    """从 K 线中提取最近 n 个交易日（按本地日期）。"""
    if n <= 0:
        return set()

    from datetime import datetime

    # 按时间升序，保证“最近”稳定
    dates: list = []
    seen = set()
    for bar in sorted(klines, key=lambda b: b.timestamp):
        d = datetime.fromtimestamp(bar.timestamp / 1000).date()
        if d not in seen:
            seen.add(d)
            dates.append(d)
    if not dates:
        return set()
    return set(dates[-n:])


def _filter_klines_by_dates(klines: list[KBar], dates: set) -> list[KBar]:
    if not dates:
        return []
    from datetime import datetime

    return [
        bar for bar in klines
        if datetime.fromtimestamp(bar.timestamp / 1000).date() in dates
    ]
