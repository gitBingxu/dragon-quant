"""premarket.main — Facade：解析日期、注入时钟、组装只读上下文。

``run_premarket`` 只调用 snapshot Adapter 与纯投影；不构造 provider、不重算、
不写库。``services`` 仅为签名兼容保留（CLI 的 ``_never_providers`` 保证为空）。
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any, Optional

from dragon_quant.infra import clock

from .context import CONTEXT_VERSION, build_context
from .snapshot import load_auction, load_local_trade_days, load_prep

SHANGHAI = ZoneInfo("Asia/Shanghai")


class PremarketError(ValueError):
    """参数错误（日期格式 / 未来日期），CLI 映射为 exit 2。"""


def normalize_trade_date(raw: str) -> str:
    """``YYYYMMDD`` / ``YYYY-MM-DD`` / ``YYYY/MM/DD`` → ``YYYY-MM-DD``。"""

    text = (raw or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    elif (len(text) == 10 and text[4] in "-/" and text[7] in "-/"):
        text = f"{text[:4]}-{text[5:7]}-{text[8:10]}"
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise PremarketError(
            f"非法日期 {raw!r}：支持 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD") \
            from exc
    return parsed.isoformat()


def resolve_trade_date(raw: Optional[str], *, now: datetime) -> str:
    if raw:
        return normalize_trade_date(raw)
    return now.astimezone(SHANGHAI).strftime("%Y-%m-%d")


def _validate_future(iso_date: str, now: datetime) -> None:
    if date.fromisoformat(iso_date) > now.astimezone(SHANGHAI).date():
        raise PremarketError(f"盘前预案日期 {iso_date} 是未来日期")


def _planned_codes(prep: dict) -> set[str]:
    result = prep.get("result") if isinstance(prep, dict) else None
    if not isinstance(result, dict):
        return set()
    candidates = result.get("review_candidates")
    return {
        candidate.get("code")
        for candidate in (candidates if isinstance(candidates, list) else [])
        if isinstance(candidate, dict) and isinstance(candidate.get("code"), str)
    }


def run_premarket(trade_date: Optional[str], *, now: Optional[datetime] = None,
                  services: Any = None) -> dict:
    """构建只读盘前上下文（T0；不触网、不写库、不重算）。"""

    _ = services  # 只接受 clock 注入；provider 必须为 None（不构造）
    moment = now if now is not None else clock.now(SHANGHAI)
    iso_date = resolve_trade_date(trade_date, now=moment)
    _validate_future(iso_date, moment)
    run_at = moment.astimezone(SHANGHAI).isoformat(timespec="seconds")

    local_trade_days = load_local_trade_days()
    prep = load_prep(iso_date)
    auction = load_auction(iso_date, now=moment,
                           planned_codes=_planned_codes(prep))
    return build_context(
        prep, auction, trade_date=iso_date, run_at=run_at,
        local_trade_days=local_trade_days)


__all__ = [
    "CONTEXT_VERSION",
    "PremarketError",
    "normalize_trade_date",
    "resolve_trade_date",
    "run_premarket",
]
