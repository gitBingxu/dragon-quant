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
#: 收益窗口还没走完（时间未成熟）——后续交易日会补齐。
PARTIAL_WINDOW_REASON = "partial_window"
#: 窗口日期已过，但个股缺日 K（停牌 / 数据缺失）——等待不会自愈。
PARTIAL_MISSING_BARS_REASON = "partial_missing_bars"
#: 所有「窗口不完整」原因；均展示但不进正式分母。
PARTIAL_REASONS = frozenset({
    PARTIAL_WINDOW_REASON, PARTIAL_MISSING_BARS_REASON,
})

#: 可成交研究口径（``tradable_open_v1``）的默认成本（买卖双边，bp）。
TRADABLE_OPEN_COST_BPS = 13.0
TRADABLE_OPEN_HORIZONS = (1, 3, 5)

__all__ = [
    "CANNOT_EVALUATE_REASON",
    "PARTIAL_MISSING_BARS_REASON",
    "PARTIAL_REASONS",
    "PARTIAL_WINDOW_REASON",
    "TRADABLE_OPEN_COST_BPS",
    "TRADABLE_OPEN_HORIZONS",
    "WINDOW_SIZE",
    "WINDOW_SKIP",
    "evaluate_strict",
    "evaluate_tradable_open",
    "is_valid_bar",
    "maturity_cutoff",
    "maturity_required_trading_days",
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
    # 两种不完整要分开：时间未到会自愈；缺 K 线（停牌）不会。
    partial_reason = None
    if partial:
        partial_reason = (
            PARTIAL_WINDOW_REASON
            if len(future_dates) < WINDOW_SIZE
            else PARTIAL_MISSING_BARS_REASON
        )
    return {
        "max_return_5d": round((max_high - buy_price) / buy_price * 100, 2),
        "max_drawdown_5d": round((min_low - buy_price) / buy_price * 100, 2),
        "max_return_hold_days": hold_days,
        "partial": partial,
        "partial_reason": partial_reason,
    }


def maturity_required_trading_days() -> int:
    """完整窗口所需的「入选后交易日数」。

    入选日 T → 买入日 T+1 → 收益窗口为买入后第 ``WINDOW_SKIP+1`` ～
    ``WINDOW_SKIP+WINDOW_SIZE`` 个交易日，因此至少需要
    ``1 + WINDOW_SKIP + WINDOW_SIZE`` 个交易日后才可能完整。
    """
    return 1 + WINDOW_SKIP + WINDOW_SIZE


def maturity_cutoff(calendar: set[str]) -> Optional[str]:
    """最新可判成熟的入选日上界；日历不足时返回 ``None``（无成熟样本）。

    与窗口公式同源，不再硬编码「入选满 5 日」——后者与实际收益窗口差一个
    交易日，是 partial 进入正式分母的根因。
    """
    days = sorted(calendar, reverse=True)
    index = maturity_required_trading_days()
    if len(days) <= index:
        return None
    return days[index]


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

    # 窗口不完整 → ``partial``：可展示，但不得进入正式成效分母（A1/KR1）。
    # reason 区分「时间未到」与「窗口内缺 K 线（停牌）」，前者会自愈、后者不会。
    return _strict_result(
        "partial" if metrics["partial"] else "completed",
        reason=metrics["partial_reason"],
        buy_date=buy_date,
        buy_price=buy_price,
        max_return_5d=metrics["max_return_5d"],
        max_drawdown_5d=metrics["max_drawdown_5d"],
        max_return_hold_days=metrics["max_return_hold_days"],
    )


def _tradable_unavailable(reason: str, entry_date: Optional[str] = None) -> dict:
    return {
        "caliber": "tradable_open_v1",
        "status": "cannot_evaluate",
        "reason": reason,
        "entry_date": entry_date,
        "entry_rule": "T+1 开盘买入；一字板（high==low）无法确认成交即排除",
        "cost_bps": TRADABLE_OPEN_COST_BPS,
        "horizons": {},
        "partial": False,
    }


def evaluate_tradable_open(klines: list[dict], calendar: set[str],
                           trade_date: str, *,
                           hold_days: tuple[int, ...] = TRADABLE_OPEN_HORIZONS,
                           cost_bps: float = TRADABLE_OPEN_COST_BPS) -> dict:
    """``tradable_open_v1``：可成交估算与固定持有期净收益（研究口径）。

    与严格口径并列、不替换历史公式：

    * 入选后首个交易日按**开盘价**买入（T+1），不顺延到更好价格；
    * 该日 ``high == low``（一字板/无法确认开盘可成交）→ 整体排除并说明；
    * 持有期 n 的退出 = 买入后第 n 个交易日**收盘**；
    * 净收益 = 毛收益 − ``cost_bps``，仅为可成交估算，不宣称真实成交；
    * 窗口不足的持有期单独标记 ``partial``，不填 0。
    """
    next_days = sorted(d for d in calendar if d > trade_date)
    if not next_days:
        return _tradable_unavailable("no_next_trading_day")
    entry_date = next_days[0]
    by_date = {k.get("date"): k for k in klines}
    entry_bar = by_date.get(entry_date)
    if not is_valid_bar(entry_bar):
        return _tradable_unavailable("entry_bar_invalid", entry_date)
    if entry_bar["high"] == entry_bar["low"]:
        return _tradable_unavailable("one_word_board_unfillable", entry_date)

    entry_price = entry_bar["open"]
    after_entry = [d for d in sorted(calendar) if d > entry_date]
    horizons: dict[str, dict] = {}
    any_partial = False
    for n in sorted({int(v) for v in hold_days if int(v) > 0}):
        if len(after_entry) < n:
            any_partial = True
            horizons[str(n)] = {
                "status": "partial",
                "exit_date": None,
                "exit_price": None,
                "gross_return_pct": None,
                "net_return_pct": None,
            }
            continue
        exit_date = after_entry[n - 1]
        exit_bar = by_date.get(exit_date)
        if not is_valid_bar(exit_bar):
            any_partial = True
            horizons[str(n)] = {
                "status": "partial",
                "exit_date": exit_date,
                "exit_price": None,
                "gross_return_pct": None,
                "net_return_pct": None,
            }
            continue
        exit_price = exit_bar["close"]
        gross = (exit_price / entry_price - 1) * 100
        horizons[str(n)] = {
            "status": "completed",
            "exit_date": exit_date,
            "exit_price": round(exit_price, 4),
            "gross_return_pct": round(gross, 2),
            "net_return_pct": round(gross - cost_bps / 100.0, 2),
        }
    return {
        "caliber": "tradable_open_v1",
        "status": "partial" if any_partial else "completed",
        "reason": None,
        "entry_date": entry_date,
        "entry_price": round(entry_price, 4),
        "entry_rule": "T+1 开盘买入；一字板（high==low）无法确认成交即排除",
        "cost_bps": cost_bps,
        "horizons": horizons,
        "partial": any_partial,
    }
