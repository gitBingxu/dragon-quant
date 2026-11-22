"""政策比较、决策包与纯核输入上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from dragon_quant.review_account.evidence import EvidenceSnapshot, freeze, thaw

from .account import AccountView, MarketView, RiskEnvelope
from .candidates import CandidateView
from .config import BSPolicyConfig
from .plans import PlanAction, PlanComparison, PolicyPlan, ProtectionObligation
from .scenario_contracts import (
    PositionPolicyState,
    ProbabilityInput,
    RecoveryState,
    ScenarioSet,
)

@dataclass(frozen=True)
class DecisionBundle:
    """纯函数输出：候选政策、逐项拒绝、场景矩阵、选择与审计。"""

    status: str
    decision_time: str
    policy_version: str
    config_hash: Optional[str]
    snapshot_id: Optional[str]
    plans: tuple[PolicyPlan, ...] = ()
    rejected: tuple[dict, ...] = ()
    scenarios: tuple[dict, ...] = ()
    outcome_matrix: Mapping = field(default_factory=dict)
    comparisons: tuple[PlanComparison, ...] = ()
    selected_plan_id: Optional[str] = None
    selected_actions: tuple[PlanAction, ...] = ()
    action_limits: Mapping = field(default_factory=dict)
    next_observation: tuple[dict, ...] = ()
    risk_checks: tuple[dict, ...] = ()
    gate_trace: tuple[dict, ...] = ()
    recovery: Mapping = field(default_factory=dict)
    used_capabilities: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    reasons: tuple[dict, ...] = ()
    audit: Mapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome_matrix", freeze(dict(self.outcome_matrix or {})))
        object.__setattr__(self, "action_limits", freeze(dict(self.action_limits or {})))
        object.__setattr__(self, "recovery", freeze(dict(self.recovery or {})))
        object.__setattr__(self, "audit", freeze(dict(self.audit or {})))

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "decision_time": self.decision_time,
            "policy_version": self.policy_version,
            "config_hash": self.config_hash,
            "snapshot_id": self.snapshot_id,
            "plans": [plan.to_dict() for plan in self.plans],
            "rejected": [thaw(item) for item in self.rejected],
            "scenarios": [thaw(item) for item in self.scenarios],
            "outcome_matrix": thaw(self.outcome_matrix),
            "comparisons": [item.to_dict() for item in self.comparisons],
            "selected_plan_id": self.selected_plan_id,
            "selected_actions": [action.to_dict() for action in self.selected_actions],
            "action_limits": thaw(self.action_limits),
            "next_observation": [thaw(item) for item in self.next_observation],
            "risk_checks": [thaw(item) for item in self.risk_checks],
            "gate_trace": [thaw(item) for item in self.gate_trace],
            "recovery": thaw(self.recovery),
            "used_capabilities": list(self.used_capabilities),
            "missing_capabilities": list(self.missing_capabilities),
            "reasons": [thaw(item) for item in self.reasons],
            "audit": thaw(self.audit),
        }


@dataclass(frozen=True)
class PolicyContext:
    """一次纯政策评估的全部输入（调用方负责只读取数与冻结版本）。"""

    decision_time: str
    config: Optional[BSPolicyConfig] = None
    snapshot: Optional[EvidenceSnapshot] = None
    account: Optional[AccountView] = None
    candidates: tuple[CandidateView, ...] = ()
    market: MarketView = field(default_factory=MarketView)
    risk_envelope: Optional[RiskEnvelope] = None
    scenarios: Optional[ScenarioSet] = None
    probability: Optional[ProbabilityInput] = None
    recovery_state: Optional[RecoveryState] = None
    policy_states: tuple[PositionPolicyState, ...] = ()
    previous_decision_id: str = ""
    previous_decision: Optional[Mapping] = None
    strict: bool = False
    #: 外部解析器预计算的**不可变** owner/批次保护义务；``None`` = 走纯核内部
    #: 旧聚合口径（W2 场景/纯单测）。给定时纯核不再从聚合持仓推导价/时间失效。
    owner_protections: Optional[tuple[ProtectionObligation, ...]] = None
    #: 受限 owner（快照/参数/数据不可用）原因；只影响新增风险，不取消其它保护。
    owner_restrictions: tuple[Mapping, ...] = ()
    #: 当前运行时自己的冻结归属：``owner_protections`` 模式下只管理这个归属。
    active_owner: Mapping = field(default_factory=dict)
    #: 账户主运行链（AccountEngine/场景政策链）要求 add 具备**真实 probe 成交证明**
    #: （父买单 ID + 真实成交量 + 计划 target）；纯核教学 fixture 默认为 False。
    require_fill_proof: bool = False
    #: 本事件 PIT 可执行估值是否可得（``None`` = 调用方未声明）；``False`` 时
    #: 新增风险受限且不用旧 ``last_close`` 推导回撤/恢复授权。
    current_valuation_known: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.owner_protections is not None:
            object.__setattr__(self, "owner_protections",
                               tuple(self.owner_protections))
        object.__setattr__(self, "owner_restrictions",
                           tuple(self.owner_restrictions or ()))
        object.__setattr__(self, "active_owner",
                           freeze(dict(self.active_owner or {})))
        # 生产（非 fixture）配置**强制**要求真实 probe 成交证明：不能靠调用者
        # 记得置 ``require_fill_proof=True`` 才生效。显式教学 fixture 才保留旧
        # 「无量支持」兼容（并且仅当调用者显式选择）。
        if self.config is not None and not bool(getattr(self.config, "fixture", True)):
            object.__setattr__(self, "require_fill_proof", True)

    def to_dict(self) -> dict:
        return {
            "decision_time": self.decision_time,
            "snapshot_id": self.snapshot.snapshot_id if self.snapshot else None,
            "account": self.account.to_dict() if self.account else None,
            "candidates": [item.to_dict() for item in self.candidates],
            "market": self.market.to_dict(),
            "risk_envelope": self.risk_envelope.to_dict()
            if self.risk_envelope else None,
            "scenarios": self.scenarios.to_dict() if self.scenarios else None,
            "probability": self.probability.to_dict() if self.probability else None,
            "recovery_state": self.recovery_state.to_dict()
            if self.recovery_state else None,
            "policy_states": [item.to_dict() for item in self.policy_states],
            "previous_decision_id": self.previous_decision_id,
            "previous_decision": thaw(self.previous_decision or {}),
            "strict": self.strict,
            "owner_protections": [item.to_dict() for item in
                                  (self.owner_protections or ())],
            "owner_restrictions": [thaw(item) for item in self.owner_restrictions],
            "active_owner": thaw(self.active_owner or {}),
            "require_fill_proof": bool(self.require_fill_proof),
            "current_valuation_known": self.current_valuation_known,
        }