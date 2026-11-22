"""冻结请求与证据快照：canonical 指纹、时间/区间校验与重放。"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Any, Iterable, Mapping

from .dates import _moment, _trade_date_key
from .facts import EvidenceFact
from .frozen import (
    _integer,
    _strict_keys,
    _strict_mapping,
    _string,
    freeze,
    thaw,
)
from .integrity import content_hash
from .schema import (
    DEFAULT_BAR_LIMIT,
    DEFAULT_FACT_LIMIT,
    EVIDENCE_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    EvidenceError,
)

@dataclass(frozen=True)
class EvidenceRequest:
    """构建快照时冻结的请求母集（W2 所需范围来自这里，而非实际到货事实）。"""

    cutoff: str
    codes: tuple[str, ...] = ()
    trade_dates: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    periods: tuple[str, ...] = ()
    fact_limit: int = DEFAULT_FACT_LIMIT
    bar_limit: int = DEFAULT_BAR_LIMIT
    all_codes: bool = False

    def to_dict(self) -> dict:
        return {
            "cutoff": self.cutoff,
            "codes": list(self.codes),
            "trade_dates": list(self.trade_dates),
            "capabilities": list(self.capabilities),
            "periods": list(self.periods),
            "fact_limit": self.fact_limit,
            "bar_limit": self.bar_limit,
            "all_codes": self.all_codes,
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> "EvidenceRequest":
        payload = _strict_mapping(payload, what="EvidenceRequest")
        _strict_keys(payload, [item.name for item in fields(cls)],
                     what="EvidenceRequest")
        cutoff = _string(payload, "cutoff", required=True)
        if _moment(cutoff) is None:
            raise EvidenceError(f"非法请求 cutoff: {cutoff!r}",
                                reason="invalid_time")
        return cls(
            cutoff=cutoff,
            codes=tuple(_strings(payload.get("codes"), "codes")),
            trade_dates=tuple(_strings(payload.get("trade_dates"),
                                       "trade_dates")),
            capabilities=tuple(_strings(payload.get("capabilities"),
                                        "capabilities")),
            periods=tuple(_strings(payload.get("periods"), "periods")),
            fact_limit=_integer(payload, "fact_limit",
                                default=DEFAULT_FACT_LIMIT),
            bar_limit=_integer(payload, "bar_limit",
                               default=DEFAULT_BAR_LIMIT),
            all_codes=bool(payload.get("all_codes", False)),
        )


def _strings(value: Any, what: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise EvidenceError(f"{what} 必须是数组", reason="schema_mismatch")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise EvidenceError(f"{what} 只能包含字符串",
                                reason="schema_mismatch")
        result.append(item)
    return result


# ─── 证据快照 ────────────────────────────────────────────────────────────────


def _fact_sort_key(fact: EvidenceFact):
    return (fact.capability, fact.source, fact.code, fact.obs_type,
            fact.period or "", fact.interval_end or "", fact.evidence_id,
            fact.revision)


def _snapshot_id(digest: str) -> str:
    return f"es_{digest[:20]}"


def _snapshot_digest(cutoff: str, facts: Iterable[EvidenceFact],
                     request: EvidenceRequest, manifest: Mapping,
                     coverage: Mapping) -> str:
    """canonical 快照指纹：覆盖全部会改变能力/动作判定的语义。"""
    ordered = sorted(facts, key=_fact_sort_key)
    body = {
        "cutoff": cutoff,
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "request": request.to_dict(),
        "manifest": thaw(manifest),
        "coverage": thaw(coverage),
        "facts": [fact.to_dict() for fact in ordered],
    }
    try:
        return content_hash(body)
    except (TypeError, ValueError) as exc:
        raise EvidenceError(f"快照内容不可规范化: {type(exc).__name__}: {exc}",
                            reason="invalid_payload") from exc


@dataclass(frozen=True)
class EvidenceSnapshot:
    """冻结证据集：``snapshot_id`` 由内容决定，重放不读活动库。"""

    snapshot_id: str
    cutoff: str
    facts: tuple["EvidenceFact", ...]
    request: EvidenceRequest
    source_manifest: Mapping
    coverage: Mapping
    content_hash: str
    schema_version: str = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", tuple(self.facts))
        object.__setattr__(self, "source_manifest",
                           freeze(dict(self.source_manifest or {})))
        object.__setattr__(self, "coverage", freeze(dict(self.coverage or {})))

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "cutoff": self.cutoff,
            "facts": [fact.to_dict() for fact in self.facts],
            "request": self.request.to_dict(),
            "source_manifest": thaw(self.source_manifest),
            "coverage": thaw(self.coverage),
            "content_hash": self.content_hash,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> "EvidenceSnapshot":
        payload = _strict_mapping(payload, what="EvidenceSnapshot")
        _strict_keys(payload, [item.name for item in fields(cls)],
                     what="EvidenceSnapshot")
        schema_version = _string(payload, "schema_version",
                                 default=EVIDENCE_SCHEMA_VERSION)
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise EvidenceError(
                f"不支持的快照 schema_version: {schema_version}",
                reason="unsupported_schema")
        cutoff = _string(payload, "cutoff", required=True)
        if _moment(cutoff) is None:
            raise EvidenceError(f"非法快照 cutoff: {cutoff!r}",
                                reason="invalid_time")
        request = EvidenceRequest.from_dict(
            _strict_mapping(payload.get("request"), what="request"))
        if request.cutoff != cutoff:
            raise EvidenceError("请求 cutoff 与快照 cutoff 不一致",
                                reason="identity_mismatch")
        raw_facts = payload.get("facts")
        if not isinstance(raw_facts, (list, tuple)):
            raise EvidenceError("facts 必须是数组", reason="schema_mismatch")
        facts = tuple(EvidenceFact.from_dict(item) for item in raw_facts)
        manifest = _strict_mapping(payload.get("source_manifest") or {},
                                   what="source_manifest")
        coverage = _strict_mapping(payload.get("coverage") or {},
                                   what="coverage")
        cutoff_moment = _moment(cutoff)
        assert cutoff_moment is not None
        for fact in facts:
            _validate_fact_within_cutoff(fact, cutoff_moment, request)
        digest = _snapshot_digest(cutoff, facts, request, manifest, coverage)
        declared_hash = _string(payload, "content_hash", required=True)
        if declared_hash != digest:
            raise EvidenceError("快照 content_hash 与内容不一致",
                                reason="hash_mismatch")
        snapshot_id = _string(payload, "snapshot_id", required=True)
        if snapshot_id != _snapshot_id(digest):
            raise EvidenceError("snapshot_id 与内容指纹不一致",
                                reason="identity_mismatch")
        return cls(
            snapshot_id=snapshot_id,
            cutoff=cutoff,
            facts=facts,
            request=request,
            source_manifest=manifest,
            coverage=coverage,
            content_hash=digest,
            schema_version=schema_version,
        )


def _validate_fact_within_cutoff(fact: "EvidenceFact", cutoff: datetime,
                                 request: EvidenceRequest) -> None:
    available = _moment(fact.available_at)
    if available is None or available > cutoff:
        raise EvidenceError(
            f"事实 {fact.evidence_id} 的 available_at 晚于快照 cutoff",
            reason="future_evidence")
    if fact.interval_end:
        end = _moment(fact.interval_end)
        if end is None or end > cutoff:
            raise EvidenceError(
                f"事实 {fact.evidence_id} 的区间晚于快照 cutoff",
                reason="future_evidence")
    if request.trade_dates and _trade_date_key(fact.trade_date) not in {
            _trade_date_key(item) for item in request.trade_dates}:
        raise EvidenceError(
            f"事实 {fact.evidence_id} 的 trade_date 不在请求范围内",
            reason="scope_mismatch")
    if request.codes and fact.code not in request.codes:
        raise EvidenceError(
            f"事实 {fact.evidence_id} 的代码不在请求范围内",
            reason="scope_mismatch")


# ─── 构建 ────────────────────────────────────────────────────────────────────


def slice_snapshot(snapshot: EvidenceSnapshot, cutoff: str) -> EvidenceSnapshot:
    """把已冻结快照按**更早 cutoff** PIT 切片（不重新读库、不引入未来事实）。

    事件级重放用同一份已冻结事实的时点切片，避免每事件重复查库；只有
    ``available_at`` 与 ``interval_end`` 均不晚于新 cutoff 的事实进入切片，
    指纹/manifest 按切片内容重算，供审计追溯 ``sliced_from``。
    """
    moment = _moment(cutoff)
    if moment is None:
        raise EvidenceError(f"非法切片 cutoff: {cutoff!r}", reason="invalid_time")
    facts = tuple(
        fact for fact in snapshot.facts
        if (_moment(fact.available_at) is not None
            and _moment(fact.available_at) <= moment)
        and (not fact.interval_end
             or (_moment(fact.interval_end) is not None
                 and _moment(fact.interval_end) <= moment)))
    request = replace(snapshot.request, cutoff=cutoff)
    coverage = dict(thaw(snapshot.coverage))
    coverage["facts"] = len(facts)
    coverage["usable_facts"] = sum(1 for fact in facts if fact.usable)
    manifest = dict(thaw(snapshot.source_manifest))
    manifest["cutoff"] = cutoff
    manifest["sliced_from"] = snapshot.snapshot_id
    digest = _snapshot_digest(cutoff, facts, request, manifest, coverage)
    return EvidenceSnapshot(
        snapshot_id=_snapshot_id(digest), cutoff=cutoff, facts=facts,
        request=request, source_manifest=manifest, coverage=coverage,
        content_hash=digest, schema_version=snapshot.schema_version)
