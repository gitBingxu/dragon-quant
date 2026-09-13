from typing import Optional

from dragon_quant.review_account.execution import break_even_price
from dragon_quant.review_account.models import Position, StrategyConfig


def evaluate_buy(candidate: dict, row: dict, cfg: StrategyConfig,
                 prev_row: Optional[dict] = None, hist_rows: Optional[list] = None,
                 intraday_bars: Optional[list] = None) -> Optional[dict]:
    if candidate.get("is_true_dragon") is False or (candidate.get("composite_score") or 0) < cfg.min_score:
        return None
    phase = row["phase"]
    if phase == "bar":
        return evaluate_divergence_buy(candidate, row, cfg, hist_rows, intraday_bars, prev_row)
    if phase != "open" or not prev_row:
        return None
    opening, ma5 = row["open"], prev_row.get("ma5")
    gap = row.get("open_gap_pct")
    amount, turnover = prev_row.get("amount") or 0, prev_row.get("turnover") or 0
    if not ma5 or gap is None or amount < cfg.min_amount or turnover < cfg.min_turnover:
        return None
    if opening >= row["limit_up"] * .999 or opening <= row["limit_down"] * 1.001:
        return None
    distance = (opening / ma5 - 1) * 100
    if cfg.require_rising_ma5 and ma5 <= (row.get("previous_ma5") or ma5):
        return None
    if cfg.ma5_min_distance_pct <= distance <= 3 and -3 < gap <= cfg.max_open_gap:
        return _buy(candidate, row, "buy_open_ma5_pullback", "开盘贴近上日MA5，回踩承接", 300)
    if (opening > prev_row["high"] and 0 <= gap <= min(5.5, cfg.max_open_gap)
            and distance <= cfg.max_close_to_ma5
            and cfg.strong_turnover_min <= turnover <= cfg.strong_turnover_max
            and amount >= 500_000_000):
        return _buy(candidate, row, "buy_open_turn_strong", "开盘突破上日高点，弱转强", 200)
    return None


def _count_shrinking_one_word_boards(hist_rows: list, cfg: StrategyConfig) -> tuple[int, bool]:
    volumes = []
    for row in reversed(hist_rows):
        if not row.get("is_one_word_board") or (row.get("pct") or 0) < 9.9:
            break
        volumes.append(row.get("volume") or 0)
    return len(volumes), all(volumes[i] <= volumes[i + 1] for i in range(len(volumes) - 1))


def evaluate_divergence_buy(candidate: dict, row: dict, cfg: StrategyConfig,
                            hist_rows: Optional[list] = None, intraday_bars: Optional[list] = None,
                            prev_row: Optional[dict] = None) -> Optional[dict]:
    if not cfg.divergence_enabled or candidate.get("is_true_dragon") is False:
        return None
    if (candidate.get("composite_score") or 0) < cfg.min_score:
        return None
    boards, shrinking = _count_shrinking_one_word_boards(hist_rows or [], cfg)
    if boards < cfg.divergence_min_boards or (cfg.divergence_require_shrinking_volume and not shrinking):
        return None
    bars = intraday_bars or []
    if len(bars) != cfg.divergence_confirm_bars:
        return None
    from dragon_quant.review_account.market import bar_times
    if [b.timestamp for b in bars] != bar_times(row["date"])[:cfg.divergence_confirm_bars]:
        return None
    if row["open"] >= row["limit_up"] * cfg.divergence_break_open_ratio:
        return None
    if min(b.low for b in bars) < row["prev_close"] or bars[-1].close < bars[0].open:
        return None
    result = _buy(candidate, row, "buy_divergence_first_break", "连续缩量一字板断板，完整窗口未破昨收且企稳", 400)
    result["signal"].update({"divergence_window_bars": len(bars), "board_count": boards})
    return result


def explain_buy_candidate(candidate: dict, row: Optional[dict], cfg: StrategyConfig,
                          prev_row: Optional[dict] = None, hist_rows: Optional[list] = None,
                          intraday_bars: Optional[list] = None) -> dict:
    signal = evaluate_buy(candidate, row, cfg, prev_row, hist_rows, intraday_bars) if row else None
    if signal:
        reason, text = signal["code"], signal["reason_text"]
    elif row is None:
        reason, text = "missing_kline", "缺少决策时点行情"
    elif candidate.get("is_true_dragon") is False:
        reason, text = "not_true_dragon", "历史记录明确否决，跳过"
    elif (candidate.get("composite_score") or 0) < cfg.min_score:
        reason, text = "score_too_low", "综合分低于门槛"
    else:
        reason, text = "no_buy_pattern", "当前已知数据未触发买点"
    return {"code": candidate["code"], "name": candidate.get("name", ""), "rank": candidate.get("rank"),
            "passed": signal is not None, "reason_code": reason, "reason_text": text,
            "signal": signal["signal"] if signal else {}}


