"""账本状态 → W2 只读 ``AccountView`` 投影（Adapter）。

按 ``(code, 冻结归属)`` 分组：同 code 不同策略/计划归属保持为独立持仓行，批次
真值（数量/可卖量/可卖日/成本/归属）原样带过去，聚合可卖量由批次之和导出，
不用聚合字段覆盖批次（R6）。主题/压力/趋势等市场属性未由账本提供时保持
``None/()``（受限），不凭空推断。
"""

from __future__ import annotations

from typing import Mapping, Optional

from dragon_quant.review_account.strategy.bs_policy.account import (
    AccountView, BatchView, PositionView, Reservation,
)

from .matching import _owner_key
from .models import LedgerState


def _one_or_none(values):
    present = {int(value) for value in values or () if value is not None}
    return present.pop() if len(present) == 1 else None


def project_account_view(state: LedgerState, *,
                         themes: Optional[Mapping[str, tuple[str, ...]]] = None,
                         pressures: Optional[Mapping[str, float]] = None,
                         trends: Optional[Mapping[str, str]] = None,
                         held_days: Optional[Mapping[str, int]] = None,
                         held_days_by_batch: Optional[Mapping[str, int]] = None,
                         ) -> AccountView:
    themes = themes or {}
    pressures = pressures or {}
    trends = trends or {}
    held_days = held_days or {}
    held_days_by_batch = held_days_by_batch or {}
    checkpoints = dict(getattr(state, "position_facts", {}) or {})
    groups: dict[tuple[str, tuple], list] = {}
    for batch in state.batches:
        if batch.remaining <= 0:
            continue
        key = (batch.code, _owner_key(batch.strategy_ref))
        groups.setdefault(key, []).append(batch)
    positions: list[PositionView] = []
    for (code, _owner), batches in sorted(groups.items()):
        checkpoints_here = [
            checkpoints.get(str(batch.batch_id))
            or checkpoints.get(f"code:{code}") or {}
            for batch in batches]
        checkpoint = next((item for item in checkpoints_here if item), {})
        batch_views = tuple(
            BatchView(
                batch_id=batch.batch_id, qty=batch.remaining,
                sellable_qty=batch.sellable(state.trade_date),
                entry_date=batch.entry_date, cost_price=batch.cost_price,
                sellable_date=(batch.sellable_date
                               if batch.sellable_mode != "unknown" else None),
                strategy_ref=batch.strategy_ref, plan_id=batch.plan_id,
                owner=_owner[1] if len(_owner) > 1 else "",
                original_qty=batch.qty, family_id=batch.family_id or "",
                initial_risk=batch.initial_risk, ledger_origin=True,
                sellable_mode=str(getattr(batch, "sellable_mode", "") or ""))
            for batch in batches)
        qty = sum(item.qty for item in batch_views)
        total_cost = sum(item.cost_price * item.qty for item in batch_views)
        positions.append(PositionView(
            code=code,
            qty=qty,
            sellable_qty=sum(item.sellable_qty for item in batch_views),
            cost_price=(total_cost / qty) if qty else 0.0,
            entry_date=min((item.entry_date for item in batch_views
                            if item.entry_date), default=""),
            mark=state.marks.get(code),
            theme_ids=tuple(themes.get(code)
                            or checkpoint.get("theme_ids") or ()),
            pressure_pct=(pressures.get(code)
                          if code in pressures
                          else checkpoint.get("pressure_pct")),
            trend=trends.get(code, "unknown"),
            held_days=_one_or_none([
                held_days_by_batch.get(str(batch.batch_id))
                for batch in batches
                if str(batch.batch_id) in held_days_by_batch]
                or [held_days.get(code)]),
            strategy_ref=batches[0].strategy_ref,
            batches=batch_views))
    reservations = tuple(
        Reservation(code=item.code, side=item.side, qty=item.qty,
                    price=item.price, theme_ids=item.theme_ids,
                    pressure_pct=item.pressure_pct,
                    purpose=item.purpose or "reserved")
        for item in state.reservations if item.status == "reserved")
    return AccountView(cash=state.cash, positions=tuple(positions),
                       reservations=reservations,
                       peak_equity=state.peak_equity or None,
                       drawdown_pct=state.drawdown_pct or None,
                       consecutive_losses=state.consecutive_losses)
