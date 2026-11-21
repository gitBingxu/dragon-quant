"""持续采集的公共类型与常量（无 I/O、无 provider 依赖）。

这些对象是 CLI / 采集器 / 仓储之间唯一的契约：

- :class:`PlanMember` —— 事前母集成员（首次获知、入池原因、跟踪截止）；
- :class:`CaptureRequest` —— 一次采集请求（范围、时窗、预算）；
- :class:`CaptureResult` —— 采集器输出（观测意图、bar 意图、缺口、错误）；
- :data:`TOPIC_WINDOWS` —— 每个主题的可采时窗（超出时窗只能标同类补采）。

时间语义：``received_at`` / ``available_at`` 是**系统**时点，``source_event_time``
是源声明的事件时间；两者绝不互相替代（PIT 纪律）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

PLAN_SCHEMA_VERSION = "v1"
OBSERVATION_SCHEMA_VERSION = "1"

#: 采集主题（与 ``market_observations.obs_type`` / bar period 对齐）。
TOPICS = (
    "auction_path",
    "auction_final",
    "minute_bars",
    "daily_bars",
    "quote",
    "sector_membership",
)

#: 以个股代码为范围的主题（sector_membership 以板块代码为范围）。
CODE_TOPICS = (
    "auction_path",
    "auction_final",
    "minute_bars",
    "daily_bars",
    "quote",
)

#: 可采时窗（本地时间 HH:MM；闭区间）。窗口外只允许显式 ``same_day_recovered``。
TOPIC_WINDOWS: dict[str, tuple[str, str]] = {
    "auction_path": ("09:15", "09:26"),
    "auction_final": ("09:26", "09:35"),
    "minute_bars": ("09:30", "15:05"),
    "daily_bars": ("15:05", "23:59"),
    "quote": ("09:30", "15:00"),
    "sector_membership": ("00:00", "23:59"),
}

#: 原因码（本模块单一 owner；来源原因与动作原因分开）。
REASON_CODES = (
    "missing_api_key",
    "external_source_disabled",
    "auth_failed",
    "permission_denied",
    "rate_limited",
    "source_unavailable",
    "coverage_incomplete",
    "history_auction_path_missing",
    "theme_membership_missing",
    "evidence_stale",
    "pit_unverified",
    "intraday_missing",
    "account_state_missing",
    "risk_budget_missing",
    "probability_unqualified",
)

#: 运行状态（``complete`` 只说明该计划范围的数据要求达到，不等于全市场覆盖）。
RUN_STATUSES = ("planned", "running", "partial", "complete", "missed",
                "failed", "skipped")

#: 观测质量等级。
QUALITY_VERIFIED = "semantics_verified"
QUALITY_UNVERIFIED = "semantics_unverified"
QUALITY_RECOVERED = "same_day_recovered"


class CaptureError(RuntimeError):
    """采集层可预期错误（计划不存在、主题未知、时窗拒绝等）。"""

    def __init__(self, reason: str, message: str, **detail) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.detail = detail

    def as_dict(self) -> dict:
        payload = {"reason": self.reason, "message": self.message}
        payload.update(self.detail)
        return payload


@dataclass(frozen=True)
class PlanMember:
    """事前母集中的一个代码。

    ``first_seen_at`` 是**首次获知**时间，盘中新增只能追加计划修订，不能回写
    盘前母集；``track_until`` 是事前评价截止（不是策略有效期）。
    """

    code: str
    first_seen_at: str
    entry_reason: str
    track_until: str
    name: str = ""
    pool_sources: tuple[str, ...] = ()
    coverage_gaps: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "first_seen_at": self.first_seen_at,
            "entry_reason": self.entry_reason,
            "pool_sources": list(self.pool_sources),
            "track_until": self.track_until,
            "coverage_gaps": list(self.coverage_gaps),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PlanMember":
        return cls(
            code=str(payload.get("code") or ""),
            first_seen_at=str(payload.get("first_seen_at") or ""),
            entry_reason=str(payload.get("entry_reason") or ""),
            track_until=str(payload.get("track_until") or ""),
            name=str(payload.get("name") or ""),
            pool_sources=tuple(payload.get("pool_sources") or ()),
            coverage_gaps=tuple(payload.get("coverage_gaps") or ()),
        )


@dataclass(frozen=True)
class CaptureRequest:
    """一次采集请求；``scope`` 由计划与主题解析而来。"""

    plan_id: str
    topic: str
    trade_date: str
    scope: tuple[str, ...]
    cutoff: str
    request_budget: int
    cursor: Optional[str] = None
    window: tuple[str, str] = ("00:00", "23:59")

    def narrowed(self, codes) -> "CaptureRequest":
        return replace(self, scope=tuple(codes))


@dataclass(frozen=True)
class ObservationRecord:
    """一条待写观测（原始内容 + 单位 + 质量）。"""

    market: str
    code: str
    obs_type: str
    payload: object
    units: dict
    quality: str
    source_event_time: Optional[str] = None
    valid_until: Optional[str] = None
    semantic_key: Optional[str] = None


@dataclass(frozen=True)
class BarRecord:
    """一根待写的不复权 bar。"""

    market: str
    code: str
    period: str
    interval_start: str
    interval_end: str
    open: float
    high: float
    low: float
    close: float
    completeness: str
    volume: Optional[float] = None
    amount: Optional[float] = None
    derived: bool = False
    #: 时间语义与单位语义的**原始事实**（未核实即 unverified）。
    time_semantics: str = "unverified"
    unit_semantics: str = "unverified"
    #: 命中的版本化校准 ID（写入时冻结为行内来源绑定；空 = 未知）。
    calibration_id: str = ""


@dataclass(frozen=True)
class CaptureResult:
    """采集器输出；不含「值得买」之类的业务判断。"""

    topic: str
    expected_scope: tuple[str, ...] = ()
    observed_scope: tuple[str, ...] = ()
    observations: tuple[ObservationRecord, ...] = ()
    bars: tuple[BarRecord, ...] = ()
    errors: tuple[dict, ...] = ()
    gaps: tuple[dict, ...] = ()
    quality: str = QUALITY_UNVERIFIED
    status: str = "complete"
    notes: tuple[str, ...] = field(default_factory=tuple)

    def with_status(self, status: str) -> "CaptureResult":
        return replace(self, status=status)


def window_state(topic: str, hhmm: str) -> str:
    """``before`` / ``inside`` / ``after``：``hhmm`` 与主题时窗比较。"""
    start, end = TOPIC_WINDOWS.get(topic, ("00:00", "23:59"))
    start = start.replace(":", "")
    end = end.replace(":", "")
    if hhmm < start:
        return "before"
    if hhmm > end:
        return "after"
    return "inside"


def normalize_hhmm(value: str) -> str:
    text = str(value or "").strip()
    if len(text) != 4 or not text.isdigit():
        raise CaptureError("invalid_slot", f"时槽必须是 HHMM: {value!r}")
    hour, minute = int(text[:2]), int(text[2:])
    if hour > 23 or minute > 59:
        raise CaptureError("invalid_slot", f"时槽越界: {value!r}")
    return text
