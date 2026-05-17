"""
去重逻辑 — 同股在 5 个交易日内重复扫描时跳过复盘

从 SQLite 查询该股票最近一次 review，检查距本次 scan 是否 ≤ 5 个交易日。
"""

from datetime import datetime, timedelta
from typing import Tuple

from dragon_quant.storage import db


def should_skip(code: str, scan_date: str) -> Tuple[bool, str]:
    """
    检查该股票是否需要跳过复盘。

    Args:
        code: 股票代码
        scan_date: 本次扫描日期 "2026-05-10"

    Returns:
        (skip, reason)
        skip=True 表示应跳过本次复盘
    """
    history = db.get_stock_review_history(code, limit=1)
    if not history:
        return False, ""

    last_entry = history[0].get("entry_date", "")
    if not last_entry:
        return False, ""

    last_dt = _parse_date(last_entry)
    scan_dt = _parse_date(scan_date)

    trading_gap = _count_trading_days_between(last_dt, scan_dt)

    if trading_gap <= 5:
        return True, f"距上次 review 仅 {trading_gap} 个交易日，已跳过"
    return False, ""


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s[:10], "%Y-%m-%d")


def _count_trading_days_between(start: datetime, end: datetime) -> int:
    """计算两个日期之间的交易日数（不含周末，含边界）"""
    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count