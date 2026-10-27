"""auction.strength — 集合竞价强度评分：一票否决闸门 + 四维加权。

纯函数、无副作用、不发请求。输入 items 为 TDX get_auction 原始明细
``[{'time','price','matched','unmatched'}, ...]``，quote 为腾讯行情（可为 None）。
数据语义（符号方向/累计 vs 逐笔）待实测，见 registry 注释。
"""

from __future__ import annotations

from typing import Optional

from dragon_quant.infra.models import Quote, ScoreResult
from . import registry as R
from dragon_quant.review_account.auction.types import AuctionVerdict


def _to_sec(ts) -> int:
    """'HH:MM:SS' 字符串或 datetime.time → 自 0 点起的秒数。"""
    if isinstance(ts, str):
        try:
            h, m, s = ts.split(":")
            return int(h) * 3600 + int(m) * 60 + int(s)
        except ValueError:
            return 0
    if hasattr(ts, "hour"):
        return ts.hour * 3600 + ts.minute * 60 + ts.second
    return 0


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _bucket_score(value: float, buckets) -> float:
    """分桶布尔化：value 落入首个匹配桶返回该桶分（不插值，防过拟合）。"""
    for lo, hi, score in buckets:
        if value <= hi:
            return score
    return buckets[-1][2]


def _lin_slope(ys: list[float]) -> tuple[float, float]:
    """最小二乘斜率与决定系数 (k, R²)。"""
    n = len(ys)
    if n < 2:
        return 0.0, 0.0
    xs = list(range(n))
    xm = sum(xs) / n
    ym = sum(ys) / n
    sxx = sum((x - xm) ** 2 for x in xs)
    sxy = sum((xs[i] - xm) * (ys[i] - ym) for i in range(n))
    if sxx == 0:
        return 0.0, 0.0
    k = sxy / sxx
    b = ym - k * xm
    ss_tot = sum((y - ym) ** 2 for y in ys)
    ss_res = sum((ys[i] - (k * xs[i] + b)) ** 2 for i in range(n))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return k, r2


def normalize(items: list[dict]) -> dict:
    """排序 + 增量序列 + 末段/前段窗口切片。

    按 time 升序；matched/unmatched 依 registry 的「累计/逐笔」约定转增量；
    buy_side = unmatched * UNMATCHED_SIGN（>0 买单/红柱）。末段默认
    09:24:30–09:25:00，根数 < TAIL_MIN_BARS 时扩窗至 09:24:00。
    """
    rows = sorted(items, key=lambda r: _to_sec(r.get("time", "")))
    times = [_to_sec(r.get("time", "")) for r in rows]
    prices = [float(r.get("price", 0.0)) for r in rows]
    matched = [float(r.get("matched", 0.0)) for r in rows]
    unmatched = [float(r.get("unmatched", 0.0)) for r in rows]

    if R.MATCHED_CUMULATIVE and len(matched) > 1:
        delta_matched = [matched[0]] + [matched[i] - matched[i - 1] for i in range(1, len(matched))]
        total_matched = matched[-1]
    else:
        delta_matched = list(matched)
        total_matched = sum(matched)

    if R.UNMATCHED_CUMULATIVE and len(unmatched) > 1:
        delta_unmatched = [unmatched[0]] + [unmatched[i] - unmatched[i - 1] for i in range(1, len(unmatched))]
    else:
        delta_unmatched = list(unmatched)
    buy_side = [u * R.UNMATCHED_SIGN for u in delta_unmatched]

    tail_idx = [i for i, t in enumerate(times) if R.TAIL_DEFAULT_START <= t <= R.AUCTION_CLOSE]
    if len(tail_idx) < R.TAIL_MIN_BARS:
        tail_idx = [i for i, t in enumerate(times) if R.TAIL_EXTENDED_START <= t <= R.AUCTION_CLOSE]
    # 前段固定 9:20–9:24:00（方法论「9:20–9:23」口径），不随末段扩窗漂移
    front_idx = [i for i, t in enumerate(times) if R.NO_CANCEL <= t < R.TAIL_EXTENDED_START]

    return {
        "times": times,
        "prices": prices,
        "matched": matched,
        "unmatched": unmatched,
        "buy_side": buy_side,
        "delta_matched": delta_matched,
        "total_matched": total_matched,
        "last_price": prices[-1] if prices else 0.0,
        "tail_idx": tail_idx,
        "front_idx": front_idx,
    }


