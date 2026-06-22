"""scorers_v2.absorption — 资金承接性 (10%)。

文案无直接对应，作为龙头识别的补充盘面证据：市场调整时其他板块资金是否被
虹吸到目标板块。实现参考旧 scorers/absorption.py，回看窗口由 5 改为 10 个交易日，
数据源改用 ths.get_sector_5min_kline_history 写入的 kline:5min:sector:{s}。
absorption 不作为硬门槛，仅加权贡献。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from dragon_quant.cache.data_cache import DataCache
from dragon_quant.models.types import KBar, ScoreResult
from dragon_quant.scorers_v2 import registry as R

DIM = "absorption"
WEIGHT = R.DIM_WEIGHTS[DIM]


def score(code: str, cache: DataCache, primary_sector: str = "",
          all_sector_codes: Optional[list[str]] = None,
          sector_name_map: Optional[dict[str, str]] = None,
          **kwargs) -> ScoreResult:
    if not primary_sector:
        return _fallback("未指定主板块")

    target: list[KBar] = cache.get(f"kline:5min:sector:{primary_sector}") or []
    if len(target) < R.ABS_WINDOW:
        return _fallback("目标板块5分K不足")

    if all_sector_codes is None:
        all_sector_codes = _discover(cache)
    other_map: dict[str, list[KBar]] = {}
    for s in all_sector_codes:
        if s == primary_sector:
            continue
        kl = cache.get(f"kline:5min:sector:{s}")
        if kl and len(kl) >= R.ABS_WINDOW:
            other_map[s] = kl
    if not other_map:
        return _fallback("无其他板块5分K数据")

    events = _detect_events(target, other_map, sector_name_map or {})
    if not events:
        return ScoreResult(dim=DIM, score=R.ABS_NEUTRAL, weight=WEIGHT,
                           details={"event_count": 0,
                                    "fallback_reason": "暂无显著跨板块资金虹吸信号"})

    scored = [{**e, "score": round(_score_event(e), 2)} for e in events]
    best = max(scored, key=lambda e: e["score"])
    best_score = best["score"]
    multi = min((len(events) - 1) * R.ABS_MULTI_BONUS_STEP, R.ABS_MULTI_BONUS_CAP)
    final = min(best_score + multi, 100)
    return ScoreResult(
        dim=DIM, score=round(final, 2), weight=WEIGHT,
        details={"event_count": len(events), "best_event": best,
                 "all_events": sorted(scored, key=lambda e: e["score"], reverse=True)[:3],
                 "best_event_score": best_score, "multi_event_bonus": multi},
    )


def _fallback(reason: str) -> ScoreResult:
    return ScoreResult(dim=DIM, score=R.ABS_NEUTRAL, weight=WEIGHT,
                       details={"fallback": True, "fallback_reason": reason})


# ─── 事件检测 ───

def _detect_events(target: list[KBar], other_map: dict[str, list[KBar]],
                   name_map: dict[str, str]) -> list[dict]:
    last_dates = _last_n_dates(target, R.ABS_MAX_TRADE_DAYS)
    target = _filter_dates(target, last_dates)
    if len(target) < R.ABS_WINDOW:
        return []
    fmap = {}
    for s, kl in other_map.items():
        f = _filter_dates(kl, last_dates)
        if len(f) >= R.ABS_WINDOW:
            fmap[s] = f
    if not fmap:
        return []

    aligned = _align(target, fmap)
    if aligned is None:
        return []
    t_al, o_al = aligned
    total = len(t_al)
    events = []

    for i in range(R.ABS_WINDOW - 1, total):
        start = i - R.ABS_WINDOW + 1
        end = i
        t_ret = _wret(t_al, start, end)
        if t_ret <= R.ABS_TARGET_MIN_UP:
            continue
        window = t_al[start:end + 1]
        yang = sum(1 for b in window if b.close > b.open)
        if yang < R.ABS_TARGET_MIN_YANG:
            continue

        fleeing = []
        dive_bars = []
        for s, sk in o_al.items():
            r_same = _wret_opt(sk, start, end)
            same_hit = r_same is not None and r_same < R.ABS_DROP_TH
            r_lead = None
            lead_hit = False
            if start >= 1:
                r_lead = _wret_opt(sk, start - 1, end - 1)
                lead_hit = r_lead is not None and r_lead < R.ABS_DROP_TH
            if not (same_hit or lead_hit):
                continue
            drop = round((r_same if same_hit else r_lead) * 100, 2)
            fleeing.append({"code": s, "name": name_map.get(s, s), "drop_pct": drop})
            dbs = []
            if same_hit:
                db = _first_dive(sk, start, end)
                if db is not None:
                    dbs.append(db)
            if lead_hit and start >= 1:
                db = _first_dive(sk, start - 1, end - 1)
                if db is not None:
                    dbs.append(db)
            if dbs:
                dive_bars.append(min(dbs))

        if len(fleeing) < R.ABS_MIN_AFFECTED:
            continue

        window_open = t_al[start].open
        peak = max(b.close for b in window)
        final_close = t_al[end].close
        if final_close >= peak:
            dd = 0.0
        else:
            dd = (peak - final_close) / (peak - window_open) if peak != window_open else 0
        if dd > R.ABS_MAX_DRAWDOWN_RATIO:
            continue
        if not dive_bars:
            continue
        earliest = min(dive_bars)
        rally = _first_rally(t_al, start, end)
        if rally is None or earliest > rally:
            continue
        dive_ts = t_al[earliest].timestamp
        rally_ts = t_al[rally].timestamp
        ddt = datetime.fromtimestamp(dive_ts / 1000)
        rdt = datetime.fromtimestamp(rally_ts / 1000)
        if ddt.date() != rdt.date():
            continue
        if rally_ts - dive_ts > R.ABS_MAX_TIME_DIFF_MS:
            continue

        avg_drop = sum(f["drop_pct"] for f in fleeing) / len(fleeing)
        events.append({
            "start_bar": start, "end_bar": end,
            "dive_time": f"{ddt.month}月{ddt.day}日 {ddt.hour}:{ddt.minute:02d}",
            "rally_time": f"{rdt.month}月{rdt.day}日 {rdt.hour}:{rdt.minute:02d}",
            "time_diff_min": round((rally_ts - dive_ts) / 60000, 1),
            "target_pct": round(t_ret * 100, 2), "yang_count": yang,
            "fleeing_count": len(fleeing), "fleeing_avg_drop": round(avg_drop, 2),
            "fleeing_sectors": fleeing, "drawdown_ratio": round(dd, 2),
            "sector_universe": len(o_al),
        })
    return events


def _score_event(e: dict) -> float:
    # 强度（正向口径）：目标拉升越高 + 出逃规模越大 → 分越高
    #   拉升分量 = clip(目标涨幅 / TARGET_REF) × 100
    #   出逃规模分量 = clip(|出逃均跌| × 出逃数 / FLIGHT_REF) × 100
    target_score = min(e["target_pct"] / R.ABS_INT_TARGET_REF, 1.0) * 100
    flight_scale = abs(e["fleeing_avg_drop"]) * e["fleeing_count"]
    flight_score = min(flight_scale / R.ABS_INT_FLIGHT_REF, 1.0) * 100
    intensity = target_score * R.ABS_INT_TARGET_W + flight_score * R.ABS_INT_FLIGHT_W
    denom = max(int(e.get("sector_universe") or 10), 10)
    breadth = min(e["fleeing_count"] / denom, 1.0) * 100
    sustain = (1 - e["drawdown_ratio"]) * 100
    return (intensity * R.ABS_INTENSITY_W + breadth * R.ABS_BREADTH_W
            + sustain * R.ABS_SUSTAIN_W)


# ─── 辅助 ───

def _wret(kl: list[KBar], start: int, end: int) -> float:
    prev = kl[0].open if start == 0 else kl[start - 1].close
    return 0.0 if prev == 0 else (kl[end].close - prev) / prev


def _wret_opt(kl: list[Optional[KBar]], start: int, end: int) -> Optional[float]:
    if start < 0 or end < 0 or end >= len(kl) or start > end:
        return None
    if start == 0:
        if kl[0] is None:
            return None
        prev = kl[0].open
    else:
        if kl[start - 1] is None:
            return None
        prev = kl[start - 1].close
    if kl[end] is None or prev == 0:
        return None
    return (kl[end].close - prev) / prev


def _align(target, other_map):
    if not target:
        return None
    tb = {b.timestamp // R.ABS_BUCKET_MS: b for b in target}
    buckets = sorted(tb.keys())
    if len(buckets) < R.ABS_WINDOW:
        return None
    t_al = [tb[b] for b in buckets]
    o_al = {}
    for s, kl in other_map.items():
        m = {b.timestamp // R.ABS_BUCKET_MS: b for b in kl}
        o_al[s] = [m.get(b) for b in buckets]
    return t_al, o_al


def _first_dive(sk, ws, we) -> Optional[int]:
    for i in range(ws + 1, we + 1):
        if i - 1 < 0 or i >= len(sk):
            continue
        prev, cur = sk[i - 1], sk[i]
        if prev is None or cur is None or prev.close == 0:
            continue
        if (cur.close - prev.close) / prev.close < -0.005 and cur.close < cur.open:
            return i
    return None


def _first_rally(t_al, ws, we) -> Optional[int]:
    for i in range(ws, we + 1):
        if t_al[i].close > t_al[i].open:
            return i
    return None


def _discover(cache: DataCache) -> list[str]:
    codes = set()
    for key in cache.snapshot():
        if key.startswith("kline:5min:sector:"):
            codes.add(key.replace("kline:5min:sector:", ""))
    return list(codes)


def _last_n_dates(klines: list[KBar], n: int) -> set:
    if n <= 0:
        return set()
    dates, seen = [], set()
    for b in sorted(klines, key=lambda b: b.timestamp):
        d = datetime.fromtimestamp(b.timestamp / 1000).date()
        if d not in seen:
            seen.add(d)
            dates.append(d)
    return set(dates[-n:])


def _filter_dates(klines: list[KBar], dates: set) -> list[KBar]:
    if not dates:
        return []
    return [b for b in klines
            if datetime.fromtimestamp(b.timestamp / 1000).date() in dates]
