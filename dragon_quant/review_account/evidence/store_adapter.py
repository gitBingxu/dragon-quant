"""仓储读取适配：把 assets.db 时点观测冻结为证据快照。"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping, Optional

from dragon_quant.infra.assets import observations as store

from .dates import _moment
from .facts import EvidenceFact, _validate_fact
from .schema import (
    BAR_CAPABILITIES,
    CALIBRATION_VERSION,
    CAPABILITIES,
    DEFAULT_BAR_LIMIT,
    DEFAULT_FACT_LIMIT,
    OBS_CAPABILITIES,
    QUALITY_UNVERIFIED,
    QUALITY_VERIFIED,
    UNVERIFIED,
    VERIFIED,
    EvidenceError,
)
from .semantics import SourceSemantics, semantics_for
from .snapshots import (
    EvidenceRequest,
    EvidenceSnapshot,
    _fact_sort_key,
    _snapshot_digest,
    _snapshot_id,
)

def _calibration_of(source: str, kind: str,
                    calibrations: Optional[Iterable[SourceSemantics]]
                    ) -> SourceSemantics:
    return semantics_for(source, kind, calibrations)


def _fact_from_observation(row: dict, *, calibration: SourceSemantics,
                           strict: bool = False) -> EvidenceFact:
    capability = OBS_CAPABILITIES.get(row["obs_type"], "market_context")
    if strict:
        # 严格证据：只认行内冻结来源绑定；未知/未实证能力保持 unverified。
        from dragon_quant.infra.calibration import verify_binding

        spec = verify_binding(row.get("calibration"), source=str(row["source"]),
                              period=str(row["obs_type"]))
        calibration = (SourceSemantics(source=str(row["source"]),
                                       kind=str(row["obs_type"]))
                       if spec is None else SourceSemantics(
                           source=str(row["source"]), kind=str(row["obs_type"]),
                           time_semantics=spec.time_semantics,
                           unit_semantics=spec.unit_semantics,
                           content_semantics=spec.content_semantics,
                           quality=QUALITY_VERIFIED,
                           version=spec.calibration_id, evidence=spec.evidence))
    effective_time = (VERIFIED if calibration.time_semantics == VERIFIED
                      else UNVERIFIED)
    return EvidenceFact(
        evidence_id=row["obs_id"],
        capability=capability,
        source=row["source"],
        obs_type=row["obs_type"],
        market=row["market"],
        code=row["code"],
        trade_date=row["trade_date"],
        available_at=row["available_at"],
        received_at=row["received_at"],
        first_received_at=row["first_received_at"],
        revision=int(row["revision"]),
        content_hash=row["content_hash"],
        source_quality=row["quality"],
        quality=calibration.quality,
        units=row.get("units") or {},
        payload=row.get("payload") or {},
        calibration=calibration.to_dict(),
        time_semantics=effective_time,
        unit_semantics=calibration.unit_semantics,
        content_semantics=calibration.content_semantics,
    )


def _fact_from_bar(row: dict, *, calibration: SourceSemantics) -> EvidenceFact:
    capability = BAR_CAPABILITIES.get(row["period"], "market_context")
    # U3：以**行内冻结来源绑定**为准，不接受当前机器指纹替历史行背书；
    # 旧无绑定行保持 unverified（即使调用方传了当前校准）。
    from dragon_quant.infra.calibration import verify_binding

    spec = verify_binding(row.get("calibration"), source=str(row["source"]),
                          period=str(row["period"]))
    if spec is None:
        calibration = SourceSemantics(source=str(row["source"]),
                                      kind=str(row["period"]))
    else:
        calibration = SourceSemantics(
            source=str(row["source"]), kind=str(row["period"]),
            time_semantics=spec.time_semantics,
            unit_semantics=spec.unit_semantics,
            content_semantics=spec.content_semantics,
            quality=QUALITY_VERIFIED,
            version=spec.calibration_id, evidence=spec.evidence)
    effective_time = VERIFIED if (
        calibration.time_semantics == VERIFIED
        and row["time_semantics"] == VERIFIED) else UNVERIFIED
    # 行内 unit_semantics 也是独立依据：校准与落库双方都确认才视为 verified。
    effective_unit = VERIFIED if (
        calibration.unit_semantics == VERIFIED
        and row["unit_semantics"] == VERIFIED) else UNVERIFIED
    return EvidenceFact(
        evidence_id=row["bar_id"],
        capability=capability,
        source=row["source"],
        obs_type=f"bar:{row['period']}",
        market=row["market"],
        code=row["code"],
        trade_date=row["trade_date"],
        available_at=row["available_at"],
        received_at=row["observed_at"],
        first_received_at=row["observed_at"],
        revision=int(row["revision"]),
        content_hash=row["content_hash"],
        source_quality=QUALITY_UNVERIFIED,
        quality=calibration.quality,
        units={"volume": effective_unit, "amount": effective_unit},
        payload={"open": row["open"], "high": row["high"],
                 "low": row["low"], "close": row["close"],
                 "volume": row["volume"], "amount": row["amount"]},
        calibration=calibration.to_dict(),
        period=row["period"],
        adjustment=row["adjustment"],
        interval_start=row["interval_start"],
        interval_end=row["interval_end"],
        completeness=row["completeness"],
        time_semantics=effective_time,
        unit_semantics=effective_unit,
        content_semantics=calibration.content_semantics,
        derived=bool(row.get("derived", False)),
    )


def _row_is_temporally_usable(row: Mapping, cutoff: datetime,
                              *, strict: bool = False) -> bool:
    available = _moment(row.get("available_at") or "")
    if available is None or available > cutoff:
        return False
    end = row.get("interval_end")
    if end is not None:
        end_moment = _moment(end)
        if end_moment is not None and end_moment > cutoff:
            return False
    valid_until = row.get("valid_until")
    if valid_until is not None:
        until_moment = _moment(valid_until)
        if until_moment is not None and until_moment < cutoff:
            return False
    binding = row.get("calibration") if "calibration" in row else None
    if binding:
        calibrated_at = _moment(binding.get("calibrated_at") or "")
        if calibrated_at is None or calibrated_at > cutoff:
            return False
    elif strict and "calibration" in row:
        # 严格模式下无绑定的观测不因“当前可读”变成当时已知；仍保留但 unverified。
        pass
    return True


def build_snapshot(*, cutoff: str, codes: Optional[Iterable[str]] = None,
                   trade_dates: Optional[Iterable[str]] = None,
                   capabilities: Optional[Iterable[str]] = None,
                   db_path=None,
                   calibrations: Optional[Iterable[SourceSemantics]] = None,
                   fact_limit: int = DEFAULT_FACT_LIMIT,
                   bar_limit: int = DEFAULT_BAR_LIMIT,
                   strict_calibration: bool = False) -> EvidenceSnapshot:
    """在 ``cutoff`` 时刻冻结当时可用的合格事实。

    显式 ``codes`` 会成为冻结请求母集；省略表示“未限定范围”，能力检查会把它
    标为未冻结范围并最多返回 ``partial``，不会因为“目前到货的事实都可用”而
    宣称全覆盖。
    """
    if _moment(cutoff) is None:
        raise EvidenceError(f"非法 cutoff: {cutoff!r}")
    codes_list = sorted({str(code) for code in codes or () if str(code)})
    dates_list = sorted({str(item) for item in trade_dates or () if str(item)}) or None
    wanted = set(capabilities) if capabilities else None
    if wanted is not None:
        unknown = sorted(wanted - set(CAPABILITIES))
        if unknown:
            raise EvidenceError(f"未知能力: {unknown}")
    obs_topics = [obs for obs, cap in OBS_CAPABILITIES.items()
                  if wanted is None or cap in wanted]
    periods = [period for period, cap in BAR_CAPABILITIES.items()
               if wanted is None or cap in wanted]
    request = EvidenceRequest(
        cutoff=cutoff, codes=tuple(codes_list),
        trade_dates=tuple(dates_list or ()),
        capabilities=tuple(sorted(wanted)) if wanted is not None else (),
        periods=tuple(sorted(periods)) if wanted is not None else (),
        fact_limit=int(fact_limit), bar_limit=int(bar_limit),
        all_codes=not codes_list,
    )
    try:
        state = store.store_state(db_path)
    except store.ObservationStoreError as exc:
        raise EvidenceError(str(exc)) from exc
    if state != store.READY:
        coverage = _empty_coverage(state, request)
        manifest = {
            "store_state": state,
            "database": state,
            "calibration_version": CALIBRATION_VERSION,
            "calibrations": {},
            "sources": {},
            "truncated": False,
            "limits": {"facts": int(fact_limit), "bars": int(bar_limit)},
        }
        return _freeze(cutoff, (), manifest, coverage, request)

    try:
        obs_rows = store.select_observations_asof(
            cutoff, codes=codes_list or None, topics=obs_topics or None,
            trade_dates=dates_list, limit=fact_limit, db_path=db_path)
        bar_rows = store.select_bars_asof(
            cutoff, codes=codes_list or None, periods=periods or None,
            trade_dates=dates_list, limit=bar_limit, db_path=db_path)
    except store.ObservationStoreError as exc:
        raise EvidenceError(str(exc)) from exc

    cutoff_moment = _moment(cutoff)
    assert cutoff_moment is not None
    facts_list: list[EvidenceFact] = []
    sources: dict[str, dict] = {}
    calibration_used: dict[str, dict] = {}
    temporal_rejected = 0
    for row in obs_rows:
        if not _row_is_temporally_usable(row, cutoff_moment,
                                         strict=strict_calibration):
            temporal_rejected += 1
            continue
        calibration = _calibration_of(row["source"], row["obs_type"],
                                      calibrations)
        calibration_used[f"{row['source']}:{row['obs_type']}"] = \
            calibration.to_dict()
        fact = _fact_from_observation(row, calibration=calibration,
                                      strict=strict_calibration)
        _validate_fact(fact)
        if wanted is not None and fact.capability not in wanted:
            continue
        facts_list.append(fact)
        entry = sources.setdefault(row["source"], {"observations": 0, "bars": 0})
        entry["observations"] += 1
    for row in bar_rows:
        if not _row_is_temporally_usable(row, cutoff_moment):
            temporal_rejected += 1
            continue
        calibration = _calibration_of(row["source"], row["period"],
                                      calibrations)
        calibration_used[f"{row['source']}:{row['period']}"] = \
            calibration.to_dict()
        fact = _fact_from_bar(row, calibration=calibration)
        _validate_fact(fact)
        if wanted is not None and fact.capability not in wanted:
            continue
        facts_list.append(fact)
        entry = sources.setdefault(row["source"], {"observations": 0, "bars": 0})
        entry["bars"] += 1
    facts_list.sort(key=_fact_sort_key)

    truncated = (len(obs_rows) >= int(fact_limit)
                 or len(bar_rows) >= int(bar_limit))
    coverage = _build_coverage(state, facts_list, request,
                              truncated=truncated or request.all_codes,
                              temporal_rejected=temporal_rejected)
    manifest = {
        "store_state": state,
        "database": state,
        "cutoff": cutoff,
        "calibration_version": CALIBRATION_VERSION,
        "calibrations": calibration_used,
        "sources": sources,
        "truncated": truncated,
        "limits": {"facts": int(fact_limit), "bars": int(bar_limit)},
    }
    return _freeze(cutoff, tuple(facts_list), manifest, coverage, request)


def _empty_coverage(state: str, request: EvidenceRequest) -> dict:
    by_capability = {}
    for capability in CAPABILITIES:
        if request.capabilities and capability not in request.capabilities:
            continue
        by_capability[capability] = _capability_coverage(
            capability, [], request, truncated=False)
    return {
        "store_state": state,
        "by_capability": by_capability,
        "facts": 0,
        "usable_facts": 0,
        "truncated": False,
        "unbounded_scope": request.all_codes,
        "temporal_rejected": 0,
        "limits": {"facts": request.fact_limit, "bars": request.bar_limit},
    }


def _capability_coverage(capability: str, facts: Iterable[EvidenceFact],
                         request: EvidenceRequest, *, truncated: bool) -> dict:
    facts = list(facts)
    observed = sorted({fact.code for fact in facts})
    expected = list(request.codes)
    missing = [code for code in expected if code not in set(observed)]
    return {
        "facts": len(facts),
        "usable": sum(1 for fact in facts if fact.usable),
        "unverified": sum(1 for fact in facts if not fact.usable),
        "expected_codes": expected,
        "observed_codes": observed,
        "missing_codes": missing,
        "truncated": bool(truncated),
    }


def _build_coverage(state: str, facts: Iterable[EvidenceFact],
                    request: EvidenceRequest, *,
                    truncated: bool, temporal_rejected: int) -> dict:
    facts = list(facts)
    by_capability: dict[str, dict] = {}
    grouped: dict[str, list[EvidenceFact]] = {}
    for fact in facts:
        grouped.setdefault(fact.capability, []).append(fact)
    for capability in CAPABILITIES:
        if request.capabilities and capability not in request.capabilities:
            continue
        by_capability[capability] = _capability_coverage(
            capability, grouped.get(capability, ()), request,
            truncated=truncated)
    return {
        "store_state": state,
        "by_capability": by_capability,
        "facts": len(facts),
        "usable_facts": sum(1 for fact in facts if fact.usable),
        "truncated": bool(truncated),
        "unbounded_scope": request.all_codes,
        "temporal_rejected": int(temporal_rejected),
        "limits": {"facts": request.fact_limit, "bars": request.bar_limit},
    }


def _freeze(cutoff: str, facts: tuple[EvidenceFact, ...],
            manifest: Mapping, coverage: Mapping,
            request: EvidenceRequest) -> EvidenceSnapshot:
    digest = _snapshot_digest(cutoff, facts, request, manifest, coverage)
    return EvidenceSnapshot(
        snapshot_id=_snapshot_id(digest),
        cutoff=cutoff,
        facts=facts,
        request=request,
        source_manifest=dict(manifest),
        coverage=dict(coverage),
        content_hash=digest,
    )



def snapshot_from_dict(payload: Mapping) -> EvidenceSnapshot:
    """``EvidenceSnapshot.from_dict`` 的窄入口别名（W2/W3 稳定调用点）。"""
    return EvidenceSnapshot.from_dict(payload)
