from dataclasses import dataclass
from typing import Callable, Optional

from dragon_quant.review_account.execution import break_even_price
from dragon_quant.review_account.models import (
    BUY_SIGNAL_DIVERGENCE_BREAK, BUY_SIGNAL_DRAGON_COMEBACK,
    BUY_SIGNAL_MA5_PULLBACK, BUY_SIGNAL_OPEN_TURN_STRONG,
    Position, StrategyConfig,
)
from dragon_quant.review_account.priority import (
    auction_state, auction_veto_reason, key_components, would_veto,
)


class BuyRejected(Exception):
    """买入否决 / 禁买的早退异常，携带原因码供 explain 精确回显（而非落入 no_buy_pattern）。

    ``signal`` 仅在「本可买入但被否决」时携带，供影子统计与决策记录使用。
    """

    def __init__(self, code: str, text: str, signal: Optional[dict] = None):
        self.code, self.text, self.signal = code, text, signal
        super().__init__(text)


def entry_veto(cfg: StrategyConfig, cand: dict, row: Optional[dict]) -> Optional[str]:
    """买入信号否决钩子。默认放行（返回 None）；未来集合竞价强度一票否决在此挂载。

    返回非 None 的否决理由，由 evaluate_buy 包装为 BuyRejected("vetoed", reason) 抛出。
    """
    return None


def _entry_quality_gate(prev_row: Optional[dict], cfg: StrategyConfig) -> Optional[str]:
    """入场质量硬门槛：剔除高波动、短期追高、弱势下行的标的。

    全部基于上一交易日日K指标（与开盘量能口径一致）。返回拒绝原因或 None。
    """
    if not prev_row:
        return None
    amp = prev_row.get("avg_amplitude_5")
    if cfg.max_avg_amplitude and amp is not None and amp > cfg.max_avg_amplitude:
        return f"上日5日平均振幅{amp:.1f}% > 门槛{cfg.max_avg_amplitude:.1f}%（波动过大）"
    r3 = prev_row.get("return_3d")
    if cfg.max_return_3d and r3 is not None and r3 > cfg.max_return_3d:
        return f"上日3日涨幅{r3:.1f}% > 门槛{cfg.max_return_3d:.0f}%（短期追高）"
    dd = prev_row.get("max_drawdown_5d")
    if cfg.drawdown_5d_floor and dd is not None and dd < cfg.drawdown_5d_floor:
        return f"上日5日最大回撤{dd:.1f}% 低于门槛{cfg.drawdown_5d_floor:.1f}%（弱势下行）"
    return None


def evaluate_buy(candidate: dict, row: dict, cfg: StrategyConfig,
                 prev_row: Optional[dict] = None, hist_rows: Optional[list] = None,
                 intraday_bars: Optional[list] = None,
                 sentiment_pct: Optional[float] = None) -> Optional[dict]:
    # 情绪空仓禁买（市场维度，全局，最先判定）
    if cfg.sentiment_ban_enabled and sentiment_pct is not None and sentiment_pct <= 0:
        raise BuyRejected("sentiment_ban", "情绪空仓，禁买")
    # 竞价/其它一票否决钩子（默认放行）
    veto = entry_veto(cfg, candidate, row)
    if veto:
        raise BuyRejected("vetoed", veto)
    if candidate.get("is_true_dragon") is False or (candidate.get("composite_score") or 0) < cfg.min_score:
        return None
    signal = _compute_buy_signal(candidate, row, cfg, prev_row, hist_rows, intraday_bars)
    return _apply_entry_priority_gate(cfg, candidate, signal)


