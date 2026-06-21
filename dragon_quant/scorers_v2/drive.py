"""scorers_v2.drive — 带动性 (30%)。

txt："它一涨停整个板块的小弟全都跟风高潮 / 观察哪支股票最先涨停并稳定封死，
同时观察同板块个股是否能跟随他拉升"。仅当日盘面。
三个子因子各满分100：封板最早 EARLY_W .40 + 带动板块 LEAD_W .35 + 板块共鸣 VOICE_W .25。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from dragon_quant.cache.data_cache import DataCache
from dragon_quant.models.types import Candidate, KBar, Quote, ScoreResult, StockInfo
from dragon_quant.scorers_v2 import registry as R
from dragon_quant.scorers_v2.base import clip, common_minute_axis, gain_curve

DIM = "drive"
WEIGHT = R.DIM_WEIGHTS[DIM]
EPS = 1e-9


def score(code: str, cache: DataCache, primary_sector: str = "",
          candidate_pool: Optional[list[Candidate]] = None,
          **kwargs) -> ScoreResult:
    minute: list[KBar] = cache.get(f"kline:1min:{code}") or []
    sector_min: list[KBar] = cache.get(f"kline:1min:sector:{primary_sector}") or []
    components: list[StockInfo] = cache.get(f"sector:components:{primary_sector}") or []
    quotes: list[Quote] = cache.get("quotes:batch") or []
    qmap = {q.code: q for q in quotes}

    s_early, d_early = _early_seal(code, cache, primary_sector, components, qmap)
    s_lead, d_lead = _lead_sector(minute, sector_min)
    s_voice, d_voice = _voice(components, qmap)

    total = clip(s_early * R.EARLY_W + s_lead * R.LEAD_W + s_voice * R.VOICE_W)
    return ScoreResult(
        dim=DIM, score=round(total, 2), weight=WEIGHT,
        details={"s_early": round(s_early, 2), "early": d_early,
                 "s_lead": round(s_lead, 2), "lead": d_lead,
                 "s_voice": round(s_voice, 2), "voice": d_voice},
    )


# ─── 子因子①：封板最早（板块涨停封板池内相对排名）───

def _early_seal(code, cache, sector, components, qmap) -> tuple[float, dict]:
    # 板块当日涨停股集合（含本股）
    limit_codes = [s.code for s in components
                   if s.code in qmap and qmap[s.code].pct >= R.LIMIT_UP_PCT]
    if code not in limit_codes and qmap.get(code) and qmap[code].pct >= R.LIMIT_UP_PCT:
        limit_codes.append(code)
    pool = {}
    for c in limit_codes:
        mk: list[KBar] = cache.get(f"kline:1min:{c}") or []
        q = qmap.get(c)
        if mk and q and q.limit_up > 0:
            t = _first_seal_minute(mk, q.limit_up)
            if t is not None:
                pool[c] = t
    if code not in pool:
        # 本股未封板（或无1分K）→ 0
        q = qmap.get(code)
        return 0.0, {"sealed": False, "pool_size": len(pool),
                     "bid1_volume": q.bid1_volume if q else 0,
                     "bid1_price": q.bid1_price if q else 0}
    q = qmap.get(code)
    seal_minute = pool[code]
    detail = {"sealed": True, "seal_minute": seal_minute,
              "seal_time": _fmt_minute_bucket(seal_minute),
              "bid1_volume": q.bid1_volume if q else 0,
              "bid1_price": q.bid1_price if q else 0,
              "limit_up": q.limit_up if q else 0}
    if len(pool) == 1:
        return 100.0, {**detail, "rank": 1, "pool_size": 1}
    # 封板时点升序排名（越早 rank 越小）
    order = sorted(pool.items(), key=lambda kv: kv[1])
    rank = [c for c, _ in order].index(code) + 1
    n = len(pool)
    s = (1.0 - rank / n) * 100.0
    return clip(s), {**detail, "rank": rank, "pool_size": n}


def _first_seal_minute(minute: list[KBar], limit_up: float) -> Optional[int]:
    """首次封死涨停的分钟 bucket（close 到达涨停价）。"""
    eps = limit_up * 0.001
    for bar in minute:
        if bar.close >= limit_up - eps:
            return bar.timestamp // 60_000
    return None


def _fmt_minute_bucket(bucket: int) -> str:
    return datetime.fromtimestamp(bucket * 60).strftime("%H:%M")


# ─── 子因子②：带动板块（脉冲-跟随因果检测）───

def _lead_sector(stock: list[KBar], sector: list[KBar]) -> tuple[float, dict]:
    if not stock or not sector:
        return 40.0, {"degraded": True, "reason": "个股或板块1分K缺失"}
    axis = common_minute_axis(stock, sector)
    g_s = gain_curve(stock, axis)
    g_b = gain_curve(sector, axis)
    n = len(axis)
    w = R.THRUST_WIN
    L = R.LEAD_FOLLOW_BARS
    thrust = R.THRUST_PCT / 100.0
    follow_th = R.SECTOR_FOLLOW_PCT / 100.0

    # ① 识别个股拉升脉冲起点 t0
    thrusts: list[dict] = []
    # 开盘跳空/一字高开
    if g_s[0] is not None and g_s[0] >= thrust:
        thrusts.append({"start": 0, "trigger": 0})
    last_peak = -10
    for t in range(w, n):
        if g_s[t] is None or g_s[t - w] is None:
            continue
        dh = g_s[t] - g_s[t - w]
        if dh >= thrust and (t - last_peak) > w:
            thrusts.append({"start": t - w, "trigger": t})
            last_peak = t

    n_lead = 0
    n_follow = 0
    lead_events = []
    for thrust_event in thrusts:
        t0 = thrust_event["start"]
        if g_b[t0] is None:
            continue
        # ② 板块跟随振幅
        seg = [g_b[t0 + k] for k in range(1, L + 1) if t0 + k < n and g_b[t0 + k] is not None]
        if not seg:
            continue
        dh_b = max(seg) - g_b[t0]
        # ③ 因果：跟随达标 + 板块未抢跑
        pre = [g_b[t0 - k] for k in range(1, L + 1) if t0 - k >= 0 and g_b[t0 - k] is not None]
        no_frontrun = (g_b[t0] - min(pre)) < follow_th if pre else True
        if dh_b >= follow_th and no_frontrun:
            n_lead += 1
            lead_events.append(_build_lead_event(axis, g_s, g_b, t0,
                                                 thrust_event.get("trigger", t0), L))
        elif dh_b >= follow_th and not no_frontrun:
            n_follow += 1

    # ④ 也检测「板块先达标、个股后拉」的被带动
    follow_events = _detect_follow_events(axis, g_s, g_b, n, w, L, thrust, follow_th)
    n_follow += len(follow_events)

    # ⑤ 打分
    if n_lead == 0:
        s_lead = 0.0  # 纯跟风票，follow 不再扣分
    else:
        base = R.LEAD_BASE + (n_lead - 1) * R.LEAD_STEP
        penalty = min(n_follow * R.FOLLOW_PENALTY, R.FOLLOW_PENALTY_CAP)
        s_lead = clip(base - penalty)

    # ⑥ 整体方向 bonus（时滞互相关）
    bonus = _corr_bonus(g_s, g_b, L)
    if n_lead >= 1 and bonus > 0:
        s_lead = clip(s_lead + bonus)

    return s_lead, {"n_lead": n_lead, "n_follow": n_follow,
                    "n_thrust": len(thrusts), "bonus": bonus,
                    "suspect_follower": n_lead == 0 and n_follow > 0,
                    "lead_events": lead_events[:3],
                    "follow_events": follow_events[:3]}


def _build_lead_event(axis, g_s, g_b, t0: int, trigger: int, L: int) -> dict:
    """构造“个股先拉、板块跟随”的报告事件。"""
    end = min(len(axis), t0 + L + 1)
    sector_points = [(i, g_b[i]) for i in range(t0 + 1, end) if g_b[i] is not None]
    stock_points = [(i, g_s[i]) for i in range(t0, end) if g_s[i] is not None]
    sector_peak_idx, sector_peak = max(sector_points, key=lambda kv: kv[1]) if sector_points else (t0, g_b[t0])
    stock_peak_idx, stock_peak = max(stock_points, key=lambda kv: kv[1]) if stock_points else (trigger, g_s[trigger])
    stock_base = g_s[t0] or 0.0
    sector_base = g_b[t0] or 0.0
    return {
        "event_time": _fmt_minute_bucket(axis[t0]),
        "trigger_time": _fmt_minute_bucket(axis[trigger]),
        "stock_peak_time": _fmt_minute_bucket(axis[stock_peak_idx]),
        "sector_peak_time": _fmt_minute_bucket(axis[sector_peak_idx]),
        "stock_gain_pct": round(((stock_peak or stock_base) - stock_base) * 100, 2),
        "sector_gain_pct": round(((sector_peak or sector_base) - sector_base) * 100, 2),
    }


def _detect_follow_events(axis, g_s, g_b, n, w, L, thrust, follow_th) -> list[dict]:
    """板块先拉、个股随后跟（个股被带动）。"""
    events = []
    last = -10
    for t in range(w, n):
        if g_b[t] is None or g_b[t - w] is None:
            continue
        if (g_b[t] - g_b[t - w]) < follow_th or (t - last) <= w:
            continue
        t0 = t - w
        seg = [g_s[t0 + k] for k in range(1, L + 1)
               if t0 + k < n and g_s[t0 + k] is not None]
        if seg and g_s[t0] is not None and (max(seg) - g_s[t0]) >= thrust:
            stock_peak_offset, stock_peak = max(enumerate(seg, start=1), key=lambda kv: kv[1])
            stock_follow_idx = min(t0 + stock_peak_offset, n - 1)
            events.append({
                "sector_event_time": _fmt_minute_bucket(axis[t0]),
                "sector_trigger_time": _fmt_minute_bucket(axis[t]),
                "stock_follow_time": _fmt_minute_bucket(axis[stock_follow_idx]),
                "sector_gain_pct": round((g_b[t] - g_b[t0]) * 100, 2),
                "stock_gain_pct": round((stock_peak - g_s[t0]) * 100, 2),
            })
            last = t
    return events


def _corr_bonus(g_s, g_b, L) -> float:
    """时滞互相关：个股领先板块 τ*>0 且 ρ≥CORR_TH → +CORR_BONUS。"""
    rs = _returns(g_s)
    rb = _returns(g_b)
    if len(rs) < 5:
        return 0.0
    best_tau, best_rho = 0, -2.0
    for tau in range(0, L + 1):
        rho = _corr(rs, rb, tau)
        if rho is not None and rho > best_rho:
            best_rho, best_tau = rho, tau
    if best_tau > 0 and best_rho >= R.CORR_TH:
        return R.CORR_BONUS
    return 0.0


def _returns(g) -> list[Optional[float]]:
    out = [None]
    for t in range(1, len(g)):
        if g[t] is None or g[t - 1] is None:
            out.append(None)
        else:
            out.append(g[t] - g[t - 1])
    return out


def _corr(rs, rb, tau) -> Optional[float]:
    xs, ys = [], []
    for t in range(len(rs)):
        if t + tau >= len(rb):
            break
        a, b = rs[t], rb[t + tau]
        if a is None or b is None:
            continue
        xs.append(a); ys.append(b)
    if len(xs) < 5:
        return None
    mx = sum(xs) / len(xs); my = sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    if vx <= EPS or vy <= EPS:
        return None
    return cov / (vx * vy)


# ─── 子因子③：板块共鸣 ───

def _voice(components: list[StockInfo], qmap) -> tuple[float, dict]:
    actives = [s for s in components if s.code in qmap]
    n = len(actives)
    if n == 0:
        return 0.0, {"degraded": True, "n": 0}
    n_limit = sum(1 for s in actives if qmap[s.code].pct >= R.LIMIT_UP_PCT)
    n_strong = sum(1 for s in actives if qmap[s.code].pct >= R.FOLLOW_PCT)
    limit_ratio = n_limit / n
    strong_ratio = n_strong / n
    s_limit = clip(limit_ratio / R.VOICE_FULL, 0, 1) * 100.0
    s_strong = clip(strong_ratio / R.FOLLOW_FULL, 0, 1) * 100.0
    s_voice = s_limit * R.VOICE_LIMIT_W + s_strong * R.VOICE_STRONG_W
    return clip(s_voice), {"n": n, "n_limit": n_limit, "n_strong": n_strong,
                           "limit_ratio": round(limit_ratio, 3),
                           "strong_ratio": round(strong_ratio, 3)}
