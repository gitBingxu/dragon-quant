"""候选视图装配（A1）：只有有据本地事实才标 usable，不硬编码 True。

从 W1 ``EvidenceSnapshot`` 的**可用**事实派生价格/结构/压力/能力检查；日 K 只作
估值与结构，分钟事实才证明可执行；题材缺失保持未知（不伪造分散）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping, Optional

from dragon_quant.review_account.evidence import CapabilityCheck

from .universe import ExAnteUniverse, UniverseItem

MAX_QUOTE_AGE_MS = 120_000

CAPABILITY_DAILY = "daily_bars"
CAPABILITY_INTRADAY = "intraday_bars"
CAPABILITY_AUCTION = "auction_path"
CAPABILITY_THEME = "theme_membership"

_ENTRY_KINDS = ("breakout", "pullback", "confirm", "probe", "auction_confirm")


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number > 0 else None


def _facts(snapshot, code: str, capability: str) -> list:
    return sorted((fact for fact in snapshot.facts
                   if fact.code == code and fact.capability == capability
                   and fact.usable),
                  key=lambda fact: (str(fact.available_at or ""),
                                    str(fact.interval_end or "")))


def _payload(fact) -> Mapping:
    return dict(getattr(fact, "payload", {}) or {})


def _structure(daily: list) -> dict:
    closes, highs = [], []
    for fact in daily[-20:]:
        payload = _payload(fact)
        close = _finite(payload.get("close"))
        high = _finite(payload.get("high"))
        if close is not None:
            closes.append(close)
        if high is not None:
            highs.append(high)
    if not closes or not highs:
        return {}
    recent = closes[-5:]
    pullback = sum(recent) / len(recent)
    breakout = max(highs[-5:])
    return {
        "breakout_level": float(breakout),
        "pullback_level": float(pullback),
        "confirm_level": float(breakout * 1.01),
        "probe_level": float(closes[-1]),
        "observation_count": len(daily),
        "structure_basis": "local_daily_bars",
    }


def _pressure(daily: list) -> Optional[float]:
    closes = [_finite(_payload(fact).get("close"))
              for fact in daily[-5:]]
    closes = [value for value in closes if value is not None]
    if len(closes) < 2:
        return None
    high, low = max(closes), min(closes)
    return min(1.0, max(0.0, (high - low) / high))


def _themes(snapshot, code: str) -> tuple[str, ...]:
    themes: list[str] = []
    for fact in snapshot.facts:
        if fact.capability != CAPABILITY_THEME or not fact.usable:
            continue
        payload = _payload(fact)
        codes = payload.get("codes")
        if isinstance(codes, (list, tuple)) and code in codes:
            name = payload.get("theme_name") or payload.get("theme")
            if isinstance(name, str) and name and name not in themes:
                themes.append(name)
    return tuple(themes)


def _check(kind: str, code: str, *, daily: list, intraday: list,
           auction: list, confirmations=()) -> CapabilityCheck:
    rule_id = f"entry:{kind}:{code}"
    if kind == "confirm":
        confirmation = next((item for item in confirmations
                             if item.rule_id == f"confirm:{code}"), None)
        usable = bool(confirmation)
        return CapabilityCheck(
            rule_id=rule_id, capability=CAPABILITY_INTRADAY,
            status="usable" if usable else "missing",
            protects="new_risk",
            matched_evidence=(confirmation.evidence_ids if confirmation else ()),
            reasons=() if usable else ({"reason": "evidence_missing",
                                        "rule_id": rule_id},))
    if kind == "auction_confirm":
        usable = bool(auction)
        evidence = tuple(fact.evidence_id for fact in auction)
        capability = CAPABILITY_AUCTION
    else:
        usable = bool(daily and intraday)
        evidence = tuple(fact.evidence_id for fact in daily + intraday)
        capability = CAPABILITY_INTRADAY
    status = "usable" if usable else (
        "missing" if not (daily and intraday) else "unverified")
    return CapabilityCheck(
        rule_id=rule_id, capability=capability, status=status,
        protects="new_risk", matched_evidence=evidence,
        reasons=() if usable else ({"reason": "evidence_missing",
                                    "rule_id": rule_id},))


def _fresh(fact, cutoff: str, *, max_age_ms: int = MAX_QUOTE_AGE_MS) -> bool:
    try:
        observed = datetime.fromisoformat(str(fact.available_at))
        limit = datetime.fromisoformat(str(cutoff))
    except ValueError:
        return False
    return (limit - observed).total_seconds() * 1000 <= max_age_ms


def _candidate(item: UniverseItem, snapshot, rows: Mapping, *, entry_version: str = "local_evidence_v1"):
    from dragon_quant.review_account.strategy.bs_policy import CandidateView

    code = item.code
    daily = _facts(snapshot, code, CAPABILITY_DAILY)
    intraday = _facts(snapshot, code, CAPABILITY_INTRADAY)
    auction = _facts(snapshot, code, CAPABILITY_AUCTION)
    row = (rows or {}).get(code) or {}
    price = _finite(row.get("execution_price"))
    if price is None and intraday:
        price = _finite(_payload(intraday[-1]).get("close"))
    valuation_only = False
    if price is None and daily:
        price = _finite(_payload(daily[-1]).get("close"))
        valuation_only = True
    event_quote = bool(row and price is not None)
    fresh = event_quote or (bool(intraday)
                            and _fresh(intraday[-1], snapshot.cutoff))
    tradable = bool(price is not None and fresh and not valuation_only)
    reasons = list(item.eligibility_reasons)
    if not daily:
        reasons.append("daily_bars_missing")
    if not intraday:
        reasons.append("intraday_bars_missing")
    if price is None:
        reasons.append("price_missing")
    elif not fresh:
        reasons.append("quote_stale")
    structure = _structure(daily)
    from .facts import confirmations_for
    confirmations = confirmations_for(
        snapshot, code, confirm_level=structure.get("confirm_level"),
        price=price, cutoff=snapshot.cutoff, entry_version=entry_version)
    checks = tuple(_check(kind, code, daily=daily, intraday=intraday,
                          auction=auction, confirmations=confirmations)
                   for kind in _ENTRY_KINDS)
    # G2 的 entry:confirm 与 G3 的 confirm:<code> 保持独立语义；G3 check 引用
    # 同一条真实确认的 evidence_ids，缺确认时具名 missing。
    confirmation = next((item for item in confirmations
                         if item.rule_id == f"confirm:{code}"), None)
    checks = checks + (CapabilityCheck(
        rule_id=f"confirm:{code}", capability=CAPABILITY_INTRADAY,
        status="usable" if confirmation else "missing",
        protects="new_risk",
        matched_evidence=(confirmation.evidence_ids
                          if confirmation is not None else ()),
        reasons=() if confirmation else ({"reason": "confirmation_missing",
                                          "rule_id": f"confirm:{code}"},)),)
    return CandidateView(
        code=code, name=item.name, theme_ids=item.theme_ids or _themes(snapshot, code),
        rank=item.rank, eligible=bool(item.eligible and daily and price is not None),
        eligibility_reasons=tuple(dict.fromkeys(reasons)),
        tradable=tradable,
        tradability_reasons=() if tradable else ("execution_evidence_missing",),
        price=price, pressure_pct=_pressure(daily),
        structure=structure, evidence_checks=checks,
        confirmations=confirmations,
        entry_version=entry_version)


def build_candidates(snapshot, universe: ExAnteUniverse, *,
                     rows: Optional[Mapping] = None,
                     entry_version: str = "local_evidence_v1"):
    """按冻结快照与当前事件行装配 CandidateView 序列（无事实即受限）。"""
    return tuple(_candidate(item, snapshot, rows or {},
                            entry_version=entry_version)
                 for item in universe.items)


__all__ = ["build_candidates", "CAPABILITY_DAILY", "CAPABILITY_INTRADAY",
           "CAPABILITY_AUCTION", "CAPABILITY_THEME"]
