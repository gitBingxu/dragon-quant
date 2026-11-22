"""纯核阶段之间传递的显式状态（避免超大 dict 上下文）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .account import AccountView, RiskEnvelope
from .config import BSPolicyConfig
from .plans import HoldExit, PlanComparison, PolicyPlan, ProtectionObligation


@dataclass
class PrepareStage:
    config: BSPolicyConfig
    decision_moment: datetime
    reasons: list[dict]
    recovery: dict
    envelope: RiskEnvelope
    account: Optional[AccountView]
    equity: Optional[float]
    available_risk: float
    available_cash: Optional[float]
    market_blocks: list[str]
    giveback: dict
    step: int
    obligations: list[ProtectionObligation]
    hold_exits: list[HoldExit]
    risk_checks: list[dict] = field(default_factory=list)
    #: 本运行时自己的冻结归属持仓（owner-scope 模式）；None = 旧全账户口径。
    scoped_account: Optional[AccountView] = None
    owner_restrictions: tuple = ()


@dataclass
class ScenarioStage:
    status: str
    plans: list[PolicyPlan]
    comparisons: tuple[PlanComparison, ...]
    selected_id: Optional[str]
    outcome_matrix: dict
    probability: dict
    selection_meta: dict
    reasons: list[dict]
    gates: list[dict]
    initial_evidence: set[str]
    scenario_basis: dict
    used_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
