"""单条证据事实：不可变值、形状/时间校验与序列化。"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Mapping, Optional

from .dates import _moment, _trade_date_key, _valid_trade_date
from .frozen import (
    _integer,
    _optional_mapping,
    _strict_keys,
    _strict_mapping,
    _string,
    freeze,
    thaw,
)
from .schema import (
    EVIDENCE_SCHEMA_VERSION,
    QUALITY_UNVERIFIED,
    QUALITY_VERIFIED,
    SUPPORTED_SCHEMA_VERSIONS,
    UNVERIFIED,
    VERIFIED,
    EvidenceError,
    _PROTECTED_CAPABILITIES,
)
from .semantics import derive_usable

@dataclass(frozen=True)
class EvidenceFact:
    """一条当时可用的证据事实（值 + 来源/单位/版本/质量）。

    ``usable`` 是派生字段：构造与反序列化都会按能力要求重算。``units`` /
    ``payload`` / ``calibration`` 是递归只读映射；``to_dict`` 输出普通结构。
    """

    evidence_id: str
    capability: str
    source: str
    obs_type: str
    market: str
    code: str
    trade_date: str
    available_at: str
    received_at: str
    first_received_at: str
    revision: int
    content_hash: str
    source_quality: str = QUALITY_UNVERIFIED
    quality: str = QUALITY_UNVERIFIED
    usable: bool = False
    units: Mapping = field(default_factory=dict)
    payload: Mapping = field(default_factory=dict)
    calibration: Mapping = field(default_factory=dict)
    period: Optional[str] = None
    adjustment: Optional[str] = None
    interval_start: Optional[str] = None
    interval_end: Optional[str] = None
    completeness: Optional[str] = None
    time_semantics: str = UNVERIFIED
    unit_semantics: str = UNVERIFIED
    content_semantics: str = UNVERIFIED
    derived: bool = False
    schema_version: str = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "units", freeze(dict(self.units or {})))
        object.__setattr__(self, "payload", freeze(dict(self.payload or {})))
        object.__setattr__(self, "calibration",
                           freeze(dict(self.calibration or {})))
        object.__setattr__(self, "usable", bool(derive_usable(self)))

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "capability": self.capability,
            "source": self.source,
            "obs_type": self.obs_type,
            "market": self.market,
            "code": self.code,
            "trade_date": self.trade_date,
            "available_at": self.available_at,
            "received_at": self.received_at,
            "first_received_at": self.first_received_at,
            "revision": self.revision,
            "content_hash": self.content_hash,
            "source_quality": self.source_quality,
            "quality": self.quality,
            "usable": self.usable,
            "units": thaw(self.units),
            "payload": thaw(self.payload),
            "calibration": thaw(self.calibration),
            "period": self.period,
            "adjustment": self.adjustment,
            "interval_start": self.interval_start,
            "interval_end": self.interval_end,
            "completeness": self.completeness,
            "time_semantics": self.time_semantics,
            "unit_semantics": self.unit_semantics,
            "content_semantics": self.content_semantics,
            "derived": self.derived,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> "EvidenceFact":
        payload = _strict_mapping(payload, what="EvidenceFact")
        _strict_keys(payload, [item.name for item in fields(cls)],
                     what="EvidenceFact")
        declared_usable = payload.get("usable", False)
        if not isinstance(declared_usable, bool):
            raise EvidenceError("usable 必须是布尔值", reason="schema_mismatch")
        fact = cls(
            evidence_id=_string(payload, "evidence_id", required=True),
            capability=_string(payload, "capability", required=True),
            source=_string(payload, "source", required=True),
            obs_type=_string(payload, "obs_type", required=True),
            market=_string(payload, "market"),
            code=_string(payload, "code"),
            trade_date=_string(payload, "trade_date"),
            available_at=_string(payload, "available_at", required=True),
            received_at=_string(payload, "received_at"),
            first_received_at=_string(payload, "first_received_at"),
            revision=_integer(payload, "revision"),
            content_hash=_string(payload, "content_hash"),
            source_quality=_string(payload, "source_quality",
                                   default=QUALITY_UNVERIFIED),
            quality=_string(payload, "quality", default=QUALITY_UNVERIFIED),
            usable=declared_usable,
            units=_optional_mapping(payload, "units", what="units"),
            payload=_optional_mapping(payload, "payload", what="payload"),
            calibration=_optional_mapping(payload, "calibration",
                                          what="calibration"),
            period=payload.get("period"),
            adjustment=payload.get("adjustment"),
            interval_start=payload.get("interval_start"),
            interval_end=payload.get("interval_end"),
            completeness=payload.get("completeness"),
            time_semantics=_string(payload, "time_semantics",
                                   default=UNVERIFIED),
            unit_semantics=_string(payload, "unit_semantics",
                                   default=UNVERIFIED),
            content_semantics=_string(payload, "content_semantics",
                                      default=UNVERIFIED),
            derived=bool(payload.get("derived", False)),
            schema_version=_string(payload, "schema_version",
                                   default=EVIDENCE_SCHEMA_VERSION),
        )
        _validate_fact(fact)
        if declared_usable != fact.usable:
            raise EvidenceError(
                f"事实 {fact.evidence_id} 的 usable 与语义要求不一致",
                reason="usable_mismatch")
        return fact


def _validate_fact(fact: EvidenceFact) -> None:
    """加载/构造冻结数据时的形状、时间与区间校验。"""
    if fact.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise EvidenceError(
            f"不支持的事实 schema_version: {fact.schema_version}",
            reason="unsupported_schema")
    if fact.capability not in _PROTECTED_CAPABILITIES:
        raise EvidenceError(f"未知能力: {fact.capability}",
                            reason="schema_mismatch")
    for name in ("time_semantics", "unit_semantics", "content_semantics"):
        if getattr(fact, name) not in (VERIFIED, UNVERIFIED):
            raise EvidenceError(f"{name} 非法: {getattr(fact, name)!r}",
                                reason="schema_mismatch")
    if fact.quality not in (QUALITY_VERIFIED, QUALITY_UNVERIFIED):
        raise EvidenceError(f"quality 非法: {fact.quality!r}",
                            reason="schema_mismatch")
    if _moment(fact.available_at) is None:
        raise EvidenceError(f"非法 available_at: {fact.available_at!r}",
                            reason="invalid_time")
    if fact.received_at and _moment(fact.received_at) is None:
        raise EvidenceError(f"非法 received_at: {fact.received_at!r}",
                            reason="invalid_time")
    if fact.first_received_at and _moment(fact.first_received_at) is None:
        raise EvidenceError(f"非法 first_received_at: {fact.first_received_at!r}",
                            reason="invalid_time")
    if fact.trade_date and not _valid_trade_date(fact.trade_date):
        raise EvidenceError(f"非法 trade_date: {fact.trade_date!r}",
                            reason="invalid_time")
    start = end = None
    if fact.interval_start:
        start = _moment(fact.interval_start)
        if start is None:
            raise EvidenceError(f"非法 interval_start: {fact.interval_start!r}",
                                reason="invalid_time")
    if fact.interval_end:
        end = _moment(fact.interval_end)
        if end is None:
            raise EvidenceError(f"非法 interval_end: {fact.interval_end!r}",
                                reason="invalid_time")
    if start is not None and end is not None and end < start:
        raise EvidenceError("interval_end 早于 interval_start",
                            reason="invalid_interval")
    if end is not None and fact.trade_date and _valid_trade_date(fact.trade_date):
        if end.strftime("%Y%m%d") < _trade_date_key(fact.trade_date):
            # bar 区间落在其交易日之前：明显不一致的冻结数据。
            raise EvidenceError("interval_end 早于 trade_date",
                                reason="invalid_interval")
