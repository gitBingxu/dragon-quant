"""实盘辅助 buy/sell 命令的 service 层：策略装配 + 调用 LiveTrader + 中文输出。"""

from typing import Optional

from dragon_quant.live_trade.trader import LiveTrader
from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.priority import RANKING_MODE_LABELS
from dragon_quant.review_account.strategy import BUY_CHECK_STATUS_LABELS


def _make_config(source: str, strategy_params: Optional[dict]) -> StrategyConfig:
    cfg = StrategyConfig(source=source)
    if strategy_params is not None:
        cfg = StrategyConfig.from_dict({**cfg.to_json_dict(), **strategy_params, "source": source})
    return cfg


def run_buy(trade_date: str, source: str = "v2", verbose: bool = True,
            as_of: Optional[str] = None, strategy_params: Optional[dict] = None,
            force: bool = False) -> dict:
    cfg = _make_config(source, strategy_params)
    result = LiveTrader(cfg, source=source).buy(trade_date, as_of, force=force)
    if verbose:
        _print_buy(trade_date, result)
    return result


def run_sell(trade_date: str, source: str = "v2", verbose: bool = True,
             as_of: Optional[str] = None, strategy_params: Optional[dict] = None,
             force: bool = False) -> dict:
    cfg = _make_config(source, strategy_params)
    result = LiveTrader(cfg, source=source).sell(trade_date, as_of, force=force)
    if verbose:
        _print_sell(trade_date, result)
    return result


# ─── 输出格式化 ───

# 阻断开新仓的闸门；其余（streak_delevel 等）只降仓，live 无 sizing 时仅展示。
_BLOCKING_GATES = {"daily_loss_circuit_breaker", "streak_circuit_breaker", "shadow_equity_floor"}

_CHECK_MARKS = "①②③④"


def _check_status_text(check: dict) -> str:
    status = check.get("status")
    if check.get("selected") and status == "passed":
        return "通过，命中买点"
    return BUY_CHECK_STATUS_LABELS.get(status, status or "未知")


def _print_checks(checks):
    for i, check in enumerate(checks or []):
        mark = _CHECK_MARKS[i] if i < len(_CHECK_MARKS) else f"{i + 1}."
        label = check.get("label") or check.get("rule") or "?"
        print(f"       {mark} {label}｜{_check_status_text(check)}｜{check.get('reason', '')}")


def _latest_details(details) -> dict:
    """同一 code 取最后一条带 checks 的 detail；全都无 checks 时退回最后一条。"""
    latest_checked = {}
    last_any = {}
    for d in details or []:
        code = d.get("code")
        if not code:
            continue
        last_any[code] = d
        if d.get("checks"):
            latest_checked[code] = d
    return {code: latest_checked.get(code, last) for code, last in last_any.items()}


def _checks_with_selected(detail, reason_code):
    """买入标的的命中买点由 buys.reason_code 回填（already_holding 帧不承载命中语义）。"""
    checks = [dict(c) for c in ((detail or {}).get("checks") or [])]
    if reason_code:
        for check in checks:
            check["selected"] = (check.get("rule") == reason_code
                                 and check.get("status") == "passed")
    return checks


