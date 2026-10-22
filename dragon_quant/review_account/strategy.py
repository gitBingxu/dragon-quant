from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Optional

from dragon_quant.review_account.execution import break_even_price
from dragon_quant.review_account.market import SHANGHAI, bar_times
from dragon_quant.review_account.models import (
    BUY_SIGNAL_DIVERGENCE_BREAK, BUY_SIGNAL_DRAGON_COMEBACK,
    BUY_SIGNAL_MA5_PULLBACK, BUY_SIGNAL_OPEN_TURN_STRONG,
    DEFAULT_OFF_SELL_RULES, Position, StrategyConfig,
)
from dragon_quant.review_account.priority import (
    auction_state, auction_veto_reason, key_components, would_veto,
)


# ── 买点诊断（checks）：状态枚举、展示元数据与数据结构 ──
# 状态语义见 docs/solutions/2026-10-21-buy-checks-and-dragon-comeback.md §4.3。
BUY_CHECK_PASSED = "passed"                  # 通过
BUY_CHECK_FAILED = "failed"                  # 未通过（判定时点就在当前事件）
BUY_CHECK_PENDING = "pending"                # 待确认（确认时点未到）
BUY_CHECK_WINDOW_CLOSED = "window_closed"    # 窗口已过（最终判定未通过）
BUY_CHECK_UNAVAILABLE = "unavailable"        # 不可判定（日K兜底/数据缺失）
BUY_CHECK_SKIPPED = "skipped"                # 未评估（前置闸门/规则关闭）
BUY_CHECK_STATUSES = (BUY_CHECK_PASSED, BUY_CHECK_FAILED, BUY_CHECK_PENDING,
                      BUY_CHECK_WINDOW_CLOSED, BUY_CHECK_UNAVAILABLE, BUY_CHECK_SKIPPED)

BUY_CHECK_LABELS = {
    BUY_SIGNAL_MA5_PULLBACK: "开盘回踩MA5",
    BUY_SIGNAL_OPEN_TURN_STRONG: "弱转强",
    BUY_SIGNAL_DIVERGENCE_BREAK: "分歧买龙",
    BUY_SIGNAL_DRAGON_COMEBACK: "龙回头",
}
BUY_CHECK_STATUS_LABELS = {
    BUY_CHECK_PASSED: "通过",
    BUY_CHECK_FAILED: "未通过",
    BUY_CHECK_PENDING: "待确认",
    BUY_CHECK_WINDOW_CLOSED: "窗口已过（未通过）",
    BUY_CHECK_UNAVAILABLE: "不可判定",
    BUY_CHECK_SKIPPED: "未评估",
}
# 用户展示顺序 ①②③④
BUY_CHECK_DISPLAY_ORDER = (BUY_SIGNAL_MA5_PULLBACK, BUY_SIGNAL_OPEN_TURN_STRONG,
                           BUY_SIGNAL_DIVERGENCE_BREAK, BUY_SIGNAL_DRAGON_COMEBACK)
# 跨事件决策顺序：open ①→④，bar ②→③；仅 ①/④ 可在同一帧同时满足。
BUY_CHECK_SELECTION_ORDER = (BUY_SIGNAL_MA5_PULLBACK, BUY_SIGNAL_DRAGON_COMEBACK,
                             BUY_SIGNAL_OPEN_TURN_STRONG, BUY_SIGNAL_DIVERGENCE_BREAK)


@dataclass(frozen=True)
class RuleResult:
    """单条买点规则的纯判定结果。``unavailable`` 表示数据不足而非条件不满足。"""

    ok: bool
    reason: str
    signal: Optional[dict] = None
    unavailable: bool = False


@dataclass(frozen=True)
class BuyCheckContext:
    candidate: dict
    row: dict
    cfg: StrategyConfig
    prev_row: Optional[dict] = None
    hist_rows: Optional[list] = None
    intraday_bars: Optional[list] = None


