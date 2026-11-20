"""尾部/右尾/恢复分段与分窗样本统计（纯函数）。"""

from __future__ import annotations

from .metrics_equity import num


def tail(closed, initial_equity, config, *, threshold_pct=None):
    """尾部阈值显式冻结（研究规范优先，其次 profile 灾难线）。

    返回频度 N / 严重度 / 币种 / 样本分母 / 阈值来源，不用「字段齐全」冒充口径。
    """
    values = [num(item.get("realized_net")) for item in closed or ()]
    values = [value for value in values if value is not None]
    source = None
    pct = None
    if threshold_pct is not None:
        pct = abs(float(threshold_pct))
        source = "study_explicit"
    elif config is not None:
        stop = num(getattr(config.invalidation, "disaster_stop_pct", None))
        if stop is not None:
            pct = abs(float(stop))
            source = "profile_invalidation_disaster_stop_pct"
    threshold = (None if pct is None or not initial_equity
                 else -pct / 100.0 * float(initial_equity))
    if not values:
        return {
            "tail_loss_count": None,
            "tail_loss_severity": None,
            "right_tail_max": None, "right_tail_share": None,
            "right_tail_total": None,
            "tail_threshold": threshold, "tail_threshold_pct": pct,
            "tail_threshold_source": source, "closed_sample": 0,
            "currency": "CNY",
        }
    tails = [value for value in values
             if threshold is not None and value <= threshold]
    positives = [value for value in values if value > 0]
    gross = sum(positives)
    return {
        "tail_loss_count": len(tails) if threshold is not None else None,
        "tail_loss_severity": min(tails) if tails else 0.0,
        "right_tail_max": max(positives) if positives else 0.0,
        "right_tail_share": (max(positives) / gross
                             if positives and gross else None),
        "right_tail_total": gross if positives else None,
        "tail_threshold": threshold, "tail_threshold_pct": pct,
        "tail_threshold_source": source, "closed_sample": len(values),
        "currency": "CNY",
    }


def _exposure(state):
    batches = state.get("batches") or []
    qty = sum(num(batch.get("qty")) or 0.0 for batch in batches)
    market_known = bool(batches)
    market_value = 0.0
    for batch in batches:
        value = num(batch.get("market_value"))
        if value is None:
            market_known = False
        else:
            market_value += value
    book = state.get("risk_book") or {}
    return {
        "position_qty": qty,
        "market_value": market_value if market_known else None,
        "risk_total": num(book.get("total")),
        "risk_unknown": bool(book.get("unknown_holdings")),
        "equity": num(state.get("equity")),
    }


def _exposure_pct(exposure):
    equity = exposure.get("equity")
    value = exposure.get("market_value")
    if not equity or value is None:
        return None
    return value / equity * 100.0


