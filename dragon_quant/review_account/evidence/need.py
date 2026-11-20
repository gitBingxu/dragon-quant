"""能力需求与判定结果（纯值对象，供 W2/W3 消费）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from .frozen import freeze, thaw
from .schema import EVIDENCE_SCHEMA_VERSION, PROTECT_NEW_RISK

@dataclass(frozen=True)
class EvidenceNeed:
    """一条规则需要什么证据；``protects`` 区分新增风险与已有保护。"""

    rule_id: str
    capability: str
    scope: tuple[str, ...] = ("*",)
    period: Optional[str] = None
    expected_intervals: tuple[str, ...] = ()
    # 保留字段：本层始终按 time/unit/content 组件重新推导可用性；False 不会
    # 把未验证事实升级成可执行证据。
    require_verified: bool = True
    max_age_seconds: Optional[int] = None
    required_inputs: tuple[str, ...] = ()
    trade_dates: tuple[str, ...] = ()
    protects: str = PROTECT_NEW_RISK
    affected_actions: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "capability": self.capability,
            "scope": list(self.scope),
            "period": self.period,
            "expected_intervals": list(self.expected_intervals),
            "require_verified": self.require_verified,
            "max_age_seconds": self.max_age_seconds,
            "required_inputs": list(self.required_inputs),
            "trade_dates": list(self.trade_dates),
            "protects": self.protects,
            "affected_actions": list(self.affected_actions),
        }


@dataclass(frozen=True)
class CapabilityCheck:
    """能力判定的可追溯结果（来源原因与动作原因分开）。

    ``reasons`` 内部条目同样是递归只读映射，调用方不能通过修改检查结果篡改
    某条规则的判定依据。
    """

    rule_id: str
    capability: str
    status: str                 # usable/missing/partial/stale/future/unverified/invalid
    protects: str
    matched_evidence: tuple[str, ...] = ()
    missing_scopes: tuple[str, ...] = ()
    missing_intervals: tuple[str, ...] = ()
    reasons: tuple[Mapping, ...] = ()
    affected_actions: tuple[str, ...] = ()
    inputs_missing: tuple[str, ...] = ()
    schema_version: str = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons",
                           tuple(freeze(dict(reason)) for reason in self.reasons))

    @property
    def usable(self) -> bool:
        return self.status == "usable"

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "capability": self.capability,
            "status": self.status,
            "protects": self.protects,
            "matched_evidence": list(self.matched_evidence),
            "missing_scopes": list(self.missing_scopes),
            "missing_intervals": list(self.missing_intervals),
            "reasons": [thaw(item) for item in self.reasons],
            "affected_actions": list(self.affected_actions),
            "inputs_missing": list(self.inputs_missing),
            "schema_version": self.schema_version,
        }
