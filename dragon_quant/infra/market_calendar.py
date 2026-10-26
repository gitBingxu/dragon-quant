"""
交易日历工具 — 日 K 线推导交易日（雪球 / TDX 指数双来源）

不依赖任何外部假期表：
- ``build_trade_calendar``：用平安银行（000001）日 K（雪球）；
- ``tdx_trade_dates``：用上证指数（SH000001）日 K（TDX，无 Cookie），供 prep 日期门禁
  与 auction 的 T-1 快照选择共用。
"""

from datetime import datetime
from typing import Optional

from dragon_quant.infra import clock


def build_trade_calendar(from_date: str, to_date: str) -> set[str]:
    """用平安银行日K提取 [from_date, to_date] 内的交易日集合。

    平安银行（000001）是全市场最不容易停牌的标的之一，
    其日K线天然覆盖所有交易日，不会遗漏。
    """
    from dragon_quant.infra.provider.xueqiu import XueqiuProvider

    provider = XueqiuProvider()
    try:
        start = datetime.strptime(from_date, "%Y-%m-%d")
        end = datetime.strptime(to_date, "%Y-%m-%d")
        days = max((end - start).days + 30, 90)
    except ValueError:
        days = 90
    klines = provider.get_kline("000001", days=days)

    dates: set[str] = set()
    now = clock.now()
    today = now.strftime("%Y-%m-%d")
    market_closed = (now.hour, now.minute) >= (15, 5)
    for k in klines:
        d = datetime.fromtimestamp(k.timestamp / 1000).strftime("%Y-%m-%d")
        if from_date <= d <= to_date and (d < today or (d == today and market_closed)):
            dates.add(d)
    return dates


def tdx_trade_dates(tdx, days: int = 120) -> list[str]:
    """TDX 指数日 K → 升序交易日列表（不依赖雪球 Cookie）。

    数据源失败/为空时抛中性 ``ValueError``（不依赖 review_account.prep，避免依赖倒置）；
    prep 的日期门禁负责把它包装为既有 ``PrepError``，保持「非交易日 vs 日历源失败」区分。
    """
    from zoneinfo import ZoneInfo

    bars = tdx.get_kline("SH000001", days=days)
    shanghai = ZoneInfo("Asia/Shanghai")
    dates = sorted({
        datetime.fromtimestamp(b.timestamp / 1000, shanghai).strftime("%Y-%m-%d")
        for b in (bars or [])
    })
    if not dates:
        raise ValueError("交易日历数据源失败（TDX 指数日K为空），请稍后重试")
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
