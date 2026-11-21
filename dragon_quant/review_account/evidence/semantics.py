"""来源语义校准、按能力字段要求与 usable 推导（纯计算）。"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Optional

from .frozen import _strict_keys, _strict_mapping, _string

if TYPE_CHECKING:
    from .facts import EvidenceFact
from .schema import (
    CALIBRATION_VERSION,
    CAPABILITIES,
    QUALITY_UNVERIFIED,
    QUALITY_VERIFIED,
    SUPPORTED_SCHEMA_VERSIONS,
    TERMINAL_SOURCE_EXCHANGE_FINAL,
    UNVERIFIED,
    VERIFIED,
    EvidenceError,
)

@dataclass(frozen=True)
class SourceSemantics:
    """版本化的来源语义校准：来源 + 数据类型 → 时间/单位/内容是否已核实。

    这是**唯一**允许把 semantics 标 verified 的地方。三个组件必须分别声明：
    ``time_semantics`` / ``unit_semantics`` / ``content_semantics``；``quality``
    只是汇总标签，**不能被任何判定路径当成绕过组件要求的通行证**。

    真实校准必须记录依据（``evidence`` 非空且不是 ``unverified``）；测试/演示
    注入的规格必须显式 ``fixture=True``，生产默认清单不得把单位/内容硬设
    verified。
    """

    source: str
    kind: str
    time_semantics: str = UNVERIFIED
    unit_semantics: str = UNVERIFIED
    content_semantics: str = UNVERIFIED
    quality: str = QUALITY_UNVERIFIED
    version: str = CALIBRATION_VERSION
    evidence: str = "unverified"
    fixture: bool = False

    def __post_init__(self) -> None:
        for name in ("time_semantics", "unit_semantics", "content_semantics"):
            value = getattr(self, name)
            if value not in (VERIFIED, UNVERIFIED):
                raise EvidenceError(
                    f"{name} 必须是 verified/unverified，实际 {value!r}",
                    reason="calibration_invalid")
        if self.quality not in (QUALITY_VERIFIED, QUALITY_UNVERIFIED):
            raise EvidenceError(
                f"quality 必须是 {QUALITY_VERIFIED}/"
                f"{QUALITY_UNVERIFIED}，实际 {self.quality!r}",
                reason="calibration_invalid")
        if not self.source or not self.kind:
            raise EvidenceError("校准必须给定 source 与 kind",
                                reason="calibration_invalid")
        if not self.version or not self.version.strip():
            raise EvidenceError("校准必须给定非空 version",
                                reason="calibration_invalid")
        components = (self.time_semantics, self.unit_semantics,
                      self.content_semantics)
        if VERIFIED in components and (
                not self.evidence or self.evidence.strip() == UNVERIFIED):
            raise EvidenceError(
                "verified 校准必须记录真实依据（evidence 不能为空/unverified）",
                reason="calibration_invalid")

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "kind": self.kind,
            "time_semantics": self.time_semantics,
            "unit_semantics": self.unit_semantics,
            "content_semantics": self.content_semantics,
            "quality": self.quality,
            "version": self.version,
            "evidence": self.evidence,
            "fixture": self.fixture,
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> "SourceSemantics":
        payload = _strict_mapping(payload, what="SourceSemantics")
        _strict_keys(payload, [item.name for item in fields(cls)],
                     what="SourceSemantics")
        return cls(
            source=_string(payload, "source", required=True),
            kind=_string(payload, "kind", required=True),
            time_semantics=_string(payload, "time_semantics",
                                   default=UNVERIFIED),
            unit_semantics=_string(payload, "unit_semantics",
                                   default=UNVERIFIED),
            content_semantics=_string(payload, "content_semantics",
                                      default=UNVERIFIED),
            quality=_string(payload, "quality", default=QUALITY_UNVERIFIED),
            version=_string(payload, "version", default=CALIBRATION_VERSION),
            evidence=_string(payload, "evidence", default=UNVERIFIED),
            fixture=bool(payload.get("fixture", False)),
        )


#: 生产默认：只声明来源契约（时间）已核对；**单位与内容一律 unverified**，
#: 任何 verified 升级都必须新增版本化条目并记录依据。
DEFAULT_SEMANTICS: tuple[SourceSemantics, ...] = (
    SourceSemantics(source="tdx", kind="auction_path",
                    time_semantics=VERIFIED,
                    evidence="TDX auction rows carry source HH:MM:SS timestamps"),
    SourceSemantics(source="tdx", kind="auction_final",
                    time_semantics=VERIFIED,
                    evidence="TDX auction rows carry source HH:MM:SS timestamps"),
    SourceSemantics(source="tdx", kind="1min"),
    SourceSemantics(source="tdx", kind="5min"),
    SourceSemantics(source="tdx", kind="daily", time_semantics=VERIFIED,
                    evidence="date-level unadjusted daily bars"),
    SourceSemantics(source="tdx", kind="quote"),
    SourceSemantics(source="tdx", kind="sector_membership",
                    time_semantics=VERIFIED,
                    evidence="snapshot membership observed at collection time"),
)


def semantics_for(source: str, kind: str,
                  calibrations: Optional[Iterable[SourceSemantics]] = None
                  ) -> SourceSemantics:
    pool = tuple(calibrations) if calibrations is not None else DEFAULT_SEMANTICS
    for item in pool:
        if item.source == source and item.kind == kind:
            return item
    return SourceSemantics(source=source, kind=kind)


# ─── 能力对事实的显式要求 ────────────────────────────────────────────────────


@dataclass(frozen=True)
class CapabilityRequirement:
    """一项能力实际消费的字段要求（显式、按能力，不搞一刀切）。"""

    require_time: bool
    unit_keys: tuple[str, ...] = ()
    require_content: bool = True
    require_final: bool = False       # bar: completeness == final
    require_unadjusted: bool = False  # bar: adjustment == none
    payload_kind: str = ""            # bar/auction_final/auction_path/quote/membership


CAPABILITY_REQUIREMENTS: dict[str, CapabilityRequirement] = {
    "daily_bars": CapabilityRequirement(
        require_time=True, unit_keys=("volume", "amount"),
        require_final=True, require_unadjusted=True, payload_kind="bar"),
    "intraday_bars": CapabilityRequirement(
        require_time=True, unit_keys=("volume", "amount"),
        require_final=True, require_unadjusted=True, payload_kind="bar"),
    "auction_final": CapabilityRequirement(
        require_time=True, unit_keys=("price", "amount", "matched"),
        payload_kind="auction_final"),
    "auction_path": CapabilityRequirement(
        require_time=True, unit_keys=("price", "matched", "unmatched"),
        payload_kind="auction_path"),
    "market_context": CapabilityRequirement(
        require_time=True, unit_keys=("price",), payload_kind="quote"),
    # 题材成员不含金额：只要求内容校准与名单完整性，不套用价格/数量单位。
    "theme_membership": CapabilityRequirement(
        require_time=False, require_content=True, payload_kind="membership"),
}

_PROTECTED_CAPABILITIES = frozenset(CAPABILITIES)


def _finite_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bar_payload_valid(payload: Mapping) -> bool:
    keys = ("open", "high", "low", "close", "volume", "amount")
    if any(key not in payload for key in keys):
        return False
    values = {key: _finite_number(payload.get(key)) for key in keys}
    if any(value is None for value in values.values()):
        return False
    open_, high, low, close = (values["open"], values["high"],
                               values["low"], values["close"])
    if min(open_, high, low, close) <= 0:
        return False
    if values["volume"] < 0 or values["amount"] < 0:
        return False
    epsilon = 1e-9
    if high + epsilon < max(open_, close, low):
        return False
    if low - epsilon > min(open_, close, high):
        return False
    return True


def _auction_final_payload_valid(payload: Mapping) -> bool:
    if payload.get("terminal_source") != TERMINAL_SOURCE_EXCHANGE_FINAL:
        return False
    price = _finite_number(payload.get("price"))
    matched = _finite_number(payload.get("matched"))
    amount = _finite_number(payload.get("amount"))
    if price is None or price <= 0:
        return False
    if matched is None or matched < 0:
        return False
    if amount is None or amount < 0:
        return False
    return True


def _auction_path_payload_valid(payload: Mapping) -> bool:
    items = payload.get("items")
    if not isinstance(items, (list, tuple)) or not items:
        return False
    for item in items:
        if not isinstance(item, Mapping):
            return False
        if not isinstance(item.get("time"), str) or not item.get("time"):
            return False
        for key in ("price", "matched", "unmatched"):
            if _finite_number(item.get(key)) is None:
                return False
    return True


def _quote_payload_valid(payload: Mapping) -> bool:
    price = _finite_number(payload.get("price"))
    return price is not None and price > 0


def _membership_payload_valid(payload: Mapping) -> bool:
    codes = payload.get("codes")
    if not isinstance(codes, (list, tuple)) or not codes:
        return False
    if not all(isinstance(code, str) and code for code in codes):
        return False
    # 名单完整性必须显式声明；缺页快照不能当作完整成员表。
    return payload.get("page_completeness") in ("complete", "verified")


_PAYLOAD_VALIDATORS = {
    "bar": _bar_payload_valid,
    "auction_final": _auction_final_payload_valid,
    "auction_path": _auction_path_payload_valid,
    "quote": _quote_payload_valid,
    "membership": _membership_payload_valid,
}


def bar_payload_valid(payload: Mapping) -> bool:
    """公开的 bar 内容/有限值校验（事件执行与能力判定共用同一门禁）。"""
    return _bar_payload_valid(payload)


def derive_usable(fact: "EvidenceFact") -> bool:
    """按能力显式要求重新推导 ``usable``；从不信任调用方传入的布尔。"""
    requirement = CAPABILITY_REQUIREMENTS.get(fact.capability)
    if requirement is None:
        return False
    if not fact.evidence_id or not fact.code:
        return False
    if fact.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        return False
    if requirement.require_time and fact.time_semantics != VERIFIED:
        return False
    if requirement.require_content and fact.content_semantics != VERIFIED:
        return False
    for key in requirement.unit_keys:
        if fact.units.get(key) != VERIFIED:
            return False
    if requirement.require_final and fact.completeness != "final":
        return False
    if requirement.require_unadjusted and fact.adjustment != "none":
        return False
    if fact.derived:
        return False
    validator = _PAYLOAD_VALIDATORS.get(requirement.payload_kind)
    if validator is not None and not validator(fact.payload):
        return False
    return True


# ─── 证据事实 ────────────────────────────────────────────────────────────────
