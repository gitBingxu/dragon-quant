"""政策意图/转移契约：条件、保护义务、计划腿与场景结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from dragon_quant.review_account.evidence import freeze, thaw


@dataclass(frozen=True)
class PolicyCondition:
    """结构化政策条件（W3 可复用；不依赖中文字符串）。"""

    kind: str                 # price_below/price_above/time_limit/evidence/trend/giveback
    code: str = ""
    value: Optional[float] = None
    unit: str = ""            # cny_share/trading_day/rule_id/bool
    detail: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "code": self.code, "value": self.value,
                "unit": self.unit, "detail": self.detail}


@dataclass(frozen=True)
class HoldExit:
    """既有持仓在后续路径上的退出政策（本金/时间/回吐分开）。"""

    code: str
    stop_price: Optional[float] = None
    disaster_price: Optional[float] = None
    giveback_price: Optional[float] = None
    time_limit_step: Optional[int] = None
    giveback_amount: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "stop_price": self.stop_price,
            "disaster_price": self.disaster_price,
            "giveback_price": self.giveback_price,
            "time_limit_step": self.time_limit_step,
            "giveback_amount": self.giveback_amount,
        }


@dataclass(frozen=True)
class ProtectionObligation:
    """账户级强制保护义务：先于任何候选政策，且所有方案都必须继承。

    每项义务带**冻结归属**（``owner``）与**批次范围**（``batch_ids``）：同 code 不同
    owner/计划的持仓是不同业务身份，不得合并加权成本后触发（R6/L4）。``batch_ids``
    为空表示"该归属全部批次"的旧口径，绝非未知范围被当作明确 scope。
    """

    obligation_id: str
    code: str
    kind: str                 # price_invalidation/time_invalidation/disaster/giveback/contraction
    action: str               # exit / reduce
    qty: int
    executable: bool
    reasons: tuple[str, ...] = ()
    condition: Optional[PolicyCondition] = None
    sequence: int = 0
    owner: Mapping = field(default_factory=dict)
    batch_ids: tuple[str, ...] = ()
    plan_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", freeze(dict(self.owner or {})))
        object.__setattr__(self, "batch_ids", tuple(self.batch_ids or ()))
        object.__setattr__(self, "reasons", tuple(self.reasons or ()))

    def to_dict(self) -> dict:
        return {
            "obligation_id": self.obligation_id,
            "code": self.code,
            "kind": self.kind,
            "action": self.action,
            "qty": self.qty,
            "executable": self.executable,
            "reasons": list(self.reasons),
            "condition": self.condition.to_dict() if self.condition else None,
            "sequence": self.sequence,
            "owner": thaw(self.owner),
            "batch_ids": list(self.batch_ids),
            "plan_id": self.plan_id,
        }



@dataclass(frozen=True)
class PlanAction:
    """动作意图：目标/增量数量或风险额度；不是账务执行。"""

    action: str
    code: str
    target_qty: Optional[int] = None
    delta_qty: Optional[int] = None
    risk_amount: Optional[float] = None
    condition: str = ""
    sequence: int = 0
    fill_requirement: str = "next_step_tradable"
    condition_ref: Optional[PolicyCondition] = None
    obligation_id: str = ""
    executable: bool = True
    pending_reasons: tuple[str, ...] = ()
    owner: Mapping = field(default_factory=dict)
    batch_ids: tuple[str, ...] = ()
    #: 触发该动作的**批次计划**范围（保护义务来自具体批次时保留）。
    plan_id: str = ""
    #: 结构化证据/依赖/有效期/家族（G3 add 引用真实 probe 父腿；不丢执行语义）。
    evidence_rule: str = ""
    evidence_ids: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    valid_until_event: Optional[int] = None
    family_id: str = ""
    #: 该动作所属代码的压力估计（用于订单预留的风险占用口径，缺省=未知受限）。
    pressure_pct: Optional[float] = None
    #: 冻结的价格失效线（低于即撤销未成新风险余量；None=未知，不推断失效）。
    invalidate_below: Optional[float] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", freeze(dict(self.owner or {})))
        object.__setattr__(self, "batch_ids", tuple(self.batch_ids or ()))
        object.__setattr__(self, "pending_reasons",
                           tuple(self.pending_reasons or ()))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids or ()))
        object.__setattr__(self, "depends_on", tuple(self.depends_on or ()))

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "code": self.code,
            "target_qty": self.target_qty,
            "delta_qty": self.delta_qty,
            "risk_amount": self.risk_amount,
            "condition": self.condition,
            "sequence": self.sequence,
            "fill_requirement": self.fill_requirement,
            "condition_ref": (self.condition_ref.to_dict()
                              if self.condition_ref else None),
            "obligation_id": self.obligation_id,
            "executable": self.executable,
            "pending_reasons": list(self.pending_reasons),
            "owner": thaw(self.owner),
            "batch_ids": list(self.batch_ids),
            "plan_id": self.plan_id,
            "evidence_rule": self.evidence_rule,
            "evidence_ids": list(self.evidence_ids),
            "depends_on": list(self.depends_on),
            "valid_until_event": self.valid_until_event,
            "family_id": self.family_id,
            "pressure_pct": self.pressure_pct,
            "invalidate_below": self.invalidate_below,
        }


@dataclass(frozen=True)
class PlanLeg:
    """场景模拟腿：只在共同路径的 step 上按触发/可成交条件执行。"""

    step: int
    action: str            # buy / sell
    code: str
    qty_fraction: float = 1.0
    trigger_price: Optional[float] = None
    trigger_mode: str = "above"   # above / below
    requires_prior_fill: bool = False
    reason: str = ""
    evidence_rule: str = ""
    evidence_ids: tuple[str, ...] = ()
    #: 依赖的真实父腿所属计划 ID（G3 add → 其真实 probe 计划；不假设原子交换）。
    prior_plan_id: str = ""
    family_id: str = ""
    #: 卖出腿的**批次 scope 与显式目标量**（剩余 scope 计划与必须保护抵扣后
    #: 的数量）；空 scope 保持旧口径（该 owner 全部批次），``qty=None`` 时按
    #: ``qty_fraction`` 计算。场景模拟与真实账本按同一 scope/数量解释。
    batch_ids: tuple[str, ...] = ()
    qty: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids or ()))
        object.__setattr__(self, "batch_ids", tuple(self.batch_ids or ()))

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "action": self.action,
            "code": self.code,
            "qty_fraction": self.qty_fraction,
            "trigger_price": self.trigger_price,
            "trigger_mode": self.trigger_mode,
            "requires_prior_fill": self.requires_prior_fill,
            "reason": self.reason,
            "evidence_rule": self.evidence_rule,
            "evidence_ids": list(self.evidence_ids),
            "prior_plan_id": self.prior_plan_id,
            "family_id": self.family_id,
            "batch_ids": list(self.batch_ids),
            "qty": self.qty,
        }


@dataclass(frozen=True)
class PolicyPlan:
    plan_id: str
    plan_kind: str
    code: str = ""
    actions: tuple[PlanAction, ...] = ()
    legs: tuple[PlanLeg, ...] = ()
    entry_rules: tuple[str, ...] = ()
    data_requirements: tuple[str, ...] = ()
    invalidation: tuple[PolicyCondition, ...] = ()
    cancel_conditions: tuple[PolicyCondition, ...] = ()
    evidence_dependencies: tuple[str, ...] = ()
    risk_amount: Optional[float] = None
    target_qty: Optional[int] = None
    blocked_reasons: tuple[str, ...] = ()
    executable: bool = False
    notes: tuple[str, ...] = ()
    stop_price: Optional[float] = None
    disaster_price: Optional[float] = None
    time_limit_steps: Optional[int] = None
    hold_exits: tuple[HoldExit, ...] = ()
    protection: tuple[ProtectionObligation, ...] = ()
    entry_evidence_ids: tuple[str, ...] = ()
    entry_version: str = ""
    add_evidence_rule: str = ""
    probe_plan_id: str = ""
    initial_entered: bool = False
    initial_adds: int = 0
    initial_confirmations: tuple[str, ...] = ()
    #: 该计划的**冻结归属**（自主买卖/腿的 owner；必需保护仍按各自 obligation
    #: owner 执行）。空表示旧兼容（由调用方/账户推断）。
    owner: Mapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", freeze(dict(self.owner or {})))

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "plan_kind": self.plan_kind,
            "owner": thaw(self.owner),
            "code": self.code,
            "actions": [action.to_dict() for action in self.actions],
            "legs": [leg.to_dict() for leg in self.legs],
            "entry_rules": list(self.entry_rules),
            "data_requirements": list(self.data_requirements),
            "invalidation": [
                item.to_dict() if isinstance(item, PolicyCondition) else item
                for item in self.invalidation],
            "cancel_conditions": [
                item.to_dict() if isinstance(item, PolicyCondition) else item
                for item in self.cancel_conditions],
            "evidence_dependencies": list(self.evidence_dependencies),
            "risk_amount": self.risk_amount,
            "target_qty": self.target_qty,
            "blocked_reasons": list(self.blocked_reasons),
            "executable": self.executable,
            "notes": list(self.notes),
            "stop_price": self.stop_price,
            "disaster_price": self.disaster_price,
            "time_limit_steps": self.time_limit_steps,
            "hold_exits": [item.to_dict() for item in self.hold_exits],
            "protection": [item.to_dict() for item in self.protection],
            "entry_evidence_ids": list(self.entry_evidence_ids),
            "entry_version": self.entry_version,
            "add_evidence_rule": self.add_evidence_rule,
            "probe_plan_id": self.probe_plan_id,
            "initial_entered": self.initial_entered,
            "initial_adds": self.initial_adds,
            "initial_confirmations": list(self.initial_confirmations),
        }


@dataclass(frozen=True)
class PlanOutcome:
    plan_id: str
    scenario_id: str
    net_pnl: Optional[float]
    ending_equity: Optional[float] = None
    execution_cost: float = 0.0
    traded_notional: float = 0.0
    unfilled: tuple[str, ...] = ()
    path_equity: tuple[dict, ...] = ()
    reasons: tuple[str, ...] = ()
    timeline: tuple[Mapping, ...] = ()
    policy_states: tuple[Mapping, ...] = ()
    terminal_status: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "timeline",
                           tuple(freeze(dict(item)) for item in self.timeline))
        object.__setattr__(self, "policy_states",
                           tuple(freeze(dict(item)) for item in self.policy_states))

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "scenario_id": self.scenario_id,
            "net_pnl": self.net_pnl,
            "ending_equity": self.ending_equity,
            "execution_cost": self.execution_cost,
            "traded_notional": self.traded_notional,
            "unfilled": list(self.unfilled),
            "path_equity": [dict(item) for item in self.path_equity],
            "reasons": list(self.reasons),
            "timeline": [thaw(item) for item in self.timeline],
            "policy_states": [thaw(item) for item in self.policy_states],
            "terminal_status": self.terminal_status,
        }


@dataclass(frozen=True)
class PlanComparison:
    plan_id: str
    scenario_low: Optional[float]
    scenario_high: Optional[float]
    score: Optional[float]
    score_kind: str            # hurwicz / ev
    ev: Optional[float] = None
    probability_status: str = "unqualified"
    dominated_by: Optional[str] = None
    sensitivity: Mapping = field(default_factory=dict)
    pressure: Mapping = field(default_factory=dict)
    new_risk: float = 0.0
    turnover: float = 0.0

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "scenario_low": self.scenario_low,
            "scenario_high": self.scenario_high,
            "score": self.score,
            "score_kind": self.score_kind,
            "ev": self.ev,
            "probability_status": self.probability_status,
            "dominated_by": self.dominated_by,
            "sensitivity": thaw(self.sensitivity),
            "pressure": thaw(self.pressure),
            "new_risk": self.new_risk,
            "turnover": self.turnover,
        }
