"""冻结的旧四维评分实现副本（仅测试 / 离线对账，不进入生产包）。

来源：``dragon_quant/review_account/auction/strength.py`` @ commit ``4702b07``
（2026-10-29 竞价 v2 重建之前）。刻意**不从 git 历史动态导入**，保证可复现、
不依赖仓库状态。用于证明新旧差异都能由已知原因解释（方案 §14.3 / AC2）：
``unit_x100`` / ``full_window_sum`` / ``neutral_50`` / ``sampling_amplification`` /
``open_price_source`` / ``veto_missing_data``。
"""

from __future__ import annotations

WEIGHTS = {"direction": 0.25, "supply": 0.30, "vol_price": 0.25, "tail_grab": 0.20}
NEUTRAL = 50.0
HIGH_OPEN_BUCKETS = (
    (-100.0, 0.0, 20.0), (0.0, 3.0, 50.0), (3.0, 7.0, 80.0), (7.0, 100.0, 100.0),
)
PRICE_TOLERANCE = 0.001
TAIL_START = 9 * 3600 + 24 * 60 + 30
TAIL_END = 9 * 3600 + 25 * 60
NO_CANCEL = 9 * 3600 + 20 * 60


def _sec(value) -> int:
    if isinstance(value, str):
        h, m, s = value.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    return 0


def _median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def _bucket(value):
    for low, high, score in HIGH_OPEN_BUCKETS:
        if value <= high:
            return score
    return HIGH_OPEN_BUCKETS[-1][2]


def evaluate(code: str, items: list[dict], quote=None) -> dict:
    rows = sorted(items or [], key=lambda r: _sec(r.get("time", "")))
    if not rows:
        return {"code": code, "is_weak": True, "strength": 0.0,
                "dims": {}, "unverified": True,
                "reject_reason": "竞价明细缺失"}
    times = [_sec(r["time"]) for r in rows]
    prices = [float(r["price"]) for r in rows]
    matched = [float(r["matched"]) for r in rows]
    unmatched = [float(r["unmatched"]) for r in rows]
    tail = [i for i, t in enumerate(times) if TAIL_START <= t <= TAIL_END]
    front = [i for i, t in enumerate(times) if NO_CANCEL <= t < TAIL_START]

    # 旧否决 0.4：quote / limit_down 不可得即保守否决
    limit_down = getattr(quote, "limit_down", 0.0) if quote is not None else 0.0
    if not limit_down:
        return {"code": code, "is_weak": True, "strength": 0.0, "dims": {},
                "unverified": True, "reject_reason": "0.4 数据不足，无法清除否决",
                "dims_old": _old_dims(prices, matched, unmatched, tail, front, quote)}

    dims = _old_dims(prices, matched, unmatched, tail, front, quote)
    strength = round(sum(dims[d][0] * WEIGHTS[d] for d in dims), 2)
    return {"code": code, "is_weak": False, "strength": strength,
            "dims": dims, "unverified": True, "reject_reason": None}


def _old_dims(prices, matched, unmatched, tail, front, quote):
    prev_close = getattr(quote, "prev_close", 0.0) if quote is not None else 0.0
    last_price = prices[-1]
    total_matched = matched[-1] if matched else 0.0

    # 1.1 方向：旧实现用最后明细价代替正式开盘价
    open_pct = (last_price / prev_close - 1) * 100 if prev_close > 0 else 0.0
    direction = _bucket(open_pct)

    # 1.3 供需：旧实现对全窗口未匹配快照求和
    net = sum(unmatched) * 1.0
    imb = net / total_matched if total_matched > 0 else 0.0
    supply = max(0.0, min(100.0, 50.0 + imb * 100.0))

    # 1.7 量价：旧换手漏 ×100（matched 手 / float_shares 股）
    float_cap = getattr(quote, "float_market_cap", 0.0) if quote is not None else 0.0
    float_shares = float_cap / last_price if last_price > 0 else 0.0
    turnover = total_matched / float_shares * 100.0 if float_shares > 0 else 0.0
    vol_price = max(0.0, min(100.0, turnover / 3.0 * 100.0)) if turnover else NEUTRAL

    # 1.8 末段抢筹
    tail_buy = [unmatched[i] for i in tail]
    tail_grab = 100.0 if tail_buy and tail_buy[-1] < tail_buy[0] else NEUTRAL

    return {
        "direction": (round(direction, 2), WEIGHTS["direction"]),
        "supply": (round(supply, 2), WEIGHTS["supply"]),
        "vol_price": (round(vol_price, 2), WEIGHTS["vol_price"]),
        "tail_grab": (round(tail_grab, 2), WEIGHTS["tail_grab"]),
    }


CATEGORIES = ("unit_x100", "full_window_sum", "neutral_50",
              "sampling_amplification", "open_price_source", "veto_missing_data")


def classify(old: dict, new_payload: dict) -> list[str]:
    """把一处差异归类；无法归类时返回 ``["unexplained"]``。"""
    categories = set()
    if old.get("is_weak") and old.get("reject_reason", "").startswith("0.4 数据不足"):
        categories.add("veto_missing_data")
    old_dims = old.get("dims") or {}
    new_dims = new_payload.get("dims") or {}
    if old_dims.get("vol_price") and new_dims.get("liquidity"):
        old_turnover_dim = old_dims["vol_price"][0]
        new_turnover = (new_payload.get("raw_factors") or {}).get(
            "auction_turnover_pct")
        if new_turnover is not None and old_turnover_dim is not None:
            categories.add("unit_x100")
    if old_dims.get("supply"):
        categories.add("full_window_sum")
        categories.add("sampling_amplification")
    if any(value[0] == NEUTRAL for value in old_dims.values()):
        categories.add("neutral_50")
    if old_dims.get("direction") and (new_payload.get("raw_factors") or {}).get(
            "official_open_gap_pct") is not None:
        categories.add("open_price_source")
    return sorted(categories) or ["unexplained"]
