"""入选后表现 — 纯计算服务（无 DB / 无 provider / 无时钟）。

固定口径：**宽松**，入选后首个 ``high != low`` 日按最低价买入；观察窗口从
买入日直到 ``as_of`` 的最新有效日 K。与固定 5 日 ``review.main._window_metrics``
完全隔离（不 import / 不调用其窗口函数），仅复用公开的 ``find_entry_day`` 与
``kbar_to_dict``。

``as_of`` 必须显式传入：本模块不读时钟、不读环境。
"""

from __future__ import annotations

from typing import TypeGuard

from dragon_quant.infra.market_calendar import find_entry_day, kbar_to_dict

__all__ = [
    "HISTORY_SHORT_REASON",
    "NO_KLINE_ERROR",
    "compute_performance",
    "is_valid_daily_bar",
]

# 与 ``review.main.HISTORY_SHORT_REASON`` 保持同一文案（避免 import 私有模块）。
HISTORY_SHORT_REASON = "历史数据不足（K 线未覆盖入选日）"
NO_KLINE_ERROR = "无K线数据"

_OHLC_FIELDS = ("open", "high", "low", "close")


def is_valid_daily_bar(bar: object) -> TypeGuard[dict]:
    """日 K 是否可用于入场/窗口计算。

    逐字对齐 ``review.main._is_valid_bar``：

    * 非 dict / 缺 OHLC → 无效；
    * ``min(open, high, low, close) <= 0`` → 无效；
    * ``high < low`` → 无效；
    * ``open`` 或 ``close`` 不在 ``[low, high]`` → 无效；
    * ``volume <= 0 且 high == low``（停牌占位）→ 无效。
    """
    if not isinstance(bar, dict):
        return False
    open_px = bar.get("open")
    high = bar.get("high")
    low = bar.get("low")
    close = bar.get("close")
    if open_px is None or high is None or low is None or close is None:
        return False
    if min(open_px, high, low, close) <= 0:
        return False
    if high < low:
        return False
    if not (low <= open_px <= high and low <= close <= high):
        return False
    volume = bar.get("volume")
    if volume is not None and volume <= 0 and high == low:
        # 停牌占位行（平盘 + 零成交）；与真实一字板（有成交）区分开。
        return False
    return True


def _normalize(klines: list | None) -> list[dict]:
    """KBar → dict，按 date 升序，同日重复保留最后一条。"""
    by_date: dict[str, dict] = {}
    for item in klines or []:
        bar = item if isinstance(item, dict) else kbar_to_dict(item)
        day = bar.get("date")
        if not isinstance(day, str) or not day:
            continue
        by_date[day] = bar
    return [by_date[day] for day in sorted(by_date)]


def _rounded_pct(numerator: float, denominator: float) -> float:
    """百分比并归一化 -0.0：微小负值 round 到 0 时输出 +0.0。"""
    value = round(numerator / denominator * 100, 2)
    return 0.0 if value == 0 else value


def _result(status: str, as_of: str, *, error: str | None = None, **fields) -> dict:
    return {
        "caliber": "loose",
        "status": status,
        "as_of": as_of,
        "buy_date": fields.get("buy_date"),
        "buy_price": fields.get("buy_price"),
        "max_return_since_entry": fields.get("max_return_since_entry"),
        "max_drawdown_since_entry": fields.get("max_drawdown_since_entry"),
        "peak_date": fields.get("peak_date"),
        "latest_bar_date": fields.get("latest_bar_date"),
        "bars_in_window": fields.get("bars_in_window"),
        "bars_after_entry": fields.get("bars_after_entry"),
        "error": error,
    }


def compute_performance(
    klines: list,
    trade_date: str,
    as_of: str,
) -> dict:
    """计算入选后表现。

    Args:
        klines: 日 K（``dict`` 或 ``KBar`` 列表）；不触网。
        trade_date: 龙头入选日（``YYYY-MM-DD``）。
        as_of: 观测截止日（``YYYY-MM-DD``，必填）。

    Returns:
        稳定键集：``caliber / status / as_of / buy_date / buy_price /
        max_return_since_entry / max_drawdown_since_entry / peak_date /
        latest_bar_date / bars_in_window / bars_after_entry / error``。
        不含 ``max_return_5d`` / ``max_drawdown_5d``。

    Raises:
        ValueError: ``as_of < trade_date``（路由转为 400）。
    """
    if as_of < trade_date:
        raise ValueError("as_of 不能早于 trade_date")

    bars = _normalize(klines)
    valid = [bar for bar in bars if is_valid_daily_bar(bar)]
    if not valid:
        return _result("error", as_of, error=NO_KLINE_ERROR)

    if min(bar["date"] for bar in valid) > trade_date:
        # 不拿最近窗口冒充入场日。
        return _result("error", as_of, error=HISTORY_SHORT_REASON)

    # 未来 bar 在入场搜索前丢弃。
    eligible = [bar for bar in valid if bar["date"] <= as_of]
    entry = find_entry_day(eligible, trade_date)
    if not entry:
        return _result(
            "no_entry",
            as_of,
            latest_bar_date=eligible[-1]["date"] if eligible else None,
            bars_in_window=0,
            bars_after_entry=0,
        )

    buy_date = entry["date"]
    buy_price = entry["low"]
    post = [bar for bar in eligible if bar["date"] > buy_date]

    if not post:
        return _result(
            "empty_window",
            as_of,
            buy_date=buy_date,
            buy_price=buy_price,
            latest_bar_date=buy_date,
            bars_in_window=1,
            bars_after_entry=0,
        )

    # 峰值只看买入日之后；并列时取最早日期（post 已按日期升序）。
    peak = max(post, key=lambda bar: bar["high"])
    peak_date = peak["date"]
    draw = [bar for bar in eligible if buy_date <= bar["date"] <= peak_date]
    min_low = min(bar["low"] for bar in draw)
    window = [bar for bar in eligible if buy_date <= bar["date"] <= as_of]

    return _result(
        "ok",
        as_of,
        buy_date=buy_date,
        buy_price=buy_price,
        max_return_since_entry=_rounded_pct(
            peak["high"] - buy_price, buy_price),
        max_drawdown_since_entry=_rounded_pct(
            min_low - buy_price, buy_price),
        peak_date=peak_date,
        latest_bar_date=window[-1]["date"],
        bars_in_window=len(window),
        bars_after_entry=len(post),
    )
