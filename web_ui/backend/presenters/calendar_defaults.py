"""日历默认日期纯函数（方案 §6，无 I/O）。

输入本地已确认交易日序列（``stock_daily`` bar 日期并集）与调用方参照日，
输出默认日期、来源、依据与显式降级原因。**不**声称是交易所日历。

两个页面的默认语义不同：

* ``prep``：**默认选最近有复盘数据的日期**（优先最新可用复盘快照，
  缺快照才回退最近存在真实复盘资产的日期）；只有完全没有复盘记录时才回退
  最近本地交易日（该日真实存在，页面显示空态），绝不按「本地 K 线前一交易日」
  推断。
* ``auction``：保持既有语义（最近确认交易日），本任务不改。
"""

from __future__ import annotations

from typing import Literal, Sequence

__all__ = ["resolve_default_date"]

Page = Literal["prep", "auction"]

CALENDAR_BASIS_BARS = "local_stock_daily_bars_union"
CALENDAR_BASIS_RECORDS = "record_dates"


def _confirmed(bar_dates: Sequence[str], reference_date: str) -> list[str]:
    return sorted({str(day) for day in (bar_dates or [])
                   if day and str(day) <= reference_date})


def resolve_default_date(
    *,
    page: Page,
    bar_dates: Sequence[str],
    reference_date: str,
    latest_record_date: str | None,
    latest_snapshot_date: str | None = None,
    latest_asset_date: str | None = None,
) -> dict:
    """返回 ``default_date`` 及其来源 / 依据 / 降级信息（方案 §6.2 状态机）。"""

    confirmed = _confirmed(bar_dates, reference_date)

    if page == "prep":
        return _prep_default(
            confirmed=confirmed,
            reference_date=reference_date,
            latest_record_date=latest_record_date,
            latest_snapshot_date=latest_snapshot_date,
            latest_asset_date=latest_asset_date,
        )
    return _auction_default(confirmed, reference_date, latest_record_date)


def _prep_default(*, confirmed: list[str], reference_date: str,
                  latest_record_date: str | None,
                  latest_snapshot_date: str | None,
                  latest_asset_date: str | None) -> dict:
    """复盘默认日：真实复盘记录优先，绝不按本地 K 线前一交易日推断。"""

    def usable(day: str | None) -> str | None:
        if not day:
            return None
        text = str(day)
        return text if text <= reference_date else None

    snapshot = usable(latest_snapshot_date)
    assets = usable(latest_asset_date)
    record = snapshot or assets or usable(latest_record_date)

    common = {
        "calendar_latest_date": confirmed[-1] if confirmed else None,
    }
    if record is not None:
        # 快照优先；仅真实资产时可回退；两者都缺但仍报告了记录日（兼容旧
        # presenter 形状）同样按记录回退处理。
        if snapshot == record:
            source = "latest_snapshot"
        else:
            source = "record_date_fallback"
        return {
            "default_date": record,
            "default_date_source": source,
            "calendar_basis": CALENDAR_BASIS_RECORDS,
            "degraded": False,
            "degraded_reason": None,
            **common,
        }

    if confirmed:
        # 全年没有复盘记录：回退最近真实交易日（真实存在，不是伪造日期），
        # 页面明确空态；不使用「前一交易日」推断。
        return {
            "default_date": confirmed[-1],
            "default_date_source": "local_bar_latest",
            "calendar_basis": CALENDAR_BASIS_BARS,
            "degraded": False,
            "degraded_reason": None,
            **common,
        }

    return {
        "default_date": reference_date,
        "default_date_source": "today_fallback",
        "calendar_basis": CALENDAR_BASIS_RECORDS,
        "degraded": True,
        "degraded_reason": "local_bars_missing",
        **common,
    }


def _auction_default(confirmed: list[str], reference_date: str,
                     latest_record_date: str | None) -> dict:
    """竞价默认日：保持既有语义（本任务不擅自修改）。"""
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
    return {
        "default_date": latest,
        "default_date_source": "local_bar_latest",
        "calendar_basis": CALENDAR_BASIS_BARS,
        "calendar_latest_date": latest,
        "degraded": False,
        "degraded_reason": None,
    }
