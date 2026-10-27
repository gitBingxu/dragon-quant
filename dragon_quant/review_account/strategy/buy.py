"""策略纯拆分：买入信号、前置否决与四类买点规则（Task 14b）。

``entry_veto`` / ``evaluate_buy`` / ``_compute_buy_signal`` 与四条 ``_rule_*``
从原 ``strategy.py`` 原样搬入；阈值、默认参数与规则顺序零变化。

``entry_veto`` 的 canonical patch 目标是
``dragon_quant.review_account.strategy.buy.entry_veto``（迁移表唯一需要改的
测试 patch；包根不再转发私有 patch）。
"""

from __future__ import annotations

from typing import Optional

from dragon_quant.review_account.market import bar_times
from dragon_quant.review_account.models import (
    BUY_SIGNAL_DIVERGENCE_BREAK,
    BUY_SIGNAL_DRAGON_COMEBACK,
    BUY_SIGNAL_MA5_PULLBACK,
    BUY_SIGNAL_OPEN_TURN_STRONG,
    StrategyConfig,
)
from dragon_quant.review_account.strategy.priority import (
    auction_veto_reason,
    key_components,
)
from dragon_quant.review_account.strategy.common import (
    BuyCheckContext,
    BuyRejected,
    RuleResult,
    _buy,
    _ctx,
)


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
    """买点模式识别（纯计算，不含优先级/否决后置闸门）。

    决策路径直接调用规则函数（异常照常抛出，保持原契约）；诊断路径才用 ``_safe_rule``。
    """
    ctx = _ctx(candidate, row, cfg, prev_row, hist_rows, intraday_bars)
    phase = row["phase"]
    if phase == "bar":
        # 突破前高需首根5分钟K带量确认；否则交给分歧买龙。
        ib = intraday_bars or []
        if len(ib) == 1:
            strong = _rule_turn_strong(ctx, ib[0])
            if strong.signal:
                return strong.signal
        return _rule_divergence(ctx, ib).signal
    if phase != "open" or not prev_row:
        return None
    ma5 = _rule_ma5_pullback(ctx)
    if ma5.signal:
        return ma5.signal
    # 龙回头 / 二波买点：独立于 ma5/gap/量能前置（回调缩量恰是其特征）
    if cfg.dragon_comeback_enabled:
        comeback = _rule_dragon_comeback(ctx)
        if comeback.signal:
            return comeback.signal
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
        signal, candidate, bool(getattr(cfg, "auction_ranking_enabled", False)),
        bool(getattr(cfg, "factor_ranking_enabled", False)))
    if getattr(cfg, "auction_veto_enabled", False):
        reason = auction_veto_reason(cfg, candidate, signal)
        if reason:
            raise BuyRejected("auction_veto", reason, signal=signal)
    return signal


def _open_common_gate(ctx: BuyCheckContext) -> tuple[Optional[str], bool]:
    """① ② 共享的开盘前置门槛；返回 (reason, unavailable)。"""
    row, prev_row, cfg = ctx.row, ctx.prev_row, ctx.cfg
    ma5 = (prev_row or {}).get("ma5")
    if not prev_row or not ma5 or row.get("open_gap_pct") is None:
        return "缺少上一交易日MA5/量能基准", True
    opening = row.get("open") or 0
    limit_up = row.get("limit_up") or 0
    limit_down = row.get("limit_down") or 0
    amount, turnover = prev_row.get("amount") or 0, prev_row.get("turnover") or 0
    if amount < cfg.min_amount:
        return f"上日成交额{amount / 1e8:.2f}亿 < 门槛{cfg.min_amount / 1e8:.1f}亿", False
    if turnover < cfg.min_turnover:
        return f"上日换手{turnover:.1f}% < 门槛{cfg.min_turnover:.1f}%", False
    if limit_up and opening >= limit_up * .999:
        return f"开盘{opening:.2f}近涨停，无法开盘介入", False
    if limit_down and opening <= limit_down * 1.001:
        return f"开盘{opening:.2f}近跌停，放弃", False
    gate = _entry_quality_gate(prev_row, cfg)
    if gate:
        return gate, False
    return None, False