@dataclass(frozen=True)
class BuyCheck:
    rule: str
    label: str
    window: str
    status: str
    reason: str
    selected: bool = False

    def to_dict(self) -> dict:
        return {"rule": self.rule, "label": self.label, "window": self.window,
                "status": self.status, "selected": self.selected, "reason": self.reason}


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


def _ctx(candidate: dict, row: dict, cfg: StrategyConfig,
         prev_row: Optional[dict] = None, hist_rows: Optional[list] = None,
         intraday_bars: Optional[list] = None) -> BuyCheckContext:
    return BuyCheckContext(candidate, row, cfg, prev_row, hist_rows, intraday_bars)


def _safe_rule(fn, *args) -> RuleResult:
    """诊断用兜底：单条规则异常只降级该条，不影响其它规则与决策。"""
    try:
        return fn(*args)
    except Exception as exc:  # pragma: no cover - 由异常注入用例覆盖
        return RuleResult(False, f"诊断异常：{type(exc).__name__}", unavailable=True)


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
        signal, candidate, bool(getattr(cfg, "auction_ranking_enabled", False)))
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


def _divergence_window(cfg: StrategyConfig, date: Optional[str] = None) -> str:
    n = cfg.divergence_confirm_bars
    if date:
        times = bar_times(date)
        idx = min(max(n - 1, 0), len(times) - 1)  # 防御性钳制，避免越界掩盖配置错误
        clock = datetime.fromtimestamp(times[idx] / 1000, SHANGHAI).strftime("%H:%M")
        return f"{clock} 前{n}根5分钟K"
    return f"前{n}根5分钟K"


def _window_label(ctx: BuyCheckContext, rule: str) -> str:
    if rule == BUY_SIGNAL_MA5_PULLBACK:
        return "09:30 开盘"
    if rule == BUY_SIGNAL_OPEN_TURN_STRONG:
        return "09:35 首根5分钟K"
    if rule == BUY_SIGNAL_DIVERGENCE_BREAK:
        return _divergence_window(ctx.cfg, ctx.row.get("date"))
    return "09:30 开盘（日K形态）"


def _make_check(ctx: BuyCheckContext, rule: str, status: str, reason: str) -> BuyCheck:
    return BuyCheck(rule=rule, label=BUY_CHECK_LABELS[rule], window=_window_label(ctx, rule),
                    status=status, reason=reason)


def buy_check_placeholders(cfg: StrategyConfig, status: str, reason: str,
                           date: Optional[str] = None) -> list[dict]:
    """无行情上下文时的四条占位（missing_kline / 早退闸门 / 事件级闸门 / 诊断异常）。"""
    windows = ("09:30 开盘", "09:35 首根5分钟K",
               _divergence_window(cfg, date), "09:30 开盘（日K形态）")
    return [BuyCheck(rule=rule, label=BUY_CHECK_LABELS[rule], window=window,
                     status=status, reason=reason).to_dict()
            for rule, window in zip(BUY_CHECK_DISPLAY_ORDER, windows)]


_static_check_dicts = buy_check_placeholders


def _turn_strong_check(ctx: BuyCheckContext, daily: bool,
                       bars: list) -> tuple[str, str]:
    if daily or ctx.intraday_bars is None:
        return BUY_CHECK_UNAVAILABLE, "日K兜底，无分时数据，不可判定"
    if not bars:
        pre, pre_unavailable = _turn_strong_preconditions(ctx)
        if pre_unavailable:
            return BUY_CHECK_UNAVAILABLE, pre or "缺少开盘判定基准"
        reason = "待09:35首根5分钟K确认" + (f"；已知：{pre}" if pre else "")
        return BUY_CHECK_PENDING, reason
    result = _safe_rule(_rule_turn_strong, ctx, bars[0])
    if result.ok:
        return BUY_CHECK_PASSED, result.reason
    if result.unavailable:
        return BUY_CHECK_UNAVAILABLE, result.reason
    return (BUY_CHECK_FAILED if len(bars) == 1 else BUY_CHECK_WINDOW_CLOSED), result.reason


