"""账户级 review 的买入/卖出策略。"""

from typing import Optional

from dragon_quant.review_account.models import Position, StrategyConfig


def evaluate_buy(candidate: dict, row: dict, cfg: StrategyConfig,
                 prev_row: Optional[dict] = None) -> Optional[dict]:
    """返回开盘买入信号；不满足则返回 None。

    买入发生在当日开盘，只能使用上日龙头池、上日技术指标和当日开盘价。
    不读取当日 close/high/low 来决定是否买入，避免未来函数。
    """
    reasons: list[str] = []
    rank = candidate.get("rank") or 999
    score = candidate.get("composite_score") or 0.0
    amount = _amount_yuan(candidate.get("amount") or row.get("amount") or 0.0)
    turnover = candidate.get("turnover_rate") or row.get("turnover") or 0.0
    ref = prev_row or row
    open_px = row.get("open") or 0.0
    open_gap = row.get("open_gap_pct")
    prev_close = row.get("prev_close") or ref.get("close")
    ma5 = ref.get("ma5")
    prev_high = ref.get("high") or row.get("prev_high")
    open_to_ma5 = (open_px / ma5 - 1) * 100 if ma5 and ma5 > 0 and open_px > 0 else None

    if candidate.get("is_true_dragon") is False:
        return None
    if amount < cfg.min_amount:
        return None
    if row.get("is_one_word_board"):
        return None

    if ma5 and open_px <= ma5 * 1.03 and (open_gap is None or open_gap > -3.0):
        reasons.append("开盘贴近MA5，回踩后具备承接条件")
        code = "buy_open_ma5_pullback"
        priority = 300
    elif (
        prev_high and open_px > prev_high
        and open_gap is not None and 0 <= open_gap <= 5.5
        and open_to_ma5 is not None and open_to_ma5 <= cfg.max_close_to_ma5
        and turnover >= cfg.strong_turnover_min
        and amount >= 500_000_000
    ):
        reasons.append("开盘突破前高，竞价弱转强")
        code = "buy_open_turn_strong"
        priority = 200
    else:
        return None

    signal = _signal_payload(candidate, row, prev_row=prev_row, open_to_ma5=open_to_ma5)
    reason_text = (
        f"{'；'.join(reasons)}。上日真龙池排名{rank}，综合分{score:.1f}，"
        f"换手率{turnover:.1f}%，成交额{amount / 100000000:.1f}亿"
    )
    return {
        "action": "BUY",
        "code": code,
        "reason_text": reason_text,
        "priority": priority,
        "score": priority,
        "signal": signal,
    }


def explain_buy_candidate(candidate: dict, row: Optional[dict], cfg: StrategyConfig,
                          prev_row: Optional[dict] = None) -> dict:
    """解释候选未买入原因；满足买入时返回买入信号摘要。"""
    rank = candidate.get("rank") or 999
    name = candidate.get("name") or candidate.get("code") or "候选"
    if not row:
        return _buy_explain(
            candidate, "missing_kline", f"{name} 缺少当日开盘日K数据，无法判断买点"
        )

    signal = evaluate_buy(candidate, row, cfg, prev_row=prev_row)
    if signal:
        return {
            "code": candidate.get("code", ""),
            "name": candidate.get("name", ""),
            "rank": rank,
            "passed": True,
            "reason_code": signal["code"],
            "reason_text": signal["reason_text"],
            "signal": signal["signal"],
        }

    amount = _amount_yuan(candidate.get("amount") or row.get("amount") or 0.0)
    turnover = candidate.get("turnover_rate") or row.get("turnover") or 0.0
    ref = prev_row or row
    open_px = row.get("open") or 0.0
    open_gap = row.get("open_gap_pct")
    ma5 = ref.get("ma5")
    prev_high = ref.get("high") or row.get("prev_high")
    open_to_ma5 = (open_px / ma5 - 1) * 100 if ma5 and ma5 > 0 and open_px > 0 else None

    if candidate.get("is_true_dragon") is False:
        return _buy_explain(candidate, "not_true_dragon", f"{name} 不是上日真龙候选")
    if amount < cfg.min_amount:
        return _buy_explain(
            candidate, "amount_too_low",
            f"{name} 成交额{amount / 100000000:.1f}亿，低于{cfg.min_amount / 100000000:.1f}亿门槛",
        )
    if row.get("is_one_word_board"):
        return _buy_explain(candidate, "one_word_board", f"{name} 当日一字板，无法按开盘策略介入")
    if not ma5:
        return _buy_explain(candidate, "missing_ma5", f"{name} 缺少上日MA5，无法判断回踩承接")

    pullback_ok = open_px <= ma5 * 1.03 and (open_gap is None or open_gap > -3.0)
    turn_strong_ok = (
        bool(prev_high and open_px > prev_high)
        and open_gap is not None and 0 <= open_gap <= 5.5
        and open_to_ma5 is not None and open_to_ma5 <= cfg.max_close_to_ma5
        and turnover >= cfg.strong_turnover_min
        and amount >= 500_000_000
    )
    if not pullback_ok and not turn_strong_ok:
        parts = []
        if open_to_ma5 is not None:
            parts.append(f"开盘距MA5 {open_to_ma5:.1f}%")
        if prev_high:
            relation = "未突破" if open_px <= prev_high else "突破"
            parts.append(f"{relation}上日高点")
        if open_gap is not None:
            parts.append(f"开盘涨幅{open_gap:.1f}%")
        suffix = "，".join(parts) or "缺少有效开盘形态"
        return _buy_explain(
            candidate, "no_buy_pattern",
            f"{name} 未触发回踩MA5或弱转强买点（{suffix}）",
        )

    return _buy_explain(candidate, "filtered", f"{name} 未触发买入")