def _rule_ma5_pullback(ctx: BuyCheckContext) -> RuleResult:
    """① 开盘回踩 MA5 承接（判定时点：09:30 open）。"""
    reason, unavailable = _open_common_gate(ctx)
    if reason:
        return RuleResult(False, reason, unavailable=unavailable)
    row, prev_row, cfg = ctx.row, ctx.prev_row, ctx.cfg
    if prev_row is None:      # 不可达：_open_common_gate 已拦截缺基准
        return RuleResult(False, "缺少上一交易日MA5/量能基准", unavailable=True)
    opening, ma5, gap = row["open"], prev_row["ma5"], row["open_gap_pct"]
    distance = (opening / ma5 - 1) * 100
    if cfg.require_rising_ma5 and ma5 <= (row.get("previous_ma5") or ma5):
        return RuleResult(False, "MA5未上行（require_rising_ma5）")
    if distance > 3:
        return RuleResult(False, f"开盘距MA5 {distance:+.1f}% > 3%（未贴近，不满足回踩承接）")
    if distance < cfg.ma5_min_distance_pct:
        return RuleResult(False, f"开盘距MA5 {distance:+.1f}% "
                                 f"< 门槛{cfg.ma5_min_distance_pct:+.1f}%（偏离过深）")
    if not (-3 < gap <= cfg.max_open_gap):
        return RuleResult(False, f"开盘涨幅{gap:+.1f}% 不在(-3%, {cfg.max_open_gap:.0f}%]"
                                 f"（回踩承接买点）")
    text = "开盘贴近上日MA5，回踩承接"
    return RuleResult(True, text, _buy(ctx.candidate, row, BUY_SIGNAL_MA5_PULLBACK, text, 300))


def _turn_strong_preconditions(ctx: BuyCheckContext) -> tuple[Optional[str], bool]:
    """② 除首根 K 换手外的全部条件（前高/缺口/换手区间/成交额/距MA5）。"""
    reason, unavailable = _open_common_gate(ctx)
    if reason:
        return reason, unavailable
    row, prev_row, cfg = ctx.row, ctx.prev_row, ctx.cfg
    if prev_row is None:      # 不可达：_open_common_gate 已拦截缺基准
        return "缺少上一交易日MA5/量能基准", True
    ma5, gap, opening = prev_row["ma5"], row["open_gap_pct"], row["open"]
    amount, turnover = prev_row.get("amount") or 0, prev_row.get("turnover") or 0
    prev_high = prev_row.get("high")
    if cfg.require_rising_ma5 and ma5 <= (row.get("previous_ma5") or ma5):
        return "MA5未上行（require_rising_ma5）", False
    if prev_high is None:
        return "缺少上日高点基准", True
    if opening <= prev_high:
        return f"开盘{opening:.2f} 未突破上日高点{prev_high:.2f}", False
    if not (0 <= gap <= min(5.5, cfg.max_open_gap)):
        return (f"开盘涨幅{gap:+.1f}% 不在[0, {min(5.5, cfg.max_open_gap):.1f}%]"
                f"（弱转强买点）", False)
    if not (cfg.strong_turnover_min <= turnover <= cfg.strong_turnover_max):
        return (f"上日换手{turnover:.1f}% 不在弱转强区间"
                f"[{cfg.strong_turnover_min:.0f}%, {cfg.strong_turnover_max:.0f}%]", False)
    if amount < 500_000_000:
        return f"上日成交额{amount / 1e8:.2f}亿 < 弱转强要求5亿", False
    distance = (opening / ma5 - 1) * 100
    if distance > cfg.max_close_to_ma5:
        return (f"开盘距MA5 {distance:+.1f}% > {cfg.max_close_to_ma5:.0f}%"
                f"（弱转强要求贴近）", False)
    return None, False


def _rule_turn_strong(ctx: BuyCheckContext, first_bar) -> RuleResult:
    """② 高开突破前高 + 首根5分钟K带量（判定时点：09:35）。"""
    reason, unavailable = _turn_strong_preconditions(ctx)
    if reason:
        return RuleResult(False, reason, unavailable=unavailable)
    if first_bar is None:
        return RuleResult(False, "缺少首根5分钟K", unavailable=True)
    bar_turnover = getattr(first_bar, "turnover", 0) or 0
    cfg = ctx.cfg
    if bar_turnover < cfg.turn_strong_bar_turnover_min:
        return RuleResult(False, f"首根5分钟K换手{bar_turnover:.2f}% "
                                 f"< {cfg.turn_strong_bar_turnover_min:.1f}%（突破无量，不认可）")
    text = f"开盘突破上日高点，首根5分钟K带量{bar_turnover:.2f}%，弱转强"
    result = _buy(ctx.candidate, ctx.row, BUY_SIGNAL_OPEN_TURN_STRONG, text, 200)
    result["signal"]["first_bar_turnover"] = bar_turnover
    return RuleResult(True, text, result)


