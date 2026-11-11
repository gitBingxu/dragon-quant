"""research 公共 Facade：CLI / Web 共享的用例入口（不导出内部 SQL）。"""

from __future__ import annotations

from typing import Optional

from .account import account_view, validate_run_id
from .envelope import (
    InvalidRequest,
    ReadonlyUnavailable,
    envelope,
    error_envelope,
    normalize_date,
    validate_range,
)
from .query import datasets_payload, run_query, schema_payload
from .scan_quality import SCAN_QUALITY_VERSION, run_scan_quality
from .snapshots import snapshots as _snapshots
from .strategy import strategy as _strategy

__all__ = [
    "account",
    "datasets",
    "experiments",
    "query",
    "scan_quality",
    "schema",
    "signals",
    "snapshots",
    "strategy",
    "error_envelope",
    "handle_error",
]


def datasets() -> dict:
    data = datasets_payload()
    available = [item for item in data if item["availability"]["status"] == "available"]
    return envelope(
        command="research datasets",
        request={},
        data=data,
        meta={
            "data_quality": "ok",
            "catalog_version": "research_catalog_v1",
            "dataset_count": len(data),
            "available_count": len(available),
            "returned": len(data),
            "truncated": False,
            "next_cursor": None,
        },
        warnings=["dsn/凭据/任意系统表永不开放；未知 dataset 一律拒绝"],
    )


def schema(name: str) -> dict:
    data = schema_payload(name)
    return envelope(
        command="research schema",
        request={"dataset": name},
        data=data,
        meta={
            "catalog_version": "research_catalog_v1",
            "data_quality": "ok",
            "returned": 1,
            "truncated": False,
            "next_cursor": None,
        },
    )


def query(*, name: str, filters: Optional[dict] = None, fields: Optional[str] = None,
          limit: Optional[int] = None, cursor: Optional[str] = None) -> dict:
    return run_query(name=name, filters=filters, fields=fields, limit=limit,
                     cursor=cursor)


def scan_quality(*, date_from: str, date_to: str, include_samples: bool = False,
                 sample_limit: Optional[int] = None,
                 sample_cursor: Optional[str] = None) -> dict:
    from dragon_quant.infra.assets import research_reads as assets_reads
    from dragon_quant.infra.storage import paths
    from dragon_quant.scan.storage import research_reads as scan_reads

    start = normalize_date(date_from, "--from")
    end = normalize_date(date_to, "--to")
    validate_range(start, end)
    if not start or not end:
        raise InvalidRequest("scan-quality 需要 --from 与 --to")
    if not paths.DB_PATH.is_file():
        return envelope(
            command="research scan-quality",
            request={"date_from": start, "date_to": end},
            data=None,
            meta={"data_quality": "degraded", "empty_reason": "database_missing",
                  "data_quality_scope": "no_scan_database",
                  "returned": 0, "truncated": False, "next_cursor": None},
            warnings=["dragon.db 不存在；没有可研究的扫描记录"],
        )

    def _list_dates(low, high):
        rows = scan_reads.scan_dates(low, high)
        return rows if rows is not None else []

    def _contributions(day):
        rows = scan_reads.contributions_by_date(day)
        return rows if rows is not None else []

    def _load_bars(codes, low, high):
        return assets_reads.stock_daily_bars(codes, low, high)

    def _load_calendar(low, high):
        return assets_reads.stock_daily_bar_dates(low, high)

    payload = run_scan_quality(
        date_from=start, date_to=end, list_dates=_list_dates,
        contributions=_contributions, load_bars=_load_bars,
        load_calendar=_load_calendar, include_samples=include_samples)
    samples_meta = {"samples_returned": 0, "samples_truncated": False,
                    "samples_next_cursor": None}
    if include_samples:
        from .envelope import decode_cursor, encode_cursor

        records = payload.pop("samples", []) or []
        try:
            size = int(sample_limit) if sample_limit is not None else 100
        except (TypeError, ValueError) as exc:
            raise InvalidRequest(f"--sample-limit 必须是整数：{sample_limit!r}") from exc
        if size < 1 or size > 1000:
            raise InvalidRequest("--sample-limit 范围 1..1000")
        filters = {"date_from": start, "date_to": end}
        offset = decode_cursor(sample_cursor, dataset="scan_quality_samples",
                               filters=filters, fields=None)
        page = records[offset: offset + size]
        payload["samples"] = page
        samples_meta = {
            "samples_returned": len(page),
            "samples_truncated": offset + size < len(records),
            "samples_next_cursor": (
                encode_cursor(dataset="scan_quality_samples", filters=filters,
                              fields=None, offset=offset + size)
                if offset + size < len(records) else None),
            "samples_total": len(records),
        }
    status = payload.get("status")
    return envelope(
        command="research scan-quality",
        request={"date_from": start, "date_to": end},
        data=payload,
        meta={
            "data_quality": "ok" if status == "ok" else "degraded",
            "empty_reason": payload.get("reason"),
            "scan_quality_version": SCAN_QUALITY_VERSION,
            "returned": (payload.get("coverage") or {}).get("mature_samples", 0),
            "truncated": False,
            "next_cursor": None,
            "caveats": list(payload.get("caveats") or []),
            "sample_unit": payload.get("sample_unit"),
            "coverage": payload.get("coverage"),
            **samples_meta,
        },
        warnings=list(payload.get("read_errors") or []),
    )


