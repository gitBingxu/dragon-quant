"""auction.normalize — 时间 / 窗口 / 市场识别 / 增量序列（纯函数）。

语义契约（方案 §6.1 / §7.1）：
  - ``items[].time`` 为 ``HH:MM:SS``，只信任 09:15:00–09:25:00；
  - ``matched`` 为**虚拟匹配量（累计，手）**，09:20 前可下降、09:20 后不下降；
  - ``unmatched`` 为**带符号时点快照**（正=买方剩余，负=卖方剩余），禁止全窗口求和；
  - 核心评分窗口固定为 [09:20:00, 09:25:00]，末点不要求正好 09:25:00。

本模块无 I/O、无 wall clock、无随机。
"""

from __future__ import annotations

from typing import Optional

from . import registry as R


def to_seconds(value) -> Optional[int]:
    """``'HH:MM:SS'`` 或 ``datetime.time`` → 自 0 点起的秒数；非法 → None。"""
    if isinstance(value, str):
        try:
            h, m, s = value.strip().split(":")
            return int(h) * 3600 + int(m) * 60 + int(int(float(s)))
        except (ValueError, TypeError):
            return None
    if hasattr(value, "hour"):
        return int(value.hour) * 3600 + int(value.minute) * 60 + int(value.second)
    return None


def market_of(code: str) -> str:
    """代码前缀 → 市场：6→sh，0/3→sz，4/8/9→bj，其余 unknown。"""
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(digits) != 6:
        return "unknown"
    head = digits[0]
    if head == "6":
        return "sh"
    if head in ("0", "3"):
        return "sz"
    if head in ("4", "8", "9"):
        return "bj"
    return "unknown"


def _number(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def normalize(items: list[dict]) -> dict:
    """排序 → 秒数 → 窗口切片。返回列表与索引，供 features/events 复用。"""
    rows = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        seconds = to_seconds(raw.get("time"))
        if seconds is None:
            continue
        price = _number(raw.get("price"))
        matched = _number(raw.get("matched"))
        unmatched = _number(raw.get("unmatched"))
        if price is None or matched is None or unmatched is None:
            continue
        rows.append((seconds, price, matched, unmatched))

    rows.sort(key=lambda row: row[0])          # 稳定排序，同秒保留原序

    times = [row[0] for row in rows]
    prices = [row[1] for row in rows]
    matched = [row[2] for row in rows]
    unmatched = [row[3] for row in rows]

    # 截断 09:25:00 之后的异常/延迟记录
    truncated = any(t > R.AUCTION_CLOSE for t in times)
    if truncated:
        keep = [i for i, t in enumerate(times) if t <= R.AUCTION_CLOSE]
        times = [times[i] for i in keep]
        prices = [prices[i] for i in keep]
        matched = [matched[i] for i in keep]
        unmatched = [unmatched[i] for i in keep]

    n = len(times)
    core_idx = [i for i, t in enumerate(times) if R.NO_CANCEL <= t <= R.AUCTION_CLOSE]
    pre_idx = [i for i, t in enumerate(times) if R.AUCTION_OPEN <= t < R.NO_CANCEL]
    post20_idx = list(core_idx)
    front_idx = [i for i, t in enumerate(times)
                 if R.NO_CANCEL <= t < R.TAIL_DEFAULT_START]

    tail_idx = [i for i, t in enumerate(times)
                if R.TAIL_DEFAULT_START <= t <= R.AUCTION_CLOSE]
    tail_extended = False
    if len(tail_idx) < R.TAIL_MIN_BARS:
        extended = [i for i, t in enumerate(times)
                    if R.TAIL_EXTENDED_START <= t <= R.AUCTION_CLOSE]
        if len(extended) > len(tail_idx):
            tail_idx = extended
            tail_extended = True

    at20_idx = core_idx[0] if core_idx else None
    tail_start_idx = tail_idx[0] if tail_idx else None
    last_idx = core_idx[-1] if core_idx else (n - 1 if n else None)

    return {
        "rows": rows,
        "times": times,
        "prices": prices,
        "matched": matched,
        "unmatched": unmatched,
        "n": n,
        "pre_idx": pre_idx,
        "core_idx": core_idx,
        "post20_idx": post20_idx,
        "front_idx": front_idx,
        "tail_idx": tail_idx,
        "tail_extended": tail_extended,
        "at20_idx": at20_idx,
        "tail_start_idx": tail_start_idx,
        "last_idx": last_idx,
        "truncated_after_close": truncated,
    }


def matched_deltas(norm: dict) -> list[Optional[float]]:
    """逐点增量（首点 None，避免把累计首值当成交）。"""
    matched = norm["matched"]
    if not matched:
        return []
    return [None] + [matched[i] - matched[i - 1] for i in range(1, len(matched))]