def _count_shrinking_one_word_boards(hist_rows: list, cfg: StrategyConfig) -> tuple[int, bool]:
    volumes = []
    for row in reversed(hist_rows):
        if not row.get("is_one_word_board") or (row.get("pct") or 0) < 9.9:
            break
        volumes.append(row.get("volume") or 0)
    return len(volumes), all(volumes[i] <= volumes[i + 1] for i in range(len(volumes) - 1))


def _divergence_preconditions(ctx: BuyCheckContext) -> tuple[Optional[str], bool]:
    """③ 除窗口外的全部条件（真龙/综合分/连续一字板/缩量）。"""
    candidate, cfg = ctx.candidate, ctx.cfg
    if candidate.get("is_true_dragon") is False:
        return "非真龙，跳过分歧买龙", False
    if (candidate.get("composite_score") or 0) < cfg.min_score:
        return (f"综合分{candidate.get('composite_score') or 0:.1f} "
                f"< 门槛{cfg.min_score:.0f}", False)
    if ctx.hist_rows is None:
        return "缺少历史日K，无法判定连板", True
    boards, shrinking = _count_shrinking_one_word_boards(ctx.hist_rows, cfg)
    if boards < cfg.divergence_min_boards:
        return f"连续一字板{boards} < {cfg.divergence_min_boards}板（非分歧形态）", False
    if cfg.divergence_require_shrinking_volume and not shrinking:
        return "一字板期间未持续缩量（分歧买龙）", False
    return None, False


def _rule_divergence(ctx: BuyCheckContext, bars: list) -> RuleResult:
    """③ 分歧买龙（判定时点：前 N 根5分钟K）。

    ``bars`` 必须是确认窗口的 N 根：决策层仅在 len(intraday_bars)==N 时调用；
    诊断层传 intraday_bars[:N]，保证 14:55 重算与 10:00 判定一致。
    """
    if not ctx.cfg.divergence_enabled:
        return RuleResult(False, "分歧买龙已关闭（divergence_enabled=False）")
    reason, unavailable = _divergence_preconditions(ctx)
    if reason:
        return RuleResult(False, reason, unavailable=unavailable)
    bars = bars or []
    n = ctx.cfg.divergence_confirm_bars
    if len(bars) != n:
        return RuleResult(False, f"承接窗口需{n}根5分钟K，当前{len(bars)}根")
    if [b.timestamp for b in bars] != bar_times(ctx.row["date"])[:n]:
        return RuleResult(False, "承接窗口时点不连续（缺根/跨段）")
    row, cfg = ctx.row, ctx.cfg
    if row["open"] >= row["limit_up"] * cfg.divergence_break_open_ratio:
        return RuleResult(False, "开盘仍近涨停，未断板（分歧买龙）")
    if min(b.low for b in bars) < row["prev_close"]:
        return RuleResult(False, "断板后跌破昨收（承接不足）")
    if bars[-1].close < bars[0].open:
        return RuleResult(False, "承接窗口收盘走弱（承接不足）")
    text = "连续缩量一字板断板，完整窗口未破昨收且企稳"
    result = _buy(ctx.candidate, row, BUY_SIGNAL_DIVERGENCE_BREAK, text, 400)
    boards, _ = _count_shrinking_one_word_boards(ctx.hist_rows or [], cfg)
    result["signal"].update({"divergence_window_bars": len(bars), "board_count": boards})
    return RuleResult(True, text, result)


def evaluate_divergence_buy(candidate: dict, row: dict, cfg: StrategyConfig,
                            hist_rows: Optional[list] = None, intraday_bars: Optional[list] = None,
                            prev_row: Optional[dict] = None) -> Optional[dict]:
    """兼容包装：保持「必须恰好 N 根」的旧契约（决策路径不变）。"""
    bars = intraday_bars or []
    if len(bars) != cfg.divergence_confirm_bars:
        return None
    return _rule_divergence(_ctx(candidate, row, cfg, prev_row, hist_rows, bars), bars).signal


def _find_wave_peak(hist_rows: list, cfg: StrategyConfig) -> Optional[int]:
    """龙回头第一波峰值：T-1 往前 15 日内，最近的「含≥min_boards 涨停收盘且""" \
    """区间累计涨幅≥min_rise_pct」上涨段的峰值日 index。"""
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


