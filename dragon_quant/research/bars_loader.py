"""本地日 K 递归分批加载（方案 §8.2）。

调用方注入 ``load_bars(codes, date_from, date_to) -> dict``；结果出现
``truncated`` 时先按日期二分，再按 code 二分。单 code + 单日仍截断 → 抛
:class:`BarsTruncated`，由调用方整体降级为 ``local_bars_truncated``，**绝不**
输出部分统计。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Iterable, Mapping

__all__ = ["BarsTruncated", "load_bars_resilient"]


class BarsTruncated(RuntimeError):
    """单 code + 单日仍触发仓储截断，无法继续拆分。"""


def _shift(day: str, days: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=days)).strftime(
        "%Y-%m-%d")


def _split_date_range(date_from: str, date_to: str) -> tuple[
        tuple[str, str], tuple[str, str]]:
    start = datetime.strptime(date_from, "%Y-%m-%d")
    end = datetime.strptime(date_to, "%Y-%m-%d")
    middle = start + timedelta(days=max(0, (end - start).days // 2))
    mid = middle.strftime("%Y-%m-%d")
    return (date_from, mid), (_shift(mid, 1), date_to)


def load_bars_resilient(
    load_bars: Callable[[list[str], str, str], Mapping],
    codes: Iterable[str],
    date_from: str,
    date_to: str,
) -> dict:
    """加载并合并日 K；截断时递归拆分，无法拆分则抛 ``BarsTruncated``。"""

    wanted = sorted({str(code) for code in codes if code})
    merged: dict[tuple[str, str], dict] = {}

    def collect(batch_codes: list[str], start: str, end: str) -> None:
        if not batch_codes:
            return
        loaded = load_bars(batch_codes, start, end) or {}
        if not bool(loaded.get("truncated")):
            for code, bars in (loaded.get("rows") or {}).items():
                if not isinstance(bars, list):
                    continue
                for bar in bars:
                    if not isinstance(bar, dict):
                        continue
                    day = str(bar.get("date") or "")
                    if not day:
                        continue
                    if day < start or day > end:
                        continue
                    merged[(str(code), day)] = bar
            return

        if start < end:
            (left_start, left_end), (right_start, right_end) = (
                _split_date_range(start, end))
            collect(batch_codes, left_start, left_end)
            collect(batch_codes, right_start, right_end)
            return

        if len(batch_codes) > 1:
            middle = len(batch_codes) // 2
            collect(batch_codes[:middle], start, end)
            collect(batch_codes[middle:], start, end)
            return

        raise BarsTruncated(
            f"单 code + 单日仍截断: code={batch_codes[0]} date={start}")

    collect(wanted, date_from, date_to)

    grouped: dict[str, list[dict]] = {}
    for (code, _day), bar in merged.items():
        grouped.setdefault(code, []).append(bar)
    for bars in grouped.values():
        bars.sort(key=lambda item: str(item.get("date") or ""))

    bar_dates = sorted({day for _code, day in merged})
    return {
        "rows": grouped,
        "truncated": False,
        "bar_dates": bar_dates,
        "codes": sorted(grouped),
    }
