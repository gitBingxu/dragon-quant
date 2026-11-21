"""能力检查、覆盖摘要与来源状态（只读，不执行交易）。"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping, Optional

from .dates import _moment, _trade_date_key, _valid_trade_date
from .facts import EvidenceFact
from .frozen import thaw
from .need import CapabilityCheck, EvidenceNeed
from .schema import (
    INPUT_ACCOUNT_STATE,
    INPUT_RISK_BUDGET,
    INPUT_SELLABLE_QTY,
    PROTECT_EXISTING,
    EvidenceError,
    _PROTECTED_CAPABILITIES,
)
from .semantics import derive_usable
from .snapshots import EvidenceSnapshot

def coverage_summary(snapshot: EvidenceSnapshot) -> dict:
    """只读覆盖摘要：显式披露请求范围、分页限制、截断与可见缺口。"""
    coverage = dict(snapshot.coverage or {})
    by_capability = {name: dict(thaw(value))
                     for name, value in (coverage.get("by_capability") or {}).items()}
    return {
        "snapshot_id": snapshot.snapshot_id,
        "cutoff": snapshot.cutoff,
        "request": snapshot.request.to_dict(),
        "facts": coverage.get("facts", 0),
        "usable_facts": coverage.get("usable_facts", 0),
        "truncated": bool(coverage.get("truncated")),
        "unbounded_scope": bool(coverage.get("unbounded_scope")),
        "limits": dict(coverage.get("limits") or {}),
        "temporal_rejected": coverage.get("temporal_rejected", 0),
        "by_capability": by_capability,
    }


# ─── 能力检查 ────────────────────────────────────────────────────────────────



def _scope_codes(snapshot: EvidenceSnapshot, capability: str,
                 scope: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """解析范围：返回 (检查母集, 请求外代码)。母集来自冻结请求。"""
    requested = list(snapshot.request.codes)
    if "*" in scope:
        if requested:
            return requested, []
        return sorted({fact.code for fact in snapshot.facts
                       if fact.capability == capability}), []
    codes = list(scope)
    if requested:
        outside = [code for code in codes if code not in set(requested)]
        return [code for code in codes if code in set(requested)], outside
    return codes, []


def _input_reasons(missing: Iterable[str]) -> list[dict]:
    mapping = {
        INPUT_ACCOUNT_STATE: ("account_state_missing", "账户/持仓状态缺失"),
        INPUT_RISK_BUDGET: ("risk_budget_missing", "风险预算缺失"),
        INPUT_SELLABLE_QTY: ("account_state_missing", "可卖数量缺失"),
    }
    reasons = []
    for name in missing:
        code, detail = mapping.get(
            name, ("unverified", f"输入缺失: {name}"))
        reasons.append({"reason": code, "input": name, "detail": detail})
    return reasons


def _fact_temporal_state(fact: EvidenceFact, cutoff: datetime
                         ) -> tuple[str, Optional[datetime]]:
    """事实的时点状态：ok / future / invalid。"""
    available = _moment(fact.available_at)
    if available is None:
        return "invalid", None
    if available > cutoff:
        return "future", None
    if fact.interval_start and _moment(fact.interval_start) is None:
        return "invalid", None
    if fact.interval_end:
        end = _moment(fact.interval_end)
        if end is None:
            return "invalid", None
        if end > cutoff:
            return "future", None
    if fact.trade_date and not _valid_trade_date(fact.trade_date):
        return "invalid", None
    return "ok", available


def _expected_state(need: EvidenceNeed, cutoff: datetime):
    checked, future, invalid = [], [], 0
    for end in need.expected_intervals:
        moment = _moment(end)
        if moment is None:
            invalid += 1
        elif moment > cutoff:
            future.append(end)
        else:
            checked.append(end)
    return checked, future, invalid


def _code_facts(snapshot, need, code, scope_dates):
    facts = [fact for fact in snapshot.facts
             if fact.capability == need.capability and fact.code == code]
    if need.period:
        facts = [fact for fact in facts if fact.period == need.period]
    if scope_dates is not None:
        facts = [fact for fact in facts
                 if _trade_date_key(fact.trade_date) in scope_dates]
    return facts


def _code_result(facts, cutoff: datetime, need: EvidenceNeed,
                 checked_expected: list[str]) -> dict:
    usable, invalid, future, unverified, stale = [], 0, 0, 0, 0
    for fact in facts:
        state, available = _fact_temporal_state(fact, cutoff)
        if state == "future":
            future += 1
            continue
        if state == "invalid":
            invalid += 1
            continue
        if not derive_usable(fact):
            unverified += 1
            continue
        if need.max_age_seconds is not None and available is not None \
                and (cutoff - available).total_seconds() > need.max_age_seconds:
            stale += 1
            continue
        usable.append(fact)
    matched: list[str] = []
    missing_intervals: list[str] = []
    if checked_expected and usable:
        expected = set(checked_expected)
        present = {fact.interval_end for fact in usable
                   if fact.interval_end in expected}
        missing_intervals = [end for end in checked_expected
                             if end not in present]
        matched = [fact.evidence_id for fact in usable
                   if fact.interval_end in present]
    else:
        matched = [fact.evidence_id for fact in usable]
    return {"usable": usable, "matched": matched,
            "missing_intervals": missing_intervals, "invalid": invalid,
            "future": future, "unverified": unverified, "stale": stale}


def _status_from(need, matched_count, missing_scopes, missing_intervals,
                 counts, truncated, unbounded) -> str:
    hard_gap = bool(missing_scopes or missing_intervals)
    wildcard = "*" in need.scope
    if hard_gap:
        return "missing" if wildcard or not matched_count else "partial"
    if counts["invalid"] and not matched_count:
        return "invalid"
    if counts["future"] and not matched_count and not counts["unverified"]:
        return "future"
    if counts["unverified"] and not matched_count:
        return "unverified"
    if counts["stale"] and not matched_count:
        return "stale"
    if not matched_count:
        return "missing"
    if any(counts[key] for key in ("invalid", "future", "unverified", "stale")):
        return "partial"
    if truncated or unbounded:
        return "partial"
    return "usable"


def check_need(snapshot: EvidenceSnapshot, need: EvidenceNeed, *,
               inputs: Optional[Mapping[str, bool]] = None,
               now: Optional[str] = None) -> CapabilityCheck:
    """判定一条证据需求是否可用；不读写数据库、不执行交易。"""
    if need.capability not in _PROTECTED_CAPABILITIES:
        raise EvidenceError(f"未知能力: {need.capability}")
    inputs = dict(inputs or {})
    now_moment = _moment(now or snapshot.cutoff)
    cutoff_moment = _moment(snapshot.cutoff)
    if now_moment is None or cutoff_moment is None:
        raise EvidenceError("快照/当前时刻非法")
    effective_cutoff = min(now_moment, cutoff_moment)
    missing_inputs = tuple(name for name in need.required_inputs
                           if not inputs.get(name))
    if missing_inputs:
        return CapabilityCheck(
            rule_id=need.rule_id, capability=need.capability, status="missing",
            protects=need.protects, reasons=tuple(_input_reasons(missing_inputs)),
            affected_actions=need.affected_actions, inputs_missing=missing_inputs)

    checked, future_intervals, expected_invalid = _expected_state(
        need, effective_cutoff)
    if need.expected_intervals and not checked and future_intervals:
        return CapabilityCheck(
            rule_id=need.rule_id, capability=need.capability, status="future",
            protects=need.protects,
            reasons=({"reason": "window_not_open",
                      "future_intervals": list(future_intervals)},),
            affected_actions=need.affected_actions, inputs_missing=())

    codes, outside_request = _scope_codes(snapshot, need.capability, need.scope)
    reasons: list[dict] = []
    missing_scopes: list[str] = list(outside_request)
    if outside_request:
        reasons.append({"reason": "scope_outside_request",
                        "codes": list(outside_request)})
    need_dates = {_trade_date_key(item) for item in need.trade_dates} \
        if need.trade_dates else set()
    request_dates = {_trade_date_key(item)
                     for item in snapshot.request.trade_dates}
    if need_dates and request_dates:
        outside_dates = need_dates - request_dates
        if outside_dates:
            missing_scopes.append("*dates")
            reasons.append({"reason": "scope_outside_request",
                            "trade_dates": sorted(outside_dates)})
    scope_dates = need_dates or request_dates or None
    coverage = dict(snapshot.coverage or {})
    truncated = bool(coverage.get("truncated"))
    unbounded = bool(coverage.get("unbounded_scope"))
    if truncated or unbounded:
        reasons.append({"reason": "coverage_truncated",
                        "truncated": truncated, "unbounded_scope": unbounded,
                        "limits": dict(coverage.get("limits") or {})})

    matched: list[str] = []
    missing_intervals: list[str] = []
    counts = {"invalid": expected_invalid, "future": 0, "unverified": 0,
              "stale": 0}
    for code in codes:
        facts = _code_facts(snapshot, need, code, scope_dates)
        if not facts:
            missing_scopes.append(code)
            continue
        result = _code_result(facts, effective_cutoff, need, checked)
        for key in counts:
            counts[key] += result[key]
        matched.extend(result["matched"])
        missing_intervals.extend(result["missing_intervals"])
        if not result["usable"]:
            reasons.append({"reason": _unusable_reason(need), "code": code,
                            "capability": need.capability})

    if missing_scopes:
        reasons.append({"reason": _missing_scope_reason(need.capability),
                        "missing_scopes": list(dict.fromkeys(missing_scopes))})
    if missing_intervals:
        reasons.append({"reason": "intraday_missing",
                        "missing_intervals": sorted(set(missing_intervals))})
    for key, reason in (("invalid", "pit_invalid"),
                        ("future", "future_evidence"),
                        ("unverified", "pit_unverified"),
                        ("stale", "evidence_stale")):
        if counts[key]:
            reasons.append({"reason": reason, "count": counts[key]})

    status = _status_from(need, len(matched), missing_scopes,
                          missing_intervals, counts, truncated, unbounded)
    return CapabilityCheck(
        rule_id=need.rule_id, capability=need.capability, status=status,
        protects=need.protects, matched_evidence=tuple(matched),
        missing_scopes=tuple(dict.fromkeys(missing_scopes)),
        missing_intervals=tuple(sorted(set(missing_intervals))),
        reasons=tuple(reasons), affected_actions=need.affected_actions,
        inputs_missing=missing_inputs)


def _unusable_reason(need: EvidenceNeed) -> str:
    if need.capability == "intraday_bars":
        return "intraday_missing"
    if need.capability == "theme_membership":
        return "theme_membership_missing"
    return "pit_unverified"


def _missing_scope_reason(capability: str) -> str:
    if capability == "intraday_bars":
        return "intraday_missing"
    if capability == "theme_membership":
        return "theme_membership_missing"
    return "coverage_incomplete"



def check_needs(snapshot: EvidenceSnapshot, needs: Iterable[EvidenceNeed], *,
                inputs: Optional[Mapping[str, bool]] = None,
                now: Optional[str] = None) -> tuple[CapabilityCheck, ...]:
    return tuple(check_need(snapshot, need, inputs=inputs, now=now)
                 for need in needs)


def summarize_checks(checks: Iterable[CapabilityCheck]) -> dict:
    """按 ``protects`` 分开汇总：新增风险与已有保护互不吞并。"""
    checks = tuple(checks)
    blocked = {"new_risk": [], "existing_protection": []}
    for check in checks:
        if check.status == "usable":
            continue
        bucket = (blocked["existing_protection"]
                  if check.protects == PROTECT_EXISTING
                  else blocked["new_risk"])
        bucket.append({"rule_id": check.rule_id, "capability": check.capability,
                       "status": check.status,
                       "reasons": [thaw(reason) for reason in check.reasons],
                       "affected_actions": list(check.affected_actions)})
    return {
        "new_risk_blocked": bool(blocked["new_risk"]),
        "existing_protection_blocked": bool(blocked["existing_protection"]),
        "blocked": blocked,
        "usable_rules": [check.rule_id for check in checks if check.usable],
        "checked_rules": len(checks),
    }


def source_status(snapshot: EvidenceSnapshot) -> dict:
    """来源状态（是否配置/可用）与数据能力分开表达。"""
    manifest = dict(snapshot.source_manifest or {})
    sources = dict(manifest.get("sources") or {})
    status: dict[str, dict] = {}
    for name in ("tdx", "ths", "tencent"):
        status[name] = {"configured": True,
                        "state": "ready" if name in sources else "no_data_local"}
    for name in ("meoz", "tushare"):
        status[name] = {"configured": False, "state": "missing_api_key"}
    return {"store_state": manifest.get("store_state"),
            "sources": status,
            "calibration_version": manifest.get("calibration_version"),
            "calibrations": thaw(manifest.get("calibrations") or {})}