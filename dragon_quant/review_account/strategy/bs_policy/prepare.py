"""评估准备：输入校验、账户/风险/保护义务与基础门禁（阶段 1）。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Mapping, Optional

from dragon_quant.review_account.evidence import coverage_summary

from .account import AccountView, RiskEnvelope
from .config import BSPolicyConfig, PolicyConfigError
from .decision import PolicyContext
from .pressure import _pressure_checks
from .protection import _giveback_decision
from .protection import (
    _hold_exits, _protection_obligations, prioritize_obligations,
)
from .recovery import _recovery_decision
from .stage import PrepareStage


def _active_covered_quantities(context: PolicyContext,
                               account: Optional[AccountView]) -> dict[str, int]:
    """本归属逐批强失效义务**真正覆盖**的数量（按批次 scope 分配）。

    预算类减仓只抵扣这些数量，其余批次仍按剩余目标继续收缩/回吐，不能因同
    code 已有强失效而整项豁免（O1）。
    """
    from dragon_quant.review_account.execution_core.identity import ref_identity

    if account is None:
        return {}
    remaining = {batch.batch_id: batch.qty for position in account.positions
                 for batch in position.batches}
    key = ref_identity(context.active_owner)
    covered: dict[str, int] = {}
    for obligation in context.owner_protections or ():
        if not obligation.batch_ids or ref_identity(obligation.owner) != key:
            continue
        left = int(obligation.qty or 0)
        for batch_id in obligation.batch_ids:
            free = remaining.get(batch_id, 0) - covered.get(batch_id, 0)
            take = min(left, max(0, free))
            if take > 0:
                covered[batch_id] = covered.get(batch_id, 0) + take
                left -= take
            if left <= 0:
                break
    return covered


def _scoped_account(account: Optional[AccountView],
                    owner: Mapping) -> Optional[AccountView]:
    """只保留该冻结归属的持仓行（同 code 的不同归属不参与本运行时决策）。"""
    if account is None:
        return None
    from dragon_quant.review_account.execution_core.identity import ref_identity

    key = ref_identity(owner)
    positions = tuple(item for item in account.positions
                      if ref_identity(item.strategy_ref) == key)
    return replace(account, positions=positions)


def prepare_stage(context: PolicyContext, config: BSPolicyConfig,
                  decision_moment: datetime) -> PrepareStage:
    """构建不依赖候选/场景的账户级阶段数据。"""
    reasons: list[dict] = []
    envelope = context.risk_envelope or RiskEnvelope()
    account = context.account
    # owner-scope 模式：本运行时只管理自己的冻结归属（其余 owner 的保护由外部
    # 解析器逐批提供）。账户级风险/压力仍按全账户计算（G6）。
    scoped_account = (_scoped_account(account, context.active_owner)
                      if context.owner_protections is not None else account)
    recovery = _recovery_decision(context, config,
                                  positions_account=scoped_account)
    valuation_known = context.current_valuation_known

    if context.snapshot is not None:
        coverage = coverage_summary(context.snapshot)
        if coverage["truncated"] or coverage["unbounded_scope"]:
            reasons.append({"reason": "coverage_truncated",
                            "truncated": coverage["truncated"],
                            "unbounded_scope": coverage["unbounded_scope"]})
            if context.strict:
                raise PolicyConfigError("严格模式证据快照被截断或范围未冻结",
                                        reason="coverage_truncated")
    if account is None:
        reasons.append({"reason": "account_state_missing",
                        "detail": "没有只读账户/持仓输入；不伪造资金"})
    elif account.cash is None:
        reasons.append({"reason": "account_state_missing",
                        "detail": "现金未知；不能模拟账户净损益"})
    equity = (account.marked_equity()
              if account is not None and valuation_known is not False else None)
    if account is not None and equity is None:
        reasons.append({"reason": "valuation_missing",
                        "detail": "持仓缺少盯市价；不能可靠估值"})

    available_risk = envelope.available_risk
    if available_risk is None:
        available_risk = config.budgets.account
    available_cash = envelope.available_cash
    if available_cash is None and account is not None and account.cash is not None:
        reserved_cash = sum(
            (reservation.price or 0.0) * reservation.qty
            for reservation in account.reservations
            if reservation.side.lower() == "buy" and reservation.qty > 0)
        available_cash = max(0.0, float(account.cash) - reserved_cash)

    market = context.market
    market_blocks: list[str] = []
    if market.new_risk_allowed is not True:
        market_blocks.append("market_new_risk_unknown")
    if recovery["active"] and not recovery["can_resume"]:
        market_blocks.append("recovery_restricted")
    if envelope.halted:
        market_blocks.append("risk_envelope_halted")
    if valuation_known is False:
        market_blocks.append("current_valuation_missing")
    if recovery["active"] and not recovery.get("anchor_verified", True):
        market_blocks.append("recovery_anchor_unverified")
    if context.owner_protections is not None and context.owner_restrictions:
        # 某归属的冻结保护不可用：其风险无法确定，新增风险按未知占用受限
        # （不取消其它 owner 的可确定保护，也不清仓）。
        market_blocks.append("owner_protection_unknown")

    giveback = _giveback_decision(config, scoped_account, envelope)
    step = min(config.scenario.latency_steps, config.scenario.horizon_days)
    if context.owner_protections is not None:
        # owner-scope 模式：价/灾难/时间失效由外部解析器逐批提供（不同 owner/计划
        # 不聚合）；纯核只补**未覆盖批次**的账户预算类义务（giveback/收缩），
        # 按剩余目标与已覆盖数量抵扣，避免整 code 豁免或双份清仓。
        obligations = _protection_obligations(
            config, scoped_account, giveback, recovery, step,
            include_invalidation=False, owner=context.active_owner,
            covered_by_batch=_active_covered_quantities(context,
                                                        scoped_account))
        obligations.extend(context.owner_protections)
        obligations = prioritize_obligations(obligations)
    else:
        obligations = _protection_obligations(config, account, giveback, recovery,
                                              step)
    hold_exits = _hold_exits(config, scoped_account, step, giveback)
    base_checks, base_reasons = _pressure_checks(
        config, account, account_limit=envelope.available_risk)
    risk_checks: list[dict] = [dict(check) for check in base_checks]
    if base_reasons:
        market_blocks.extend(reason for reason in base_reasons
                             if reason not in market_blocks)
    if market_blocks:
        reasons.append({"reason": "new_risk_restricted",
                        "detail": list(dict.fromkeys(market_blocks))})
    return PrepareStage(
        config=config, decision_moment=decision_moment, reasons=reasons,
        recovery=recovery, envelope=envelope, account=account, equity=equity,
        available_risk=float(available_risk),
        available_cash=available_cash, market_blocks=market_blocks,
        giveback=giveback, step=step, obligations=obligations,
        hold_exits=hold_exits, risk_checks=risk_checks,
        scoped_account=scoped_account,
        owner_restrictions=tuple(context.owner_restrictions or ()))
