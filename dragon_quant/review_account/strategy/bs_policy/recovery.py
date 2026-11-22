"""G8 收缩触发、锁存冷却与分阶段恢复状态（纯计算，无 I/O）。

两条职责：

- :func:`_recovery_decision` 判定本事件是否收缩/受限于恢复条件，供纯核使用；
- :func:`advance_recovery_days` / :func:`advance_recovery_after_decision` 推进
  **锁存状态**（触发日 0、真实交易日 +1、每有效交易日最多升一阶段）。

纪律（main M1/M2/M5）：

- 本事件回撤只由**当前 PIT 可执行估值**与先前真实峰值计算；未知当前估值不
  触发、不伪造授权。
- 阶段/毕业必须有本事件是真实交易日且本周期推进锚已验证的证明；周末/缺锚
  旧行保持受限，不靠外部数字逐事件抬阶段。
- 首次触发保存**撮合前存量**的批次 scope 与已批准目标；后续只按真实完成/
  更强保护覆盖抵扣，旧 episode 不每事件重建，新恢复批次不被旧目标半仓。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from .account import AccountView, RiskEnvelope
from .config import BSPolicyConfig
from .decision import PolicyContext
from .scenario_contracts import RecoveryState
from .sizing import _half_qty
from .timeutil import to_moment


def _confirmed_conditions(context: PolicyContext,
                          config: BSPolicyConfig) -> set[str]:
    """本决策时点**可用**的恢复条件（PIT 校验，不信任裸字符串）。

    - ``available_at`` 必须可解析且不晚于决策时点（迟到修订不回填过去授权）；
    - 决策带证据快照时，确认的 ``snapshot_id`` 必须一致（错误/缺失 snapshot 不恢复）；
    - 同一 rule 只要有一条在本时点合格即可，旧修订不取消已成立事实。
    """
    state = context.recovery_state or RecoveryState()
    decision = to_moment(context.decision_time)
    required = set(config.recovery.conditions)
    snapshot = context.snapshot
    met: set[str] = set()
    for confirmation in state.confirmations:
        if confirmation.rule_id not in required:
            continue
        available = to_moment(confirmation.available_at)
        if available is None:
            continue
        if decision is not None and available > decision:
            continue
        if snapshot is not None \
                and confirmation.snapshot_id != snapshot.snapshot_id:
            continue
        met.add(confirmation.rule_id)
    return met


def _snapshot_contraction_targets(account: Optional[AccountView],
                                  config: BSPolicyConfig) -> dict:
    """触发时点**撮合前**的存量快照：批次 scope + O1 既有目标。

    目标沿用既有口径（原始买入量的一半），不改变风险偏好；保存当时批次与
    剩余量，后续按快照抵扣，不因批次关闭/新批次加入/换阶段而重算。
    """
    if account is None:
        return {}
    targets: dict[str, dict] = {}
    lot = config.scenario.lot_size
    for position in account.aggregated_positions():
        batches = [batch for batch in position.batches if batch.qty > 0]
        if position.qty <= 0 or not batches:
            continue
        total_original = sum(int(batch.original_qty or batch.qty)
                             for batch in batches)
        targets[position.code] = {
            "batch_ids": [batch.batch_id for batch in batches],
            "target_qty": int(_half_qty(total_original, lot)),
            "remaining": {batch.batch_id: int(batch.qty)
                          for batch in batches},
        }
    return targets


def _recovery_decision(context: PolicyContext, config: BSPolicyConfig, *,
                       positions_account: Optional[AccountView] = None) -> dict:
    state = context.recovery_state or RecoveryState()
    envelope = context.risk_envelope or RiskEnvelope()
    account = context.account
    drawdown = envelope.drawdown_pct
    if drawdown is None and account is not None \
            and context.current_valuation_known is not False:
        # 当前估值未知（缺新鲜 PIT 报价）时不把旧 last_close 当已证明当前风险。
        drawdown = account.drawdown_pct
    losses = account.consecutive_losses if account is not None else 0
    trigger_reasons: list[str] = []
    if state.active:
        # 锁存：上次已进入收缩，本事件仍受限；不会因单日回撤回落而自动解除，
        # 也不会因每事件重算而重置触发日/重新冷却。
        trigger_reasons.append("previous_contraction")
    if drawdown is not None and drawdown >= config.recovery.drawdown_trigger_pct:
        trigger_reasons.append("drawdown_trigger")
    if losses >= config.recovery.loss_streak_trigger:
        trigger_reasons.append("loss_streak_trigger")
    if envelope.halted:
        trigger_reasons.append("risk_envelope_halted")
    active = bool(trigger_reasons)
    stages = config.recovery.stages
    # 只在**已锁存**的收缩期内沿用旧 stage；未锁存（含毕业后新触发）从
    # stage 0 重新开始，不能沿用上一轮的末阶段授权。
    stage = (min(max(0, int(state.stage)), len(stages) - 1)
             if state.active else 0)
    met = _confirmed_conditions(context, config)
    required = set(config.recovery.conditions)
    conditions_missing = sorted(required - met)
    anchor_verified = bool(state.trigger_event is not None
                           and state.trigger_trade_date)
    # 冷却从**本次**锁存 episode 起算：本事件刚产生新触发时还不能「恢复」
    # （旧 episode 的天数/锚不延续到新 episode，不能借旧天数跳过冷却）。
    can_resume = bool(
        active
        and state.active
        and anchor_verified
        and state.days_since_trigger >= config.recovery.cooldown_days
        and not conditions_missing)
    proposed_stage = min(stage + 1, len(stages) - 1) if can_resume else stage
    # 本事件**实际**授权上限是已锁存的 stage；proposed 只表示下一有效交易日
    # 可推进到哪里。否则同一交易日多个事件会按 proposed 连续抬高授权。
    risk_scale = stages[stage] if active else 1.0
    targets = dict(state.contraction_targets or {})
    snapshot_date = state.target_snapshot_trade_date
    # main W4a：旧 active episode 缺触发目标快照时，**不得**用当前持仓自动重建
    # 过去 scope（会把触发后买入的批次误当触发时存量）。只有「本次新触发」或
    # 显式教学 fixture 才允许从当前事实快照；否则保持受限并记录原因。
    # 空 scope 已明确捕获（target_snapshot_trade_date 有值）≠ 旧 episode 缺证明。
    target_snapshot_missing = bool(
        active and not targets and not state.target_snapshot_trade_date)
    if active and not targets and positions_account is not None:
        if not state.active or config.fixture:
            # 本次新触发（或显式教学 fixture）：从当前撮合前事实拍快照，
            # 合法空 scope 也算**已捕获**。
            targets = _snapshot_contraction_targets(positions_account, config)
            snapshot_date = (context.decision_time or "")[:10]
            target_snapshot_missing = False
        elif not state.target_snapshot_trade_date:
            # 旧 active episode 既无目标又无捕获证明：受限，不重建过去 scope。
            target_snapshot_missing = True
        # else：已锁存 episode 已有捕获日期（空快照亦完整），保留该证明；
        # 不再把「空目标」误判为「缺证明」，也不从当前持仓重建过去 scope。
    if target_snapshot_missing:
        trigger_reasons.append("target_snapshot_missing")
        # 保护 scope 不可验证时不允许靠阶段推进/毕业「恢复」：只保持受限观察。
        can_resume = False
        proposed_stage = stage
    return {
        "active": active,
        "reasons": tuple(trigger_reasons),
        "stage": stage,
        "proposed_stage": proposed_stage,
        "days_since_trigger": state.days_since_trigger,
        "trigger_event": state.trigger_event,
        "trigger_trade_date": state.trigger_trade_date,
        "anchor_verified": anchor_verified,
        "scale": risk_scale,
        "current_scale": stages[stage] if active else 1.0,
        "can_resume": can_resume,
        "conditions_met": sorted(met),
        "conditions_missing": conditions_missing,
        "contraction_targets": targets,
        "target_snapshot_trade_date": snapshot_date,
        "narrative": (
            "收缩期只限制新增风险；存量减仓/退出独立评估，不无条件清仓。"
            if active else "未触发收缩。"),
    }


def advance_recovery_days(state: RecoveryState, *, trade_date: str,
                          calendar=()) -> RecoveryState:
    """按**真实交易日历**推进收缩天数；同交易日多事件只推进一次。

    缺日历或日期不在日历内时保持原值（受限，不用截断日历假精确）；周末/休市
    不在日历内，因此不计入。旧行缺推进锚时**锚定到本次首个真实观测日**，但
    不推过去天数（不伪造历史进度）。
    """
    if not state.active or not trade_date:
        return state
    days = tuple(calendar or ())
    if not days or trade_date not in days:
        return state
    if len(set(days)) != len(days):
        return state                      # 重复日历不假精确
    last = state.last_advanced_trade_date
    if not last or last not in days:
        # 锚定：保留已累计天数，只把推进锚定到首个真实观测交易日。
        trigger = state.trigger_trade_date or trade_date
        return replace(state, trigger_trade_date=trigger,
                       last_advanced_trade_date=trade_date)
    if trade_date <= last:
        return state
    increment = days.index(trade_date) - days.index(last)
    if increment <= 0:
        return state
    return replace(
        state,
        days_since_trigger=state.days_since_trigger + increment,
        last_advanced_trade_date=trade_date)


def advance_recovery_after_decision(
        state: RecoveryState, *, recovery, config: BSPolicyConfig,
        trade_date: str, event_ts: int, calendar=(),
        confirmations=()) -> RecoveryState:
    """决策后推进锁存/阶段：触发锁存、每有效交易日最多升一阶段、合规毕业。

    - 未收缩 → 本决策触发收缩：记录触发事件/交易日（触发日 0），清零天数，
      并保存本次撮合前的收缩目标快照；
    - 已收缩且条件/冷静期满足：只有本事件是**真实交易日**、本周期推进锚已验证
      （``last_advanced_trade_date == trade_date``）且同日未升过阶段时才升一级；
    - 末阶段 ``>= 1.0`` 且条件满足才毕业；末阶段 ``< 1.0`` 保持该上限；
    - 缺锚旧行保持受限，不靠外部天数逐事件抬阶段。
    """
    stages = tuple(config.recovery.stages)
    last_index = len(stages) - 1
    if not state.active:
        if not recovery.get("active"):
            return state
        return replace(
            state, active=True, stage=0, days_since_trigger=0,
            trigger_event=int(event_ts), trigger_trade_date=trade_date,
            last_advanced_trade_date=trade_date,
            last_stage_advanced_trade_date="",
            graduated_trade_date="",
            confirmations=tuple(confirmations) or state.confirmations,
            contraction_targets=(recovery.get("contraction_targets") or {}),
            target_snapshot_trade_date=(
                recovery.get("target_snapshot_trade_date") or trade_date))
    if not recovery.get("active"):
        # 显式毕业/解除（仅配置允许时由本函数写入）；清空旧 episode 快照，
        # 让下一次真实触发按新存量重新快照。保持幂等。
        return replace(state, active=False, stage=0,
                       contraction_targets={}, target_snapshot_trade_date="")
    stage = min(max(0, int(state.stage)), last_index)
    targets = (dict(state.contraction_targets or {})
               or dict(recovery.get("contraction_targets") or {}))
    target_date = (state.target_snapshot_trade_date
                   or recovery.get("target_snapshot_trade_date") or "")
    days = tuple(calendar or ())
    anchor_ready = bool(
        len(set(days)) == len(days)
        and state.trigger_event is not None
        and state.trigger_trade_date
        and state.last_advanced_trade_date
        and state.last_advanced_trade_date in days
        and state.last_advanced_trade_date == trade_date
        and trade_date in days)
    new_day = bool(anchor_ready
                   and trade_date != state.last_stage_advanced_trade_date)
    can_advance = bool(recovery.get("can_resume") and new_day
                       and state.days_since_trigger >= 1
                       and trade_date > state.trigger_trade_date)
    if not can_advance:
        return replace(state, stage=stage,
                       confirmations=tuple(confirmations) or state.confirmations,
                       contraction_targets=targets,
                       target_snapshot_trade_date=target_date)
    new_stage = min(stage + 1, last_index)
    if new_stage == last_index and stages[last_index] >= 1.0:
        # 毕业：旧 episode 结束，清空目标快照；新触发必须按新存量重建。
        return replace(
            state, active=False, stage=last_index,
            last_stage_advanced_trade_date=trade_date,
            graduated_trade_date=trade_date,
            confirmations=tuple(confirmations) or state.confirmations,
            contraction_targets={}, target_snapshot_trade_date="")
    return replace(
        state, stage=new_stage, last_stage_advanced_trade_date=trade_date,
        confirmations=tuple(confirmations) or state.confirmations,
        contraction_targets=targets,
        target_snapshot_trade_date=target_date)


__all__ = [
    "advance_recovery_after_decision",
    "advance_recovery_days",
]
