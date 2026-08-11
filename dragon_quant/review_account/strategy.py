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

    if rank != 1:
        return None
    if amount < cfg.min_amount:
        return None
    if turnover < cfg.min_turnover:
        return None
    if row.get("is_one_word_board"):
        return None
    if open_gap is not None and open_gap > cfg.max_open_gap:
        return None
    extreme_open_to_ma5 = cfg.max_close_to_ma5 * 1.5
    if open_to_ma5 is not None and open_to_ma5 > extreme_open_to_ma5:
        return None

    if ma5 and open_px <= ma5 * 1.03 and (open_gap is None or open_gap > -5.0):
        reasons.append("开盘贴近MA5，回踩后具备承接条件")
        code = "buy_open_ma5_pullback"
        priority = 300
    elif prev_high and open_px > prev_high and (open_gap is None or open_gap > 0):
        reasons.append("开盘突破前高，竞价弱转强")
        code = "buy_open_turn_strong"
        priority = 200
    elif prev_close and ma5 and open_px >= prev_close * 0.97 and open_px >= ma5:
        reasons.append("开盘未明显转弱且位于MA5上方，延续龙头承接")
        code = "buy_open_continuation"
        priority = 100
    elif prev_close and open_px >= prev_close * 0.97:
        reasons.append("开盘未明显转弱，按上日排名第一龙头执行试错")
        code = "buy_open_rank1_trial"
        priority = 50
    else:
        return None

    signal = _signal_payload(candidate, row, prev_row=prev_row, open_to_ma5=open_to_ma5)
    reason_text = (
        f"{'；'.join(reasons)}。上日龙头池排名{rank}，综合分{score:.1f}，"
        f"换手率{turnover:.1f}%，成交额{amount / 100000000:.1f}亿"
    )
    return {
        "action": "BUY",
        "code": code,
        "reason_text": reason_text,
        "priority": priority,
        "score": priority + max(0, 100 - rank * 5) + score,
        "signal": signal,
    }


def evaluate_sell(position: Position, row: dict, hold_days: int,
                  cfg: StrategyConfig) -> Optional[dict]:
    """按优先级返回卖出信号；继续持有返回 None。"""
    if position.entry_price <= 0:
        return None

    high_ret = (row["high"] / position.entry_price - 1) * 100
    low_ret = (row["low"] / position.entry_price - 1) * 100
    close_ret = (row["close"] / position.entry_price - 1) * 100
    position.highest_return = max(position.highest_return, high_ret)
    position.highest_price = max(position.highest_price, row["high"])

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
    }

    # 日 K 无法确认盘中先后，保守口径：止损先于止盈。
    if low_ret <= cfg.stop_loss_pct:
        return _sell("hard_stop_loss", f"盘中收益触及硬止损{cfg.stop_loss_pct:.1f}%", signal)

    if position.entry_day_low and row["close"] < position.entry_day_low:
        return _sell("break_entry_day_low", "收盘跌破买入日低点，结构失效", signal)

    ma5 = row.get("ma5")
    if hold_days >= 2 and position.highest_return < 3.0 and ma5 and row["close"] < ma5:
        return _sell("weak_follow_through", "买入后未形成有效浮盈且跌回MA5下方", signal)

    if ma5 and row["close"] < ma5:
        return _sell("break_ma5", "收盘跌破MA5，趋势失守", signal)

    if high_ret >= cfg.take_profit_pct:
        return _sell("take_profit", f"盘中收益达到固定止盈{cfg.take_profit_pct:.1f}%", signal)

    if (
        position.highest_return >= cfg.trailing_activate_pct
        and position.highest_return - close_ret >= cfg.trailing_drawdown_pct
    ):
        return _sell(
            "trailing_stop",
            f"最高浮盈{position.highest_return:.1f}%后回撤超过{cfg.trailing_drawdown_pct:.1f}%",
            signal,
        )

    if hold_days >= cfg.max_hold_days:
        return _sell("max_hold_days", f"持有达到最长{cfg.max_hold_days}个交易日", signal)

    return None


def _sell(code: str, reason_text: str, signal: dict) -> dict:
    return {"action": "SELL", "code": code, "reason_text": reason_text, "signal": signal}


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
