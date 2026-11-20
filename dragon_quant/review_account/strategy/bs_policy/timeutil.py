"""BS 纯核的时间解析（不读时钟）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

CST = timezone(timedelta(hours=8))


def to_moment(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=CST)