def _rule_dragon_comeback(ctx: BuyCheckContext) -> RuleResult:
    """④ 龙回头/二波买点（全日K、无前视）：第一波大涨→缩量回调→阳线吞没确认。

    确认口径：回调缩量检查覆盖整段回调（含 T-1），确认改为「缩量回调 + 阳线吞没」
    （pct≥confirm_pct 且收>开 且收>T-2），与既有实现逐条件一致。
    """
    row, cfg = ctx.row, ctx.cfg
    hist = ctx.hist_rows or []
    opening = row["open"]
    if opening >= row["limit_up"] * .999:
        return RuleResult(False, f"开盘{opening:.2f}近涨停，无法开盘介入")
    if opening <= row["limit_down"] * 1.001:
        return RuleResult(False, f"开盘{opening:.2f}近跌停，放弃")
    n = len(hist)
    need = cfg.dragon_comeback_pullback_days_max + 5
    if n < need:
        return RuleResult(False, f"历史K线不足（需≥{need}日，当前{n}日）", unavailable=True)
    peak_idx = _find_wave_peak(hist, cfg)
    if peak_idx is None:
        return RuleResult(False, f"未找到符合条件的第一波（近15日内需"
                                 f"≥{cfg.dragon_comeback_min_boards}涨停且区间涨幅"
                                 f"≥{cfg.dragon_comeback_min_rise_pct:.0f}%）")
    pullback_days = n - 1 - peak_idx
    if pullback_days < cfg.dragon_comeback_pullback_days_min:
        return RuleResult(False, f"回调天数{pullback_days} < "
                                 f"{cfg.dragon_comeback_pullback_days_min}")
    if pullback_days > cfg.dragon_comeback_pullback_days_max:
        return RuleResult(False, f"回调天数{pullback_days} > "
                                 f"{cfg.dragon_comeback_pullback_days_max}")
    peak = hist[peak_idx]
    seg = hist[peak_idx + 1:]
    if not seg:
        return RuleResult(False, "回调段为空，无法判定")
    wave_lows = [r["low"] for r in hist[max(0, peak_idx - 10): peak_idx + 1] if r.get("low")]
    if not wave_lows or min(wave_lows) <= 0:
        return RuleResult(False, "第一波低点数据缺失", unavailable=True)
    wave_rise = (peak["close"] / min(wave_lows) - 1) * 100
    if wave_rise >= 100:  # 一波涨幅过大，动能一次性耗尽
        return RuleResult(False, f"第一波涨幅{wave_rise:.0f}% ≥ 100%（动能一次性耗尽）")
    seg_low = min(r["low"] for r in seg)
    pullback = (peak["close"] - seg_low) / peak["close"] * 100
    # 回调深度落在第一波涨幅的 30%~50%（黄金分割）
    if not (wave_rise * 0.30 <= pullback <= wave_rise * 0.50):
        return RuleResult(False, f"回调深度{pullback:.1f}% 不在第一波涨幅"
                                 f"{wave_rise:.0f}%的30%~50%")
    # 未破 MA20（生命线）
    if any((r.get("ma20") or 0) > 0 and r["low"] < r["ma20"] for r in seg):
        return RuleResult(False, "回调期跌破MA20（生命线）")
    # 回调缩量：回调期最大量 < 峰值量 × volume_ratio
    if max(r["volume"] for r in seg) >= peak["volume"] * cfg.dragon_comeback_volume_ratio:
        return RuleResult(False, f"回调期最大量未缩至峰值量的"
                                 f"{cfg.dragon_comeback_volume_ratio * 100:.1f}%以下")
    # 失败特征：回调期（T-1 之前）出现大阳反抽（涨幅≥confirm_pct）
    if any((r.get("pct") or 0) >= cfg.dragon_comeback_confirm_pct for r in seg[:-1]):
        return RuleResult(False, f"回调期出现大阳反抽（涨幅≥"
                                 f"{cfg.dragon_comeback_confirm_pct:.0f}%）")
    # 启动确认：T-1 涨停收盘，或「阳线吞没」——pct≥confirm_pct 且 收>开 且 收>T-2 收
    confirm = seg[-1]
    prev_close = seg[-2]["close"] if len(seg) >= 2 else peak["close"]
    confirmed = bool(confirm.get("is_limit_up_close")) or (
        (confirm.get("pct") or 0) >= cfg.dragon_comeback_confirm_pct
        and (confirm.get("open") is not None and confirm["close"] > confirm["open"])
        and confirm["close"] > prev_close)
    if not confirmed:
        return RuleResult(False, f"T-1未涨停且未阳线吞没（需涨幅≥"
                                 f"{cfg.dragon_comeback_confirm_pct:.0f}%且收盘高于前日/T-2）")
    text = f"龙回头：第一波涨幅{wave_rise:.0f}%后缩量回调{pullback_days}日，阳线吞没确认"
    return RuleResult(True, text, _buy(ctx.candidate, row, BUY_SIGNAL_DRAGON_COMEBACK, text, 250))
