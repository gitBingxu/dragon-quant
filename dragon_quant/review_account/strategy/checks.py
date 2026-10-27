"""策略纯拆分：四类买点诊断（Task 14b）。

``BUY_CHECK_*`` 状态常量/标签/顺序、``BuyCheck`` 与全部诊断函数从原
``strategy.py`` 原样搬入。诊断与决策共用 ``buy`` 的 ``_rule_*``，阈值只有
一份；本模块是纯投影，不改变决策。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional

from dragon_quant.review_account.market import SHANGHAI, bar_times
from dragon_quant.review_account.models import (
    BUY_SIGNAL_DIVERGENCE_BREAK,
    BUY_SIGNAL_DRAGON_COMEBACK,
    BUY_SIGNAL_MA5_PULLBACK,
    BUY_SIGNAL_OPEN_TURN_STRONG,
    StrategyConfig,
)
from dragon_quant.review_account.strategy.priority import key_components, would_veto
from dragon_quant.review_account.strategy.buy import (
    _divergence_preconditions,
    _open_common_gate,
    _rule_divergence,
    _rule_dragon_comeback,
    _rule_ma5_pullback,
    _rule_turn_strong,
    _turn_strong_preconditions,
    evaluate_buy,
)
from dragon_quant.review_account.strategy.common import (
    BuyCheckContext,  # noqa: F401  (approved re-export: docs place it in checks)
    BuyRejected,
    _ctx,
    _safe_rule,
)


# ── 买点诊断（checks）：状态枚举、展示元数据与数据结构 ──
# 状态语义见 archived_md/live_trade/2026-10-21-buy-checks-and-dragon-comeback.md §4.3。
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
        bool(getattr(cfg, "auction_ranking_enabled", False)),
        bool(getattr(cfg, "factor_ranking_enabled", False)))
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
    if signal and row is not None:
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