def recovery_segments(events, dates):
    """恢复阶段边界：按事件/交易日非重叠，带真实再暴露/权益变化与删失。

    阶段切换/退出时上一阶段 ``end`` 取最后一个仍活跃事件的交易日；同时保留
    事件时间戳。再暴露用实际持仓市值占权益（缺 mark → null），不是价格收益。
    """
    stages = []
    current = None
    previous_day = ""
    last_day = ""
    for event in events or ():
        recovery = event.get("recovery") or {}
        state = event.get("state") or {}
        day = str(event.get("trade_date") or "")
        timestamp = event.get("timestamp")
        if day:
            last_day = day
        exposure = _exposure(state)
        if not recovery.get("active"):
            if current is not None:
                current["end"] = previous_day or current["start"]
                current["end_ts"] = current.get("last_ts")
                current["censored"] = False
                stages.append(current)
                current = None
            previous_day = day or previous_day
            continue
        stage = int(recovery.get("stage") or 0)
        if current is not None and current["stage"] != stage:
            current["end"] = previous_day or current["start"]
            current["end_ts"] = current.get("last_ts")
            current["censored"] = False
            stages.append(current)
            current = None
        if current is None:
            current = {"stage": stage, "start": day, "end": "",
                       "start_ts": timestamp, "last_ts": timestamp,
                       "scale": recovery.get("current_scale"),
                       "exposure_start": exposure}
        current["exposure_end"] = exposure
        current["last_ts"] = timestamp
        previous_day = day or previous_day
    if current is not None:
        current["end"] = (dates[-1] if dates else last_day)
        current["end_ts"] = current.get("last_ts")
        current["censored"] = True
        stages.append(current)
    index = {day: pos for pos, day in enumerate(dates)}
    for segment in stages:
        start = index.get(segment["start"])
        end = index.get(segment["end"])
        segment["trade_days"] = (end - start + 1
                                 if start is not None and end is not None
                                 else None)
        start_ts = segment.get("start_ts")
        end_ts = segment.get("end_ts")
        segment["duration_ms"] = (
            None if start_ts is None or end_ts is None
            else int(end_ts) - int(start_ts))
        start_exposure = segment.pop("exposure_start", {})
        end_exposure = segment.pop("exposure_end", {})
        segment.pop("last_ts", None)
        segment["position_qty_start"] = start_exposure.get("position_qty")
        segment["position_qty_end"] = end_exposure.get("position_qty")
        segment["exposure_pct_start"] = _exposure_pct(start_exposure)
        segment["exposure_pct_end"] = _exposure_pct(end_exposure)
        segment["exposure_change_pct"] = (
            None if segment["exposure_pct_start"] is None
            or segment["exposure_pct_end"] is None
            else segment["exposure_pct_end"] - segment["exposure_pct_start"])
        segment["risk_total_start"] = start_exposure.get("risk_total")
        segment["risk_total_end"] = end_exposure.get("risk_total")
        segment["risk_unknown"] = bool(start_exposure.get("risk_unknown")
                                       or end_exposure.get("risk_unknown"))
        start_equity = start_exposure.get("equity")
        end_equity = end_exposure.get("equity")
        segment["equity_start"] = start_equity
        segment["equity_end"] = end_equity
        segment["equity_change"] = (
            None if start_equity is None or end_equity is None
            else end_equity - start_equity)
        segment["equity_return_pct"] = (
            None if not start_equity or end_equity is None
            else (end_equity - start_equity) / start_equity * 100.0)
    return stages


def split_samples(events, splits, *, initial_equity=None, calendar=()):
    """按分窗聚合样本；期初权益取窗口首日前的最后状态，不用首笔成交后权益。"""
    days = [str(day) for day in calendar or ()]
    order = {day: pos for pos, day in enumerate(days)}
    states = [event for event in events or ()
              if (event.get("state") or {}).get("equity") is not None]

    def opening_equity(first_day):
        if not days:
            return initial_equity
        position = order.get(first_day)
        if position is None:
            return initial_equity
        previous = None
        for event in states:
            day = str(event.get("trade_date") or "")
            pos = order.get(day)
            if pos is not None and pos < position:
                previous = num((event.get("state") or {}).get("equity"))
        return initial_equity if previous is None else previous

    result = {}
    for name, window in (splits.get("purged_days") or {}).items():
        allowed = set(window or ())
        subset = [event for event in events or ()
                  if str(event.get("trade_date")) in allowed]
        fills = sum(len(event.get("fills") or ()) for event in subset)
        orders = sum(len((event.get("state") or {}).get("orders") or ())
                     for event in subset)
        equities = [num((event.get("state") or {}).get("equity"))
                    for event in subset]
        equities = [value for value in equities if value is not None]
        window_days = sorted(allowed)
        start_equity = opening_equity(window_days[0]) if window_days else None
        end_equity = equities[-1] if equities else None
        net = (None if start_equity in (None, 0) or end_equity is None
               else (end_equity - start_equity) / start_equity * 100.0)
        peak = None
        dd = 0.0
        for value in ([start_equity] if start_equity is not None else []) \
                + equities:
            if peak is None or value > peak:
                peak = value
            elif peak and value < peak - 1e-12:
                dd = max(dd, (peak - value) / peak * 100.0)
        result[name] = {
            "status": "evaluated" if equities else "insufficient_data",
            "events": len(subset), "fills": fills,
            "order_snapshots": orders, "dates": window_days,
            "opening_equity": start_equity,
            "closing_equity": end_equity,
            "net_return_pct": net,
            "max_drawdown_pct": dd if equities else None,
            "holding_from_previous_window": (start_equity is not None
                                             and start_equity != initial_equity),
            "note": ("fixed-parameter split; no training/tuning; returns are "
                     "window-relative descriptive, not walk-forward"),
        }
    return result
