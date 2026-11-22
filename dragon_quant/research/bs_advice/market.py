"""市场风险授权（U1）：只消费有 PIT/来源/明确含义的市场风险事实。

授权来源：严格早于决策日的 **T-1 prep 快照 `market` 状态**（现有复盘公开资产，
含 state/score/coeff/partial/evidence 与 created_at）。只有：

- state 属于已知四态（strong/neutral/weak/ice）；
- 非 partial/unknown、`missing_inputs` 为空；
- prep 快照 `created_at` 可解析、带时区且不晚于决策 cutoff；

才允许新增风险（具体仓位仍由 prep 既有 sizing 与候选 eligibility 决定）。
只有个股日K/分钟事实（boundaries）→ ``None``（新增受限，已有保护继续）；
数据覆盖 breadth 仅作展示，不单独变授权。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from dragon_quant.review_account.strategy.bs_policy import MarketView

KNOWN_STATES = ("strong", "neutral", "weak", "ice")


def _moment(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    # 只接受带时区的真实接收时刻；旧无时区 created_at 严格 PIT 视为不可用。
    return moment if moment.tzinfo is not None else None


def _breadth(snapshot) -> float:
    if snapshot is None:
        return 0.0
    facts = [fact for fact in snapshot.facts if fact.usable]
    codes = {fact.code for fact in facts if fact.code}
    intraday = {fact.code for fact in facts
                if fact.capability == "intraday_bars"}
    return (len(intraday) / len(codes)) if codes else 0.0


def market_from_universe(universe, *, cutoff: str, snapshot=None) -> MarketView:
    context = dict(getattr(universe, "market_context", {}) or {})
    meta = dict(getattr(universe, "market_meta", {}) or {})
    breadth = _breadth(snapshot)
    state = context.get("state")
    created = _moment(meta.get("created_at"))
    decision = _moment(cutoff)
    reasons = []
    explicit = context.get("new_risk_allowed")
    if state not in KNOWN_STATES:
        reasons.append("market_state_unknown")
    if context.get("partial") is True:
        reasons.append("market_partial")
    if context.get("missing_inputs"):
        reasons.append("market_inputs_missing")
    if created is None:
        reasons.append("market_received_at_missing")
    elif decision is not None and created > decision:
        reasons.append("market_context_after_cutoff")
    if reasons:
        # 未来/坏时间/未知状态一律 unknown，不允许 explicit/coeff 提前泄漏授权或否决。
        return MarketView(regime="unknown", new_risk_allowed=None,
                          breadth=breadth, reasons=tuple(reasons))
    if isinstance(explicit, bool) and explicit is False:
        return MarketView(regime=str(state or "unknown"),
                          new_risk_allowed=False, breadth=breadth,
                          reasons=("explicit_market_deny",))
    coeff = context.get("coeff")
    if isinstance(coeff, bool) or not isinstance(coeff, (int, float)):
        reasons.append("market_coeff_missing")
        coeff_value = None
    else:
        coeff_value = float(coeff)
        if coeff_value != coeff_value or coeff_value in (float("inf"),
                                                          float("-inf")):
            reasons.append("market_coeff_invalid")
            coeff_value = None
        elif coeff_value <= 0:
            # 现有 sizing 系数 0 = 不建新仓；不得被 state 重新授权。
            return MarketView(
                regime=str(state), new_risk_allowed=False, breadth=breadth,
                reasons=("market_coeff_zero", f"coeff={coeff_value}"))
    if reasons:
        return MarketView(regime="unknown", new_risk_allowed=None,
                          breadth=breadth, reasons=tuple(reasons))
    return MarketView(
        regime=str(state), new_risk_allowed=True, breadth=breadth,
        reasons=("prep_market_state", f"state={state}",
                 f"score={context.get('score')}",
                 f"coeff={coeff_value}",
                 f"prep_created_at={meta.get('created_at')}"))


__all__ = ["KNOWN_STATES", "market_from_universe"]
