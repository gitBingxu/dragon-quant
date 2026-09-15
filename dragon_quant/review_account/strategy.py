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
        # 突破前高需首根5分钟K带量确认；否则交给分歧买龙。
        if len(intraday_bars or []) == 1:
            strong = _turn_strong_signal(candidate, row, cfg, prev_row, intraday_bars[0])
            if strong:
                return strong
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
    return None


def _turn_strong_signal(candidate: dict, row: dict, cfg: StrategyConfig,
                        prev_row: Optional[dict], first_bar) -> Optional[dict]:
    """高开突破前高，且首根5分钟K带量（换手≥turn_strong_bar_turnover_min）。

    开盘/缺口/前高/上日换手区间等沿用原开盘弱转强门槛，新增首根5分钟K换手确认：
    只有真实带量才认可突破有效，避免无量假突破。缺分时（日K兜底）时不触发。
    """
    if not prev_row:
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
    bar_turnover = getattr(first_bar, "turnover", 0) or 0
    if (opening > prev_row["high"] and 0 <= gap <= min(5.5, cfg.max_open_gap)
            and distance <= cfg.max_close_to_ma5
            and cfg.strong_turnover_min <= turnover <= cfg.strong_turnover_max
            and amount >= 500_000_000
            and bar_turnover >= cfg.turn_strong_bar_turnover_min):
        result = _buy(candidate, row, "buy_open_turn_strong",
                      f"开盘突破上日高点，首根5分钟K带量{bar_turnover:.2f}%，弱转强", 200)
        result["signal"]["first_bar_turnover"] = bar_turnover
        return result
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


def _no_pattern_reason(candidate: dict, row: dict, cfg: StrategyConfig,
                       prev_row: Optional[dict], hist_rows: Optional[list],
                       intraday_bars: Optional[list]) -> str:
    """逐条说明当前已知数据为何未触发任一买点（给用户看的可读原因）。"""
    phase = row.get("phase")
    ma5 = (prev_row or {}).get("ma5")
    gap = row.get("open_gap_pct")
    amount = (prev_row or {}).get("amount") or 0
    turnover = (prev_row or {}).get("turnover") or 0
    opening = row.get("open") or 0
    limit_up = row.get("limit_up") or 0
    limit_down = row.get("limit_down") or 0
    prev_high = (prev_row or {}).get("high")

    # 通用量能门槛（开盘两类买点共用）
    if not prev_row or ma5 is None:
        base = "缺少上一交易日MA5/量能基准"
    elif amount < cfg.min_amount:
        base = f"上日成交额{amount / 1e8:.2f}亿 < 门槛{cfg.min_amount / 1e8:.1f}亿"
    elif turnover < cfg.min_turnover:
        base = f"上日换手{turnover:.1f}% < 门槛{cfg.min_turnover:.1f}%"
    elif limit_up and opening >= limit_up * .999:
        base = f"开盘{opening:.2f}近涨停，无法开盘介入"
    elif limit_down and opening <= limit_down * 1.001:
        base = f"开盘{opening:.2f}近跌停，放弃"
    else:
        base = None

    if base:
        return base

    distance = (opening / ma5 - 1) * 100 if ma5 else None
    parts = []
    # 买点B：开盘贴近MA5
    if phase == "open":
        if distance is not None and distance > 3:
            parts.append(f"开盘距MA5 {distance:+.1f}% > 3%（未贴近，不满足回踩承接）")
        elif gap is not None and not (-3 < gap <= cfg.max_open_gap):
            parts.append(f"开盘涨幅{gap:+.1f}% 不在(-3%, {cfg.max_open_gap:.0f}%]（回踩承接买点）")
        else:
            parts.append("未满足开盘贴近MA5回踩承接")
    # 买点C：首根5分钟带量突破前高
    if phase == "bar" and len(intraday_bars or []) == 1:
        bar_turnover = getattr(intraday_bars[0], "turnover", 0) or 0
        if prev_high and opening <= prev_high:
            parts.append(f"开盘{opening:.2f} 未突破上日高点{prev_high:.2f}")
        elif gap is not None and not (0 <= gap <= min(5.5, cfg.max_open_gap)):
            parts.append(f"开盘涨幅{gap:+.1f}% 不在[0, {min(5.5, cfg.max_open_gap):.1f}%]（弱转强买点）")
        elif not (cfg.strong_turnover_min <= turnover <= cfg.strong_turnover_max):
            parts.append(f"上日换手{turnover:.1f}% 不在弱转强区间[{cfg.strong_turnover_min:.0f}%, {cfg.strong_turnover_max:.0f}%]")
        elif amount < 500_000_000:
            parts.append(f"上日成交额{amount / 1e8:.2f}亿 < 弱转强要求5亿")
        elif bar_turnover < cfg.turn_strong_bar_turnover_min:
            parts.append(f"首根5分钟K换手{bar_turnover:.2f}% < {cfg.turn_strong_bar_turnover_min:.1f}%（突破无量，不认可）")
        else:
            parts.append("未满足首根5分钟带量突破前高")
    # 买点A：分歧买龙
    if phase == "bar":
        boards, shrinking = _count_shrinking_one_word_boards(hist_rows or [], cfg)
        if not cfg.divergence_enabled:
            pass
        elif boards < cfg.divergence_min_boards:
            parts.append(f"连续一字板{boards} < {cfg.divergence_min_boards}板（非分歧形态）")
        elif cfg.divergence_require_shrinking_volume and not shrinking:
            parts.append("一字板期间未持续缩量（分歧买龙）")
        elif len(intraday_bars or []) != cfg.divergence_confirm_bars:
            parts.append(f"承接窗口需{cfg.divergence_confirm_bars}根5分钟K，当前{len(intraday_bars or [])}根")
        elif row["open"] >= row["limit_up"] * cfg.divergence_break_open_ratio:
            parts.append("开盘仍近涨停，未断板（分歧买龙）")
        else:
            parts.append("断板后承接不足（破昨收或收盘走弱）")

    return "；".join(parts) if parts else "当前已知数据未触发买点"


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
        reason, text = "score_too_low", f"综合分{candidate.get('composite_score') or 0:.1f} < 门槛{cfg.min_score:.0f}"
    else:
        reason, text = "no_buy_pattern", _no_pattern_reason(
            candidate, row, cfg, prev_row, hist_rows, intraday_bars)
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
            "prev_close", "open_gap_pct", "bar_high", "bar_low", "bar_close", "limit_up", "limit_down",
            "data_quality")}


def _buy(candidate: dict, row: dict, code: str, text: str, priority: int) -> dict:
    signal = _payload(row)
    signal.update({"candidate_trade_date": candidate.get("trade_date"), "rank": candidate.get("rank"),
                   "composite_score": candidate.get("composite_score")})
    return {"action": "BUY", "code": code, "reason_text": text, "priority": priority, "signal": signal}


def _sell(code: str, text: str, row: dict) -> dict:
    return {"action": "SELL", "code": code, "reason_text": text, "signal": _payload(row)}
