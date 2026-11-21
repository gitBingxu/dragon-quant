"""权益曲线、回撤、订单/资金/风险占用与滑点分解（纯函数，不触库）。"""

from __future__ import annotations

_TERMINAL = ("filled", "cancelled", "rejected")
_PENDING = ("proposed", "reserved", "pending")


def num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def fills(events):
    out = []
    for event in events or ():
        out.extend(event.get("fills") or ())
    return out


def trade_dates(events, calendar):
    names = list(calendar or ())
    names.extend(str(event.get("trade_date")) for event in events or ()
                 if event.get("trade_date"))
    return sorted(set(names))


def equity_curve(events, final_state):
    curve = []
    for event in events or ():
        state = event.get("state") or {}
        equity = num(state.get("equity"))
        if equity is not None:
            curve.append((str(event.get("trade_date") or ""), equity))
    final = num((final_state or {}).get("equity"))
    if final is not None:
        curve.append((str((final_state or {}).get("trade_date") or ""), final))
    return curve


def drawdown(curve, dates):
    """峰谷/水下持续：平峰不是水下；回到旧峰即结束；无回撤 0 天。"""
    if not curve or not dates:
        return None, None
    index = {day: pos for pos, day in enumerate(dates)}
    peak = None
    peak_day = ""
    max_dd = 0.0
    underwater_start = None
    max_duration = 0
    for day, equity in curve:
        pos = index.get(day)
        if pos is None:
            continue
        if peak is None or equity > peak:
            peak, peak_day = equity, day
            if underwater_start is not None:
                max_duration = max(max_duration, pos - underwater_start)
                underwater_start = None
            continue
        if peak and peak > 0 and equity < peak - 1e-12:
            dd = (peak - equity) / peak * 100.0
            max_dd = max(max_dd, dd)
            if underwater_start is None:
                underwater_start = index.get(peak_day, pos)
            max_duration = max(max_duration, pos - underwater_start + 1)
        elif underwater_start is not None:
            max_duration = max(max_duration, pos - underwater_start)
            underwater_start = None
    if underwater_start is not None:
        max_duration = max(max_duration, len(dates) - underwater_start)
    return max_dd, max_duration


def _funding_snapshot(state, reserved_cash=0.0):
    batches = state.get("batches") or []
    cash = num(state.get("cash"))
    cost_value = 0.0
    market_value = 0.0
    market_known = True
    for batch in batches:
        qty = num(batch.get("qty"))
        cost = num(batch.get("cost_price"))
        if qty is None or cost is None:
            market_known = False
            continue
        cost_value += qty * cost
        value = num(batch.get("market_value"))
        if value is None:
            market_known = False
        else:
            market_value += value
    book = state.get("risk_book")
    return {
        "cash": cash,
        "position_cost_value": cost_value if batches else 0.0,
        "position_market_value": market_value if (batches and market_known)
        else (0.0 if not batches else None),
        "equity": num(state.get("equity")),
        "risk_total": (None if int((book or {}).get("unknown_holdings") or 0)
                       else num((book or {}).get("total"))),
        "risk_unknown_holdings": int((book or {}).get("unknown_holdings") or 0),
        "risk_snapshot": bool(book),
        "reserved_cash": reserved_cash,
    }


