"""候选与已到达确认事实（W2 输入契约）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from dragon_quant.review_account.evidence import CapabilityCheck, thaw

from .base import PolicyInputError


@dataclass(frozen=True)
class EvidenceConfirmation:
    """一次**已实际到达**的确认事实（身份/时点/版本/快照），不是裸 usable 布尔。"""

    rule_id: str
    evidence_ids: tuple[str, ...]
    available_at: str
    version: str
    snapshot_id: str = ""
    independent_of: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids or ()))
        object.__setattr__(self, "independent_of",
                           tuple(self.independent_of or ()))
        if not self.rule_id or not self.evidence_ids:
            raise PolicyInputError("确认事实必须带 rule_id 与 evidence_ids")
        if not self.available_at:
            raise PolicyInputError("确认事实必须带 available_at")
        if not self.version:
            raise PolicyInputError("确认事实必须带版本")

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "evidence_ids": list(self.evidence_ids),
            "available_at": self.available_at,
            "version": self.version,
            "snapshot_id": self.snapshot_id,
            "independent_of": list(self.independent_of),
        }



@dataclass(frozen=True)
class CandidateView:
    """事前候选：绝对资格/可交易性/结构/压力估计 + W1 能力检查。"""

    code: str
    name: str = ""
    theme_ids: tuple[str, ...] = ()
    rank: Optional[int] = None
    eligible: bool = False
    eligibility_reasons: tuple[str, ...] = ()
    tradable: bool = False
    tradability_reasons: tuple[str, ...] = ()
    price: Optional[float] = None
    pressure_pct: Optional[float] = None
    structure: Mapping = field(default_factory=dict)
    evidence_checks: tuple[CapabilityCheck, ...] = ()
    confirmations: tuple["EvidenceConfirmation", ...] = ()
    entry_version: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "theme_ids", tuple(self.theme_ids or ()))
        object.__setattr__(self, "evidence_checks", tuple(self.evidence_checks or ()))
        object.__setattr__(self, "confirmations", tuple(self.confirmations or ()))

    def confirmation(self, rule_id: str) -> Optional["EvidenceConfirmation"]:
        for item in self.confirmations:
            if item.rule_id == rule_id:
                return item
        return None

    def check(self, rule_id: str) -> Optional[CapabilityCheck]:
        for item in self.evidence_checks:
            if item.rule_id == rule_id:
                return item
        return None

    def usable(self, rule_id: str) -> bool:
        check = self.check(rule_id)
        return bool(check and check.usable)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "theme_ids": list(self.theme_ids),
            "rank": self.rank,
            "eligible": self.eligible,
            "eligibility_reasons": list(self.eligibility_reasons),
            "tradable": self.tradable,
            "tradability_reasons": list(self.tradability_reasons),
            "price": self.price,
            "pressure_pct": self.pressure_pct,
            "structure": thaw(self.structure),
            "evidence_checks": [check.to_dict() for check in self.evidence_checks],
            "confirmations": [item.to_dict() for item in self.confirmations],
            "entry_version": self.entry_version,
        }
