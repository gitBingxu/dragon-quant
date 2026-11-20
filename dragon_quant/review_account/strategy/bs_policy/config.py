"""BS 账户政策的显式配置契约（schema_version = 1，纯值对象，无 I/O）。

设计要点（方案 §14.2 / G1–G8）：

- **不替用户发明风险偏好**：``rho``、预算、试探/确认额度、失效、冷却/恢复、
  场景期限与费用口径都必须在文件里显式给出；缺项直接拒绝加载。
- 单位清楚：金额一律「人民币元」，比例一律「百分数（% 或 0–1 分数）」
  在字段名与文档中写明；所有数值必须 finite，并做交叉校验
  （单笔 ≤ 账户、试探 ≤ 确认、灾难线宽于结构线等）。
- ``teaching_fixture()`` 是**教学假设、非推荐参数**，测试/演示必须显式取用；
  生产研究不能把 fixture 当默认值。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, fields
from typing import Any, Mapping, Optional

from .base import PolicyConfigError

from .policy_parts import (
    Invalidation,
    Preference,
    RecoveryPolicy,
    RiskBudgets,
    SizingLimits,
)
from .scenario_spec import EvaluationPolicy, ScenarioSpec

BS_POLICY_SCHEMA_VERSION = 1
BS_POLICY_VERSION = "bs_policy/1"

#: 账户政策策略的稳定身份（W4 才接用户入口；C2 只用于冻结归属分派）。
BS_ACCOUNT_STRATEGY_ID = "builtin:bs_account_decision"
#: 兼容早期内部 ref（无 ``builtin:`` 前缀）的同一身份。
BS_ACCOUNT_STRATEGY_ALIASES = frozenset({BS_ACCOUNT_STRATEGY_ID, "bs_account_decision"})

ENTRY_KINDS = ("breakout", "pullback", "confirm", "auction_confirm")


def _mapping(payload: Any, what: str) -> Mapping:
    if not isinstance(payload, Mapping):
        raise PolicyConfigError(f"{what} 必须是对象")
    return payload


def _strict_keys(payload: Mapping, allowed: set[str], what: str) -> None:
    unknown = sorted(set(map(str, payload)) - allowed)
    if unknown:
        raise PolicyConfigError(f"{what} 含未知字段: {unknown}")


def _number(payload: Mapping, key: str, *, what: str = "配置",
            low: Optional[float] = None, high: Optional[float] = None,
            integer: bool = False) -> float:
    if key not in payload:
        raise PolicyConfigError(f"{what} 缺少必填数值 {key}",
                                reason="policy_config_missing")
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyConfigError(f"{what}.{key} 必须是数值")
    if integer and not isinstance(value, int):
        raise PolicyConfigError(f"{what}.{key} 必须是整数")
    if not math.isfinite(value):
        raise PolicyConfigError(f"{what}.{key} 必须是有限数值")
    if low is not None and value < low:
        raise PolicyConfigError(f"{what}.{key} 不能小于 {low}")
    if high is not None and value > high:
        raise PolicyConfigError(f"{what}.{key} 不能大于 {high}")
    return value


def _string(payload: Mapping, key: str, *, what: str = "配置") -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PolicyConfigError(f"{what}.{key} 必须是非空字符串",
                                reason="policy_config_missing")
    return value.strip()


def _bool(payload: Mapping, key: str, *, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise PolicyConfigError(f"{key} 必须是布尔值")
    return value


def _strings(payload: Mapping, key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise PolicyConfigError(f"{key} 必须是数组")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PolicyConfigError(f"{key} 只能包含非空字符串")
        result.append(item.strip())
    return tuple(result)



@dataclass(frozen=True)
class BSPolicyConfig:
    """完整 BS 政策 profile（schema_version=1）。

    ``fixture=True`` 表示教学/测试假设，禁止被当作真实研究参数。
    """

    schema_version: int
    policy_id: str
    profile_id: str
    profile_version: str
    preference: Preference
    budgets: RiskBudgets
    sizing: SizingLimits
    invalidation: Invalidation
    recovery: RecoveryPolicy
    scenario: ScenarioSpec
    evaluation: EvaluationPolicy
    supported_entries: tuple[str, ...] = ENTRY_KINDS
    entry_mode: str = "compete"
    label: str = ""
    fixture: bool = False

    def validate(self) -> None:
        if self.schema_version != BS_POLICY_SCHEMA_VERSION:
            raise PolicyConfigError(
                f"不支持的 schema_version: {self.schema_version}",
                reason="policy_config_schema")
        for name in ("policy_id", "profile_id", "profile_version"):
            if not getattr(self, name).strip():
                raise PolicyConfigError(f"{name} 不能为空")
        self.preference.validate()
        self.budgets.validate()
        self.sizing.validate(self.budgets)
        self.invalidation.validate()
        self.recovery.validate()
        self.scenario.validate()
        self.evaluation.validate()
        if self.entry_mode not in ("compete", "probe_then_confirm"):
            raise PolicyConfigError(
                f"未知 entry_mode: {self.entry_mode!r}（仅 compete / "
                "probe_then_confirm）")
        unknown = [item for item in self.supported_entries if item not in ENTRY_KINDS]
        if unknown:
            raise PolicyConfigError(f"未知入场政策: {unknown}")
        if not self.supported_entries:
            raise PolicyConfigError("supported_entries 不能为空")
        if not isinstance(self.fixture, bool):
            raise PolicyConfigError("fixture 必须是布尔值")

    @property
    def content_hash(self) -> str:
        body = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True,
                          allow_nan=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "preference": self.preference.to_dict(),
            "budgets": self.budgets.to_dict(),
            "sizing": self.sizing.to_dict(),
            "invalidation": self.invalidation.to_dict(),
            "recovery": self.recovery.to_dict(),
            "scenario": self.scenario.to_dict(),
            "evaluation": self.evaluation.to_dict(),
            "supported_entries": list(self.supported_entries),
            **({"entry_mode": self.entry_mode}
               if self.entry_mode != "compete" else {}),
            "label": self.label,
            "fixture": self.fixture,
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> "BSPolicyConfig":
        payload = _mapping(payload, "BSPolicyConfig")
        _strict_keys(payload, {item.name for item in fields(cls)},
                     "BSPolicyConfig")
        preference = _mapping(payload.get("preference"), "preference")
        budgets = _mapping(payload.get("budgets"), "budgets")
        sizing = _mapping(payload.get("sizing"), "sizing")
        invalidation = _mapping(payload.get("invalidation"), "invalidation")
        recovery = _mapping(payload.get("recovery"), "recovery")
        scenario = _mapping(payload.get("scenario"), "scenario")
        evaluation = payload.get("evaluation") or {}
        evaluation = _mapping(evaluation, "evaluation")
        _strict_keys(preference, {"rho"}, "preference")
        _strict_keys(budgets, {"per_trade", "account", "per_theme",
                               "unknown_group", "giveback", "held_principal"},
                     "budgets")
        _strict_keys(sizing, {"probe_risk", "confirmed_risk",
                              "add_step_risk", "max_adds"}, "sizing")
        _strict_keys(invalidation, {"price_stop_pct", "time_limit_days",
                                    "confirm_deadline_days", "disaster_stop_pct"},
                     "invalidation")
        _strict_keys(recovery, {"drawdown_trigger_pct", "loss_streak_trigger",
                                "cooldown_days", "stages", "conditions"},
                     "recovery")
        _strict_keys(scenario, {"horizon_days", "latency_steps",
                                "commission_rate", "min_commission",
                                "stamp_tax_rate", "buy_slippage",
                                "sell_slippage", "lot_size", "reinvest_cash",
                                "step_unit"}, "scenario")
        _strict_keys(evaluation, {"min_probability_samples", "rho_sensitivity",
                                  "cost_sensitivity",
                                  "latency_sensitivity_steps"}, "evaluation")
        config = cls(
            schema_version=int(_number(payload, "schema_version",
                                       integer=True)),
            policy_id=_string(payload, "policy_id"),
            profile_id=_string(payload, "profile_id"),
            profile_version=_string(payload, "profile_version"),
            preference=Preference(
                rho=_number(preference, "rho", what="preference", low=0,
                            high=1)),
            budgets=RiskBudgets(
                per_trade=_number(budgets, "per_trade", what="budgets", low=0),
                account=_number(budgets, "account", what="budgets", low=0),
                per_theme=_number(budgets, "per_theme", what="budgets", low=0),
                unknown_group=_number(budgets, "unknown_group",
                                      what="budgets", low=0),
                giveback=_number(budgets, "giveback", what="budgets", low=0),
                held_principal=_number(budgets, "held_principal",
                                       what="budgets", low=0)),
            sizing=SizingLimits(
                probe_risk=_number(sizing, "probe_risk", what="sizing", low=0),
                confirmed_risk=_number(sizing, "confirmed_risk", what="sizing",
                                       low=0),
                add_step_risk=_number(sizing, "add_step_risk", what="sizing",
                                      low=0),
                max_adds=int(_number(sizing, "max_adds", what="sizing",
                                     integer=True, low=0))),
            invalidation=Invalidation(
                price_stop_pct=_number(invalidation, "price_stop_pct",
                                       what="invalidation"),
                time_limit_days=int(_number(invalidation, "time_limit_days",
                                            what="invalidation", integer=True,
                                            low=1)),
                confirm_deadline_days=int(_number(
                    invalidation, "confirm_deadline_days", what="invalidation",
                    integer=True, low=1)),
                disaster_stop_pct=_number(invalidation, "disaster_stop_pct",
                                          what="invalidation")),
            recovery=RecoveryPolicy(
                drawdown_trigger_pct=_number(recovery, "drawdown_trigger_pct",
                                             what="recovery", low=0),
                loss_streak_trigger=int(_number(
                    recovery, "loss_streak_trigger", what="recovery",
                    integer=True, low=1)),
                cooldown_days=int(_number(recovery, "cooldown_days",
                                          what="recovery", integer=True,
                                          low=0)),
                stages=tuple(_number({"stages": item},
                                     "stages", what="recovery.stages")
                             for item in _sequence(recovery, "stages")),
                conditions=_strings(recovery, "conditions")),
            scenario=ScenarioSpec(
                horizon_days=int(_number(scenario, "horizon_days",
                                         what="scenario", integer=True, low=1)),
                latency_steps=int(_number(scenario, "latency_steps",
                                          what="scenario", integer=True,
                                          low=1)),
                commission_rate=_number(scenario, "commission_rate",
                                        what="scenario", low=0, high=0.999),
                min_commission=_number(scenario, "min_commission",
                                       what="scenario", low=0),
                stamp_tax_rate=_number(scenario, "stamp_tax_rate",
                                       what="scenario", low=0, high=0.999),
                buy_slippage=_number(scenario, "buy_slippage", what="scenario",
                                     low=0, high=0.999),
                sell_slippage=_number(scenario, "sell_slippage", what="scenario",
                                      low=0, high=0.999),
                lot_size=int(_number(scenario, "lot_size", what="scenario",
                                     integer=True, low=1)),
                reinvest_cash=_bool(scenario, "reinvest_cash", default=True),
                step_unit=str(scenario.get("step_unit") or "trading_day")),
            evaluation=EvaluationPolicy(
                min_probability_samples=int(_number(
                    evaluation, "min_probability_samples",
                    what="evaluation", integer=True, low=1)
                    if "min_probability_samples" in evaluation else 30),
                rho_sensitivity=(_number(evaluation, "rho_sensitivity",
                                         what="evaluation", low=0, high=0.5)
                                 if "rho_sensitivity" in evaluation else 0.10),
                cost_sensitivity=(_number(evaluation, "cost_sensitivity",
                                          what="evaluation", low=0, high=1)
                                  if "cost_sensitivity" in evaluation else 0.50),
                latency_sensitivity_steps=int(_number(
                    evaluation, "latency_sensitivity_steps",
                    what="evaluation", integer=True, low=0)
                    if "latency_sensitivity_steps" in evaluation else 1)),
            supported_entries=_strings(payload, "supported_entries")
            or ENTRY_KINDS,
            entry_mode=_entry_mode(payload.get("entry_mode", "compete")),
            label=str(payload.get("label") or ""),
            fixture=_bool(payload, "fixture", default=False),
        )
        config.validate()
        return config



def _entry_mode(value) -> str:
    if not isinstance(value, str) or value not in ("compete",
                                                   "probe_then_confirm"):
        raise PolicyConfigError(
            f"entry_mode 必须是 compete 或 probe_then_confirm，得到 {value!r}")
    return value


def _sequence(payload: Mapping, key: str) -> list:
    value = payload.get(key)
    if not isinstance(value, (list, tuple)) or not value:
        raise PolicyConfigError(f"recovery.{key} 必须是非空数组")
    return list(value)


def teaching_fixture(**overrides: Any) -> BSPolicyConfig:
    """教学/测试用固定参数（**非推荐**，``fixture=True``）。

    真实研究必须显式提供 rho/预算/失效/恢复/场景规格，不能沿用这里数字。
    """
    base = {
        "schema_version": BS_POLICY_SCHEMA_VERSION,
        "policy_id": "bs_account_decision",
        "profile_id": "teaching-profile",
        "profile_version": "fixture-1",
        "preference": {"rho": 0.6},
        "budgets": {
            "per_trade": 1000.0,
            "account": 3000.0,
            "per_theme": 1500.0,
            "unknown_group": 1000.0,
            "giveback": 800.0,
            "held_principal": 2000.0,
        },
        "sizing": {
            "probe_risk": 300.0,
            "confirmed_risk": 900.0,
            "add_step_risk": 300.0,
            "max_adds": 2,
        },
        "invalidation": {
            "price_stop_pct": -6.0,
            "time_limit_days": 5,
            "confirm_deadline_days": 2,
            "disaster_stop_pct": -10.0,
        },
        "recovery": {
            "drawdown_trigger_pct": 8.0,
            "loss_streak_trigger": 3,
            "cooldown_days": 2,
            "stages": [0.25, 0.5, 0.75],
            "conditions": ["market_regime_verified", "execution_quality_ok"],
        },
        "scenario": {
            "horizon_days": 5,
            "latency_steps": 1,
            "commission_rate": 0.0003,
            "min_commission": 5.0,
            "stamp_tax_rate": 0.0005,
            "buy_slippage": 0.002,
            "sell_slippage": 0.002,
            "lot_size": 100,
            "reinvest_cash": True,
            "step_unit": "trading_day",
        },
        "evaluation": {
            "min_probability_samples": 30,
            "rho_sensitivity": 0.1,
            "cost_sensitivity": 0.5,
            "latency_sensitivity_steps": 1,
        },
        "supported_entries": list(ENTRY_KINDS),
        "label": "教学假设，非推荐参数",
        "fixture": True,
    }
    for key, value in overrides.items():
        base[key] = value
    return BSPolicyConfig.from_dict(base)