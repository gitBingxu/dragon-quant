"""日线指标计算，供账户级 review 的买卖规则使用。"""

import math
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from dragon_quant.infra.models import KBar


def is_tradable_daily_bar(bar: Optional[KBar]) -> bool:
    """当日日K是否代表真实成交。

    停牌日的数据源形态有两种：整行缺失，或保留占位行但 `volume<=0 且 amount<=0`。
    两者都不能用来构造兜底成交价，故按「有量或有额」判定可交易。
    """
    return bool(bar is not None and ((bar.volume or 0) > 0 or (bar.amount or 0) > 0))


def kbar_date(k: KBar) -> str:
    return datetime.fromtimestamp(k.timestamp / 1000, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def kbar_to_row(k: KBar) -> dict:
    return {
        "date": kbar_date(k),
        "open": k.open,
        "high": k.high,
        "low": k.low,
        "close": k.close,
        "pct": k.pct,
        "volume": k.volume,
        "turnover": k.turnover,
        "amount": k.amount,
    }


def enrich_daily_klines(klines: list[KBar]) -> list[dict]:
    """将日 K 转为带常用指标的 dict 列表。输入按时间升序或乱序均可。"""
    rows = sorted([kbar_to_row(k) for k in klines], key=lambda r: r["date"])
    closes: list[float] = []
    volumes: list[float] = []
    amounts: list[float] = []
    turnovers: list[float] = []
    prev_close: Optional[float] = None
    prev_volume: Optional[float] = None

    for i, r in enumerate(rows):
        closes.append(r["close"])
        volumes.append(r["volume"])
        amounts.append(r["amount"])
        turnovers.append(r["turnover"])

        r["ma5"] = _ma(closes, 5)
        r["ma10"] = _ma(closes, 10)
        r["ma20"] = _ma(closes, 20)
        r["volume_ma5"] = _ma(volumes, 5)
        r["amount_ma5"] = _ma(amounts, 5)
        r["turnover_ma5"] = _ma(turnovers, 5)
        r["prev_close"] = prev_close
        r["prev_volume"] = prev_volume
        r["prev_high"] = rows[i - 1]["high"] if i > 0 else None
        r["prev_low"] = rows[i - 1]["low"] if i > 0 else None
        r["open_gap_pct"] = (
            (r["open"] / prev_close - 1) * 100 if prev_close and prev_close > 0 else None
        )
        r["close_to_ma5_pct"] = (
            (r["close"] / r["ma5"] - 1) * 100 if r["ma5"] and r["ma5"] > 0 else None
        )
        r["low_touch_ma5"] = bool(r["ma5"] and r["low"] <= r["ma5"] * 1.01)
        r["is_one_word_board"] = r["high"] == r["low"]
        r["is_limit_up_close"] = r["pct"] >= 9.9 and r["close"] == r["high"]
        r["return_3d"] = _return_from(closes, 3)
        r["return_5d"] = _return_from(closes, 5)
        r["max_drawdown_5d"] = _max_drawdown(rows[max(0, i - 4): i + 1])
        r["avg_amplitude_5"] = _avg_amplitude(rows[max(0, i - 4): i + 1])
        prev_close = r["close"]
        prev_volume = r["volume"]
    return rows


def row_by_date(rows: list[dict], date: str) -> Optional[dict]:
    for r in rows:
        if r["date"] == date:
            return r
    return None


def latest_on_or_before(rows: list[dict], date: str) -> Optional[dict]:
    latest = None
    for r in rows:
        if r["date"] <= date:
            latest = r
        else:
            break
    return latest


def _ma(values: list[float], n: int) -> Optional[float]:
    if len(values) < n:
        return None
    sample = values[-n:]
    return sum(sample) / n


def _return_from(closes: list[float], n: int) -> Optional[float]:
    if len(closes) <= n or closes[-n - 1] <= 0:
        return None
    return (closes[-1] / closes[-n - 1] - 1) * 100


def _max_drawdown(rows: list[dict]) -> Optional[float]:
    if not rows:
        return None
    peak = rows[0]["high"]
    max_dd = 0.0
    for r in rows:
        peak = max(peak, r["high"])
        if peak > 0:
            max_dd = min(max_dd, (r["low"] / peak - 1) * 100)
    return max_dd


def _avg_amplitude(rows: list[dict]) -> Optional[float]:
    vals = []
    for r in rows:
        base = r.get("prev_close") or r.get("close")
        if base and base > 0:
            vals.append((r["high"] - r["low"]) / base * 100)
    return sum(vals) / len(vals) if vals else None


def post_exit_stats(history: list[KBar], exit_date: str, exit_price: float,
                    window: int = 5) -> dict:
    """清仓后窗口指标（纯函数，无 I/O）。

    窗口 = ``exit_date`` **之后**的 ``window`` 根有效日K（按时间升序），
    停牌占位行（无成交量/成交额）与非法 OHLC（<=0 或 NaN/inf）不占窗口名额。
    不足 ``window`` 根按实际根数；无有效 bar → 两列为 None、天数为 0。

    最高盈利 = (窗口最高价 - 卖出价) / 卖出价 * 100。
    最大回撤 = min(0, (窗口最低价 - 卖出价) / 卖出价 * 100)，即卖出价起算的单边跌幅：
    从未跌破卖出价时为 0。
    """
    empty = {"post_exit_max_return": None, "post_exit_max_drawdown": None,
             "post_exit_window_days": 0}
    try:
        exit_price = float(exit_price)
    except (TypeError, ValueError):
        return empty
    if not math.isfinite(exit_price) or exit_price <= 0:
        return empty
    try:
        limit = max(0, int(window))
    except (TypeError, ValueError):
        return empty
    if not limit:
        return empty

    bars = []
    for bar in history:
        if kbar_date(bar) <= exit_date or not is_tradable_daily_bar(bar):
            continue
        try:
            valid = (math.isfinite(bar.high) and math.isfinite(bar.low)
                     and bar.high > 0 and bar.low > 0)
        except TypeError:
            valid = False
        if valid:
            bars.append(bar)
    bars.sort(key=lambda b: b.timestamp)
    bars = bars[:limit]
    if not bars:
        return empty
    high = max(b.high for b in bars)
    low = min(b.low for b in bars)
    return {
        "post_exit_max_return": (high - exit_price) / exit_price * 100,
        "post_exit_max_drawdown": min(0.0, (low - exit_price) / exit_price * 100),
        "post_exit_window_days": len(bars),
    }