# ─── 否决闸门（Layer 0，评估顺序即优先级 0.4→0.1→0.2→0.3，首个命中短路）───

def _reject_0_4(data: dict, quote: Optional[Quote]):
    """跌停价排队卖。子条件(b) TDX-only 卖单极值放大；子条件(a) 价格贴跌停。"""
    tail_buy = [data["buy_side"][i] for i in data["tail_idx"]]
    front_buy = [data["buy_side"][i] for i in data["front_idx"]]
    if tail_buy:
        tail_min = min(tail_buy)
        front_min = min(front_buy) if front_buy else 0.0
        if tail_min < 0 and front_min < 0 and tail_min < front_min * R.REJECT_TAIL_NEG_RATIO:
            return ("0.4 跌停价排队卖", False)
    limit_down = quote.limit_down if quote else 0.0
    if limit_down > 0:
        tail_prices = [data["prices"][i] for i in data["tail_idx"]]
        if tail_prices and min(tail_prices) <= limit_down * (1 + R.PRICE_TOLERANCE):
            return ("0.4 跌停价排队卖", False)
        return None
    # quote 缺失 / limit_down 不可得，且子条件(b)未命中 → 保守否决
    return ("0.4 数据不足，无法清除否决", True)


def _reject_0_1(data: dict, quote: Optional[Quote]):
    """高位绿柱递增（逃顶）：竞价涨幅 > +3% 且末段卖单量单调递增。"""
    prev_close = quote.prev_close if quote else 0.0
    if prev_close <= 0 or data["last_price"] <= 0:
        return None
    pct = (data["last_price"] / prev_close - 1.0) * 100.0
    if pct <= R.REJECT_HIGH_PCT:
        return None
    neg = [abs(b) for b in (data["buy_side"][i] for i in data["tail_idx"]) if b < 0]
    if len(neg) >= 2 and all(neg[i] >= neg[i - 1] for i in range(1, len(neg))):
        return ("0.1 高位绿柱递增", False)
    return None


def _reject_0_2(data: dict, quote: Optional[Quote]):
    """强转弱：9:20–9:23 买单为主 → 末段转卖单且负值扩大。"""
    front_buy = [data["buy_side"][i] for i in data["front_idx"]]
    tail_buy = [data["buy_side"][i] for i in data["tail_idx"]]
    if not front_buy or not tail_buy:
        return None
    if _median(front_buy) <= 0 or _median(tail_buy) >= 0:
        return None
    half = len(tail_buy) // 2
    if half >= 1 and _median(tail_buy[half:]) < _median(tail_buy[:half]):
        return ("0.2 强转弱", False)
    return None


def _reject_0_3(data: dict, quote: Optional[Quote]):
    """卡点撤单诱多：9:20 前大买单峰值 → 9:20 后骤降且价格未同步回落。"""
    times = data["times"]
    buy = data["buy_side"]
    pre_idx = [i for i, t in enumerate(times) if R.NO_CANCEL - 10 <= t < R.NO_CANCEL]
    peak = max((buy[i] for i in pre_idx if buy[i] > 0), default=0.0)
    if peak <= 0:
        return None
    front_buy = [buy[i] for i in data["front_idx"] if buy[i] > 0]
    if not front_buy or max(front_buy) >= peak * R.REJECT_DROP_RATIO:
        return None
    prices = data["prices"]
    peak_price = prices[pre_idx[-1]] if pre_idx else data["last_price"]
    if prices[-1] < peak_price * 0.99:
        return None
    return ("0.3 卡点撤单诱多", False)


def _reject_gate(data: dict, quote: Optional[Quote]):
    for fn in (_reject_0_4, _reject_0_1, _reject_0_2, _reject_0_3):
        r = fn(data, quote)
        if r is not None:
            return r
    return None


# ─── 四维打分（Layer 1）───

