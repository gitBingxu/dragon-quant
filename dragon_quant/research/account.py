"""``research account`` / ``research signals`` 用例（只读、不触发回测）。"""

from __future__ import annotations

from typing import Optional

from .envelope import (
    InvalidRequest,
    envelope,
    normalize_int,
)
from .query import read_with_status, run_query

_PAGE = 1000
_MAX_LEDGER_ROWS = 200_000


def _page_all(loader, *, max_rows: int = _MAX_LEDGER_ROWS, **filters
             ) -> tuple[list[dict], bool]:
    """Internal paging for metrics: owner reads cap single-call limits.

    Returns ``(rows, truncated)``; reaching ``max_rows`` on a full page means the
    ledger is incomplete and account metrics must not be claimed complete.
    """
    rows: list[dict] = []
    offset = 0
    truncated = False
    while len(rows) < max_rows:
        budget = min(_PAGE, max_rows - len(rows))
        page = loader(limit=budget, offset=offset, **filters)
        if not page:
            break
        rows.extend(page[:budget])
        offset += len(page)
        if len(page) < budget:
            break
        if len(rows) >= max_rows:
            truncated = True
    return rows, truncated


def _load_run(run_id: int):
    """Return ``(run | None, reason, warnings)`` with unified read-only mapping."""
    from dragon_quant.review_account import research_reads

    rows, reason, warnings = read_with_status(
        research_reads.account_runs, run_id=run_id, limit=1, offset=0)
    return (rows[0] if rows else None), reason, warnings


def _run_not_found(run_id: int, reason: str | None = None) -> dict:
    from dragon_quant.infra.storage import paths

    reason = reason or (
        "run_not_found" if paths.DB_PATH.is_file() else "database_missing")
    warning = ("run_id 不在 review_account_runs；不构造空账户冒充结果"
               if reason == "run_not_found" else
               "本地账户表不可用或结构过旧；不构造空账户冒充结果")
    return envelope(
        command="research account",
        request={"run_id": run_id, "view": "summary"},
        data={"run_id": run_id, "available": False},
        ok=True,
        meta={
            "data_quality": "degraded",
            "empty_reason": reason,
            "returned": 0,
            "truncated": False,
            "next_cursor": None,
        },
        warnings=[warning],
    )


def account_summary(run_id: int) -> dict:
    from dragon_quant.review_account import evaluation, research_reads

    run, reason, read_warnings = _load_run(run_id)
    if run is None:
        return _run_not_found(run_id, reason)
    snapshots, s_trunc = _page_all(research_reads.account_snapshots, run_id=run_id)
    trades, t_trunc = _page_all(research_reads.account_trades, run_id=run_id)
    positions, p_trunc = _page_all(research_reads.account_positions, run_id=run_id)
    events, e_trunc = _page_all(research_reads.account_events, run_id=run_id)
    ledger_truncated = s_trunc or t_trunc or p_trunc or e_trunc
    metrics_run = dict(run)
    metrics_run["id"] = run.get("run_id")
    summary = evaluation.metrics_from_rows(metrics_run, snapshots, trades, positions)
    warnings = list(read_warnings)
    if run.get("_corrupt"):
        warnings.append(
            "corrupt_json_fields=" + ",".join(run["_corrupt"])
            + "：相关字段按默认值展示，账户摘要不宣称完整")
    quality = (run.get("run_meta") or {}).get("data_quality")
    if quality and quality != "ok":
        warnings.append(f"run_meta.data_quality={quality}；收益口径可能降级")
    if not run.get("start_state_recorded") and run.get("base_run_id"):
        warnings.append("增量批次缺少可验证期初状态记录；不可复现性由原始批次承担")
    if ledger_truncated:
        warnings.append(
            f"账本达到上限 {_MAX_LEDGER_ROWS} 行；账户指标仅为部分账本，不能宣称完整")
    run_out = dict(run)
    corrupt_fields = run_out.pop("_corrupt", None)
    if corrupt_fields:
        run_out["corrupt_json_fields"] = corrupt_fields
    return envelope(
        command="research account",
        request={"run_id": run_id, "view": "summary"},
        data={
            "run": run_out,
            "summary": summary,
            "counts": {
                "snapshots": len(snapshots),
                "trades": len(trades),
                "positions": len(positions),
                "events": len(events),
            },
            "ledger_truncated": ledger_truncated,
            "caliber": {
                "return": "run.total_return（既有 evaluation 口径）",
                "win_rate": "已平仓持仓 realized_return>0 占比；分母不为 0 才给值",
                "contribution": summary.get("contribution_basis"),
                "fees_stamp_tax_included": True,
                "not_broker_statement": True,
            },
        },
        ok=True,
        warnings=warnings,
        meta={
            "data_quality": ("degraded" if (read_warnings or run.get("_corrupt")
                                            or ledger_truncated)
                             else ("ok" if snapshots or trades or positions
                                   else "empty")),
            "ledger_truncated": ledger_truncated,
            "empty_reason": None if (snapshots or trades or positions)
            else "run_has_no_rows",
            "returned": 1,
            "truncated": False,
            "next_cursor": None,
            "caveats": ["账户结果是回测撮合，不是券商成交或对账单"],
        },
    )


_VIEW_DATASETS = {
    "snapshots": "account_snapshots",
    "trades": "account_trades",
    "positions": "account_positions",
    "events": "account_events",
}


def account_view(run_id: int, view: str, *, limit: int | None = None,
                 cursor: str | None = None) -> dict:
    if view == "summary":
        return account_summary(run_id)
    dataset = _VIEW_DATASETS.get(view)
    if dataset is None:
        raise InvalidRequest(
            f"--view 只接受 summary/{'/'.join(_VIEW_DATASETS)}（收到 {view!r}）")
    run, reason, warnings = _load_run(run_id)
    if run is None:
        return _run_not_found(run_id, reason)
    payload = run_query(name=dataset, filters={"run_id": run_id},
                        limit=limit, cursor=cursor)
    payload["command"] = "research account"
    payload["request"]["run_id"] = run_id
    payload["request"]["view"] = view
    if warnings:
        payload["warnings"] = list(payload.get("warnings") or []) + warnings
        payload["meta"]["data_quality"] = "degraded"
    return payload


def signals(*, date_from: Optional[str] = None, date_to: Optional[str] = None,
            code: Optional[str] = None, status: str = "all",
            limit: int | None = None, cursor: str | None = None) -> dict:
    payload = run_query(name="signals", filters={
        "date_from": date_from, "date_to": date_to,
        "code": code, "status": status,
    }, limit=limit, cursor=cursor)
    payload["command"] = "research signals"
    payload["meta"]["signal_scope"] = {
        "not_broker_fills": True,
        "half_leg_available": True,
        "diagnostics": "unavailable_unless_stored",
        "source": "buy_sell_signals",
    }
    payload["warnings"] = list(payload.get("warnings") or []) + [
        "信号是历史记录，不运行 buy/sell 生成新记录",
        "数据库未存过的诊断字段不可查询（unavailable）",
    ]
    return payload


def validate_run_id(raw) -> int:
    value = normalize_int(raw, "--run-id")
    if value is None:
        raise InvalidRequest("缺少 --run-id")
    return value
