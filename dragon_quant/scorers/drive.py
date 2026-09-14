"""带动性：稳定封板排名、个股领先板块、板块共鸣。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from dragon_quant.cache.data_cache import DataCache
from dragon_quant.models.types import Candidate, KBar, Quote, ScoreResult, StockInfo
from dragon_quant.scorers import registry as R
from dragon_quant.scorers.base import (
    CHINA_TZ, at_limit, clip, common_minute_axis, continuous_ranges, gain_curve,
)

DIM = "drive"
WEIGHT = R.DIM_WEIGHTS[DIM]
EPS = 1e-9


def score(code: str, cache: DataCache, primary_sector: str = "",
          candidate_pool: Optional[list[Candidate]] = None, **kwargs) -> ScoreResult:
    minute = cache.get(f"kline:1min:{code}") or []
    sector_min = cache.get(f"kline:1min:sector:{primary_sector}") or []
    components = cache.get(f"sector:components:{primary_sector}") or []
    qmap = {q.code: q for q in cache.get("quotes:batch") or []}
    s_early, d_early = _early_seal(code, cache, primary_sector, components, qmap,
                                  candidate_pool)
    quote = qmap.get(code)
    s_lead, d_lead = _lead_sector(minute, sector_min, quote.limit_up if quote else 0)
    s_voice, d_voice = _voice(components, qmap)
    total = clip(s_early * R.EARLY_W + s_lead * R.LEAD_W + s_voice * R.VOICE_W)
    return ScoreResult(
        dim=DIM, score=round(total, 2), weight=WEIGHT,
        details={"s_early": round(s_early, 2), "early": d_early,
                 "s_lead": round(s_lead, 2), "lead": d_lead,
                 "s_voice": round(s_voice, 2), "voice": d_voice,
                 "degraded": any(d.get("degraded") for d in (d_early, d_lead, d_voice))},
    )


def _early_seal(code, cache, sector, components, qmap, candidate_pool=None):
    component_codes = {s.code for s in components}
    if candidate_pool is None:
        limit_codes = component_codes | {code}
    else:
        limit_codes = {c.code for c in candidate_pool
                       if c.code in component_codes or c.code == code}
    limit_codes = {c for c in limit_codes
                   if c not in qmap or qmap[c].pct >= R.LIMIT_UP_PCT}
    pool = {}
    missing = []
    for c in sorted(limit_codes):
        minute = cache.get(f"kline:1min:{c}") or []
        quote = qmap.get(c)
        if not minute or not quote or quote.limit_up <= 0:
            missing.append(c)
            continue
        sealed = _first_seal_minute(minute, quote.limit_up)
        if sealed is not None:
            pool[c] = sealed
    quote = qmap.get(code)
    detail = {"pool_size": len(pool), "sample_size": len(limit_codes),
              "missing_codes": missing, "degraded": bool(missing),
              "bid1_volume": quote.bid1_volume if quote else 0,
              "bid1_price": quote.bid1_price if quote else 0,
              "limit_up": quote.limit_up if quote else 0}
    if not quote or quote.limit_up <= 0 or not cache.get(f"kline:1min:{code}"):
        return R.DRIVE_NEUTRAL, {**detail, "sealed": False, "degraded": True,
                                 "reason": "个股分时或涨停行情缺失"}
    if code not in pool:
        return 0.0, {**detail, "sealed": False}
    seal_minute = pool[code]
    rank = 1 + sum(t < seal_minute for t in pool.values())
    result = 100.0 if len(pool) == 1 else (1 - rank / len(pool)) * 100
    return result, {**detail, "sealed": True, "seal_minute": seal_minute,
                    "seal_time": _fmt_minute_bucket(seal_minute), "rank": rank}


def _first_seal_minute(minute: list[KBar], limit_up: float) -> Optional[int]:
    sealed = None
    for bar in sorted(minute, key=lambda b: b.timestamp):
        if at_limit(bar.close, limit_up):
            if sealed is None:
                sealed = bar.timestamp // 60_000
        else:
            sealed = None
    return sealed


def _fmt_minute_bucket(bucket: int) -> str:
    return datetime.fromtimestamp(bucket * 60, CHINA_TZ).strftime("%H:%M")


def _pulses(g, w, threshold, opening=False):
    candidates = []
    for t in range(w, len(g)):
        if any(v is None for v in g[t - w:t + 1]):
            continue
        gain = g[t] - g[t - w]
        if gain + EPS < threshold:
            continue
        onset = next(k for k in range(t - w + 1, t + 1) if g[k] > g[k - 1] + EPS)
        candidates.append({"base": t - w, "start": onset, "trigger": t, "gain": gain})
    selected = []
    for pulse in sorted(candidates, key=lambda p: (-p["gain"], p["trigger"])):
        if all(abs(pulse["trigger"] - p["trigger"]) > w for p in selected):
            selected.append(pulse)
    if opening and g and g[0] is not None and g[0] >= threshold:
        selected.append({"base": 0, "start": 0, "trigger": 0, "gain": g[0]})
    return sorted(selected, key=lambda p: p["start"])


def _lead_sector(stock: list[KBar], sector: list[KBar], limit_up=0):
    if not stock or not sector:
        return R.DRIVE_NEUTRAL, {"degraded": True, "reason": "个股或板块1分K缺失"}
    axis = common_minute_axis(stock, sector)
    g_s, g_b = gain_curve(stock, axis), gain_curve(sector, axis)
    values = [g for g in g_s if g is not None]
    if not values or max(values) - min(values) <= EPS:
        return R.DRIVE_NEUTRAL, {"degraded": True, "reason": "个股全程平线，无法验证带动时序"}
    L, w = R.LEAD_FOLLOW_BARS, R.THRUST_WIN
    thrust, follow_th = R.THRUST_PCT / 100, R.SECTOR_FOLLOW_PCT / 100
    first_touch = next((b.timestamp // 60_000 for b in sorted(stock, key=lambda b: b.timestamp)
                        if at_limit(b.close, limit_up)), None)
    lead_events, follow_events = [], []
    n_thrust = 0
    bonus = 0.0
    for start, end in continuous_ranges(axis):
        sa, sb, times = g_s[start:end], g_b[start:end], axis[start:end]
        pulses = _pulses(sa, w, thrust, opening=start == 0)
        sector_pulses = _pulses(sb, w, follow_th, opening=start == 0)
        n_thrust += len(pulses)
        used = set()
        for pulse in pulses:
            t = pulse["start"]
            matches = [(i, p) for i, p in enumerate(sector_pulses)
                       if i not in used and abs(p["start"] - t) <= L]
            if not matches:
                continue
            idx, peer = min(matches, key=lambda ip: (abs(ip[1]["start"] - t), ip[1]["start"]))
            used.add(idx)
            delta = peer["start"] - t
            if delta == 0:
                continue
            base = max(0, t - 1)
            if sb[base] is None:
                continue
            pre = [v for v in sb[max(0, base - L):base] if v is not None]
            no_frontrun = not pre or sb[base] - min(pre) < follow_th
            event = {
                "event_time": _fmt_minute_bucket(times[t]),
                "trigger_time": _fmt_minute_bucket(times[pulse["trigger"]]),
                "sector_event_time": _fmt_minute_bucket(times[peer["start"]]),
                "sector_trigger_time": _fmt_minute_bucket(times[peer["trigger"]]),
                "stock_follow_time": _fmt_minute_bucket(times[t]),
                "stock_gain_pct": round(pulse["gain"] * 100, 2),
                "sector_gain_pct": round(peer["gain"] * 100, 2),
            }
            after = [v for v in sb[t:min(len(sb), t + L + 1)] if v is not None]
            if delta > 0 and no_frontrun and after and max(after) - sb[base] + EPS >= follow_th:
                lead_events.append(event)
            elif delta < 0:
                follow_events.append(event)
        cutoff = next((i for i, ts in enumerate(times) if first_touch is not None and ts >= first_touch), len(times))
        bonus = max(bonus, _corr_bonus(sa[:cutoff], sb[:cutoff], L))
    n_lead, n_follow = len(lead_events), len(follow_events)
    result = 0.0
    if n_lead:
        base = min(100.0, R.LEAD_BASE + (n_lead - 1) * R.LEAD_STEP)
        result = clip(base - min(n_follow * R.FOLLOW_PENALTY, R.FOLLOW_PENALTY_CAP) + bonus)
    return result, {"n_lead": n_lead, "n_follow": n_follow, "n_thrust": n_thrust,
                    "bonus": bonus if n_lead else 0.0,
                    "suspect_follower": n_lead == 0 and n_follow > 0,
                    "lead_events": lead_events[:3], "follow_events": follow_events[:3]}


def _corr_bonus(g_s, g_b, L) -> float:
    rs, rb = _returns(g_s), _returns(g_b)
    best_tau, best_rho = 0, -2.0
    for tau in range(L + 1):
        rho = _corr(rs, rb, tau)
        if rho is not None and rho > best_rho:
            best_rho, best_tau = rho, tau
    return R.CORR_BONUS if best_tau > 0 and best_rho >= R.CORR_TH else 0.0


def _returns(g):
    return [None] + [None if g[t] is None or g[t - 1] is None else g[t] - g[t - 1]
                     for t in range(1, len(g))]


def _corr(rs, rb, tau):
    pairs = [(a, rb[t + tau]) for t, a in enumerate(rs)
             if t + tau < len(rb) and a is not None and rb[t + tau] is not None]
    if len(pairs) < 5:
        return None
    xs, ys = zip(*pairs)
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in pairs)
    vx, vy = sum((x - mx) ** 2 for x in xs) ** .5, sum((y - my) ** 2 for y in ys) ** .5
    return None if vx <= EPS or vy <= EPS else cov / (vx * vy)


def _voice(components: list[StockInfo], qmap):
    actives = [s for s in components if s.code in qmap]
    n = len(actives)
    if not n:
        return R.DRIVE_NEUTRAL, {"degraded": True, "reason": "板块行情缺失", "n": 0}
    n_limit = sum(qmap[s.code].pct >= R.LIMIT_UP_PCT for s in actives)
    n_strong = sum(qmap[s.code].pct > R.FOLLOW_PCT for s in actives)
    limit_ratio, strong_ratio = n_limit / n, n_strong / n
    result = (clip(limit_ratio / R.VOICE_FULL, 0, 1) * 100 * R.VOICE_LIMIT_W
              + clip(strong_ratio / R.FOLLOW_FULL, 0, 1) * 100 * R.VOICE_STRONG_W)
    return result, {"n": n, "sample_size": len(components), "n_limit": n_limit,
                    "n_strong": n_strong, "degraded": n < len(components),
                    "limit_ratio": round(limit_ratio, 3), "strong_ratio": round(strong_ratio, 3)}
