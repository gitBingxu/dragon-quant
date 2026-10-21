"""实盘辅助 buy/sell 命令的 service 层：策略装配 + 调用 LiveTrader + 中文输出。"""

from typing import Optional

from dragon_quant.live_trade.trader import LiveTrader
from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.priority import RANKING_MODE_LABELS


def _make_config(source: str, strategy_params: Optional[dict]) -> StrategyConfig:
    cfg = StrategyConfig(source=source)
    if strategy_params is not None:
        cfg = StrategyConfig.from_dict({**cfg.to_json_dict(), **strategy_params, "source": source})
    return cfg


def run_buy(trade_date: str, source: str = "v2", verbose: bool = True,
            as_of: Optional[str] = None, strategy_params: Optional[dict] = None) -> dict:
    cfg = _make_config(source, strategy_params)
    result = LiveTrader(cfg, source=source).buy(trade_date, as_of)
    if verbose:
        _print_buy(trade_date, result)
    return result


def run_sell(trade_date: str, source: str = "v2", verbose: bool = True,
             as_of: Optional[str] = None, strategy_params: Optional[dict] = None) -> dict:
    cfg = _make_config(source, strategy_params)
    result = LiveTrader(cfg, source=source).sell(trade_date, as_of)
    if verbose:
        _print_sell(trade_date, result)
    return result


# ─── 输出格式化 ───

# 阻断开新仓的闸门；其余（streak_delevel 等）只降仓，live 无 sizing 时仅展示。
_BLOCKING_GATES = {"daily_loss_circuit_breaker", "streak_circuit_breaker", "shadow_equity_floor"}

def _print_buy(trade_date: str, result: dict):
    print(f"【买入建议 · {trade_date}】")
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
    for b in result.get("buys", []):
        mark = "" if b.get("persisted", True) else "（未落库：已有未平仓信号）"
        print(f"  🟢 买入 {b['name'] or b['code']}（{b['code']}）@{b['entry_price']:.2f}{mark}")
        print(f"     {b['reason_text']}")
    if not result.get("buys"):
        print(f"  {result.get('reason_text', '')}")
    rejected = []
    seen = set()
    for d in result.get("details", []):
        if d.get("passed"):
            continue
        code = d.get("code")
        if code in seen:
            continue
        seen.add(code)
        rejected.append(d)
    vetoed = [d for d in result.get("details", [])
              if d.get("reason_code") == "auction_veto"]
    if vetoed:
        items = "、".join(
            f"{d.get('name') or d.get('code')}"
            f"（{(d.get('vetoed_signal') or {}).get('code') or '?'}）"
            for d in vetoed[:5])
        print(f"  ⛔ 竞价否决 {len(vetoed)} 只（本可买入）：{items}")
    shadow = sum(1 for d in result.get("details", [])
                 if d.get("would_veto") and d.get("reason_code") != "auction_veto")
    if shadow:
        print(f"  👁 影子否决（未开启）{shadow} 只，可对照 auction_veto_enabled 效果")
    if rejected:
        print(f"  候选未触发买点（{len(rejected)} 只）：")
        for d in rejected:
            label = d.get("name") or d.get("code") or "?"
            print(f"     - {label}（{d.get('code', '')}）：{d.get('reason_text', '')}")
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


def _print_quality(result: dict):
    quality = result.get("data_quality")
    if quality and quality != "strict_5min":
        label = "日K兜底" if quality == "daily_fallback" else "混合（部分日K兜底）"
        print(f"  ⚠️ 数据分级：{label}，成交价为保守近似")
    for note in result.get("degraded") or []:
        print(f"  ⚠️ {note}")