def evaluate_sell(position: Position, row: dict, hold_days: int,
                  cfg: StrategyConfig) -> list[dict]:
    """按优先级返回卖出信号列表；继续持有返回空列表。"""
    if position.entry_price <= 0:
        return []

    high_ret = (row["high"] / position.entry_price - 1) * 100
    low_ret = (row["low"] / position.entry_price - 1) * 100
    close_ret = (row["close"] / position.entry_price - 1) * 100
    position.highest_return = max(position.highest_return, high_ret)
    position.highest_price = max(position.highest_price, row["high"])
    ma5 = row.get("ma5")
    volume_change_pct = _volume_change_pct(row)

    signal = {
        "date": row["date"],
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        "ma5": row.get("ma5"),
        "hold_days": hold_days,
        "close_return": close_ret,
        "low_return": low_ret,
        "highest_return": position.highest_return,
        "took_profit_half": position.took_profit_half,
        "intraday_mode": "daily_k_approx",
        "is_limit_up_close": row.get("is_limit_up_close"),
        "volume": row.get("volume"),
        "prev_volume": row.get("prev_volume"),
        "volume_change_pct": volume_change_pct,
    }

    sells: list[dict] = []

    if low_ret <= cfg.stop_loss_pct:
        return [_sell(
            "hard_stop_loss",
            f"日K近似：当日最低价触及{cfg.stop_loss_pct:.1f}%硬止损，卖出全部持仓",
            signal,
        )]

    close_below_open = row["close"] < row["open"]
    close_below_ma5 = bool(ma5 and row["close"] < ma5)
    close_above_ma5 = bool(ma5 and row["close"] > ma5)

    if hold_days == 1:
        if row.get("is_limit_up_close"):
            sells.append(_sell(
                "next_day_limit_up_half",
                "买入次日收盘涨停，按涨停价卖出半仓",
                signal,
            ))
            if close_below_ma5 or close_below_open:
                sells.append(_sell(
                    "next_day_limit_up_clear",
                    "买入次日涨停后收盘转弱，清仓剩余持仓",
                    signal,
                ))
            return sells
        if close_below_open:
            return [_sell("next_day_close_below_open", "买入次日收盘价低于开盘价，清仓离场", signal)]

    if close_below_open:
        return [_sell("close_below_open_stop", "收盘价低于开盘价，止损离场", signal)]

    if close_below_ma5:
        return [_sell("break_intraday_ma_stop", "日K近似：收盘价低于日内均线，止损离场", signal)]

    if position.highest_return >= cfg.breakeven_activate_pct and row["low"] <= position.entry_price:
        return [_sell(
            "profit_back_to_cost_take_profit",
            f"最高浮盈达到过{cfg.breakeven_activate_pct:.1f}%，日K近似回落至成本线，止盈保护离场",
            signal,
        )]

    if close_above_ma5:
        return [_sell("close_above_ma5_take_profit", "收盘价高于5日线，止盈离场", signal)]

    if volume_change_pct is not None and volume_change_pct >= cfg.volume_spike_pct:
        return [_sell(
            "volume_spike_take_profit",
            f"成交量较上日增加{volume_change_pct:.1f}%，止盈离场",
            signal,
        )]

    return []


def _sell(code: str, reason_text: str, signal: dict) -> dict:
    return {"action": "SELL", "code": code, "reason_text": reason_text, "signal": signal}


def _volume_change_pct(row: dict) -> Optional[float]:
    prev_volume = row.get("prev_volume")
    volume = row.get("volume")
    if not prev_volume or prev_volume <= 0 or volume is None:
        return None
    return (volume / prev_volume - 1) * 100


def _buy_explain(candidate: dict, reason_code: str, reason_text: str) -> dict:
    return {
        "code": candidate.get("code", ""),
        "name": candidate.get("name", ""),
        "rank": candidate.get("rank"),
        "passed": False,
        "reason_code": reason_code,
        "reason_text": reason_text,
        "signal": {},
    }


def _signal_payload(candidate: dict, row: dict,
                    prev_row: Optional[dict] = None,
                    open_to_ma5: Optional[float] = None) -> dict:
    keys = (
        "date", "open", "high", "low", "close", "pct", "ma5", "ma10", "ma20",
        "open_gap_pct", "close_to_ma5_pct", "low_touch_ma5", "prev_high",
        "return_3d", "return_5d", "max_drawdown_5d", "avg_amplitude_5",
    )
    payload = {k: row.get(k) for k in keys}
    ref = prev_row or {}
    payload.update({
        "rank": candidate.get("rank"),
        "candidate_trade_date": candidate.get("trade_date"),
        "composite_score": candidate.get("composite_score"),
        "turnover_rate": candidate.get("turnover_rate"),
        "amount": candidate.get("amount"),
        "amount_yuan": _amount_yuan(candidate.get("amount") or row.get("amount") or 0.0),
        "board_count": candidate.get("board_count"),
        "decision_price": row.get("open"),
        "decision_timing": "open",
        "prev_ma5": ref.get("ma5"),
        "prev_close": row.get("prev_close") or ref.get("close"),
        "prev_day_high": ref.get("high"),
        "open_to_ma5_pct": open_to_ma5,
    })
    return payload


def _amount_yuan(amount: float) -> float:
    """兼容成交额元/万元两种口径。

    当前腾讯快照入库常见为万元口径（如 74080 表示约 7.4 亿），
    账户策略统一按元和 min_amount 比较。
    """
    if amount <= 0:
        return 0.0
    return amount * 10000 if amount < 10_000_000 else amount