def evaluate_sell(position: Position, row: dict, hold_days: int,
                  cfg: StrategyConfig, intraday_bars: Optional[list] = None) -> list[dict]:
    if hold_days <= 0 or position.entry_date >= row["date"]:
        return []
    close = row["bar_close"]
    stop = cfg.first_day_stop_loss_pct if hold_days == 1 else cfg.stop_loss_pct
    low_return = (row["bar_low"] / position.entry_price - 1) * 100
    if low_return <= stop:
        code = "first_day_stop_loss" if hold_days == 1 else "hard_stop_loss"
        return [_sell(code, f"已完成K线触及{stop:.1f}%止损，等待可执行报价", row)]
    if position.highest_return >= cfg.trailing_activate_pct:
        drawdown = cfg.trailing_drawdown_pct
        if cfg.trailing_atr_multiple and row.get("atr"):
            drawdown = max(drawdown, row["atr"] / close * 100 * cfg.trailing_atr_multiple)
        retrace = (position.highest_price - close) / position.highest_price * 100
        if retrace >= drawdown or (row["phase"] == "late" and close < row["ma5"]):
            return [_sell("trailing_take_profit", "已有浮盈后回撤或尾盘失守MA5，移动止盈", row)]
    if position.highest_return >= cfg.breakeven_activate_pct and row["bar_low"] <= break_even_price(position, cfg):
        return [_sell("profit_back_to_cost_take_profit", "已有浮盈后触及完整成本线，按随后可执行价保护退出", row)]
    bars = intraday_bars or []
    gap = row.get("open_gap_pct") or 0
    window = 1 if gap >= 7 else 6 if gap >= 5 else 0
    if window and len(bars) == window and not any(b.high >= row["limit_up"] * .999 for b in bars):
        minutes = window * 5
        code = "high_open_7pct_no_limit_5m_clear" if window == 1 else "high_open_5pct_no_limit_30m_clear"
        return [_sell(code, f"高开后{minutes}分钟未触板，清仓", row)]
    if row["phase"] != "late":
        return []
    weak = _weak_close_break(row, cfg)
    below_ma = close < row["ma5"]
    if weak or below_ma:
        code = "next_day_close_below_open" if hold_days == 1 and weak else "close_below_open_stop" if weak else "break_intraday_ma_stop"
        return [_sell(code, "14:55已知数据转弱，清仓", row)]
    if (hold_days == 1 and cfg.next_day_half_enabled and not position.took_profit_half
            and close >= row["limit_up"] * .999):
        return [_sell("next_day_limit_up_half", "首个可卖日14:55仍封板，减半仓", row)]
    volume = row.get("prev_volume")
    if (volume and row["volume"] / volume >= 1 + cfg.volume_spike_pct / 100
            and close < row["limit_up"] * .999):
        return [_sell("volume_spike_take_profit", "14:55累计量较上日全天放大且未封板，清仓", row)]
    return []


def _weak_close_break(row: dict, cfg: StrategyConfig) -> bool:
    if row["close"] >= row["open"]:
        return False
    drop = (row["open"] - row["close"]) / row["open"] * 100
    return (drop > cfg.weak_close_tolerance_pct or row["close"] < row["ma5"]
            or row["close"] < row["prev_close"])


def _payload(row: dict) -> dict:
    return {key: row.get(key) for key in ("date", "phase", "observed_at", "open", "close", "ma5",
            "prev_close", "open_gap_pct", "bar_high", "bar_low", "bar_close", "limit_up", "limit_down")}


def _buy(candidate: dict, row: dict, code: str, text: str, priority: int) -> dict:
    signal = _payload(row)
    signal.update({"candidate_trade_date": candidate.get("trade_date"), "rank": candidate.get("rank"),
                   "composite_score": candidate.get("composite_score")})
    return {"action": "BUY", "code": code, "reason_text": text, "priority": priority, "signal": signal}


def _sell(code: str, text: str, row: dict) -> dict:
    return {"action": "SELL", "code": code, "reason_text": text, "signal": _payload(row)}
