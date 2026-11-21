"""共享可序列化 DTO（W4a）：Advice / RunRecord / Capabilities / Coverage。

CLI、研究 envelope 与后续 W4b Web/冻结实验共用这些形状；内部 hash/原因码可留
审计字段，普通中文解释与动作限制放在可读字段。``preference``（Hurwicz ρ）不叫
EV，避免把偏好分误读成期望收益。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from dragon_quant.review_account.evidence import thaw

BS_ADVICE_SCHEMA_VERSION = "bs_advice/1"


@dataclass(frozen=True)
class Capabilities:
    usable: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    detail: tuple[Mapping, ...] = ()
    calibration_version: str = ""

    def to_dict(self) -> dict:
        return {
            "usable_capabilities": list(self.usable),
            "missing_capabilities": list(self.missing),
            "by_capability": [thaw(item) for item in self.detail],
            "calibration_version": self.calibration_version,
        }


@dataclass(frozen=True)
class Coverage:
    payload: Mapping = field(default_factory=dict)

    def to_dict(self) -> dict:
        return thaw(dict(self.payload))


@dataclass(frozen=True)
class Advice:
    """一次只读建议的完整结果（不下单、不写账本）。"""

    status: str
    cutoff: str
    strategy_id: str
    account: Mapping = field(default_factory=dict)
    decision: Optional[Mapping] = None
    intents: tuple[Mapping, ...] = ()
    protections: tuple[Mapping, ...] = ()
    rejected: tuple[Mapping, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    restrictions: tuple[Mapping, ...] = ()
    batches: tuple[Mapping, ...] = ()
    recovery: Mapping = field(default_factory=dict)
    capabilities: Optional[Capabilities] = None
    coverage: Optional[Coverage] = None
    policy: Mapping = field(default_factory=dict)
    snapshot_id: Optional[str] = None
    calibration: Mapping = field(default_factory=dict)
    runtime_facts: Mapping = field(default_factory=dict)
    evidence_refs: Mapping = field(default_factory=dict)
    universe: Mapping = field(default_factory=dict)
    candidates: tuple[Mapping, ...] = ()
    market: Mapping = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    schema_version: str = BS_ADVICE_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "cutoff": self.cutoff,
            "strategy_id": self.strategy_id,
            "account": thaw(dict(self.account or {})),
            "decision": thaw(dict(self.decision)) if self.decision else None,
            "intents": [thaw(dict(item)) for item in self.intents],
            "protections": [thaw(dict(item)) for item in self.protections],
            "rejected": [thaw(dict(item)) for item in self.rejected],
            "missing_capabilities": list(self.missing_capabilities),
            "restrictions": [dict(item) for item in self.restrictions],
            "batches": [dict(item) for item in self.batches],
            "recovery": thaw(dict(self.recovery or {})),
            "capabilities": (self.capabilities.to_dict()
                             if self.capabilities else None),
            "coverage": (self.coverage.to_dict() if self.coverage else None),
            "policy": thaw(dict(self.policy or {})),
            "snapshot_id": self.snapshot_id,
            "calibration": thaw(dict(self.calibration or {})),
            "runtime_facts": thaw(dict(self.runtime_facts or {})),
            "evidence_refs": thaw(dict(self.evidence_refs or {})),
            "universe": thaw(dict(self.universe or {})),
            "candidates": [thaw(dict(item)) for item in self.candidates],
            "market": thaw(dict(self.market or {})),
            "notes": list(self.notes),
            "persisted": False,
            "orders_placed": False,
        }


@dataclass(frozen=True)
class RunRecord:
    """review-account BS 分支的本地重放结果（显式不落库，除非后续提供 BS 账本）。"""

    ledger_id: str
    mode: str
    events: tuple[Mapping, ...] = ()
    final_state: Mapping = field(default_factory=dict)
    fills: tuple[Mapping, ...] = ()
    policy: Mapping = field(default_factory=dict)
    calibration: Mapping = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    schema_version: str = "bs_replay/1"

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "ledger_id": self.ledger_id,
            "mode": self.mode,
            "persisted": False,
            "orders_placed": False,
            "events": [thaw(dict(item)) for item in self.events],
            "final_state": thaw(dict(self.final_state or {})),
            "fills": [thaw(dict(item)) for item in self.fills],
            "policy": thaw(dict(self.policy or {})),
            "calibration": thaw(dict(self.calibration or {})),
            "notes": list(self.notes),
        }


__all__ = [
    "BS_ADVICE_SCHEMA_VERSION", "Capabilities", "Coverage", "Advice",
    "RunRecord",
]
