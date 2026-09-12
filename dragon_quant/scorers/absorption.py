"""资金承接：最近十个交易日内的跨板块同步下跌与拉升事件。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from dragon_quant.cache.data_cache import DataCache
from dragon_quant.models.types import KBar, ScoreResult
from dragon_quant.scorers import registry as R
from dragon_quant.scorers.base import CHINA_TZ, trading_session

DIM = "absorption"
WEIGHT = R.DIM_WEIGHTS[DIM]


def score(code: str, cache: DataCache, primary_sector: str = "",
          all_sector_codes: Optional[list[str]] = None,
          sector_name_map: Optional[dict[str, str]] = None, **kwargs) -> ScoreResult:
    if not primary_sector:
        return _fallback("未指定主板块")
    target = cache.get(f"kline:5min:sector:{primary_sector}") or []
    if len(target) < R.ABS_WINDOW:
        return _fallback("目标板块5分K不足")
    if all_sector_codes is None:
        all_sector_codes = cache.get("__meta__:sector_codes") or []
    other_map = {}
    for sector in set(all_sector_codes) - {primary_sector}:
        bars = cache.get(f"kline:5min:sector:{sector}") or []
        if len(bars) >= R.ABS_WINDOW:
            other_map[sector] = bars
    if len(other_map) < R.ABS_MIN_AFFECTED:
        return _fallback("有效对手板块不足")
    events = _detect_events(target, other_map, sector_name_map or {})
    if not events:
        return _fallback("暂无显著跨板块资金虹吸信号")
    dates = sorted(_last_n_dates(target, R.ABS_MAX_TRADE_DAYS))
    final, details = _aggregate_events(events, dates)
    return ScoreResult(dim=DIM, score=final, weight=WEIGHT, details=details)


def _aggregate_events(events: list[dict], dates: list) -> tuple[float, dict]:
    scored = [{**event, "score": _score_event(event)} for event in events]
    groups = []
    for event in sorted(scored, key=lambda e: (e["start_timestamp"], e["end_timestamp"])):
        session = trading_session(event["start_timestamp"])
        if (groups and session == groups[-1]["session"]
                and event["start_timestamp"] <= groups[-1]["end_timestamp"]):
            group = groups[-1]
            group["end_timestamp"] = max(group["end_timestamp"], event["end_timestamp"])
            group["window_count"] += 1
            if (event["score"], event["end_timestamp"]) > (group["best"]["score"], group["best"]["end_timestamp"]):
                group["best"] = event
        else:
            groups.append({"session": session, "start_timestamp": event["start_timestamp"],
                           "end_timestamp": event["end_timestamp"], "window_count": 1, "best": event})
    date_ages = {day: len(dates) - 1 - i for i, day in enumerate(dates)}
    independent = []
    for group in groups:
        event = group["best"]
        age = date_ages[trading_session(event["end_timestamp"])[0]]
        decay = 2 ** (-age / R.ABS_RECENCY_HALF_LIFE_DAYS)
        adjusted = R.ABS_NEUTRAL + (event["score"] - R.ABS_NEUTRAL) * decay
        independent.append({**event, "age_trade_days": age, "recency_weight": decay,
                            "adjusted_score": adjusted, "window_count": group["window_count"],
                            "group_start_timestamp": group["start_timestamp"],
                            "group_end_timestamp": group["end_timestamp"]})
    independent.sort(key=lambda e: (-e["adjusted_score"], -e["end_timestamp"]))
    selected = independent[:R.ABS_TOP_EVENTS]
    final = sum(e["adjusted_score"] for e in selected) / len(selected)
    return round(final, 2), {"raw_event_count": len(events), "event_count": len(groups),
                             "selected_event_count": len(selected), "best_event": selected[0],
                             "all_events": selected, "best_event_score": selected[0]["score"]}


def _fallback(reason: str) -> ScoreResult:
    return ScoreResult(dim=DIM, score=R.ABS_NEUTRAL, weight=WEIGHT,
                       details={"fallback": True, "fallback_reason": reason, "event_count": 0})


def _continuous(bars) -> bool:
    if not bars or any(bar is None for bar in bars):
        return False
    session = trading_session(bars[0].timestamp)
    return (all(trading_session(b.timestamp) == session for b in bars)
            and all(b.timestamp - a.timestamp == R.ABS_BUCKET_MS for a, b in zip(bars, bars[1:])))


def _detect_events(target: list[KBar], other_map: dict[str, list[KBar]], name_map: dict[str, str]):
    last_dates = _last_n_dates(target, R.ABS_MAX_TRADE_DAYS)
    target = _filter_dates(target, last_dates)
    fmap = {s: _filter_dates(bars, last_dates) for s, bars in other_map.items()}
    aligned = _align(target, fmap)
    if aligned is None:
        return []
    t_al, o_al = aligned
    events = []
    for end in range(R.ABS_WINDOW - 1, len(t_al)):
        start = end - R.ABS_WINDOW + 1
        window = t_al[start:end + 1]
        if not _continuous(window):
            continue
        t_ret = _wret(t_al, start, end)
        yang = sum(b.close > b.open for b in window)
        if t_ret <= R.ABS_TARGET_MIN_UP or yang < R.ABS_TARGET_MIN_YANG:
            continue
        rally = _first_rally(t_al, start, end)
        if rally is None:
            continue
        rally_ts = t_al[rally].timestamp
        fleeing = []
        for sector, bars in sorted(o_al.items()):
            hits = []
            for ws, we in ((start, end), (start - 1, end - 1)):
                drop = _wret_opt(bars, ws, we)
                if drop is None or drop >= R.ABS_DROP_TH:
                    continue
                dive = _first_dive(bars, ws, we)
                if dive is None:
                    continue
                dive_ts = bars[dive].timestamp
                if (trading_session(dive_ts) != trading_session(rally_ts)
                        or not 0 <= rally_ts - dive_ts <= R.ABS_MAX_TIME_DIFF_MS):
                    continue
                hits.append((drop, dive_ts))
            if hits:
                drop, dive_ts = min(hits)
                fleeing.append({"code": sector, "name": name_map.get(sector, sector),
                                "drop_pct": round(drop * 100, 2), "dive_timestamp": dive_ts})
        if len(fleeing) < R.ABS_MIN_AFFECTED:
            continue
        peak = max(b.close for b in window)
        final_close = window[-1].close
        dd = ((peak - final_close) / (peak - window[0].open)
              if peak > window[0].open and final_close < peak else 0.0)
        if dd > R.ABS_MAX_DRAWDOWN_RATIO:
            continue
        dive_ts = min(f["dive_timestamp"] for f in fleeing)
        ddt = datetime.fromtimestamp(dive_ts / 1000, CHINA_TZ)
        rdt = datetime.fromtimestamp(rally_ts / 1000, CHINA_TZ)
        events.append({
            "start_bar": start, "end_bar": end,
            "start_timestamp": window[0].timestamp, "end_timestamp": window[-1].timestamp,
            "dive_time": f"{ddt.month}月{ddt.day}日 {ddt.hour}:{ddt.minute:02d}",
            "rally_time": f"{rdt.month}月{rdt.day}日 {rdt.hour}:{rdt.minute:02d}",
            "time_diff_min": round((rally_ts - dive_ts) / 60000, 1),
            "target_pct": round(t_ret * 100, 2), "yang_count": yang,
            "fleeing_count": len(fleeing),
            "fleeing_avg_drop": round(sum(f["drop_pct"] for f in fleeing) / len(fleeing), 2),
            "fleeing_sectors": fleeing, "drawdown_ratio": round(dd, 2),
            "sector_universe": len(o_al),
        })
    return events


def _score_event(e: dict) -> float:
    target_score = min(e["target_pct"] / R.ABS_INT_TARGET_REF, 1.0) * 100
    flight_scale = abs(e["fleeing_avg_drop"]) * e["fleeing_count"]
    flight_score = min(flight_scale / R.ABS_INT_FLIGHT_REF, 1.0) * 100
    intensity = target_score * R.ABS_INT_TARGET_W + flight_score * R.ABS_INT_FLIGHT_W
    denom = max(int(e.get("sector_universe") or 10), 10)
    breadth = min(e["fleeing_count"] / denom, 1.0) * 100
    sustain = (1 - e["drawdown_ratio"]) * 100
    return (intensity * R.ABS_INTENSITY_W + breadth * R.ABS_BREADTH_W
            + sustain * R.ABS_SUSTAIN_W)


def _wret(kl: list[KBar], start: int, end: int) -> float:
    opening = kl[start].open
    return (kl[end].close - opening) / opening if opening > 0 else 0.0


def _wret_opt(kl: list[Optional[KBar]], start: int, end: int) -> Optional[float]:
    if start < 0 or end >= len(kl) or start > end or not _continuous(kl[start:end + 1]):
        return None
    return _wret(kl, start, end)


def _align(target, other_map):
    tb = {b.timestamp // R.ABS_BUCKET_MS: b for b in target}
    buckets = sorted(tb)
    if len(buckets) < R.ABS_WINDOW:
        return None
    t_al = [tb[b] for b in buckets]
    o_al = {}
    for sector, bars in other_map.items():
        lookup = {b.timestamp // R.ABS_BUCKET_MS: b for b in bars}
        o_al[sector] = [lookup.get(b) for b in buckets]
    return t_al, o_al


def _first_dive(bars, start, end) -> Optional[int]:
    for i in range(start, end + 1):
        if bars[i] is None:
            continue
        previous = bars[start].open if i == start else bars[i - 1].close
        if bars[i].close < previous:
            return i
    return None


def _first_rally(bars, start, end) -> Optional[int]:
    return next((i for i in range(start, end + 1) if bars[i].close > bars[i].open), None)


def _last_n_dates(klines: list[KBar], n: int) -> set:
    dates = sorted({trading_session(b.timestamp)[0] for b in klines})
    return set(dates[-n:]) if n > 0 else set()


def _filter_dates(klines: list[KBar], dates: set) -> list[KBar]:
    return [b for b in klines if trading_session(b.timestamp)[0] in dates]