def _score_direction(code: str, data: dict, quote: Optional[Quote]) -> ScoreResult:
    w = R.AUCTION_DIM_WEIGHTS["direction"]
    prev_close = quote.prev_close if quote else 0.0
    if prev_close <= 0 or data["last_price"] <= 0:
        return ScoreResult(dim="direction", score=R.AUCTION_NEUTRAL, weight=w,
                           details={"degraded": True, "reason": "昨收缺失"})
    pct = (data["last_price"] / prev_close - 1.0) * 100.0
    s1 = _bucket_score(pct, R.HIGH_OPEN_BUCKETS)
    tp = [data["prices"][i] for i in data["tail_idx"]]
    s2 = R.AUCTION_NEUTRAL
    if len(tp) >= 2:
        k, r2 = _lin_slope(tp)
        if r2 >= R.SLOPE_R2_MIN:
            s2 = 100.0 if k > 0 else 0.0
    score = 0.5 * s1 + 0.5 * s2
    return ScoreResult(dim="direction", score=round(score, 2), weight=w,
                       details={"open_pct": round(pct, 2), "open_score": s1, "slope_score": s2})


def _score_supply(code: str, data: dict, quote: Optional[Quote]) -> ScoreResult:
    w = R.AUCTION_DIM_WEIGHTS["supply"]
    total = data["total_matched"]
    net = sum(data["buy_side"])
    imb = net / total if total > 0 else 0.0
    s13 = _clip(50.0 + imb * 100.0)
    tail_buy = [data["buy_side"][i] for i in data["tail_idx"]]
    front_buy = [data["buy_side"][i] for i in data["front_idx"]]
    tail_med = _median(tail_buy)
    front_med = _median(front_buy)
    s14 = 100.0 if tail_med > 0 else (0.0 if tail_med < 0 else R.AUCTION_NEUTRAL)
    weak_to_strong = front_med < 0 and tail_med > 0
    s15 = 100.0 if weak_to_strong else R.AUCTION_NEUTRAL
    score = 0.5 * s13 + 0.3 * s14 + 0.2 * s15
    return ScoreResult(dim="supply", score=round(score, 2), weight=w,
                       details={"imb": round(imb, 4), "tail_med": round(tail_med, 2),
                                "weak_to_strong": weak_to_strong})


def _score_vol_price(code: str, data: dict, quote: Optional[Quote]) -> ScoreResult:
    w = R.AUCTION_DIM_WEIGHTS["vol_price"]
    dm = data["delta_matched"]
    prices = data["prices"]
    dp = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    n = min(len(dm) - 1, len(dp))
    if n <= 0:
        return ScoreResult(dim="vol_price", score=R.AUCTION_NEUTRAL, weight=w,
                           details={"degraded": True, "reason": "样本不足"})
    agree = sum(1 for i in range(n) if (dm[i + 1] > 0 and dp[i] > 0) or (dm[i + 1] < 0 and dp[i] < 0))
    agree_ratio = agree / n
    s16 = agree_ratio * 100.0 if agree_ratio >= R.VOL_PRICE_AGREE_RATIO else agree_ratio * 50.0
    turnover = 0.0
    if quote and quote.float_market_cap > 0 and data["last_price"] > 0:
        float_shares = quote.float_market_cap / data["last_price"]
        if float_shares > 0:
            turnover = data["total_matched"] / float_shares * 100.0
    if data["total_matched"] <= 0:
        s17 = 0.0  # 零成交 → 0 分（弱），不给中性虚高
    elif turnover:
        s17 = _clip(turnover / 3.0 * 100.0)
    else:
        s17 = R.AUCTION_NEUTRAL  # 流通市值缺失，降级中性
    score = 0.6 * s16 + 0.4 * s17
    return ScoreResult(dim="vol_price", score=round(score, 2), weight=w,
                       details={"agree_ratio": round(agree_ratio, 3), "turnover_pct": round(turnover, 3)})