def order_stats(events, trade_days=()):
    """订单身份/未成交/占用/资金：峰值、期末、交易日时间加权分别定义。

    - 所有订单按 ``order_id`` 去重；已成交/取消/拒绝仍保留在分母。
    - ``unfilled_orders`` = 期末仍有剩余数量的订单（含 cancelled/rejected/
      partially_filled/open pending），不是“仍 open pending”。
    - 风险占用优先读共享 ``state.risk_book``（含存量批次 initial_risk + 在途
      reserved risk + 未知持仓），缺失/未知时输出 null + 下界，不伪装 0。
    """
    unique: dict[str, dict] = {}
    per_day_risk: dict[str, float] = {}
    per_day_cash: dict[str, float] = {}
    per_day_funding: dict[str, dict] = {}
    peak_risk = peak_lower = peak_cash = 0.0
    end_risk = end_cash = None
    risk_unknown = False
    risk_missing_snapshot = False
    unknown_holdings_peak = 0
    for event in events or ():
        day = str(event.get("trade_date") or "")
        state = event.get("state") or {}
        active = {}
        for order in state.get("orders") or ():
            order_id = str(order.get("order_id") or "")
            if not order_id:
                continue
            unique[order_id] = dict(order)
            if str(order.get("status")) in _TERMINAL:
                continue
            active[order_id] = order
        cash = sum(num(item.get("reserved_amount")) or 0.0
                   for item in active.values())
        peak_cash = max(peak_cash, cash)
        end_cash = cash
        book = state.get("risk_book")
        if not book:
            risk_missing_snapshot = True
            risk = None
        else:
            total = num(book.get("total"))
            unknown = int(book.get("unknown_holdings") or 0)
            unknown_holdings_peak = max(unknown_holdings_peak, unknown)
            if total is not None:
                peak_lower = max(peak_lower, total)
            if unknown:
                risk_unknown = True
                risk = None
            else:
                risk = total
                if risk is not None:
                    peak_risk = max(peak_risk, risk)
        end_risk = risk
        if day:
            per_day_risk[day] = risk
            per_day_cash[day] = cash
            per_day_funding[day] = _funding_snapshot(state, cash)
    statuses: dict[str, int] = {}
    reasons: dict[str, int] = {}
    total_qty = filled_qty_total = unfilled_qty_total = 0
    unfilled_orders = partial_unfilled = open_pending = 0
    cancelled = rejected = 0
    for order in unique.values():
        status = str(order.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        qty = int(num(order.get("qty")) or 0)
        filled = int(num(order.get("filled_qty")) or 0)
        total_qty += qty
        filled_qty_total += max(0, min(filled, qty))
        remaining = max(0, qty - filled)
        unfilled_qty_total += remaining
        if status == "cancelled":
            cancelled += 1
        if status == "rejected":
            rejected += 1
        if status in _PENDING:
            open_pending += 1
        if remaining > 0:
            unfilled_orders += 1
            if filled > 0:
                partial_unfilled += 1
            reason = str(order.get("reason") or "")
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
    days = [str(day) for day in trade_days or ()]
    known_days = set(days)

    def average(mapping):
        values = [mapping[day] for day in days
                  if day in mapping and mapping[day] is not None]
        return sum(values) / len(values) if values else None

    def last(mapping):
        present = [day for day in mapping if day in known_days]
        present.sort()
        return mapping[present[-1]] if present else None

    funding_end = last(per_day_funding) or {}
    risk_complete = bool(unique or per_day_funding) and not risk_unknown \
        and not risk_missing_snapshot
    return {
        "orders": unique,
        "status_counts": statuses,
        "denominator": len(unique),
        "unfilled": unfilled_orders,
        "unfilled_partial": partial_unfilled,
        "open_pending": open_pending,
        "cancelled": cancelled,
        "rejected": rejected,
        "unfilled_reasons": reasons,
        "unfilled_qty": unfilled_qty_total,
        "filled_qty": filled_qty_total,
        "requested_qty": total_qty,
        "peak_risk": peak_risk if risk_complete else None,
        "peak_risk_lower_bound": peak_lower,
        "risk_unknown": risk_unknown or risk_missing_snapshot,
        "risk_missing_snapshot": risk_missing_snapshot,
        "unknown_holdings_peak": unknown_holdings_peak,
        "peak_cash": peak_cash if unique else None,
        "end_risk": end_risk,
        "end_cash": end_cash,
        "time_weighted_risk": average(per_day_risk),
        "time_weighted_cash": average(per_day_cash),
        "funding": {
            "cash_end": funding_end.get("cash"),
            "position_cost_value_end": funding_end.get("position_cost_value"),
            "position_market_value_end": funding_end.get("position_market_value"),
            "equity_end": funding_end.get("equity"),
            "reserved_risk_end": funding_end.get("risk_total"),
            "reserved_cash_end": funding_end.get("reserved_cash"),
            "idle_cash_end": (
                None if funding_end.get("cash") is None
                else funding_end["cash"] - (funding_end.get("reserved_cash")
                                            or 0.0)),
        },
    }


def _reference_rows(events, reference_rows):
    """真实 replay 事件无 rows 时，按 timestamp join 冻结输入的参考执行价。"""
    merged = {}
    if reference_rows:
        for timestamp, rows in reference_rows.items():
            merged[int(timestamp)] = dict(rows or {})
    for event in events or ():
        timestamp = event.get("timestamp")
        rows = event.get("rows")
        if rows and timestamp is not None:
            merged[int(timestamp)] = dict(rows)
    return merged


def slippage(events, config, reference_rows=None):
    """实际分边滑点：用冻结参考执行价分解；无参考价只给模型估计（不冒实测）。"""
    refs = _reference_rows(events, reference_rows)
    buy_notional = sell_notional = 0.0
    buy_cost = sell_cost = 0.0
    buy_fills = sell_fills = 0
    realized_fills = 0
    missing_reference = 0
    for event in events or ():
        rows = refs.get(int(event.get("timestamp") or -1)) or {}
        for fill in event.get("fills") or ():
            side = str(fill.get("side") or "")
            price = num(fill.get("price"))
            qty = num(fill.get("qty"))
            if price is None or qty is None:
                continue
            notional = price * qty
            if side == "buy":
                buy_notional += notional
                buy_fills += 1
            elif side == "sell":
                sell_notional += notional
                sell_fills += 1
            reference = num((rows.get(str(fill.get("code") or ""))
                             or {}).get("execution_price"))
            if reference is None:
                missing_reference += 1
                continue
            realized_fills += 1
            adverse = (price - reference) * qty
            if side == "sell":
                adverse = -adverse
            if side == "buy":
                buy_cost += adverse
            elif side == "sell":
                sell_cost += adverse
    buy_rate = float(config.scenario.buy_slippage) if config is not None else None
    sell_rate = (float(config.scenario.sell_slippage)
                 if config is not None else None)
    model = {
        "buy": (buy_notional * buy_rate if buy_rate is not None else None),
        "sell": (sell_notional * sell_rate if sell_rate is not None else None),
    }
    model["total"] = (None if model["buy"] is None or model["sell"] is None
                      else model["buy"] + model["sell"])
    realized = None
    if realized_fills and not missing_reference:
        realized = {
            "buy": {"fills": buy_fills, "notional": buy_notional,
                    "cost": buy_cost,
                    "rate_pct": (buy_cost / buy_notional * 100.0
                                 if buy_notional else None)},
            "sell": {"fills": sell_fills, "notional": sell_notional,
                     "cost": sell_cost,
                     "rate_pct": (sell_cost / sell_notional * 100.0
                                  if sell_notional else None)},
            "total": buy_cost + sell_cost,
        }
    return {
        "realized": realized,
        "model_estimate": model,
        "basis": ("realized_from_frozen_reference_price" if realized
                  else "model_estimate_only"),
        "reference": "frozen_input.events[timestamp].rows[code].execution_price",
        "missing_reference_fills": missing_reference,
        "realized_fills": realized_fills,
    }
