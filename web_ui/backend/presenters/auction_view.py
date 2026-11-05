"""``/auction`` 视图投影：采集状态 vs 决策质量、运行概况（方案 §5.1 / §6.4）。

纯函数：输入 ``auction_snapshots`` 行 + ``auction_assets`` 名称/代码，输出视图模型。
不读库、不触网；``veto`` / ``unavailable`` 是决策结果，不影响采集完备。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

_BAD_QUALITY = "unavailable"


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _quality(row: Mapping[str, Any]) -> str:
    """决策质量：error → vetoed → unavailable → degraded → ok → unknown。

    与既有 ``_auction_summary`` 的判定顺序对齐，避免同一行在两个块里语义不同。
    """
    if row.get("error"):
        return "error"
    if row.get("is_weak") is True:
        return "vetoed"
    quality = row.get("data_quality")
    status = quality.get("status") if isinstance(quality, Mapping) else None
    if (status == _BAD_QUALITY or row.get("unverified") is True
            or _finite(row.get("strength")) is None
            or row.get("level") is None):
        return "unavailable"
    if status == "degraded":
        return "degraded"
    if row.get("level") in ("强", "中", "弱"):
        return "ok"
    return "unknown"


def _legacy_summary(rows: list[dict]) -> dict[str, int]:
    """既有 ``summary`` 契约（强度分布），键与语义保持不变。"""
    summary = {
        "strong": 0,
        "medium": 0,
        "weak": 0,
        "vetoed": 0,
        "unavailable": 0,
        "unknown": 0,
    }
    for row in rows:
        quality = row.get("data_quality") if isinstance(row.get("data_quality"), dict) else {}
        if row.get("is_weak"):
            summary["vetoed"] += 1
        elif quality.get("status") == "unavailable":
            summary["unavailable"] += 1
        elif row.get("unverified"):
            summary["unknown"] += 1
        elif row.get("level") == "强":
            summary["strong"] += 1
        elif row.get("level") == "中":
            summary["medium"] += 1
        elif row.get("level") == "弱":
            summary["weak"] += 1
        else:
            summary["unknown"] += 1
    return summary


def _evidence(row: Mapping[str, Any]) -> dict | None:
    evidence = row.get("evidence")
    if not isinstance(evidence, Mapping):
        return None
    return {
        "last_time": evidence.get("last_time"),
        "official_open_px": evidence.get("official_open_px"),
        "last_indicative_price": evidence.get("last_indicative_price"),
        "last_matched_lots": evidence.get("last_matched_lots"),
        "final_unmatched_lots_signed": evidence.get("final_unmatched_lots_signed"),
    }


def _sort_rows(rows: Iterable[dict]) -> list[dict]:
    ordered = sorted(rows, key=lambda row: str(row.get("code") or ""))
    ordered.sort(key=lambda row: -(_finite(row.get("strength")) or 0.0))
    ordered.sort(key=lambda row: (row.get("rank") is None, row.get("rank") or 0))
    return ordered


def _collection_status(code: str, asset_set: set[str]) -> str:
    return "ok" if code in asset_set else "snapshot_only"


def build_auction_day(
    *,
    trade_date: str,
    rows: list[dict],
    names: Mapping[str, str] | None = None,
    asset_codes: list[str] | None = None,
) -> dict:
    """单日竞价视图：快照行 + 采集状态 + 运行概况 + 名称映射。"""

    asset_list = [str(code) for code in (asset_codes or []) if code]
    asset_set = set(asset_list)
    name_map = dict(names or {})
    snapshot_codes = [str(row.get("code") or "") for row in rows]
    snapshot_set = set(snapshot_codes)

    snapshots: list[dict] = []
    for row in _sort_rows(rows):
        code = str(row.get("code") or "")
        snapshots.append({
            "code": code,
            "version": row.get("version") or row.get("config_version"),
            "is_weak": row.get("is_weak"),
            "strength": row.get("strength"),
            "level": row.get("level"),
            "direction": row.get("direction"),
            "rank": row.get("rank"),
            "validation_status": row.get("validation_status"),
            "data_quality": row.get("data_quality"),
            "risk_events": row.get("risk_events"),
            "reject_reason": row.get("reject_reason"),
            "error": row.get("error"),
            "name": name_map.get(code),
            "collected": code in asset_set,
            "evaluated": not bool(row.get("error")),
            "collection_status": _collection_status(code, asset_set),
            "key_evidence": _evidence(row),
        })

    quality_counts = {
        "ok": 0, "degraded": 0, "unavailable": 0,
        "vetoed": 0, "unknown": 0,
    }
    error_rows = 0
    validation: Counter[str] = Counter()
    versions: set[str] = set()
    created: list[str] = []
    for row in rows:
        quality = _quality(row)
        if quality == "error":
            error_rows += 1
        else:
            quality_counts[quality] = quality_counts.get(quality, 0) + 1
        validation[str(row.get("validation_status") or "unknown")] += 1
        version = row.get("config_version") or row.get("version")
        if isinstance(version, str) and version:
            versions.add(version)
        stamp = row.get("created_at")
        if isinstance(stamp, str) and stamp:
            created.append(stamp)

    run = {
        "snapshot_count": len(rows),
        "asset_count": len(asset_list),
        "snapshot_only_codes": sorted(snapshot_set - asset_set),
        "asset_only_codes": sorted(asset_set - snapshot_set),
        "quality_counts": quality_counts,
        "error_rows": error_rows,
        "config_versions": sorted(versions),
        "validation_statuses": dict(sorted(validation.items())),
        "created_at_min": min(created) if created else None,
        "created_at_max": max(created) if created else None,
        "evaluation_available": any(not row.get("error") for row in rows),
    }

    return {
        "trade_date": trade_date,
        "available": bool(rows),
        "count": len(rows),
        "snapshots": snapshots,
        "summary": _legacy_summary(rows),
        "assets_count": len(asset_list),
        "run": run,
        "names": name_map,
        "asset_codes": asset_list,
    }


def build_auction_calendar(
    *, rows: Mapping[str, dict], start_date: str, end_date: str
) -> dict:
    """竞价日历三态：两组 code 都空 = missing；快照 ⊆ 资产 = complete。"""

    days: list[dict] = []
    latest: str | None = None
    for trade_date in sorted(rows):
        raw = rows[trade_date] or {}
        asset_codes = [str(code) for code in (raw.get("asset_codes") or [])]
        snapshot_codes = [str(code) for code in (raw.get("snapshot_codes") or [])]
        asset_set, snapshot_set = set(asset_codes), set(snapshot_codes)
        if not asset_set and not snapshot_set:
            status = "missing"
        elif asset_set and snapshot_set and snapshot_set <= asset_set:
            status = "complete"
        else:
            status = "incomplete"
        days.append({
            "trade_date": trade_date,
            "status": status,
            "has_snapshot": bool(snapshot_set),
            "has_assets": bool(asset_set),
            "snapshot_count": len(snapshot_codes),
            "asset_count": len(asset_codes),
            "snapshot_only_codes": sorted(snapshot_set - asset_set),
            "asset_only_codes": sorted(asset_set - snapshot_set),
        })
        if asset_set or snapshot_set:
            latest = trade_date
    return {
        "start_date": start_date,
        "end_date": end_date,
        "latest_record_date": latest,
        "days": days,
    }


__all__ = ["build_auction_calendar", "build_auction_day"]