def _print_buy(trade_date: str, result: dict):
    print(f"【买入建议 · {trade_date}】")
    _print_forced(result)
    sp = result.get("sentiment_pct")
    if sp is None:
        print("  建议仓位 —（无有效情绪仓位数据，请人工判断）")
    else:
        print(f"  建议仓位 {sp * 100:.0f}%（盘后复盘情绪温度，仅参考不代下单）")
    mode = result.get("ranking_mode")
    if mode:
        print(f"  排序模式 {mode}：{RANKING_MODE_LABELS.get(mode, mode)}")
    if result.get("fallback_reason"):
        print(f"  ⚠️ 候选池回退 {result.get('fallback_reason')}"
              f"（pool_source={result.get('pool_source')}）")
    for note in result.get("buy_eval_skipped") or []:
        print(f"  ⚠️ 本轮买点未评估：{note}")
    details = result.get("details", [])
    latest = _latest_details(details)
    bought = {b["code"]: b for b in result.get("buys", [])}
    for b in result.get("buys", []):
        mark = "" if b.get("persisted", True) else "（未落库：已有未平仓信号）"
        print(f"  🟢 买入 {b['name'] or b['code']}（{b['code']}）@{b['entry_price']:.2f}{mark}")
        print(f"     {b['reason_text']}")
        _print_checks(_checks_with_selected(latest.get(b["code"]), b.get("reason_code")))
    if not result.get("buys"):
        print(f"  {result.get('reason_text', '')}")
    vetoed = list({d.get("code"): d for d in details
                   if d.get("reason_code") == "auction_veto"}.values())
    if vetoed:
        items = "、".join(
            f"{d.get('name') or d.get('code')}"
            f"（{(d.get('vetoed_signal') or {}).get('code') or '?'}）"
            for d in vetoed[:5])
        print(f"  ⛔ 竞价否决 {len(vetoed)} 只（本可买入）：{items}")
    shadow = sum(1 for d in details
                 if d.get("would_veto") and d.get("reason_code") != "auction_veto")
    if shadow:
        print(f"  👁 影子否决（未开启）{shadow} 只，可对照 auction_veto_enabled 效果")
    others = [d for code, d in latest.items() if code not in bought]
    if others:
        print(f"  其他候选（{len(others)} 只）：")
        for d in others:
            label = d.get("name") or d.get("code") or "?"
            if d.get("reason_code") == "no_buy_pattern" or not d.get("reason_text"):
                print(f"     - {label}（{d.get('code', '')}）")
            else:
                print(f"     - {label}（{d.get('code', '')}）：{d.get('reason_text', '')}")
            _print_checks(d.get("checks"))
    _print_quality(result)
    gates = result.get("fired_gate") or []
    blocking = [g for g in gates if g in _BLOCKING_GATES]
    soft = [g for g in gates if g not in _BLOCKING_GATES]
    if blocking:
        print(f"  风控闸门已触发：{', '.join(blocking)}（当日暂停开新仓）")
    if soft:
        print(f"  仓位降级提示：{', '.join(soft)}（live 无 sizing，仅展示不生效）")


def _print_sell(trade_date: str, result: dict):
    print(f"【卖出建议 · {trade_date}】")
    _print_forced(result)
    for s in result.get("sells", []):
        action = "减半仓" if s.get("partial") else "卖出"
        mark = "" if s.get("persisted", True) else "（未落库：信号状态已变化）"
        print(f"  🔴 {action} {s['name'] or s['code']}（{s['code']}）@{s['exit_price']:.2f}"
              f"，持有 {s['hold_days']} 天{mark}")
        print(f"     理由：{s['reason_text']}")
    if not result.get("sells"):
        print(f"  {result.get('reason_text', '')}")
    for h in result.get("held", []):
        half = "（已减半仓）" if h.get("took_profit_half") else ""
        print(f"  🟢 继续持有 {h['name'] or h['code']}（{h['code']}）{half}"
              f"，当前最高 {h['highest_return']:+.1f}%")
    _print_quality(result)


def _print_forced(result: dict):
    if not result.get("forced"):
        return
    quality = result.get("data_quality")
    if quality and quality != "strict_5min":
        print("  ⚠️ 强制模式：当前非交易时段，已回放至最后一个已完成时点"
              "（日K兜底，不按收盘价补成交）")
    else:
        print("  ⚠️ 强制模式：当前非交易时段，已回放至最后一个完整 5 分钟 K")


def _print_quality(result: dict):
    quality = result.get("data_quality")
    if quality and quality != "strict_5min":
        label = "日K兜底" if quality == "daily_fallback" else "混合（部分日K兜底）"
        print(f"  ⚠️ 数据分级：{label}，成交价为保守近似")
    for note in result.get("degraded") or []:
        print(f"  ⚠️ {note}")