def _compute_buy_signal(candidate: dict, row: dict, cfg: StrategyConfig,
                        prev_row: Optional[dict] = None,
                        hist_rows: Optional[list] = None,
                        intraday_bars: Optional[list] = None) -> Optional[dict]:
    """买点模式识别（纯计算，不含优先级/否决后置闸门）。"""
    phase = row["phase"]
    if phase == "bar":
        # 突破前高需首根5分钟K带量确认；否则交给分歧买龙。
        ib = intraday_bars or []
        if len(ib) == 1:
            strong = _turn_strong_signal(candidate, row, cfg, prev_row, ib[0])
            if strong:
                return strong
        return evaluate_divergence_buy(candidate, row, cfg, hist_rows, intraday_bars, prev_row)
    if phase != "open" or not prev_row:
        return None
    opening, ma5 = row["open"], prev_row.get("ma5")
    gap = row.get("open_gap_pct")
    amount, turnover = prev_row.get("amount") or 0, prev_row.get("turnover") or 0
    # 开盘 MA5 回踩承接（需 ma5/gap/标准量能门槛）
    if (ma5 and gap is not None and amount >= cfg.min_amount and turnover >= cfg.min_turnover
            and not _entry_quality_gate(prev_row, cfg)
            and not (opening >= row["limit_up"] * .999 or opening <= row["limit_down"] * 1.001)):
        distance = (opening / ma5 - 1) * 100
        if not (cfg.require_rising_ma5 and ma5 <= (row.get("previous_ma5") or ma5)):
            if cfg.ma5_min_distance_pct <= distance <= 3 and -3 < gap <= cfg.max_open_gap:
                return _buy(candidate, row, BUY_SIGNAL_MA5_PULLBACK, "开盘贴近上日MA5，回踩承接", 300)
    # 龙回头 / 二波买点：独立于 ma5/gap/量能前置（回调缩量恰是其特征）
    if cfg.dragon_comeback_enabled:
        comeback = _dragon_comeback_signal(candidate, row, cfg, hist_rows)
        if comeback:
            return comeback
    return None