def _divergence_check(ctx: BuyCheckContext, daily: bool,
                      bars: list) -> tuple[str, str]:
    cfg = ctx.cfg
    n = cfg.divergence_confirm_bars
    if not cfg.divergence_enabled:
        return BUY_CHECK_SKIPPED, "分歧买龙已关闭（divergence_enabled=False）"
    if daily or ctx.intraday_bars is None:
        return BUY_CHECK_UNAVAILABLE, "日K兜底，无分时数据，不可判定"
    if len(bars) < n:
        pre, pre_unavailable = _divergence_preconditions(ctx)
        if pre_unavailable:
            return BUY_CHECK_UNAVAILABLE, pre or "缺少判定的历史数据"
        reason = f"承接窗口需{n}根5分钟K，当前{len(bars)}根" + (f"；已知：{pre}" if pre else "")
        return BUY_CHECK_PENDING, reason
    result = _safe_rule(_rule_divergence, ctx, bars[:n])
    if result.ok:
        return BUY_CHECK_PASSED, result.reason
    if result.unavailable:
        return BUY_CHECK_UNAVAILABLE, result.reason
    return (BUY_CHECK_FAILED if len(bars) == n else BUY_CHECK_WINDOW_CLOSED), result.reason


def _dragon_check(ctx: BuyCheckContext, phase: Optional[str]) -> tuple[str, str]:
    if not ctx.prev_row:
        # 决策路径在缺 prev_row 时不评估 ④；诊断保持一致，避免「决策 None 但 checks 通过」。
        return BUY_CHECK_UNAVAILABLE, "缺少上一交易日基准（历史不足）"
    if not ctx.cfg.dragon_comeback_enabled:
        return BUY_CHECK_SKIPPED, "龙回头买点已关闭（dragon_comeback_enabled=False）"
    result = _safe_rule(_rule_dragon_comeback, ctx)
    if result.ok:
        return BUY_CHECK_PASSED, result.reason
    if result.unavailable:
        return BUY_CHECK_UNAVAILABLE, result.reason
    return (BUY_CHECK_FAILED if phase == "open" else BUY_CHECK_WINDOW_CLOSED), result.reason


def evaluate_buy_checks(ctx: BuyCheckContext,
                        selected_rule: Optional[str] = None) -> list[BuyCheck]:
    """四类买点的诊断投影（纯函数；不改变决策）。

    状态映射见方案 §5.1：09:30 的 ②③ 为 pending；窗口已过未通过为 window_closed；
    日K兜底 ②③ 为 unavailable；前置闸门由调用方整体标 skipped。
    """
    row = ctx.row
    phase = row.get("phase")
    daily = row.get("data_quality") == "daily_fallback"
    bars = ctx.intraday_bars or []

    ma5 = _safe_rule(_rule_ma5_pullback, ctx)
    if ma5.unavailable:
        ma5_status = BUY_CHECK_UNAVAILABLE
    elif ma5.ok:
        ma5_status = BUY_CHECK_PASSED
    else:
        ma5_status = BUY_CHECK_FAILED if phase == "open" else BUY_CHECK_WINDOW_CLOSED

    checks = [
        _make_check(ctx, BUY_SIGNAL_MA5_PULLBACK, ma5_status, ma5.reason),
        _make_check(ctx, BUY_SIGNAL_OPEN_TURN_STRONG, *_turn_strong_check(ctx, daily, bars)),
        _make_check(ctx, BUY_SIGNAL_DIVERGENCE_BREAK, *_divergence_check(ctx, daily, bars)),
        _make_check(ctx, BUY_SIGNAL_DRAGON_COMEBACK, *_dragon_check(ctx, phase)),
    ]
    if selected_rule:
        checks = [replace(c, selected=(c.rule == selected_rule and c.status == BUY_CHECK_PASSED))
                  for c in checks]
    return checks


