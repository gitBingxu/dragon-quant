"""纯参数解析函数（无 I/O，便于单测）。"""

from __future__ import annotations

import datetime as dt

SOURCES = ("v1", "v2")


def normalize_date(raw: str | None) -> str:
    """``YYYYMMDD`` / ``YYYY-MM-DD`` → ``YYYY-MM-DD``；非法抛 ``ValueError``。"""
    value = (raw or "").strip()
    if len(value) == 8 and value.isdigit():
        value = f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    if not value:
        raise ValueError("date is required")
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD or YYYYMMDD") from exc
    return parsed.strftime("%Y-%m-%d")


def parse_source(value: str | None, default: str = "v2") -> str:
    """source 白名单（v1/v2）；非法值回退默认，与旧 ``_parse_source`` 一致。"""
    source = (value or default or "v2").lower().strip()
    return source if source in SOURCES else "v2"


def parse_caliber(value: str | None, source: str = "v2") -> str:
    """与 ``dragons.resolve_caliber`` 同规则：v1 强制 loose，v2 仅显式 loose。"""
    if parse_source(source) == "v1":
        return "loose"
    return "loose" if (value or "").strip().lower() == "loose" else "strict"


def clamp_limit(value: int | None, default: int = 20, maximum: int = 500) -> int:
    """limit 越界收敛到 ``[1, maximum]``；缺省取 ``default``。"""
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(maximum, number))
