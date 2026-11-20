"""BS 账户政策纯核入口：``evaluate_account_policy(context)``。

只做阶段编排：准备账户/风险 → 候选与存量方案 → 保护义务注入 → 共同场景比较
→ 决策包。具体职责在 prepare/candidate_stage/position_stage/scenario_stage/
finalize 中；本模块不读时钟/env/数据库，不执行交易。
"""

from __future__ import annotations

from dataclasses import replace

from .config import BSPolicyConfig, PolicyConfigError
from .decision import DecisionBundle, PolicyContext
from .finalize import build_bundle
from .base import PolicyInputError
from .candidate_stage import build_candidate_stage
from .entry_mode import apply_entry_mode
from .output import _observation_bundle
from .position_stage import build_position_stage
from .prepare import prepare_stage
from .protection import _inject_protection
from .scenario_stage import build_scenario_stage
from .timeutil import to_moment

__all__ = ["evaluate_account_policy"]


def _validate_context(context: PolicyContext) -> tuple[BSPolicyConfig, object]:
    decision_moment = to_moment(context.decision_time)
    if decision_moment is None:
        raise PolicyInputError(f"非法 decision_time: {context.decision_time!r}")
    config = context.config
    if not isinstance(config, BSPolicyConfig):
        raise PolicyConfigError("config 必须是 BSPolicyConfig")
    config.validate()
    if context.snapshot is not None:
        snapshot_cutoff = to_moment(context.snapshot.cutoff)
        if snapshot_cutoff is None or snapshot_cutoff > decision_moment:
            raise PolicyInputError(
                "证据快照 cutoff 晚于本次决策时点，不能把未来快照当作可用证据",
                reason="snapshot_after_decision")
    return config, decision_moment


def evaluate_account_policy(context: PolicyContext) -> DecisionBundle:
    """纯函数：先保护义务、后可选方案；场景逐事件推进。"""
    decision_moment = to_moment(context.decision_time)
    if decision_moment is None:
        raise PolicyInputError(f"非法 decision_time: {context.decision_time!r}")
    if context.config is None:
        reason = {"reason": "preference_missing",
                  "detail": "没有用户风险偏好/预算配置；不发明默认 rho 或预算"}
        if context.strict:
            raise PolicyConfigError("严格模式缺少 BS 政策配置",
                                    reason="preference_missing")
        return _observation_bundle(context, None, (reason,), "observe")
    config, decision_moment = _validate_context(context)
    if config.fixture and context.strict:
        return _observation_bundle(
            context, config,
            ({"reason": "fixture_policy_not_allowed_in_strict_mode"},),
            "observe")

    stage = prepare_stage(context, config, decision_moment)
    candidate_plans, gates, transitions = build_candidate_stage(
        context, stage, tuple(context.candidates))
    position_plans, position_gates = build_position_stage(
        context, stage, tuple(context.candidates))
    gates.extend(position_gates)
    plans = [_inject_protection(plan, stage.obligations, stage.hold_exits)
             for plan in candidate_plans + position_plans]
    # 自主动作/腿归属活动冻结 owner；必需保护仍按各自 obligation.owner 执行。
    plans = [replace(plan, owner=dict(context.active_owner or {}))
             for plan in plans]
    plans, entry_gates = apply_entry_mode(plans, context, config)
    gates.extend(entry_gates)
    result = build_scenario_stage(context, stage, plans, gates,
                                  tuple(context.candidates))
    return build_bundle(context, stage, result, transitions)
