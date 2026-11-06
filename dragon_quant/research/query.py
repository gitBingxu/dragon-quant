"""``research query`` 用例：注册表校验 + 只读分页 + 可解释空态。"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from dragon_quant.infra.storage.readonly import ReadonlyUnavailable as _ReadonlyUnavailable
from dragon_quant.infra.storage.readonly import date_range, open_readonly

from .catalog import CLI_FILTER_ARGS, DatasetSpec, get_dataset, load_rows
from .envelope import (
    InvalidRequest,
    ReadonlyUnavailable,
    decode_cursor,
    encode_cursor,
    envelope,
    normalize_code,
    normalize_date,
    normalize_int,
    validate_range,
)

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


def normalize_filters(spec: DatasetSpec, raw: dict[str, Any]) -> dict:
    """Drop absent values, reject undeclared filters instead of ignoring them."""
    provided = {key: value for key, value in raw.items()
                if value is not None and not (isinstance(value, str) and value.strip() == "")}
    unknown = sorted(set(provided) - set(spec.filters))
    if unknown:
        names = ", ".join(f"{name} ({CLI_FILTER_ARGS.get(name, name)})"
                          for name in unknown)
        raise InvalidRequest(
            f"dataset {spec.name} 不支持过滤器：{names}；"
            f"支持的过滤器：{', '.join(spec.filters) or '无'}")
    normalized: dict = {}
    if "date_from" in provided:
        normalized["date_from"] = normalize_date(provided["date_from"], "--from")
    if "date_to" in provided:
        normalized["date_to"] = normalize_date(provided["date_to"], "--to")
    if "code" in provided:
        normalized["code"] = normalize_code(provided["code"])
    if "scan_id" in provided:
        normalized["scan_id"] = str(provided["scan_id"]).strip()
        if not normalized["scan_id"]:
            raise InvalidRequest("--scan-id 不能为空")
    if "run_id" in provided:
        normalized["run_id"] = normalize_int(provided["run_id"], "--run-id")
    if "experiment_id" in provided:
        normalized["experiment_id"] = normalize_int(
            provided["experiment_id"], "--experiment-id")
    if "status" in provided:
        status = str(provided["status"]).strip()
        if spec.statuses and status not in spec.statuses:
            raise InvalidRequest(
                f"dataset {spec.name} 的 --status 只接受：{', '.join(spec.statuses)}")
        normalized["status"] = status
    validate_range(normalized.get("date_from"), normalized.get("date_to"))
    missing = [name for name in spec.required_filters if name not in normalized]
    if missing:
        raise InvalidRequest(
            f"dataset {spec.name} 必须提供：{', '.join(CLI_FILTER_ARGS[n] for n in missing)}")
    return normalized


def normalize_fields(spec: DatasetSpec, raw: Optional[str]) -> tuple[list[str], list[str]]:
    """Return (selected fields, warnings). Unknown fields are rejected."""
    if not raw:
        return list(spec.default_field_names()), []
    names: list[str] = []
    for token in str(raw).replace(" ", ",").split(","):
        name = token.strip()
        if name and name not in names:
            names.append(name)
    if not names:
        raise InvalidRequest("--fields 不能为空")
    known = set(spec.field_names())
    unknown = sorted(set(names) - known)
    if unknown:
        raise InvalidRequest(
            f"dataset {spec.name} 未知字段：{', '.join(unknown)}；"
            f"可用字段见 research schema --dataset {spec.name}")
    return names, []


def _availability(spec: DatasetSpec) -> dict:
    from .catalog import db_path as resolve_db_path

    from dragon_quant.infra.storage.readonly import has_table

    path = resolve_db_path(spec)
    conn = open_readonly(path)
    if conn is None:
        return {"status": "database_missing", "reason": "数据库文件不存在（数据目录下尚未产生该库）",
                "db": spec.db, "table": spec.table, "date_range": None}
    try:
        if not has_table(conn, spec.table):
            return {"status": "table_missing",
                    "reason": f"表 {spec.table} 不存在（旧 schema 或尚未产生数据）",
                    "db": spec.db, "table": spec.table, "date_range": None}
        result = {"status": "available", "reason": None, "db": spec.db,
                  "table": spec.table, "date_range": None}
        if spec.date_field:
            try:
                low, high = date_range(conn, spec.table, spec.date_field)
                result["date_range"] = {"from": low, "to": high,
                                        "field": spec.date_field}
            except sqlite3.OperationalError:
                result["date_range"] = None
        return result
    finally:
        conn.close()


def datasets_payload() -> list[dict]:
    from .catalog import DATASETS

    result = []
    for spec in DATASETS.values():
        item = spec.as_dict()
        item["availability"] = _availability(spec)
        result.append(item)
    return result


def schema_payload(name: str) -> dict:
    try:
        spec = get_dataset(name)
    except KeyError as exc:
        raise InvalidRequest(f"未知数据集：{name}") from exc
    payload = spec.as_dict()
    payload["availability"] = _availability(spec)
    payload["keys"] = [field for field in ("scan_id", "trade_date", "code", "run_id",
                                           "signal_id", "experiment_id",
                                           "factor_name", "model_name")
                       if field in spec.field_names()]
    payload["related_datasets"] = _related(spec)
    return payload


def _related(spec: DatasetSpec) -> list[str]:
    related: list[str] = []
    names = spec.field_names()
    if "scan_id" in names:
        related.append("scan_runs")
        if spec.name != "scan_candidates":
            related.append("scan_candidates")
    if "run_id" in names and spec.name != "account_runs":
        related.append("account_runs")
    if "factor_name" in names and spec.name != "factor_registry":
        related.append("factor_registry")
    return related


def _degraded(spec: DatasetSpec, filters: dict, *, reason: str,
              data_quality: str = "degraded") -> dict:
    request = {"dataset": spec.name, "filters": filters}
    return envelope(
        command="research query", request=request, data=[], ok=True,
        meta={
            "dataset": spec.name,
            "data_quality": data_quality,
            "empty_reason": reason,
            "returned": 0,
            "truncated": False,
            "next_cursor": None,
            "caveats": list(spec.caveats),
            "fields": list(spec.default_field_names()),
        },
    )


def read_with_status(loader, **kwargs):
    """Run a dedicated read-only loader with one shared error mapping.

    Returns ``(result, reason, warnings)`` where ``reason`` distinguishes
    ``database_missing`` / ``table_missing`` / ``schema_mismatch`` / ``None``.
    Real faults (readonly unavailable, corrupt I/O) still raise.
    """
    try:
        result = loader(**kwargs)
    except _ReadonlyUnavailable as exc:
        raise ReadonlyUnavailable(str(exc)) from exc
    except sqlite3.OperationalError as exc:
        lowered = str(exc).lower()
        if "no such table" in lowered:
            return None, "table_missing", []
        if "no such column" in lowered:
            return None, "schema_mismatch", [
                "本地表结构缺少查询所需列（旧 schema）；请升级/重建后重试"]
        raise
    if isinstance(result, dict) and "rows" in result:
        return result.get("rows"), result.get("empty_reason"), list(
            result.get("warnings") or [])
    if result is None:
        return None, "database_missing", []
    return result, None, []


def _row_corrupt(row: dict) -> bool:
    if row.get("_corrupt"):
        return True
    return any(isinstance(value, dict) and "_corrupt" in value
               for value in row.values())


def run_query(*, name: str, filters: dict | None = None,
              fields: str | None = None, limit: int | None = None,
              cursor: str | None = None) -> dict:
    try:
        spec = get_dataset(name)
    except KeyError as exc:
        raise InvalidRequest(f"未知数据集：{name}") from exc
    normalized = normalize_filters(spec, dict(filters or {}))
    selected, warnings = normalize_fields(spec, fields)
    try:
        size = int(limit) if limit is not None else DEFAULT_LIMIT
    except (TypeError, ValueError) as exc:
        raise InvalidRequest(f"--limit 必须是整数：{limit!r}") from exc
    if size < 1 or size > MAX_LIMIT:
        raise InvalidRequest(f"--limit 范围 1..{MAX_LIMIT}（收到 {size}）")
    offset = decode_cursor(cursor, dataset=spec.name, filters=normalized,
                           fields=selected)
    request = {"dataset": spec.name, "filters": normalized,
               "fields": selected, "limit": size}
    large = {item.name for item in spec.fields if item.large}
    kwargs = dict(normalized)
    if any(name in large for name in selected):
        if spec.name in ("prep_snapshots", "auction_snapshots"):
            kwargs["include_payload"] = True
        elif spec.name == "factor_build_runs":
            kwargs["include_report"] = True
    rows, loader_reason, loader_warnings = read_with_status(
        lambda **call: load_rows(spec, limit=size + 1, offset=offset, **call),
        **kwargs)
    if loader_reason in ("database_missing", "table_missing", "schema_mismatch"):
        payload = _degraded(spec, normalized, reason=loader_reason)
        payload["warnings"] = list(payload["warnings"]) + loader_warnings
        return payload
    truncated = len(rows) > size
    page = rows[:size]
    corrupt_count = sum(1 for row in page if _row_corrupt(row))
    projected = [{field: row.get(field) for field in selected} for row in page]
    next_cursor = None
    if truncated:
        next_cursor = encode_cursor(dataset=spec.name, filters=normalized,
                                    fields=selected, offset=offset + size)
    if corrupt_count:
        quality = "degraded"
    else:
        quality = "ok" if projected else "empty"
    issue_warnings = list(spec.caveats) + loader_warnings
    if corrupt_count:
        issue_warnings.append(
            f"corrupt_json_rows={corrupt_count}: 损坏的 JSON 字段按默认值展示，"
            "结果不宣称完整")
    return envelope(
        command="research query", request=request, data=projected, ok=True,
        warnings=issue_warnings,
        meta={
            "dataset": spec.name,
            "data_quality": quality,
            "empty_reason": (None if projected
                             else loader_reason or "no_rows_in_scope"),
            "fields": selected,
            "returned": len(projected),
            "truncated": truncated,
            "next_cursor": next_cursor,
            "offset": offset,
            "caveats": list(spec.caveats),
        },
    )
