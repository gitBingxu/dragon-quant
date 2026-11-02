"""日历默认日期纯函数（方案 §6，无 I/O）。

输入本地已确认交易日序列（``stock_daily`` bar 日期并集）与调用方参照日，
输出默认日期、来源、依据与显式降级原因。**不**声称是交易所日历。
"""

from __future__ import annotations

from typing import Literal, Sequence

__all__ = ["resolve_default_date"]

Page = Literal["prep", "auction"]

CALENDAR_BASIS_BARS = "local_stock_daily_bars_union"
CALENDAR_BASIS_RECORDS = "record_dates"


def resolve_default_date(
    *,
    page: Page,
    bar_dates: Sequence[str],
    reference_date: str,
    latest_record_date: str | None,
) -> dict:
    """返回 ``default_date`` 及其来源 / 依据 / 降级信息（方案 §6.2 状态机）。"""

    confirmed = sorted({str(day) for day in (bar_dates or [])
                        if day and str(day) <= reference_date})

    if not confirmed:
        if latest_record_date:
            default_date = str(latest_record_date)
            source = "record_date_fallback"
        else:
            default_date = reference_date
            source = "today_fallback"
        return {
            "default_date": default_date,
            "default_date_source": source,
            "calendar_basis": CALENDAR_BASIS_RECORDS,
            "calendar_latest_date": None,
            "degraded": True,
            "degraded_reason": "local_bars_missing",
        }

    latest = confirmed[-1]
    degraded = False
    degraded_reason = None

    if page == "auction":
        default_date = latest
        source = "local_bar_latest"
    elif latest != reference_date:
        # 休息日 / 当日 bar 尚未写入：latest 本身已是前一交易日。
        default_date = latest
        source = "local_bar_latest"
    elif len(confirmed) >= 2:
        default_date = confirmed[-2]
        source = "local_bar_previous"
    else:
        # 只有一天本地 bar 且等于参照日：无前一交易日，显式降级不静默改选。
        default_date = latest
        source = "local_bar_latest"
        degraded = True
        degraded_reason = "no_previous_trading_day"

    return {
        "default_date": default_date,
        "default_date_source": source,
        "calendar_basis": CALENDAR_BASIS_BARS,
        "calendar_latest_date": latest,
        "degraded": degraded,
        "degraded_reason": degraded_reason,
    }
