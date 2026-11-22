"""证据层时间/交易日规范化（纯函数）。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .schema import CST

def _moment(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=CST)


def _valid_trade_date(value: str) -> bool:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            continue
        return True
    return False


def _trade_date_key(value: str) -> str:
    return str(value).replace("-", "")
