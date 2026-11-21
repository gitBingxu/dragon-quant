"""采集器公共协议与数值/时点辅助（无具体主题）。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..types import CaptureRequest, CaptureResult

CST = timezone(timedelta(hours=8))

_CLOCK_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)(:([0-5]\d))?$")


def _source_event_time(trade_date: str, raw) -> Optional[str]:
    """把源声明的 HH:MM[:SS] 组成当日事件时间；缺失/非法返回 None。

    绝不回填「09:25」之类的常量：源没给时点就如实留空。
    """
    text = str(raw or "")
    match = _CLOCK_RE.match(text)
    if not match:
        return None
    clock = text if match.group(3) else f"{text}:00"
    return f"{trade_date}T{clock}+08:00"


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _capture_semantics(provider, period: str) -> dict:
    """按 provider 声明的解析器身份解析版本化校准（未知即 unverified）。"""
    from dragon_quant.infra.calibration import semantics_for_capture

    provenance = None
    probe = getattr(provider, "capture_provenance", None)
    if callable(probe):
        try:
            provenance = probe()
        except Exception:  # noqa: BLE001 - 身份探测失败按未知处理，不伪造 verified
            provenance = None
    return semantics_for_capture(provenance, period=period, category="stock")


def _moment_from_ms(value) -> Optional[datetime]:
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    if millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000, tz=CST)


def _market_of(code: str) -> str:
    code = str(code or "")
    if code.startswith(("6", "5", "9")):
        return "sh"
    if code.startswith(("0", "1", "2", "3")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    return "unknown"


def _finite(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _parse_moment(value: str) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=CST)


def _valid_ohlc(open_value, high, low, close) -> Optional[tuple]:
    """规范化 bar 的价格校验：有限、正值、OHLC 自洽；不合法返回 ``None``。

    绝不用 0 补造有效价格（缺值 = 未知，不是零）。
    """
    values = [_finite(open_value), _finite(high), _finite(low), _finite(close)]
    if any(value is None for value in values):
        return None
    open_px, high_px, low_px, close_px = values
    if min(open_px, high_px, low_px, close_px) <= 0:
        return None
    if (high_px < max(open_px, low_px, close_px)
            or low_px > min(open_px, high_px, close_px)):
        return None
    return open_px, high_px, low_px, close_px


def _non_negative(value) -> Optional[float]:
    number = _finite(value)
    if number is None or number < 0:
        return None
    return number


def _in_session(moment: datetime) -> bool:
    """A 股连续竞价时段（本地时间）：09:30–11:30 / 13:00–15:00。"""
    minutes = moment.hour * 60 + moment.minute
    morning = 9 * 60 + 30 <= minutes <= 11 * 60 + 30
    afternoon = 13 * 60 <= minutes <= 15 * 60
    return morning or afternoon


def _status(expected: int, observed: int, errors: int) -> str:
    if expected <= 0:
        return "skipped"
    if errors and not observed:
        return "failed"
    if errors or observed < expected:
        return "partial"
    return "complete"


class Collector:
    """采集器协议基类：只声明主题与范围解析。"""

    topic = ""
    provider_name = "tdx"
    requires_providers = True

    def resolve_scope(self, plan: dict) -> tuple[str, ...]:
        if self.topic == "sector_membership":
            config = plan.get("task_config") or {}
            return tuple(str(item) for item in (config.get("sectors") or ()))
        return tuple(
            str(item.get("code"))
            for item in (plan.get("members") or [])
            if isinstance(item, dict) and item.get("code")
        )

    def collect(self, request: CaptureRequest, *, provider,
                plan: dict) -> CaptureResult:  # pragma: no cover - 协议
        raise NotImplementedError


# ─── 竞价过程 ────────────────────────────────────────────────────────────────



def _parse_cutoff(value: str) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=CST)
    return moment
