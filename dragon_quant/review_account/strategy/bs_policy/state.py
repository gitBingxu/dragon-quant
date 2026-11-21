"""计划 ID、范围合并与冻结政策状态查找（纯函数）。"""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

from .decision import PolicyContext
from .scenario_contracts import PositionPolicyState


def _plan_id(kind: str, code: str, step: Optional[int] = None,
             trigger: Optional[float] = None) -> str:
    suffix = f":s{step}" if step is not None else ""
    if trigger is not None:
        suffix += f":t{trigger:.4f}"
    return f"{kind}:{code or 'portfolio'}{suffix}"


def _merged_codes(context: PolicyContext) -> list[str]:
    codes = {position.code for position in context.account.positions} \
        if context.account else set()
    codes.update(candidate.code for candidate in context.candidates)
    return sorted(codes)


def _owner_key(owner) -> tuple:
    from dragon_quant.review_account.execution_core.identity import ref_identity

    return ref_identity(dict(owner or {}))


def _policy_state_for(context: PolicyContext, code: str, *,
                      owner: Optional[Mapping] = None,
                      probe_plan_id: str = "",
                      batch_ids: Sequence[str] = ()
                      ) -> Optional[PositionPolicyState]:
    """按完整 **owner + code + family/plan + batch scope** 查找政策状态。

    不是只按 code 取第一条：同 code 不同 owner / 独立 family 的状态互不串。
    显式 ``owner`` 时优先匹配同 owner；只有不存在 owner 专属状态时才回退到
    旧口径（``owner`` 为空、来源未知）的行，避免历史状态被误当成另一个 owner。
    """
    states = [state for state in context.policy_states if state.code == code]
    if probe_plan_id:
        states = [state for state in states
                  if state.probe_plan_id == probe_plan_id]
    if batch_ids:
        scope = set(batch_ids)
        states = [state for state in states
                  if scope & set(state.batch_ids or ())]
    if owner is not None:
        key = _owner_key(owner)
        typed = [state for state in states if dict(state.owner)]
        matching = [state for state in typed if _owner_key(state.owner) == key]
        if matching:
            states = matching
        elif typed:
            return None           # 有类型化 owner 状态但都不是本 owner → 不串用
        # typed 为空：保留旧口径（owner 未知）的状态作兼容回退。
    return states[0] if states else None