def _apply_entry_priority_gate(cfg: StrategyConfig, candidate: dict,
                               signal: Optional[dict]) -> Optional[dict]:
    """信号后置闸门：写入 P/A 证据；按 ``auction_veto_enabled`` 执行 D3 否决。

    否决必须知道买点类型（豁免按类型配置），因此不能放在 ``entry_veto`` 前置位。
    未开启否决时只记录证据/影子标记，不改变决策。
    """
    if signal is None:
        return None
    entry_priority = candidate.get("entry_priority")
    if isinstance(entry_priority, dict):
        signal["signal"]["entry_priority"] = entry_priority
    signal["signal"]["priority_key"] = key_components(
        signal, candidate, bool(getattr(cfg, "auction_ranking_enabled", False)))
    if getattr(cfg, "auction_veto_enabled", False):
        reason = auction_veto_reason(cfg, candidate, signal)
        if reason:
            raise BuyRejected("auction_veto", reason, signal=signal)
    return signal


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
    if _entry_quality_gate(prev_row, cfg):
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
        result = _buy(candidate, row, BUY_SIGNAL_OPEN_TURN_STRONG,
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
    result = _buy(candidate, row, BUY_SIGNAL_DIVERGENCE_BREAK, "连续缩量一字板断板，完整窗口未破昨收且企稳", 400)
    result["signal"].update({"divergence_window_bars": len(bars), "board_count": boards})
    return result


def _find_wave_peak(hist_rows: list, cfg: StrategyConfig) -> Optional[int]:
    """龙回头第一波峰值：T-1 往前 15 日内，最近的「含≥min_boards 涨停收盘且区间累计涨幅≥min_rise_pct」上涨段的峰值日 index。"""
    n = len(hist_rows)
    lookback = 15
    start = max(0, n - 1 - lookback)
    for i in range(n - 1, start - 1, -1):
        if not hist_rows[i].get("is_limit_up_close"):
            continue
        window = hist_rows[max(0, i - 10): i + 1]
        boards = sum(1 for r in window if r.get("is_limit_up_close"))
        lows = [r["low"] for r in window if r.get("low")]
        if not lows:
            continue
        low = min(lows)
        if low <= 0:
            continue
        rise = (hist_rows[i]["close"] / low - 1) * 100
        if boards >= cfg.dragon_comeback_min_boards and rise >= cfg.dragon_comeback_min_rise_pct:
            return i
    return None


def _dragon_comeback_signal(candidate: dict, row: dict, cfg: StrategyConfig,
                            hist_rows: Optional[list]) -> Optional[dict]:
    """龙回头/二波买点（全日K、无前视）：第一波大涨→缩量回调→阳线吞没确认。粗分箱，default-off。

    确认口径说明（偏离方案 §4.7「放量阳线」）：回调缩量检查已覆盖整段回调（含 T-1），
    「放量」与之冲突，故确认改为「缩量回调 + 阳线吞没（pct≥confirm_pct 且收>开 且收>T-2）」。
    """
    opening = row["open"]
    if opening >= row["limit_up"] * .999 or opening <= row["limit_down"] * 1.001:
        return None
    hist = hist_rows or []
    n = len(hist)
    if n < cfg.dragon_comeback_pullback_days_max + 5:
        return None
    peak_idx = _find_wave_peak(hist, cfg)
    if peak_idx is None:
        return None
    pullback_days = n - 1 - peak_idx
    if not (cfg.dragon_comeback_pullback_days_min <= pullback_days <= cfg.dragon_comeback_pullback_days_max):
        return None
    peak = hist[peak_idx]
    seg = hist[peak_idx + 1:]
    if not seg:
        return None
    wave_lows = [r["low"] for r in hist[max(0, peak_idx - 10): peak_idx + 1] if r.get("low")]
    if not wave_lows or min(wave_lows) <= 0:
        return None
    wave_rise = (peak["close"] / min(wave_lows) - 1) * 100
    if wave_rise >= 100:  # 一波涨幅过大，动能一次性耗尽
        return None
    seg_low = min(r["low"] for r in seg)
    pullback = (peak["close"] - seg_low) / peak["close"] * 100
    # 回调深度落在第一波涨幅的 30%~50%（黄金分割）
    if not (wave_rise * 0.30 <= pullback <= wave_rise * 0.50):
        return None
    # 未破 MA20（生命线）
    if any((r.get("ma20") or 0) > 0 and r["low"] < r["ma20"] for r in seg):
        return None
    # 回调缩量：回调期最大量 < 峰值量 × volume_ratio
    if max(r["volume"] for r in seg) >= peak["volume"] * cfg.dragon_comeback_volume_ratio:
        return None
    # 失败特征：回调期（T-1 之前）出现大阳反抽（涨幅≥confirm_pct）
    if any((r.get("pct") or 0) >= cfg.dragon_comeback_confirm_pct for r in seg[:-1]):
        return None
    # 启动确认：T-1 涨停收盘，或「阳线吞没」——pct≥confirm_pct 且 收>开 且 收>T-2 收
    confirm = seg[-1]
    prev_close = seg[-2]["close"] if len(seg) >= 2 else peak["close"]
    confirmed = bool(confirm.get("is_limit_up_close")) or (
        (confirm.get("pct") or 0) >= cfg.dragon_comeback_confirm_pct
        and (confirm.get("open") is not None and confirm["close"] > confirm["open"])
        and confirm["close"] > prev_close)
    if not confirmed:
        return None
    return _buy(candidate, row, BUY_SIGNAL_DRAGON_COMEBACK,
                f"龙回头：第一波涨幅{wave_rise:.0f}%后缩量回调{pullback_days}日，阳线吞没确认", 250)


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

    gate = _entry_quality_gate(prev_row, cfg)
    if gate:
        return gate

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
    ib = intraday_bars or []
    if phase == "bar" and len(ib) == 1:
        bar_turnover = getattr(ib[0], "turnover", 0) or 0
        if prev_high and opening <= prev_high:
            parts.append(f"开盘{opening:.2f} 未突破上日高点{prev_high:.2f}")
        elif gap is not None and not (0 <= gap <= min(5.5, cfg.max_open_gap)):
            parts.append(f"开盘涨幅{gap:+.1f}% 不在[0, {min(5.5, cfg.max_open_gap):.1f}%]（弱转强买点）")
        elif not (cfg.strong_turnover_min <= turnover <= cfg.strong_turnover_max):
            parts.append(f"上日换手{turnover:.1f}% 不在弱转强区间"
                         f"[{cfg.strong_turnover_min:.0f}%, {cfg.strong_turnover_max:.0f}%]")
        elif amount < 500_000_000:
            parts.append(f"上日成交额{amount / 1e8:.2f}亿 < 弱转强要求5亿")
        elif bar_turnover < cfg.turn_strong_bar_turnover_min:
            parts.append(f"首根5分钟K换手{bar_turnover:.2f}% "
                         f"< {cfg.turn_strong_bar_turnover_min:.1f}%（突破无量，不认可）")
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


def _enrich_detail(detail: dict, candidate: dict, cfg: StrategyConfig) -> dict:
    """为决策记录补 P/A 三值、证据来源、排序分量与影子否决标记。"""
    entry_priority = candidate.get("entry_priority")
    detail["entry_priority"] = entry_priority if isinstance(entry_priority, dict) else {}
    # 否决分支的买点得分只存在于 vetoed_signal；用它保证 priority_key 与实际买点一致。
    signal_payload = (detail.get("full_signal")
                      or detail.get("vetoed_signal")
                      or detail.get("signal") or {})
    detail["priority_key"] = key_components(
        signal_payload, candidate,
        bool(getattr(cfg, "auction_ranking_enabled", False)))
    block = detail["entry_priority"].get("auction")
    if isinstance(block, dict) and block.get("loaded"):
        detail["would_veto"] = would_veto(candidate)
    return detail


def explain_buy_candidate(candidate: dict, row: Optional[dict], cfg: StrategyConfig,
                          prev_row: Optional[dict] = None, hist_rows: Optional[list] = None,
                          intraday_bars: Optional[list] = None,
                          sentiment_pct: Optional[float] = None) -> dict:
    signal = None
    if row is not None:
        try:
            signal = evaluate_buy(candidate, row, cfg, prev_row, hist_rows, intraday_bars, sentiment_pct)
        except BuyRejected as exc:
            detail = {"code": candidate["code"], "name": candidate.get("name", ""),
                      "rank": candidate.get("rank"),
                      "passed": False, "reason_code": exc.code, "reason_text": exc.text,
                      "signal": {}, "full_signal": None}
            if exc.signal is not None:
                detail["vetoed_signal"] = exc.signal
            return _enrich_detail(detail, candidate, cfg)
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
    detail = {"code": candidate["code"], "name": candidate.get("name", ""),
              "rank": candidate.get("rank"),
              "passed": signal is not None, "reason_code": reason, "reason_text": text,
              "signal": signal["signal"] if signal else {}, "full_signal": signal}
    return _enrich_detail(detail, candidate, cfg)


@dataclass
class SellContext:
    position: Position
    row: dict
    hold_days: int
    cfg: StrategyConfig
    intraday_bars: Optional[list]


@dataclass
class SellRule:
    name: str
    late_only: bool
    evaluate: Callable[["SellContext"], Optional[dict]]


def _rule_hard_stop(ctx: SellContext) -> Optional[dict]:
    row, pos, hold_days, cfg = ctx.row, ctx.position, ctx.hold_days, ctx.cfg
    stop = cfg.first_day_stop_loss_pct if hold_days == 1 else cfg.stop_loss_pct
    low_return = (row["bar_low"] / pos.entry_price - 1) * 100
    if low_return <= stop:
        code = "first_day_stop_loss" if hold_days == 1 else "hard_stop_loss"
        return _sell(code, f"已完成K线触及{stop:.1f}%止损，等待可执行报价", row)
    return None


def _tiered_drawdown(highest_return: float, cfg: StrategyConfig, base: float) -> float:
    if highest_return >= cfg.trailing_tier3_activate_pct:
        return cfg.trailing_tier3_drawdown_pct
    if highest_return >= cfg.trailing_tier2_activate_pct:
        return cfg.trailing_tier2_drawdown_pct
    return base


def _rule_trailing(ctx: SellContext) -> Optional[dict]:
    row, pos, cfg = ctx.row, ctx.position, ctx.cfg
    if pos.highest_return < cfg.trailing_activate_pct:
        return None
    close = row["bar_close"]
    drawdown = cfg.trailing_drawdown_pct
    if cfg.tiered_trailing_enabled:
        drawdown = _tiered_drawdown(pos.highest_return, cfg, drawdown)
    if cfg.trailing_atr_multiple and row.get("atr"):
        drawdown = max(drawdown, row["atr"] / close * 100 * cfg.trailing_atr_multiple)
    retrace = (pos.highest_price - close) / pos.highest_price * 100
    if retrace >= drawdown or (row["phase"] == "late" and close < row["ma5"]):
        return _sell("trailing_take_profit", "已有浮盈后回撤或尾盘失守MA5，移动止盈", row)
    return None


def _rule_breakeven(ctx: SellContext) -> Optional[dict]:
    row, pos, cfg = ctx.row, ctx.position, ctx.cfg
    if pos.highest_return >= cfg.breakeven_activate_pct and row["bar_low"] <= break_even_price(pos, cfg):
        return _sell("profit_back_to_cost_take_profit", "已有浮盈后触及完整成本线，按随后可执行价保护退出", row)
    return None


def _rule_high_open_clear(ctx: SellContext) -> Optional[dict]:
    row, cfg = ctx.row, ctx.cfg
    bars = ctx.intraday_bars or []
    gap = row.get("open_gap_pct") or 0
    window = 1 if gap >= 7 else 6 if gap >= 5 else 0
    if window and len(bars) == window and not any(b.high >= row["limit_up"] * .999 for b in bars):
        minutes = window * 5
        code = "high_open_7pct_no_limit_5m_clear" if window == 1 else "high_open_5pct_no_limit_30m_clear"
        return _sell(code, f"高开后{minutes}分钟未触板，清仓", row)
    return None


def _rule_soft_time_stop(ctx: SellContext) -> Optional[dict]:
    row, pos, hold_days, cfg = ctx.row, ctx.position, ctx.hold_days, ctx.cfg
    if not cfg.soft_time_stop_days or hold_days < cfg.soft_time_stop_days:
        return None
    if pos.highest_return >= cfg.trailing_activate_pct:  # trailing 已激活，交给移动止盈
        return None
    if row["bar_close"] > pos.entry_price * (1 + cfg.soft_time_stop_floor_pct / 100):  # 已脱离成本区
        return None
    return _sell("soft_time_stop", f"买入后{hold_days}日未创新高且未脱离成本区，逻辑证伪退出", row)


def _rule_weak_close(ctx: SellContext) -> Optional[dict]:
    row, hold_days, cfg = ctx.row, ctx.hold_days, ctx.cfg
    weak = _weak_close_break(row, cfg)
    below_ma = row["bar_close"] < row["ma5"]
    if weak or below_ma:
        code = ("next_day_close_below_open" if hold_days == 1 and weak
                else "close_below_open_stop" if weak else "break_intraday_ma_stop")
        return _sell(code, "14:55已知数据转弱，清仓", row)
    return None


def _rule_hard_time_stop(ctx: SellContext) -> Optional[dict]:
    row, hold_days, cfg = ctx.row, ctx.hold_days, ctx.cfg
    if hold_days >= cfg.max_hold_days and row["bar_close"] < row["limit_up"] * .999:
        return _sell("hard_time_stop", f"持有达{cfg.max_hold_days}日仍未退出，时间止损（封板豁免）", row)
    return None


def _rule_half(ctx: SellContext) -> Optional[dict]:
    row, pos, hold_days, cfg = ctx.row, ctx.position, ctx.hold_days, ctx.cfg
    if (hold_days == 1 and cfg.next_day_half_enabled and not pos.took_profit_half
            and row["bar_close"] >= row["limit_up"] * .999):
        return _sell("next_day_limit_up_half", "首个可卖日14:55仍封板，减半仓", row)
    return None


def _rule_volume_spike(ctx: SellContext) -> Optional[dict]:
    row, cfg = ctx.row, ctx.cfg
    volume = row.get("prev_volume")
    if (volume and row["volume"] / volume >= 1 + cfg.volume_spike_pct / 100
            and row["bar_close"] < row["limit_up"] * .999):
        return _sell("volume_spike_take_profit", "14:55累计量较上日全天放大且未封板，清仓", row)
    return None


# 卖出规则链：代码顺序即优先级，命中即短路。late_only 承载「phase != late 即跳过」的阶段门。
_SELL_RULES = [
    SellRule("hard_stop", False, _rule_hard_stop),
    SellRule("trailing", False, _rule_trailing),
    SellRule("breakeven", False, _rule_breakeven),
    SellRule("high_open_clear", False, _rule_high_open_clear),
    SellRule("soft_time_stop", True, _rule_soft_time_stop),
    SellRule("weak_close", True, _rule_weak_close),
    SellRule("hard_time_stop", True, _rule_hard_time_stop),
    SellRule("half", True, _rule_half),
    SellRule("volume_spike", True, _rule_volume_spike),
]
# 新增规则默认关；存量规则默认开。sell_rule_toggles 空 dict 时即此默认。
_DEFAULT_OFF_RULES = {"soft_time_stop", "hard_time_stop"}


def evaluate_sell(position: Position, row: dict, hold_days: int,
                  cfg: StrategyConfig, intraday_bars: Optional[list] = None) -> list[dict]:
    if hold_days <= 0 or position.entry_date >= row["date"]:
        return []
    ctx = SellContext(position, row, hold_days, cfg, intraday_bars)
    for rule in _SELL_RULES:
        if rule.late_only and row["phase"] != "late":
            continue
        if not cfg.sell_rule_toggles.get(rule.name, rule.name not in _DEFAULT_OFF_RULES):
            continue
        sig = rule.evaluate(ctx)
        if sig:
            return [sig]
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
