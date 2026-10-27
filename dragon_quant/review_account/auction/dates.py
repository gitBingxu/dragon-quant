"""集合竞价日期工具 — 归一与交易日解析（纯函数，无 I/O）。

service 不得 import command 层（M2/M3 架构规则），因此这里保留一份与
``commands._shared`` 同口径的实现；两份实现的输入/输出由测试锁定。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dragon_quant.infra import clock


def _normalize_cli_date(d: str) -> str:
    """YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD → YYYY-MM-DD。"""
    d = d.strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    if len(d) == 10 and d[4] == "/" and d[7] == "/":
        return f"{d[:4]}-{d[5:7]}-{d[8:10]}"
    return d


def _today() -> str:
    """Asia/Shanghai 当前日期（与 ``resolve_trade_date(None)`` 同口径）。"""
    tz = timezone(timedelta(hours=8))
    return clock.now(tz).strftime("%Y-%m-%d")


def _parse_auction_date(raw: str) -> str:
    """归一 + 严格校验交易日；非法格式/不存在的日期抛 ValueError。"""
    normalized = _normalize_cli_date(raw)
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(
            f"无效日期: {raw}（支持 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD）"
        ) from exc
