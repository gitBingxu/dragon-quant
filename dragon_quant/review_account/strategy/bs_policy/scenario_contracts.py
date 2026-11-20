"""场景路径、概率/恢复/政策状态契约（纯值，供 W2/W3 共用）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from dragon_quant.review_account.evidence import freeze, thaw

from .base import (
    SCENARIO_PRESSURE,
    PolicyInputError,
    _finite,
    _valid_date,
)

@dataclass(frozen=True)
class ScenarioStep:
    """一个外部路径事件：价格、可成交状态、源可得时点、事件容量与交易日。

    ``capacity`` 显式声明**本事件**每个代码的最大可成交股数（``None``/缺省 = 未知
    → 不可成交）；``available_at`` 是报价数据可得时点（缺省 = ``at``，不得晚于
    ``at``）。教学 fixture 可以显式给出假设容量，生产缺失事实保持受限，不把
    ``tradable=True`` 当作无限容量。
    """

    index: int
    at: str
    prices: Mapping = field(default_factory=dict)
    tradable: Mapping = field(default_factory=dict)
    trade_date: str = ""
    evidence_available: tuple[str, ...] = ()
    capacity: Mapping = field(default_factory=dict)
    available_at: str = ""
    #: P2：已完成 bar 的真实高点与**明确区间**（事件时间域 = step.index）。缺区间
    #: 时 high 不作为完整买后峰值；fixture 可显式声明，生产须由数据源给出。
    bar_highs: Mapping = field(default_factory=dict)
    bar_complete: bool = False
    interval_start: Optional[int] = None
    interval_end: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "prices", freeze(dict(self.prices or {})))
        object.__setattr__(self, "tradable", freeze(dict(self.tradable or {})))
        object.__setattr__(self, "capacity", freeze(dict(self.capacity or {})))
        object.__setattr__(self, "bar_highs", freeze(dict(self.bar_highs or {})))
        object.__setattr__(self, "evidence_available",
                           tuple(self.evidence_available or ()))
        if self.trade_date and not _valid_date(self.trade_date):
            raise PolicyInputError(
                f"非法 scenario trade_date: {self.trade_date!r}")
        if self.available_at and self.at and self.available_at > self.at:
            raise PolicyInputError(
                f"scenario available_at 晚于事件时点: {self.available_at!r}")
        for code, value in self.capacity.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PolicyInputError(
                    f"scenario capacity 必须是非负整数: {code}")
        if (self.interval_start is None) != (self.interval_end is None):
            raise PolicyInputError("scenario bar 区间起点/终点必须同时给出")
        if self.interval_start is not None:
            if isinstance(self.interval_start, bool) \
                    or isinstance(self.interval_end, bool) \
                    or not isinstance(self.interval_start, int) \
                    or not isinstance(self.interval_end, int):
                raise PolicyInputError("scenario bar 区间起止必须是整数")
            if self.interval_start >= self.interval_end:
                raise PolicyInputError("scenario bar 区间起点必须早于终点")
            if self.interval_end > self.index:
                raise PolicyInputError("scenario bar 区间不能晚于事件时点")

    def price(self, code: str) -> Optional[float]:
        return _finite(self.prices.get(code))

    def can_fill(self, code: str) -> bool:
        return bool(self.tradable.get(code, False))

    def capacity_qty(self, code: str) -> Optional[int]:
        """本事件容量；缺省 None = 未知（不可成交），0 = 零容量。"""
        value = self.capacity.get(code)
        if value is None:
            return None
        return int(value)

    def support_at(self) -> str:
        return self.available_at or self.at

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "at": self.at,
            "trade_date": self.trade_date,
            "prices": thaw(self.prices),
            "tradable": thaw(self.tradable),
            "evidence_available": list(self.evidence_available),
            "capacity": thaw(self.capacity),
            "available_at": self.available_at,
            "bar_highs": thaw(self.bar_highs),
            "bar_complete": self.bar_complete,
            "interval_start": self.interval_start,
            "interval_end": self.interval_end,
        }


@dataclass(frozen=True)
class ScenarioPath:
    scenario_id: str
    kind: str
    steps: tuple[ScenarioStep, ...]
    basis: str = ""
    mature: bool = True
    provenance: Mapping = field(default_factory=dict)
    fixture: bool = False
    calendar: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance",
                           freeze(dict(self.provenance or {})))
        object.__setattr__(self, "calendar", tuple(self.calendar or ()))
        for day in self.calendar:
            if not _valid_date(day):
                raise PolicyInputError(f"非法 scenario calendar 交易日: {day!r}")

    def trade_dates(self) -> tuple[str, ...]:
        """显式交易日历；缺省时按已声明的 step trade_date 推导（fixture 显式）。"""
        if self.calendar:
            return self.calendar
        days = []
        for step in self.steps:
            if step.trade_date and step.trade_date not in days:
                days.append(step.trade_date)
        return tuple(sorted(days))

    def step(self, index: int) -> Optional[ScenarioStep]:
        for item in self.steps:
            if item.index == index:
                return item
        return None

    def final_step(self) -> Optional[ScenarioStep]:
        return self.steps[-1] if self.steps else None

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "kind": self.kind,
            "basis": self.basis,
            "mature": self.mature,
            "provenance": thaw(self.provenance),
            "fixture": self.fixture,
            "calendar": list(self.calendar),
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class ScenarioSet:
    paths: tuple[ScenarioPath, ...]
    insufficiency: tuple[str, ...] = ()

    def comparable(self) -> tuple[ScenarioPath, ...]:
        return tuple(path for path in self.paths if path.kind != SCENARIO_PRESSURE)

    def pressure(self) -> tuple[ScenarioPath, ...]:
        return tuple(path for path in self.paths if path.kind == SCENARIO_PRESSURE)

    def to_dict(self) -> dict:
        return {
            "paths": [path.to_dict() for path in self.paths],
            "insufficiency": list(self.insufficiency),
        }


@dataclass(frozen=True)
class ProbabilityInput:
    """概率资格输入：固定事件/政策/期限 + 成熟训练样本 + 校准依据。"""

    event_id: str
    policy_id: str
    profile_version: str
    horizon_days: int
    matured: bool
    sample_size: int
    calibration_ref: str
    calibration_version: str
    applicability: tuple[str, ...] = ()
    probabilities: Mapping = field(default_factory=dict)
    event_definition: str = ""
    training_end: str = ""
    label_matured_at: str = ""
    calibration_available_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "probabilities", freeze(dict(self.probabilities or {})))

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "policy_id": self.policy_id,
            "profile_version": self.profile_version,
            "horizon_days": self.horizon_days,
            "matured": self.matured,
            "sample_size": self.sample_size,
            "calibration_ref": self.calibration_ref,
            "calibration_version": self.calibration_version,
            "applicability": list(self.applicability),
            "probabilities": thaw(self.probabilities),
            "event_definition": self.event_definition,
            "training_end": self.training_end,
            "label_matured_at": self.label_matured_at,
            "calibration_available_at": self.calibration_available_at,
        }


@dataclass(frozen=True)
class RecoveryConditionConfirmation:
    """一条**可核对身份**的恢复条件确认（PIT）。

    裸条件名（``conditions_met`` 字符串）不构成授权：恢复必须由带 rule/evidence/
    source/available_at/snapshot/版本 的确认满足，且 ``available_at`` 不晚于本次
    决策时点。迟到修订不回填过去授权；错误 snapshot 不恢复。
    """

    rule_id: str
    evidence_ids: tuple[str, ...] = ()
    source: str = ""
    available_at: str = ""
    snapshot_id: str = ""
    version: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_ids",
                           tuple(self.evidence_ids or ()))
        for name in ("rule_id", "source", "available_at", "version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise PolicyInputError(f"恢复条件确认缺少 {name}")
        if not self.evidence_ids:
            raise PolicyInputError("恢复条件确认必须带 evidence_ids")

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "evidence_ids": list(self.evidence_ids),
            "source": self.source,
            "available_at": self.available_at,
            "snapshot_id": self.snapshot_id,
            "version": self.version,
        }

    @classmethod
    def from_row(cls, value: Mapping) -> "RecoveryConditionConfirmation":
        try:
            return cls(
                rule_id=str(value["rule_id"]),
                evidence_ids=tuple(value.get("evidence_ids") or ()),
                source=str(value.get("source") or ""),
                available_at=str(value.get("available_at") or ""),
                snapshot_id=str(value.get("snapshot_id") or ""),
                version=str(value.get("version") or ""))
        except (KeyError, TypeError, ValueError) as exc:
            raise PolicyInputError(f"损坏的恢复条件确认: {exc}") from exc


@dataclass(frozen=True)
class RecoveryState:
    """G8 收缩/恢复的**锁存**状态（由调用方冻结传入，本层只推进一格）。

    ``trigger_trade_date`` 是触发交易日（触发日 = 0）；``days_since_trigger`` 只按
    真实交易日历推进，周末/休市不计。``confirmations`` 是 PIT 确认；``conditions_met``
    仅为旧行兼容的展示字段，**不作为解锁依据**。
    """

    active: bool = False
    stage: int = 0
    days_since_trigger: int = 0
    conditions_met: tuple[str, ...] = ()
    trigger_event: Optional[int] = None
    trigger_trade_date: str = ""
    last_advanced_trade_date: str = ""
    last_stage_advanced_trade_date: str = ""
    graduated_trade_date: str = ""
    confirmations: tuple[RecoveryConditionConfirmation, ...] = ()
    #: 触发时点**撮合前**的存量快照与已批准收缩目标（code → {batch_ids,
    #: target_qty, remaining}）；旧 episode 不每事件重建。
    contraction_targets: Mapping = field(default_factory=dict)
    target_snapshot_trade_date: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "conditions_met",
                           tuple(self.conditions_met or ()))
        object.__setattr__(self, "confirmations", tuple(
            self.confirmations or ()))
        object.__setattr__(self, "contraction_targets",
                           freeze(dict(self.contraction_targets or {})))
        for name in ("trigger_trade_date", "last_advanced_trade_date",
                     "last_stage_advanced_trade_date", "graduated_trade_date"):
            value = getattr(self, name)
            if value and not _valid_date(value):
                raise PolicyInputError(f"非法恢复状态交易日: {value!r}")
        if isinstance(self.stage, bool) or not isinstance(self.stage, int) \
                or self.stage < 0:
            raise PolicyInputError("恢复 stage 必须是非负整数")
        if isinstance(self.days_since_trigger, bool) \
                or not isinstance(self.days_since_trigger, int) \
                or self.days_since_trigger < 0:
            raise PolicyInputError("恢复 days_since_trigger 必须是非负整数")

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "stage": self.stage,
            "days_since_trigger": self.days_since_trigger,
            "conditions_met": list(self.conditions_met),
            "trigger_event": self.trigger_event,
            "trigger_trade_date": self.trigger_trade_date,
            "last_advanced_trade_date": self.last_advanced_trade_date,
            "last_stage_advanced_trade_date": self.last_stage_advanced_trade_date,
            "graduated_trade_date": self.graduated_trade_date,
            "confirmations": [item.to_dict() for item in self.confirmations],
            "contraction_targets": thaw(self.contraction_targets),
            "target_snapshot_trade_date": self.target_snapshot_trade_date,
        }

    @classmethod
    def from_row(cls, value: Mapping) -> "RecoveryState":
        try:
            return cls(
                active=bool(value.get("active")),
                stage=int(value.get("stage") or 0),
                days_since_trigger=int(value.get("days_since_trigger") or 0),
                conditions_met=tuple(value.get("conditions_met") or ()),
                trigger_event=(None if value.get("trigger_event") is None
                               else int(value.get("trigger_event"))),
                trigger_trade_date=str(value.get("trigger_trade_date") or ""),
                last_advanced_trade_date=str(
                    value.get("last_advanced_trade_date") or ""),
                last_stage_advanced_trade_date=str(
                    value.get("last_stage_advanced_trade_date") or ""),
                graduated_trade_date=str(
                    value.get("graduated_trade_date") or ""),
                confirmations=tuple(
                    RecoveryConditionConfirmation.from_row(item)
                    for item in (value.get("confirmations") or ())),
                contraction_targets=dict(value.get("contraction_targets") or {}),
                target_snapshot_trade_date=str(
                    value.get("target_snapshot_trade_date") or ""))
        except (TypeError, ValueError) as exc:
            raise PolicyInputError(f"损坏的恢复状态行: {exc}") from exc





@dataclass(frozen=True)
class PositionPolicyState:
    """已成交试探/加仓的冻结政策状态（W2 只读，W3 落账）。

    C3b2：状态由**真实 probe/add 成交**推进（见 ``policy_states.advance_*``），
    带完整 ``owner + code + family`` 归并身份与真实量/时点/证据。查找按完整
    scope，不只 code；``family_id`` 默认为试探计划 ID，使 probe 与其 add 归并成
    同一闭合家族。旧行缺省字段按 unknown/空处理（不猜历史）。
    """

    code: str
    probe_plan_id: str
    probe_filled_at: str
    probe_evidence_ids: tuple[str, ...] = ()
    confirmations_used: tuple[str, ...] = ()
    adds_done: int = 0
    version: str = ""
    #: 完整冻结归属；空 = 旧口径（未知 owner，按兼容处理）。
    owner: Mapping = field(default_factory=dict)
    #: 试探家族（probe + 其 add 合并身份）；空时回退到 probe_plan_id。
    family_id: str = ""
    #: 该家族涉及的批次 scope（真实成交批次）。
    batch_ids: tuple[str, ...] = ()
    #: 真实试探父买单 ID（add 依赖的持久化身份）。
    probe_order_id: str = ""
    #: 试探订单真实累计成交量（partial probe 只按实际成交量记录）。
    probe_filled_qty: int = 0
    #: 试探 + 加仓的真实累计成交量。
    cumulative_filled_qty: int = 0
    #: 加仓真实累计成交量。
    add_filled_qty: int = 0
    #: 已推进 adds_done 的加仓订单 ID（同单部分成交只计一次）。
    consumed_orders: tuple[str, ...] = ()
    #: 尚未达成的加仓目标（计划量，不代表已授权；缺失=未知）。
    pending_target_qty: Optional[int] = None
    #: 试探/加仓的失效到期时点（ISO；空=未知，不推断）。
    expires_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "probe_evidence_ids",
                           tuple(self.probe_evidence_ids or ()))
        object.__setattr__(self, "confirmations_used",
                           tuple(self.confirmations_used or ()))
        object.__setattr__(self, "batch_ids", tuple(self.batch_ids or ()))
        object.__setattr__(self, "consumed_orders",
                           tuple(self.consumed_orders or ()))
        object.__setattr__(self, "owner", freeze(dict(self.owner or {})))
        if not self.code or not self.probe_plan_id:
            raise PolicyInputError("政策状态必须带 code 与 probe_plan_id")
        if not self.probe_filled_at:
            raise PolicyInputError("政策状态必须带 probe_filled_at")
        object.__setattr__(self, "family_id", self.family_id or self.probe_plan_id)
        for name in ("adds_done", "probe_filled_qty", "cumulative_filled_qty",
                     "add_filled_qty"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PolicyInputError(f"{name} 必须是非负整数")
        if self.pending_target_qty is not None:
            value = self.pending_target_qty
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PolicyInputError("pending_target_qty 必须是非负整数或 None")

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "probe_plan_id": self.probe_plan_id,
            "probe_filled_at": self.probe_filled_at,
            "probe_evidence_ids": list(self.probe_evidence_ids),
            "confirmations_used": list(self.confirmations_used),
            "adds_done": self.adds_done,
            "version": self.version,
            "owner": thaw(self.owner),
            "family_id": self.family_id,
            "batch_ids": list(self.batch_ids),
            "probe_order_id": self.probe_order_id,
            "probe_filled_qty": self.probe_filled_qty,
            "cumulative_filled_qty": self.cumulative_filled_qty,
            "add_filled_qty": self.add_filled_qty,
            "consumed_orders": list(self.consumed_orders),
            "pending_target_qty": self.pending_target_qty,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_row(cls, value: Mapping) -> "PositionPolicyState":
        def _ints(row):
            for name in ("adds_done", "probe_filled_qty",
                         "cumulative_filled_qty", "add_filled_qty"):
                row[name] = int(row.get(name) or 0)
            target = row.get("pending_target_qty")
            row["pending_target_qty"] = (None if target is None else int(target))
            return row

        try:
            row = _ints(dict(value))
            return cls(
                code=str(row["code"]),
                probe_plan_id=str(row["probe_plan_id"]),
                probe_filled_at=str(row["probe_filled_at"]),
                probe_evidence_ids=tuple(row.get("probe_evidence_ids") or ()),
                confirmations_used=tuple(row.get("confirmations_used") or ()),
                adds_done=row["adds_done"],
                version=str(row.get("version") or ""),
                owner=dict(row.get("owner") or {}),
                family_id=str(row.get("family_id") or ""),
                batch_ids=tuple(row.get("batch_ids") or ()),
                probe_order_id=str(row.get("probe_order_id") or ""),
                probe_filled_qty=row["probe_filled_qty"],
                cumulative_filled_qty=row["cumulative_filled_qty"],
                add_filled_qty=row["add_filled_qty"],
                consumed_orders=tuple(row.get("consumed_orders") or ()),
                pending_target_qty=row["pending_target_qty"],
                expires_at=str(row.get("expires_at") or ""))
        except (KeyError, TypeError, ValueError) as exc:
            raise PolicyInputError(f"损坏的政策状态行: {exc}") from exc
