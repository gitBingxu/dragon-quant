"""
买入时机判断 — 涨停穿透 + 炸板检测

从 scan_date 开始逐日向前搜索，找到第一个非涨停日作为买入日。
如果该日有 5 分钟 K 线数据，精确检测炸板时刻作为买入点。
"""

from datetime import datetime
from typing import Optional, List
from dragon_quant.models.types import KBar


def find_entry(klines_day: List[KBar],
               klines_5min: Optional[List[KBar]],
               scan_date: str) -> Optional[dict]:
    """
    在日 K 线序列中定位买入时机。

    Args:
        klines_day: 日 K 线列表（按时间升序）
        klines_5min: 买入日的 5 分钟 K 线列表（按时间升序），可为 None
        scan_date: 扫描日期 "2026-05-10"

    Returns:
        {
            "entry_date": "2026-05-12",
            "entry_price": 12.50,
            "entry_type": "minute" | "daily",   # minute=炸板精确, daily=日K开盘
            "note": "",
        }
        如果找不到可买入日，返回 None。
    """
    if not klines_day:
        return None

    scan_dt = _parse_date(scan_date)
    day_bars = sorted(klines_day, key=lambda b: b.timestamp)

    entry_bar = None
    for bar in day_bars:
        bar_date = _ts_to_date(bar.timestamp).date()
        if bar_date < scan_dt.date():
            continue
        if bar.pct < 9.9:
            entry_bar = bar
            break

    if entry_bar is None:
        return None

    entry_date = _ts_to_date(entry_bar.timestamp)

    if klines_5min and len(klines_5min) > 0:
        min_bars = sorted(klines_5min, key=lambda b: b.timestamp)
        for mb in min_bars:
            mb_date = _ts_to_date(mb.timestamp).date()
            if mb_date != entry_date.date():
                continue
            if mb.pct < 9.5:
                return {
                    "entry_date": _fmt_date(entry_date),
                    "entry_price": round(mb.open, 4),
                    "entry_type": "minute",
                    "note": "",
                }

    return {
        "entry_date": _fmt_date(entry_date),
        "entry_price": round(entry_bar.open, 4),
        "entry_type": "daily",
        "note": "",
    }


def find_exit(klines_day: List[KBar], entry_date_str: str,
              trading_days: int = 5) -> Optional[dict]:
    """
    在日 K 线序列中定位出场日（买入日后第 N 个交易日）。

    Returns:
        {
            "exit_date": "2026-05-19",
            "exit_price": 13.80,
            "actual_days": 5,         # 实际交易天数
            "note": "",
        }
        如果 K 线不足，返回实际可用的最后一天。
    """
    if not klines_day:
        return None

    entry_dt = _parse_date(entry_date_str)
    day_bars = sorted(klines_day, key=lambda b: b.timestamp)

    bars_after = [b for b in day_bars if _ts_to_date(b.timestamp).date() > entry_dt.date()]

    if len(bars_after) < trading_days:
        if len(bars_after) == 0:
            return None
        last = bars_after[-1]
        return {
            "exit_date": _fmt_date(_ts_to_date(last.timestamp)),
            "exit_price": round(last.close, 4),
            "actual_days": len(bars_after),
            "note": f"仅{len(bars_after)}个交易日数据",
        }

    exit_bar = bars_after[trading_days - 1]
    return {
        "exit_date": _fmt_date(_ts_to_date(exit_bar.timestamp)),
        "exit_price": round(exit_bar.close, 4),
        "actual_days": trading_days,
        "note": "",
    }


def calculate_return(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return round((exit_price / entry_price - 1) * 100, 2)


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s[:10], "%Y-%m-%d")


def _ts_to_date(ts: int) -> datetime:
    return datetime.fromtimestamp(ts / 1000)


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")