def _score_tail_grab(code: str, data: dict, quote: Optional[Quote]) -> ScoreResult:
    w = R.AUCTION_DIM_WEIGHTS["tail_grab"]
    tail = data["tail_idx"]
    front = data["front_idx"]
    if not tail or not front:
        return ScoreResult(dim="tail_grab", score=R.AUCTION_NEUTRAL, weight=w,
                           details={"degraded": True, "reason": "窗口不足"})
    dm_tail = [data["delta_matched"][i] for i in tail]
    dm_front = [data["delta_matched"][i] for i in front if i > 0]  # 排除首值（累计语义首值非增量）
    tail_mean = sum(dm_tail) / len(dm_tail)
    front_mean = sum(dm_front) / len(dm_front) if dm_front else 0.0
    spike = tail_mean > front_mean * R.TAIL_GRAB_RATIO
    if not spike:
        return ScoreResult(dim="tail_grab", score=R.AUCTION_NEUTRAL, weight=w, details={"event": "无"})
    tail_buy = [data["buy_side"][i] for i in tail]
    if tail_buy[-1] < tail_buy[0]:
        return ScoreResult(dim="tail_grab", score=100.0, weight=w, details={"event": "抢筹"})
    return ScoreResult(dim="tail_grab", score=0.0, weight=w, details={"event": "出货"})


_SCORERS = {
    "direction": _score_direction,
    "supply": _score_supply,
    "vol_price": _score_vol_price,
    "tail_grab": _score_tail_grab,
}


def _resolve_direction(dims: dict[str, ScoreResult]) -> str:
    d = dims.get("direction")
    s = dims.get("supply")
    if not d or not s:
        return "neutral"
    open_pct = d.details.get("open_pct", 0.0)
    tail_med = s.details.get("tail_med", 0.0)
    weak_to_strong = s.details.get("weak_to_strong", False)
    base = "bull" if open_pct > 0 else ("bear" if open_pct < 0 else "neutral")
    if weak_to_strong:
        return "bull"
    if tail_med > 0 and base == "bull":
        return "bull"
    if tail_med < 0 and base == "bear":
        return "bear"
    if tail_med > 0 and base == "bear":
        return "neutral"
    if tail_med < 0 and base == "bull":
        return "neutral"
    return base


def _level_of(strength: float) -> str:
    for th, name in R.STRENGTH_LEVELS:
        if strength >= th:
            return name
    return "弱"


def evaluate(code: str, items: list[dict], quote: Optional[Quote] = None) -> AuctionVerdict:
    """对单只个股竞价盘口做否决闸门 + 四维加权。"""
    if not items:
        return AuctionVerdict(code=code, is_weak=True, strength=0.0,
                              direction="neutral", level="弱", reject_reason="竞价明细缺失")
    try:
        data = normalize(items)
        reject = _reject_gate(data, quote)
    except Exception:
        return AuctionVerdict(code=code, is_weak=True, strength=0.0,
                              direction="neutral", level="弱",
                              reject_reason="数据异常", unverified=True)
    if reject is not None:
        reason, data_unverified = reject
        return AuctionVerdict(code=code, is_weak=True, strength=0.0,
                              direction="neutral", level="弱",
                              reject_reason=reason,
                              unverified=data_unverified or (not R.SEMANTICS_VERIFIED))
    dims: dict[str, ScoreResult] = {}
    for dim, fn in _SCORERS.items():
        try:
            dims[dim] = fn(code, data, quote)
        except Exception as e:
            dims[dim] = ScoreResult(dim=dim, score=R.AUCTION_NEUTRAL,
                                    weight=R.AUCTION_DIM_WEIGHTS[dim],
                                    details={"degraded": True, "error": str(e)})
    strength = sum(dims[d].score * R.AUCTION_DIM_WEIGHTS[d] for d in dims)
    return AuctionVerdict(
        code=code, is_weak=False, strength=round(strength, 2),
        direction=_resolve_direction(dims), level=_level_of(strength),
        dims=dims, unverified=not R.SEMANTICS_VERIFIED,
    )


def rank_auctions(verdicts: list[AuctionVerdict]) -> list[AuctionVerdict]:
    """对通过否决且已验证（unverified=False）的 verdicts 按 strength 降序赋 rank（其余 rank=None）。"""
    for v in verdicts:
        v.rank = None
    usable = [v for v in verdicts if not v.is_weak and not v.unverified]
    usable.sort(key=lambda v: (-v.strength, v.code))
    for i, v in enumerate(usable, 1):
        v.rank = i
    return verdicts