def explain_buy_checks(candidate: dict, row: dict, cfg: StrategyConfig,
                       prev_row: Optional[dict] = None, hist_rows: Optional[list] = None,
                       intraday_bars: Optional[list] = None,
                       selected_rule: Optional[str] = None) -> list[dict]:
    """detail 用的 JSON 版；任何异常都降级为四条 unavailable，绝不抛出。"""
    try:
        ctx = _ctx(candidate, row, cfg, prev_row, hist_rows, intraday_bars)
        return [check.to_dict() for check in evaluate_buy_checks(ctx, selected_rule)]
    except Exception as exc:  # pragma: no cover - 由异常注入用例覆盖
        return _static_check_dicts(
            cfg, BUY_CHECK_UNAVAILABLE, f"诊断异常：{type(exc).__name__}",
            date=(row or {}).get("date"))


def _no_pattern_reason(candidate: dict, row: dict, cfg: StrategyConfig,
                       prev_row: Optional[dict], hist_rows: Optional[list],
                       intraday_bars: Optional[list]) -> str:
    """逐条说明为何未触发任一买点（给用户/Web UI 的可读摘要）。

    phase 无关：四类规则都按各自窗口切片合成，14:55（late）也有具体原因。
    """
    ctx = _ctx(candidate, row, cfg, prev_row, hist_rows, intraday_bars)
    gate, _ = _open_common_gate(ctx)
    if gate:
        return gate
    parts = [_safe_rule(_rule_ma5_pullback, ctx).reason]
    bars = intraday_bars or []
    daily = row.get("data_quality") == "daily_fallback"
    # ② 弱转强
    if daily:
        parts.append("日K兜底，无分时，弱转强不可判定")
    elif not bars:
        pre, _ = _turn_strong_preconditions(ctx)
        parts.append("待09:35首根5分钟K确认" + (f"；已知：{pre}" if pre else ""))
    else:
        parts.append(_safe_rule(_rule_turn_strong, ctx, bars[0]).reason)
    # ③ 分歧买龙
    if cfg.divergence_enabled:
        n = cfg.divergence_confirm_bars
        if daily:
            parts.append("日K兜底，无分时，分歧买龙不可判定")
        elif len(bars) < n:
            pre, _ = _divergence_preconditions(ctx)
            parts.append(f"承接窗口需{n}根5分钟K，当前{len(bars)}根"
                         + (f"；已知：{pre}" if pre else ""))
        else:
            parts.append(_safe_rule(_rule_divergence, ctx, bars[:n]).reason)
    # ④ 龙回头
    if cfg.dragon_comeback_enabled:
        parts.append(_safe_rule(_rule_dragon_comeback, ctx).reason)
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


def _detail_frame(row: Optional[dict]) -> tuple[Optional[int], Optional[str]]:
    if row is None:
        return None, None
    return row.get("observed_at"), row.get("phase")


