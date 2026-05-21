"""
交易日历工具 — 基于雪球日 K 线数据计算交易日

不依赖任何外部假期表，用平安银行（000001）日K线天然包含的
交易日集合来判断任意两个日期之间的交易日数量。
"""

from datetime import datetime
from typing import Optional


def build_trade_calendar(from_date: str, to_date: str) -> set[str]:
    """用平安银行日K提取 [from_date, to_date] 内的交易日集合。

    平安银行（000001）是全市场最不容易停牌的标的之一，
    其日K线天然覆盖所有交易日，不会遗漏。
    """
    from dragon_quant.providers.xueqiu import XueqiuProvider

    provider = XueqiuProvider()
    klines = provider.get_kline("000001", days=90)

    dates: set[str] = set()
    for k in klines:
        d = datetime.fromtimestamp(k.timestamp / 1000).strftime("%Y-%m-%d")
        if from_date <= d <= to_date:
            dates.add(d)
    return dates


def trade_days_between(a: str, b: str, calendar: set[str]) -> int:
    """返回 a 到 b 之间的交易日数（包含 b，不包含 a）。"""
    return sum(1 for d in calendar if a < d <= b)


def is_limit_up(kline: dict) -> bool:
    """判断一根日K是否涨停。

    规则：涨幅 ≥ 9.9% 且收盘价等于最高价（封板到收盘）。
    """
    pct = kline.get("pct", 0)
    close = kline.get("close", 0)
    high = kline.get("high", 0)
    return pct >= 9.9 and close == high


def kbar_to_dict(kbar) -> dict:
    """将 KBar dataclass 转为 dict，用于统一处理。"""
    return {
        "date": datetime.fromtimestamp(kbar.timestamp / 1000).strftime("%Y-%m-%d"),
        "open": kbar.open,
        "close": kbar.close,
        "high": kbar.high,
        "low": kbar.low,
        "volume": kbar.volume,
        "pct": kbar.pct,
    }


def find_entry_day(klines: list[dict], entry_date: str) -> Optional[dict]:
    """在日K列表中寻找入选日后第一个可介入日。

    一字板（high == low，全天无波动）不可介入，
    high != low 即可介入，买入价为当日最低价。

    返回可介入日的日K dict，找不到则返回 None。
    """
    future = [k for k in klines if k["date"] > entry_date]
    future.sort(key=lambda k: k["date"])

    for k in future:
        if k["high"] != k["low"]:
            return k

    return None
