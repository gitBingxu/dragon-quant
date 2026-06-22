"""scorers_v2.anti_drop — 抗跌性 (15%)。

txt："大盘跳水时它能横盘稳住，大盘一旦企稳它第一个起飞 / 扛住分歧不死"。
当日盘中行为（非历史多日）。双基准：大盘 MARKET_W .6 + 板块 SECTOR_W .4。
单维内：横盘稳住 HOLD_W .6 + 率先起飞 REBOUND_W .4。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from dragon_quant.cache.data_cache import DataCache
from dragon_quant.models.types import KBar, ScoreResult
from dragon_quant.scorers_v2 import registry as R
from dragon_quant.scorers_v2.base import clip, common_minute_axis, gain_curve

DIM = "anti_drop"
WEIGHT = R.DIM_WEIGHTS[DIM]
EPS = 1e-9


def score(code: str, cache: DataCache, primary_sector: str = "",
          **kwargs) -> ScoreResult:
    stock: list[KBar] = cache.get(f"kline:1min:{code}") or []
    market: list[KBar] = cache.get("kline:1min:000001") or []
    sector: list[KBar] = cache.get(f"kline:1min:sector:{primary_sector}") or []

    if not stock:
        return ScoreResult(dim=DIM, score=R.ANTIDROP_NEUTRAL, weight=WEIGHT,
                           details={"degraded": True, "reason": "个股1分K缺失"})

    s_market, d_market = _antidrop_vs(market, stock)
    s_sector, d_sector = _antidrop_vs(sector, stock)

    if d_market.get("degraded") and d_sector.get("degraded"):
        total = R.ANTIDROP_NEUTRAL
    else:
        total = clip(s_market * R.MARKET_W + s_sector * R.SECTOR_W)
    return ScoreResult(
        dim=DIM, score=round(total, 2), weight=WEIGHT,
        details={"s_market": round(s_market, 2), "s_sector": round(s_sector, 2),
                 "market": d_market, "sector": d_sector},
    )


def _antidrop_vs(base: list[KBar], stock: list[KBar]) -> tuple[float, dict]:
    """以 base（大盘/板块）为基准，评估 stock 的当日抗跌韧性。返回 (score, details)。"""
    if not base:
        return R.ANTIDROP_NEUTRAL, {"degraded": True, "reason": "基准1分K缺失"}

    axis = common_minute_axis(base, stock)
    g_x = gain_curve(base, axis)
    g_s = gain_curve(stock, axis)

    segs = _dip_segments(g_x)
    if not segs:
        return R.ANTIDROP_NEUTRAL, {"no_dip": True}

    # ── 横盘稳住：各跳水段按基准跌幅加权 ──
    hold_num = 0.0
    hold_den = 0.0
    dip_events = []
    for a, b in segs:
        gx_a, gx_b = g_x[a], g_x[b]
        gs_a, gs_b = g_s[a], g_s[b]
        if None in (gx_a, gx_b, gs_a, gs_b):
            continue
        d_x = gx_a - gx_b  # >0
        if d_x <= EPS:
            continue
        d_s = gs_a - gs_b
        ratio = d_s / max(d_x, EPS)
        s_hold_seg = clip((1.0 - ratio) * 100.0)
        hold_num += s_hold_seg * d_x
        hold_den += d_x
        dip_events.append({
            "start_time": _fmt_minute_bucket(axis[a]),
            "bottom_time": _fmt_minute_bucket(axis[b]),
            "base_drop_pct": round((gx_b - gx_a) * 100, 2),
            "stock_change_pct": round((gs_b - gs_a) * 100, 2),
            "stock_drop_pct": round(d_s * 100, 2),
            "hold_score": round(s_hold_seg, 2),
            "base_drop_abs": round(d_x * 100, 2),
        })
    s_hold = (hold_num / hold_den) if hold_den > 0 else R.ANTIDROP_NEUTRAL

    # ── 率先起飞：取最深跳水段的底部 b，看个股领先见底 + 反弹更猛 ──
    deepest = max(segs, key=lambda ab: (g_x[ab[0]] - g_x[ab[1]])
                  if None not in (g_x[ab[0]], g_x[ab[1]]) else -1)
    s_rebound = _rebound(g_x, g_s, deepest[1])
    deepest_event = None
    if dip_events:
        deepest_event = max(dip_events, key=lambda e: e.get("base_drop_abs", 0))

    s_dim = clip(s_hold * R.HOLD_W + s_rebound * R.REBOUND_W)
    return s_dim, {"n_dip_seg": len(segs), "s_hold": round(s_hold, 2),
                   "s_rebound": round(s_rebound, 2),
                   "dip_events": dip_events[:3],
                   "deepest_event": deepest_event}


def _fmt_minute_bucket(bucket: int) -> str:
    return datetime.fromtimestamp(bucket * 60).strftime("%H:%M")


def _dip_segments(g_x: list[Optional[float]]) -> list[tuple[int, int]]:
    """识别基准跳水段：滑窗净跌幅 Δd < DIP_TH 的连续分钟合并为 [a,b]。

    a=起跌点（窗口起），b=段内最低点。
    """
    w = R.DIP_WIN
    th = R.DIP_TH / 100.0
    n = len(g_x)
    falling = [False] * n
    for t in range(w, n):
        if g_x[t] is None or g_x[t - w] is None:
            continue
        if (g_x[t] - g_x[t - w]) < th:
            for k in range(t - w, t + 1):
                falling[k] = True
    segs: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if falling[i]:
            j = i
            while j + 1 < n and falling[j + 1]:
                j += 1
            # 段内最低点
            lo_idx = i
            lo_val = None
            for k in range(i, j + 1):
                if g_x[k] is None:
                    continue
                if lo_val is None or g_x[k] < lo_val:
                    lo_val = g_x[k]
                    lo_idx = k
            segs.append((i, lo_idx))
            i = j + 1
        else:
            i += 1
    return segs


def _rebound(g_x, g_s, b: int) -> float:
    """率先起飞：早见底 REBOUND_LEAD_W + 反弹幅度 REBOUND_AMP_W。"""
    L = R.REBOUND_LEAD_BARS
    n = len(g_x)
    # 个股触底分钟（基准底 b 附近 ±L 窗口内 g_s 最低点）
    lo = max(0, b - L)
    hi = min(n - 1, b + L)
    tb_s = b
    val = None
    for k in range(lo, hi + 1):
        if g_s[k] is None:
            continue
        if val is None or g_s[k] < val:
            val = g_s[k]
            tb_s = k
    lead = clip(b - tb_s, 0, L)  # 个股更早见底=领先

    # 反弹幅度比：b 后 L 根内回升
    end = min(n - 1, b + L)
    up_x = _rise(g_x, b, end)
    up_s = _rise(g_s, b, end)
    amp = clip(up_s / max(up_x, EPS), 0, 2)

    return clip((lead / L) * 100.0 * R.REBOUND_LEAD_W
                + (amp / 2.0) * 100.0 * R.REBOUND_AMP_W)


def _rise(g, a: int, b: int) -> float:
    if g[a] is None:
        return 0.0
    mx = g[a]
    for k in range(a, b + 1):
        if g[k] is not None and g[k] > mx:
            mx = g[k]
    return max(0.0, mx - g[a])
