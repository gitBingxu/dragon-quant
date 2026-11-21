"""只读数据装配（W4a）：本地冻结资产 → 证据快照 + 能力/覆盖投影。

默认只读本地 ``assets.db`` 观测/bar；**不联网、不补数、不启动采集**。校准来自
:mod:`dragon_quant.infra.calibration` 的版本化规格：只有当前安装解析器逐项匹配
时才把 ``tdx`` 日 K/1 分 K 标为 verified，否则保持 unverified。

输出：``SnapshotBundle``（证据快照 + 解析器身份 + 校准 manifest），供
``research bs-advice`` 与 review-account BS 分支共用；W4b 冻结实验/Web 读同一
DTO，不再另拼一套取数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from dragon_quant.infra.calibration import (
    CALIBRATION_VERSION, CaptureProvenance, calibration_manifest,
    current_tdx_provenance, resolve_spec,
)
from dragon_quant.review_account.evidence import (
    QUALITY_UNVERIFIED, QUALITY_VERIFIED, SourceSemantics, build_snapshot,
    coverage_summary,
)
from dragon_quant.review_account.evidence.schema import VERIFIED


def production_calibrations(provenance: Optional[CaptureProvenance] = None
                            ) -> tuple[SourceSemantics, ...]:
    """当前安装解析器命中的生产校准（日 K/1 分 K）；未知返回空元组。"""
    provenance = provenance if provenance is not None else current_tdx_provenance()
    calibrations: list[SourceSemantics] = []
    for period in ("daily", "1min"):
        spec = resolve_spec(provenance, period=period)
        if spec is None:
            continue
        components = (spec.time_semantics, spec.unit_semantics,
                      spec.content_semantics)
        calibrations.append(SourceSemantics(
            source="tdx", kind=period,
            time_semantics=spec.time_semantics,
            unit_semantics=spec.unit_semantics,
            content_semantics=spec.content_semantics,
            quality=(QUALITY_VERIFIED if all(item == VERIFIED
                                             for item in components)
                     else QUALITY_UNVERIFIED),
            version=spec.calibration_id, evidence=spec.evidence))
    return tuple(calibrations)


@dataclass(frozen=True)
class SnapshotBundle:
    snapshot: object
    provenance: Optional[CaptureProvenance]
    calibration: dict

    def to_dict(self) -> dict:
        return {
            "snapshot": self.snapshot.to_dict(),
            "calibration": self.calibration,
        }


def assemble_snapshot(*, cutoff: str, codes: Iterable[str] = (),
                      trade_dates: Iterable[str] = (),
                      db_path=None,
                      provenance: Optional[CaptureProvenance] = None,
                      max_facts: int = 5000,
                      max_bars: int = 5000) -> SnapshotBundle:
    """在 ``cutoff`` 冻结本地合格证据；只读、不联网、不补数。"""
    resolved = provenance if provenance is not None else current_tdx_provenance()
    calibrations = production_calibrations(resolved)
    codes_list = tuple(sorted({str(code) for code in codes if str(code)}))
    snapshot = build_snapshot(
        cutoff=cutoff, codes=codes_list or None,
        trade_dates=tuple(sorted({str(item) for item in trade_dates if str(item)}))
        or None,
        calibrations=calibrations, db_path=db_path,
        fact_limit=max_facts, bar_limit=max_bars, strict_calibration=True)
    return SnapshotBundle(snapshot=snapshot, provenance=resolved,
                          calibration=calibration_manifest(resolved))


def capabilities_from_snapshot(snapshot) -> dict:
    """能力投影：usable / missing / unverified，按能力逐项，不因一项可用判全市场。"""
    coverage = coverage_summary(snapshot)
    by_capability = coverage.get("by_capability") or {}
    usable, missing, detail = [], [], []
    for capability in sorted(by_capability):
        row = by_capability[capability]
        facts = int(row.get("facts") or 0)
        usable_count = int(row.get("usable") or 0)
        if facts == 0:
            state = "missing"
        elif usable_count == 0:
            state = "unverified"
        elif row.get("missing_codes"):
            state = "partial"
        else:
            state = "usable"
        entry = {
            "capability": capability,
            "status": state,
            "facts": facts,
            "usable": usable_count,
            "missing_codes": list(row.get("missing_codes") or ()),
            "truncated": bool(row.get("truncated")),
        }
        detail.append(entry)
        if state == "usable":
            usable.append(capability)
        elif state == "missing":
            missing.append(capability)
    return {
        "usable_capabilities": usable,
        "missing_capabilities": missing,
        "by_capability": detail,
        "calibration_version": CALIBRATION_VERSION,
    }


def coverage_from_snapshot(snapshot) -> dict:
    return coverage_summary(snapshot)


__all__ = [
    "SnapshotBundle", "assemble_snapshot", "production_calibrations",
    "capabilities_from_snapshot", "coverage_from_snapshot",
]
