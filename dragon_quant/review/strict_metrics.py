"""严格口径唯一纯核（无 I/O，供 review CLI 与 Web 共用）。

逐字迁移自 ``dragon_quant.review.main`` 的历史私有实现：

* 买入：入选后**首个交易日**按**开盘价**买入，不顺延；
* 收益窗口：买入后第 2~5 个交易日（``WINDOW_SKIP=1`` / ``WINDOW_SIZE=4``）；
* 最大回撤：买入日 → 最大收益出现日的最低相对买价。

本模块**不得** import provider / storage / infra；只接受普通 dict 列表与交易日
字符串集合，保证 Web 进程不因复用严格公式而拉入 I/O 依赖。
"""

from __future__ import annotations

from typing import Optional, TypeGuard

WINDOW_SKIP = 1
WINDOW_SIZE = 4

CANNOT_EVALUATE_REASON = "停牌或数据缺失无法区分"
PARTIAL_WINDOW_REASON = "partial_window"

__all__ = [
    "CANNOT_EVALUATE_REASON",
    "PARTIAL_WINDOW_REASON",
    "WINDOW_SIZE",
    "WINDOW_SKIP",
    "evaluate_strict",
    "is_valid_bar",
    "window_metrics",
]


def is_valid_bar(bar: object) -> TypeGuard[dict]:
    """次日 K 线是否可用于严格口径（缺 bar / 无效 OHLC / 零成交平盘占位即不可用）。"""
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


def window_metrics(klines: list[dict], calendar: set[str],
                   buy_date: str, buy_price: float) -> Optional[dict]:
    """计算共享收益 / 回撤窗口；窗口为空返回 ``None``。"""
    after_buy = sorted(d for d in calendar if d > buy_date)
    future_dates = after_buy[WINDOW_SKIP:WINDOW_SKIP + WINDOW_SIZE]
    if not future_dates:
        return None

    by_date = {k["date"]: k for k in klines}
    future_klines = [by_date[d] for d in future_dates if d in by_date]
    if not future_klines:
        return None

    peak_k = max(future_klines, key=lambda k: k["high"])
    max_high = peak_k["high"]
    peak_date = peak_k["date"]

    drawdown_klines = [k for k in klines
                       if buy_date <= k["date"] <= peak_date]
    min_low = min(k["low"] for k in drawdown_klines) if drawdown_klines else buy_price

    hold_days = sum(1 for d in calendar if buy_date < d <= peak_date)
    partial = (len(future_dates) < WINDOW_SIZE
               or len(future_klines) < len(future_dates))
    return {
        "max_return_5d": round((max_high - buy_price) / buy_price * 100, 2),
        "max_drawdown_5d": round((min_low - buy_price) / buy_price * 100, 2),
        "max_return_hold_days": hold_days,
        "partial": partial,
    }


def _strict_result(status: str, *, reason: Optional[str] = None, **fields) -> dict:
    return {
        "buy_date": fields.get("buy_date"),
        "buy_price": fields.get("buy_price"),
        "max_return_5d": fields.get("max_return_5d"),
        "max_drawdown_5d": fields.get("max_drawdown_5d"),
        "max_return_hold_days": fields.get("max_return_hold_days"),
        "review_status": status,
        "reason": reason,
    }


def _cannot_evaluate() -> dict:
    return _strict_result("cannot_evaluate", reason=CANNOT_EVALUATE_REASON)


def evaluate_strict(klines: list[dict], calendar: set[str],
                    trade_date: str) -> dict:
    """严格口径：入选后首个交易日按开盘价买入，不顺延。"""
    next_days = sorted(d for d in calendar if d > trade_date)
    if not next_days:
        return _cannot_evaluate()

    buy_date = next_days[0]
    bar = next((k for k in klines if k["date"] == buy_date), None)
    if not is_valid_bar(bar):
        return _cannot_evaluate()

    buy_price = bar["open"]
    metrics = window_metrics(klines, calendar, buy_date, buy_price)
    if metrics is None:
        return _cannot_evaluate()

    return _strict_result(
        "completed",
        reason=PARTIAL_WINDOW_REASON if metrics["partial"] else None,
        buy_date=buy_date,
        buy_price=buy_price,
        max_return_5d=metrics["max_return_5d"],
        max_drawdown_5d=metrics["max_drawdown_5d"],
        max_return_hold_days=metrics["max_return_hold_days"],
    )
