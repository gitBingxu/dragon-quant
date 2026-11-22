"""BS 冻结实验 KR6 指标聚合（窄入口：equity/risk/splits 分职责）。"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional

from .metrics_equity import (
    drawdown, equity_curve, fills, num, order_stats, slippage, trade_dates,
)
from .metrics_risk import recovery_segments, split_samples, tail
from .metrics_splits import maturity_report, time_splits

METRICS_VERSION = "bs_metrics/5"
_UNSET = object()


def _sample_id(item, index):
    return str(item.get("sample_id") or item.get("closed_trade_id")
               or f"sample:{index}")


def _qualified_closed(closed, maturity_report_data):
    """只保留成熟且合格的样本；返回 (qualified, descriptive)。"""
    if maturity_report_data is None:
        return closed, []
    ids = set(maturity_report_data.get("matured_ids") or ())
    qualified, descriptive = [], []
    for index, item in enumerate(closed):
        if _sample_id(item, index) in ids:
            qualified.append(item)
        else:
            descriptive.append(item)
    return qualified, descriptive


def _gate_summary(events) -> dict:
    rejected: dict[str, int] = {}
    unfilled: dict[str, int] = {}
    decided = 0
    for event in events or ():
        execution = event.get("execution_record") or {}
        if event.get("decided"):
            decided += 1
        for item in execution.get("rejected") or ():
            reason = str(item.get("reason") or "unknown")
            rejected[reason] = rejected.get(reason, 0) + 1
        for item in execution.get("matches") or ():
            if item.get("reason"):
                reason = str(item["reason"])
                unfilled[reason] = unfilled.get(reason, 0) + 1
    return {"decided_events": decided, "rejected": rejected,
            "unfilled": unfilled}


def _recovery_active_days(events) -> int:
    return len({str(event.get("trade_date") or "")
                for event in events or ()
                if (event.get("recovery") or {}).get("active")
                and event.get("trade_date")})


def definitions() -> dict:
    """指标定义/单位/分母/成熟依据（随结果持久化，CLI/Web 共用）。"""
    return {
        "net_return_pct": {
            "definition": "(final_equity - initial_equity) / initial_equity",
            "unit": "percent", "denominator": "initial_equity",
            "requires": "initial_equity and final_equity known"},
        "max_drawdown_pct": {
            "definition": "max peak-to-trough decline on trade-day equity curve",
            "unit": "percent", "denominator": "running peak equity",
            "requires": "initial equity seeded; research-window trade days"},
        "max_drawdown_duration_trade_days": {
            "definition": "trade days from peak day (inclusive) to trough or "
                          "recovery; flat curve is 0",
            "unit": "trade_days", "denominator": "frozen trade calendar"},
        "fees": {"definition": "sum of realized fill fees", "unit": "CNY",
                 "denominator": "fill sample"},
        "turnover": {
            "definition": "fill notional / average of initial and final equity",
            "unit": "ratio", "denominator": "(initial_equity+final_equity)/2"},
        "slippage": {
            "definition": "realized adverse difference between fill price and "
                          "frozen input reference execution_price joined by "
                          "event timestamp, per side; model estimate uses "
                          "per-side configured rate only when reference missing",
            "unit": "CNY", "denominator": "fill notional by side"},
        "tail_loss_count": {
            "definition": "QUALIFIED matured closed trades with realized_net "
                          "<= frozen threshold; zero sample -> null",
            "unit": "count", "denominator": "qualified matured closed_trades",
            "requires": "explicit threshold or profile disaster stop"},
        "right_tail_share": {
            "definition": "largest positive realized_net / sum of positive "
                          "realized_net on qualified matured samples",
            "unit": "ratio", "denominator": "qualified positive closed sample"},
        "risk_budget_used_peak": {
            "definition": "peak shared RiskBook.total (held initial_risk + live "
                          "reserved risk) across events; null when unknown",
            "unit": "CNY", "denominator": "account risk budget",
            "requires": "risk snapshot present and no unknown holdings"},
        "risk_budget_used_time_weighted": {
            "definition": "mean of each research trade day's last known "
                          "RiskBook.total; unknown days excluded",
            "unit": "CNY", "denominator": "research trade days with snapshots"},
        "unfilled_orders": {
            "definition": "unique orders whose filled_qty < qty at final state "
                          "(includes cancelled/rejected/partial/pending)",
            "unit": "count", "denominator": "unique order ids"},
        "unfilled_ratio": {
            "definition": "unfilled_orders / unique order ids",
            "unit": "ratio", "denominator": "unique order ids"},
        "unfilled_quantity_ratio": {
            "definition": "(requested_qty - filled_qty) / requested_qty",
            "unit": "ratio", "denominator": "requested order quantity"},
        "recovery_active_trade_days": {
            "definition": "distinct trade days with an active recovery episode "
                          "(stage spans may overlap; not summed)",
            "unit": "trade_days", "denominator": "events"},
        "recovery_segments": {
            "definition": "recovery stage spans with event timestamps, "
                          "censoring, equity return and real exposure change",
            "unit": "segment", "denominator": "events"},
        "alternatives": {
            "definition": "qualified_missed_amount = primary_final_equity - "
                          "alt_final_equity, positive means the primary kept "
                          "more value than the executable alternative; "
                          "descriptive_equity_diff is NOT qualified",
            "unit": "CNY", "denominator": "comparable executable policies"},
        "effect_status": {
            "definition": "evaluated only when every relevant label is matured "
                          "before a valid tz-aware cutoff and coverage/execution "
                          "support are usable and the qualified sample is non-empty",
            "unit": "enum", "denominator": "frozen labels and support facts"},
    }


def compute_metrics(*, initial_cash: Optional[float],
                    initial_equity: object = _UNSET,
                    events, final_state: Mapping, config=None,
                    trade_calendar: Iterable[str] = (),
                    date_from: Optional[str] = None,
                    date_to: Optional[str] = None,
                    alternatives: Optional[Mapping] = None,
                    maturity: Optional[bool] = None,
                    maturity_report_data: Optional[Mapping] = None,
                    splits: Optional[Mapping] = None,
                    study: Optional[Mapping] = None,
                    reference_rows: Optional[Mapping] = None,
                    execution_support: Optional[Mapping] = None) -> dict:
    """从一次已冻结 replay 计算 KR6；显式 initial_equity=None 表示未知（不退现金）。"""
    cash = num(initial_cash)
    if initial_equity is _UNSET:
        initial = cash
    else:
        initial = num(initial_equity)
    final_equity = num((final_state or {}).get("equity"))
    dates = trade_dates(events, trade_calendar)
    if date_from:
        dates = [day for day in dates if day >= str(date_from)]
    if date_to:
        dates = [day for day in dates if day <= str(date_to)]
    curve = equity_curve(events, final_state)
    if initial is not None:
        first_day = (dates[0] if dates else
                     (curve[0][0] if curve else None))
        if first_day is not None:
            curve = [(first_day, initial)] + [
                (day, value) for day, value in curve
                if not (day == first_day and abs(value - initial) < 1e-12)]
    fill_rows = fills(events)
    closed = list((final_state or {}).get("closed_trades") or ())
    qualified_closed, descriptive_closed = _qualified_closed(
        closed, maturity_report_data)
    fees = sum(num(fill.get("fee")) or 0.0 for fill in fill_rows)
    notional = sum((num(fill.get("price")) or 0.0)
                   * (num(fill.get("qty")) or 0.0) for fill in fill_rows)
    net_return = (None if initial in (None, 0) or final_equity is None
                  else (final_equity - initial) / initial * 100.0)
    max_dd, dd_days = drawdown(curve, dates)
    study = dict(study or {})
    threshold = study.get("tail_threshold_pct")
    tail_data = tail(qualified_closed, initial or 0.0, config,
                     threshold_pct=threshold)
    descriptive_tail = tail(descriptive_closed, initial or 0.0, config,
                            threshold_pct=threshold)
    values = [num(item.get("realized_net")) for item in qualified_closed]
    values = [value for value in values if value is not None]
    wins = sum(1 for value in values if value > 0)
    losses = sum(1 for value in values if value < 0)
    orders = order_stats(events, dates)
    filled_orders = len({fill.get("order_id") for fill in fill_rows})
    budget = num(getattr(config.budgets, "account", None)) if config else None
    recovery = recovery_segments(events, dates)
    slip = slippage(events, config, reference_rows=reference_rows)
    slippage_cost = slip["model_estimate"]["total"]
    labels_qualified = bool(maturity_report_data is not None
                            and maturity_report_data.get("status") == "evaluated")
    alternatives_out = {}
    for name, item in (alternatives or {}).items():
        alt_equity = num(item.get("final_equity"))
        alt_status = str(item.get("status") or "unknown")
        executable = bool(item.get("executable", alt_status == "evaluated"))
        comparable = bool(item.get("comparable", executable))
        qualified = bool(executable and comparable and labels_qualified
                         and final_equity is not None and alt_equity is not None)
        missed = (float(final_equity) - float(alt_equity)
                  if qualified else None)
        descriptive = (None if alt_equity is None or final_equity is None
                       else float(alt_equity) - float(final_equity))
        alternatives_out[name] = {
            "status": alt_status,
            "executable": executable,
            "comparable": comparable,
            "qualified": qualified,
            "final_equity": alt_equity,
            "qualified_missed_amount": missed,
            "missed_return_pct": (None if missed is None or initial in (None, 0)
                                  else missed / initial * 100.0),
            "descriptive_equity_diff": descriptive,
        }
    reasons = []
    engineering = "completed"
    if final_equity is None:
        engineering = "insufficient_data"
        reasons.append("equity_unavailable")
    if initial is None:
        engineering = "insufficient_data"
        reasons.append("initial_equity_unknown")
    effect = "evaluated"
    if maturity_report_data is not None:
        if maturity_report_data.get("status") != "evaluated":
            effect = "insufficient_data"
            reasons.append("labels_not_qualified")
            for blocker in maturity_report_data.get("blockers") or ():
                reasons.append(f"label_blocker:{blocker}")
    elif maturity is True:
        effect = "insufficient_data"
        reasons.append("maturity_unverified")
    elif maturity is False:
        effect = "insufficient_data"
        reasons.append("labels_not_qualified")
    else:
        effect = "insufficient_data"
        reasons.append("maturity_not_provided")
    if execution_support is not None and \
            execution_support.get("status") not in (None, "usable"):
        effect = "insufficient_data"
        reasons.append(f"execution_support:{execution_support.get('status')}")
    if not values:
        effect = "insufficient_data"
        reasons.append("no_qualified_closed_trades")
    if orders["risk_unknown"]:
        reasons.append("risk_unknown")
    if orders["risk_missing_snapshot"]:
        reasons.append("risk_snapshot_missing")
    status = "evaluated" if engineering == "completed" else "insufficient_data"
    peak_risk = orders["peak_risk"]
    excess_peak = (None if peak_risk is None or not budget
                   else max(0.0, peak_risk - budget))
    return {
        "metrics_version": METRICS_VERSION,
        "status": status,
        "engineering_status": engineering,
        "effect_status": effect,
        "reasons": reasons,
        "initial_cash": cash,
        "initial_equity": initial,
        "final_equity": final_equity,
        "currency": "CNY",
        "net_return_pct": net_return,
        "fees": fees if fill_rows else None,
        "fees_pct": (fees / initial * 100.0 if fill_rows and initial else None),
        "slippage_cost_estimate": slippage_cost,
        "slippage": slip,
        "slippage_basis": slip["basis"],
        "turnover": (notional / ((initial + final_equity) / 2.0)
                     if fill_rows and initial and final_equity else None),
        "max_drawdown_pct": max_dd,
        "max_drawdown_duration_trade_days": dd_days,
        **tail_data,
        "descriptive_tail": descriptive_tail,
        "qualified_sample_n": len(values),
        "qualified_wins": wins,
        "qualified_losses": losses,
        "qualified_win_rate": (wins / len(values) if values else None),
        "risk_budget_used_peak": peak_risk,
        "risk_budget_used_peak_lower_bound": orders["peak_risk_lower_bound"],
        "risk_budget_used_end": orders["end_risk"],
        "risk_budget_used_time_weighted": orders["time_weighted_risk"],
        "risk_budget_utilization_pct": (peak_risk / budget * 100.0
                                        if peak_risk is not None and budget
                                        else None),
        "risk_budget": budget,
        "risk_unknown": orders["risk_unknown"],
        "risk_unknown_holdings": orders["unknown_holdings_peak"],
        "excess_risk_budget_peak": excess_peak,
        "cash_reserved_peak": orders["peak_cash"],
        "cash_reserved_end": orders["end_cash"],
        "cash_reserved_time_weighted": orders["time_weighted_cash"],
        "funding": {**orders["funding"],
                    "risk_budget": budget,
                    "risk_unknown": orders["risk_unknown"],
                    "excess_risk_budget_end": (
                        None if orders["end_risk"] is None or not budget
                        else max(0.0, orders["end_risk"] - budget))},
        "unfilled_orders": orders["unfilled"],
        "unfilled_partial_orders": orders["unfilled_partial"],
        "open_pending_orders": orders["open_pending"],
        "cancelled_orders": orders["cancelled"],
        "rejected_orders": orders["rejected"],
        "unfilled_reasons": orders["unfilled_reasons"],
        "unfilled_qty": orders["unfilled_qty"],
        "filled_qty": orders["filled_qty"],
        "requested_qty": orders["requested_qty"],
        "filled_orders": filled_orders if fill_rows else 0,
        "order_denominator": orders["denominator"],
        "order_status_counts": orders["status_counts"],
        "unfilled_ratio": (orders["unfilled"] / orders["denominator"]
                           if orders["denominator"] else None),
        "unfilled_quantity_ratio": (orders["unfilled_qty"]
                                    / orders["requested_qty"]
                                    if orders["requested_qty"] else None),
        "gates": _gate_summary(events),
        "recovery_segments": recovery,
        "recovery_active_trade_days": _recovery_active_days(events),
        "recovery_stage_trade_day_sum": sum(
            int(segment.get("trade_days") or 0) for segment in recovery),
        "recovery_final_stage": int((final_state or {}).get("recovery_stage")
                                    or 0),
        "alternatives": alternatives_out,
        "maturity": (maturity_report_data if maturity_report_data is not None
                     else maturity),
        "splits": dict(splits or {}),
        "denominator": {
            "events": len(tuple(events or ())),
            "trade_dates": len(dates),
            "fills": len(fill_rows),
            "closed_trades": len(closed),
            "qualified_closed_trades": len(values),
            "orders": orders["denominator"],
        },
        "definitions": definitions(),
        "notes": [
            "future_high_only_diagnostic_not_realizable",
            "hurwicz_preference_is_not_ev",
            "unfilled_or_empty_does_not_imply_improvement",
            "alternatives_are_actual_replayed_policies_not_future_prices",
            "drawdown_peak_to_trough_with_underwater_duration_trade_days",
            "effect_status_requires_mature_labels_and_usable_support",
            "quality_metrics_use_qualified_matured_samples_only",
        ],
    }


__all__ = [
    "METRICS_VERSION", "compute_metrics", "definitions", "time_splits",
    "split_samples", "maturity_report",
]