def account(*, run_id: int, view: str = "summary", limit: Optional[int] = None,
            cursor: Optional[str] = None) -> dict:
    value = validate_run_id(run_id)
    return account_view(value, view, limit=limit, cursor=cursor)


def signals(*, date_from: Optional[str] = None, date_to: Optional[str] = None,
            code: Optional[str] = None, status: str = "all",
            limit: Optional[int] = None, cursor: Optional[str] = None) -> dict:
    from .account import signals as _signals

    return _signals(date_from=date_from, date_to=date_to, code=code,
                    status=status, limit=limit, cursor=cursor)


def snapshots(*, kind: str, date: Optional[str] = None,
              date_from: Optional[str] = None, date_to: Optional[str] = None,
              code: Optional[str] = None, limit: Optional[int] = None,
              cursor: Optional[str] = None) -> dict:
    return _snapshots(kind=kind, date=date, date_from=date_from, date_to=date_to,
                      code=code, limit=limit, cursor=cursor)


def strategy(*, kind: str, run_id: Optional[int] = None) -> dict:
    return _strategy(kind=kind, run_id=run_id)


class experiments:
    """命名空间：list / show / run（run 是唯一写动作，需 confirm）。"""

    @staticmethod
    def list(run_id: int, *, limit: Optional[int] = None,
             cursor: Optional[str] = None) -> dict:
        from .experiments import list_experiments

        return list_experiments(run_id, limit=limit, cursor=cursor)

    @staticmethod
    def show(experiment_id: int, *, run_id: Optional[int] = None) -> dict:
        from .experiments import show_experiment

        return show_experiment(experiment_id, run_id=run_id)

    @staticmethod
    def run(run_id: int, *, confirm: bool, sell_rules: Optional[list[str]] = None,
            lock_path=None, strategy_params: Optional[list] = None) -> dict:
        from .experiments import experiment_run

        return experiment_run(run_id, confirm=confirm, sell_rules=sell_rules,
                              lock_path=lock_path, strategy_params=strategy_params)


def handle_error(*, command: str, request: dict, exc: Exception) -> tuple[dict, int]:
    """Map domain exceptions to (structured envelope, exit code)."""
    from .envelope import ResearchError

    if isinstance(exc, ResearchError):
        return error_envelope(command=command, request=request, exc=exc), exc.exit_code
    if isinstance(exc, KeyError):
        payload = error_envelope(
            command=command, request=request,
            exc=InvalidRequest(str(exc).strip("'")))
        return payload, 2
    if isinstance(exc, ReadonlyUnavailable):
        return error_envelope(command=command, request=request, exc=exc), 1
    payload = error_envelope(
        command=command, request=request,
        exc=ReadonlyUnavailable(f"{type(exc).__name__}: {exc}"))
    payload["error"]["code"] = "read_failed"
    return payload, 1
