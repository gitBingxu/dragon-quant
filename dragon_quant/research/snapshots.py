"""``research snapshots``：已存 prep / auction 快照只读回放。"""

from __future__ import annotations

from .envelope import InvalidRequest, normalize_code, normalize_date, validate_range
from .query import run_query

_KINDS = {"prep": "prep_snapshots", "auction": "auction_snapshots"}


def snapshots(*, kind: str, date: str | None = None, date_from: str | None = None,
              date_to: str | None = None, code: str | None = None,
              limit: int | None = None, cursor: str | None = None) -> dict:
    dataset = _KINDS.get(kind)
    if dataset is None:
        raise InvalidRequest(f"--kind 只接受 prep/auction（收到 {kind!r}）")
    if date:
        normalized = normalize_date(date, "--date")
        date_from = normalized
        date_to = normalized
    else:
        date_from = normalize_date(date_from, "--from")
        date_to = normalize_date(date_to, "--to")
    validate_range(date_from, date_to)
    code = normalize_code(code)
    payload = run_query(name=dataset, filters={
        "date_from": date_from, "date_to": date_to, "code": code,
    }, limit=limit, cursor=cursor)
    payload["command"] = "research snapshots"
    payload["request"].update({"kind": kind, "date": date})
    payload["meta"]["snapshot_kind"] = kind
    payload["meta"]["frozen_source"] = "stored_snapshot_only"
    payload["warnings"] = list(payload.get("warnings") or []) + [
        "缺失快照不采集、不重算；只返回已存内容",
        "payload 大字段默认不返回，需用 --fields result / --fields analysis 显式选择（通过 query）",
    ]
    return payload