def explain_buy_candidate(candidate: dict, row: Optional[dict], cfg: StrategyConfig,
                          prev_row: Optional[dict] = None, hist_rows: Optional[list] = None,
                          intraday_bars: Optional[list] = None,
                          sentiment_pct: Optional[float] = None) -> dict:
    signal = None
    observed_at, phase = _detail_frame(row)
    if row is not None:
        try:
            signal = evaluate_buy(candidate, row, cfg, prev_row, hist_rows, intraday_bars, sentiment_pct)
        except BuyRejected as exc:
            if exc.signal is not None:  # 竞价否决：形态判定完整保留
                checks = explain_buy_checks(
                    candidate, row, cfg, prev_row, hist_rows, intraday_bars,
                    selected_rule=(exc.signal or {}).get("code"))
            else:  # 情绪空仓 / 通用前置否决：未进入形态判断
                checks = _static_check_dicts(
                    cfg, BUY_CHECK_SKIPPED, f"前置闸门：{exc.text}，未进入形态判断",
                    date=row.get("date"))
            detail = {"code": candidate["code"], "name": candidate.get("name", ""),
                      "rank": candidate.get("rank"),
                      "passed": False, "reason_code": exc.code, "reason_text": exc.text,
                      "signal": {}, "full_signal": None,
                      "observed_at": observed_at, "phase": phase, "checks": checks}
            if exc.signal is not None:
                detail["vetoed_signal"] = exc.signal
            return _enrich_detail(detail, candidate, cfg)
    if signal:
        reason, text = signal["code"], signal["reason_text"]
        checks = explain_buy_checks(candidate, row, cfg, prev_row, hist_rows, intraday_bars,
                                    selected_rule=signal["code"])
    elif row is None:
        reason, text = "missing_kline", "缺少决策时点行情"
        checks = _static_check_dicts(cfg, BUY_CHECK_UNAVAILABLE, "缺少决策时点行情")
    elif candidate.get("is_true_dragon") is False:
        reason, text = "not_true_dragon", "历史记录明确否决，跳过"
        checks = _static_check_dicts(cfg, BUY_CHECK_SKIPPED,
                                     f"前置闸门：{text}，未进入形态判断",
                                     date=row.get("date"))
    elif (candidate.get("composite_score") or 0) < cfg.min_score:
        reason = "score_too_low"
        text = f"综合分{candidate.get('composite_score') or 0:.1f} < 门槛{cfg.min_score:.0f}"
        checks = _static_check_dicts(cfg, BUY_CHECK_SKIPPED,
                                     f"前置闸门：{text}，未进入形态判断",
                                     date=row.get("date"))
    else:
        reason, text = "no_buy_pattern", _no_pattern_reason(
            candidate, row, cfg, prev_row, hist_rows, intraday_bars)
        checks = explain_buy_checks(candidate, row, cfg, prev_row, hist_rows, intraday_bars)
    detail = {"code": candidate["code"], "name": candidate.get("name", ""),
              "rank": candidate.get("rank"),
              "passed": signal is not None, "reason_code": reason, "reason_text": text,
              "signal": signal["signal"] if signal else {}, "full_signal": signal,
              "observed_at": observed_at, "phase": phase, "checks": checks}
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


def _rule_first_day_stop(ctx: SellContext) -> Optional[dict]:
    """首日止损（新增规则，默认关）：盘中浮亏达减半线先卖一半，灾难线优先整清。

    - 仅 `hold_days == 1` 生效，首日之后交给 `hard_stop` 等既有规则。
    - 灾难段**优先且不带半仓守卫**：首次击穿即整仓退出（不得先卖一半），已减半后再次
      击穿则清掉剩余半仓。
    - `took_profit_half` 是「该信号已完成一条半仓腿」的唯一性守卫（与 `next_day_limit_up_half`
      共用，语义不分盈亏），因此同一信号只会有一条半仓腿。
    """
    row, pos, hold_days, cfg = ctx.row, ctx.position, ctx.hold_days, ctx.cfg
    if hold_days != 1:
        return None
    low_return = (row["bar_low"] / pos.entry_price - 1) * 100
    if cfg.first_day_disaster_stop_pct and low_return <= cfg.first_day_disaster_stop_pct:
        return _sell("first_day_disaster_stop",
                     f"首日触及灾难止损线{cfg.first_day_disaster_stop_pct:.1f}%，清仓", row)
    if pos.took_profit_half:  # 同一信号只允许一条半仓腿
        return None
    if low_return <= cfg.first_day_stop_loss_pct:
        return _sell("first_day_stop_half",
                     f"首日浮亏触及{cfg.first_day_stop_loss_pct:.1f}%止损线，"
                     "执行半仓腿（不足一手则全卖）", row)
    return None


def _rule_hard_stop(ctx: SellContext) -> Optional[dict]:
    row, pos, hold_days, cfg = ctx.row, ctx.position, ctx.hold_days, ctx.cfg
    if hold_days == 1 and cfg.first_day_stop_enabled():
        # 首日由 first_day_stop 接管；判定收在 StrategyConfig，规则之间不互相读 toggle。
        return None
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
    SellRule("first_day_stop", False, _rule_first_day_stop),
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
# 新增规则默认关；存量规则默认开。sell_rule_toggles 空 dict 时即此默认（与 models 同一真值来源）。
_DEFAULT_OFF_RULES = set(DEFAULT_OFF_SELL_RULES)


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
