"""premarket.snapshot — 只读快照 Adapter（``assets.db``，``mode=ro``）。

职责：把异构的 ``prep_snapshots`` / ``auction_snapshots`` 行适配为统一信封，
只读取、只解析 JSON、逐行隔离损坏；**不解释业务、不评分、不写库**。

不变量：

* 只走 ``infra.assets.db._connect_readonly``（``mode=ro``、零 DDL、零
  WAL/shm sidecar）；缺表（空库 / 残缺库）映射为 ``missing``；其它 ``sqlite3.Error`` 转为 ``read_error``。
* prep 只取**严格早于** T 的最近快照（由存储层 SQL 保证，绝不自比 / 取未来）。
* 完整嵌入 ``prep_snapshots.result_json`` 与 ``auction_snapshots.analysis_json``
  （仅去掉 envelope 键 ``trade_date`` / ``created_at`` / ``error``）。
* ``frozen_at`` 只从 ``result.strategy_context.frozen_at`` 原样读取，缺失即
  ``None`` + 原因，绝不由 ``prep_frozen_at()`` 合成。
* 缓存状态不持久化 → ``cache.status="unknown"``，不伪造。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable, Mapping, Optional

from dragon_quant.infra.assets import db as assets_db

PREP_SUPPORTED_SCHEMA = "0.8"
AUCTION_SUPPORTED_SCHEMA = "2"
OPEN_TIME = (9, 25)

_ENVELOPE_KEYS = ("trade_date", "created_at", "error")


def _as_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _as_of(trade_date: str) -> str:
    compact = str(trade_date or "").replace("-", "")
    if len(compact) == 8 and compact.isdigit():
        return (f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
                "T09:25:00+08:00")
    return f"{trade_date}T09:25:00+08:00"


# ─── prep ──────────────────────────────────────────────────────────────────


def _base_prep() -> dict:
    return {
        "available": False,
        "state": "missing",
        "trade_date": None,
        "created_at": None,
        "frozen_at": None,
        "frozen_at_reason": None,
        "schema_version": None,
        "schema_supported": False,
        "review_available": False,
        "review_config": None,
        "result": None,
    }


def _prep_review_fields(result: Mapping[str, Any]) -> tuple[bool, Optional[dict]]:
    review = result.get("review")
    if not isinstance(review, Mapping):
        return False, None
    if review.get("enabled") is False:
        return False, None
    config = {
        "config_version": review.get("config_version"),
        "config_hash": review.get("config_hash"),
        "review_version": review.get("review_version"),
    }
    return True, config


def _missing_table(exc: sqlite3.Error) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and \
        "no such table" in str(exc).lower()


def load_prep(before_date: str) -> dict:
    """读取严格早于 ``before_date`` 的最近 prep 快照（只读信封）。"""

    envelope = _base_prep()
    try:
        payload, meta = assets_db.get_previous_prep_snapshot(
            before_date, readonly=True)
    except sqlite3.Error as exc:
        if _missing_table(exc):
            return envelope           # 空库 / 缺表 = 无数据，不是读取失败
        envelope["state"] = "read_error"
        envelope["error"] = f"{type(exc).__name__}: {exc}"
        return envelope

    meta = meta if isinstance(meta, Mapping) else {}
    envelope["trade_date"] = meta.get("trade_date")
    envelope["created_at"] = meta.get("created_at")
    error = meta.get("error")
    if error:
        envelope["state"] = "corrupt"
        return envelope
    if not isinstance(payload, Mapping):
        return envelope

    result = dict(payload)
    envelope["result"] = result
    envelope["available"] = True
    version = _as_str(result.get("version"))
    envelope["schema_version"] = version
    envelope["schema_supported"] = version == PREP_SUPPORTED_SCHEMA
    envelope["state"] = ("ready" if envelope["schema_supported"]
                         else "unsupported_schema")

    context = result.get("strategy_context")
    if isinstance(context, Mapping):
        envelope["frozen_at"] = _as_str(context.get("frozen_at"))
        if envelope["frozen_at"] is None:
            envelope["frozen_at_reason"] = "frozen_at_missing"
    else:
        envelope["frozen_at_reason"] = "strategy_context_missing"

    review_available, review_config = _prep_review_fields(result)
    envelope["review_available"] = review_available
    envelope["review_config"] = review_config
    return envelope


# ─── auction ───────────────────────────────────────────────────────────────


def _base_auction(trade_date: str) -> dict:
    return {
        "available": False,
        "state": "missing",
        "trade_date": trade_date,
        "as_of": _as_of(trade_date),
        "ready_at": _as_of(trade_date),
        "row_count": 0,
        "pre_ready_rows_present": False,
        "corrupt_codes": [],
        "legacy_schema_codes": [],
        "created_at": {"min": None, "max": None},
        "config": {
            "config_versions": [],
            "config_hashes": [],
            "semantics_versions": [],
            "validation_status": [],
            "mixed": False,
        },
        "cache": {"status": "unknown", "reason": "not_persisted_in_snapshot"},
        "positions": [],
        # loader-only：context 读取后剔除，不进入最终 JSON 契约。
        "weekend": False,
    }


def _strip_envelope(row: Mapping[str, Any]) -> dict:
    return {key: value for key, value in row.items()
            if key not in _ENVELOPE_KEYS}


def _unique_strings(values: Iterable[Any]) -> list[str]:
    seen = {value for value in values if isinstance(value, str) and value}
    return sorted(seen)


def _auction_config(rows: list[Mapping[str, Any]]) -> dict:
    versions: list[Any] = []
    hashes: list[Any] = []
    semantics: list[Any] = []
    statuses: list[Any] = []
    for row in rows:
        analysis = _strip_envelope(row)
        if not isinstance(analysis, Mapping) or row.get("error"):
            continue
        versions.append(analysis.get("config_version"))
        hashes.append(analysis.get("config_hash"))
        semantics_value = _as_dict(analysis.get("semantics")).get(
            "semantics_version")
        semantics.append(semantics_value)
        statuses.append(analysis.get("validation_status"))
    config_versions = _unique_strings(versions)
    config_hashes = _unique_strings(hashes)
    return {
        "config_versions": config_versions,
        "config_hashes": config_hashes,
        "semantics_versions": _unique_strings(semantics),
        "validation_status": _unique_strings(statuses),
        "mixed": len(config_versions) > 1 or len(config_hashes) > 1,
    }


def _created_range(rows: list[Mapping[str, Any]]) -> dict:
    values = [row.get("created_at") for row in rows
              if isinstance(row.get("created_at"), str) and row.get("created_at")]
    if not values:
        return {"min": None, "max": None}
    return {"min": min(values), "max": max(values)}


def load_auction(trade_date: str, *, now, planned_codes=None) -> dict:
    """读取 T 日竞价强度快照（只读信封，逐行损坏隔离）。"""

    envelope = _base_auction(trade_date)
    planned = {str(code) for code in (planned_codes or ()) if code}
    is_today = trade_date == now.strftime("%Y-%m-%d")
    weekend = is_today and now.weekday() >= 5
    envelope["weekend"] = bool(weekend)

    try:
        rows = assets_db.list_auction_snapshots(
            trade_date, source="v2", with_meta=True, readonly=True)
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            envelope["state"] = "read_error"
            envelope["error"] = f"{type(exc).__name__}: {exc}"
            return envelope
        rows = []

    rows = [row for row in rows if isinstance(row, Mapping)]
    envelope["row_count"] = len(rows)
    envelope["created_at"] = _created_range(rows)
    envelope["config"] = _auction_config(rows)

    positions = []
    corrupt_codes = []
    legacy_codes = []
    for row in rows:
        code = str(row.get("code") or "")
        error = row.get("error")
        analysis = _strip_envelope(row)
        if error:
            corrupt_codes.append(code)
        elif not isinstance(analysis.get("data_quality"), Mapping):
            legacy_codes.append(code)
        positions.append({
            "code": code,
            "name": "",
            "planned": code in planned,
            "usable": False,
            "created_at": row.get("created_at"),
            "error": error,
            "analysis": analysis,
        })

    before_open = (now.hour, now.minute) < OPEN_TIME
    if weekend:
        state = "missing"
    elif is_today and before_open:
        state = "not_ready"
    elif not rows:
        state = "missing"
    elif len(corrupt_codes) == len(rows):
        state = "corrupt"
    elif corrupt_codes or legacy_codes:
        state = "partial"
    else:
        state = "ready"

    usable_rows = state in ("ready", "partial")
    for position in positions:
        position["usable"] = bool(
            usable_rows and position["error"] is None)

    envelope["positions"] = positions
    envelope["corrupt_codes"] = sorted(corrupt_codes)
    envelope["legacy_schema_codes"] = sorted(legacy_codes)
    envelope["pre_ready_rows_present"] = bool(rows) and state == "not_ready"
    envelope["state"] = state
    envelope["available"] = state in ("ready", "partial")
    return envelope


def load_local_trade_days() -> list[str]:
    """本地 ``assets.db.dragons`` 镜像交易日（``proven_gap`` 单边证据）。

    只读、零 DDL；镜像不可读 / 缺表时返回 ``[]``（离线无法证明 gap）。
    """

    from dragon_quant.infra.assets import dragons_mirror

    try:
        return dragons_mirror.list_dragon_trade_dates(source="v2",
                                                      readonly=True)
    except sqlite3.Error:
        return []


__all__ = [
    "AUCTION_SUPPORTED_SCHEMA",
    "PREP_SUPPORTED_SCHEMA",
    "load_auction",
    "load_local_trade_days",
    "load_prep",
]
