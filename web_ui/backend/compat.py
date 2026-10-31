"""兼容端点业务组装（旧 stdlib ``ReviewHandler`` 的逻辑原样搬运）。

键集与今天逐键一致；错误语义也原样保留：
- 冻结 GET 端点的 ``ValueError`` 仍是 500 ``{"error": <message>}``；
- 兼容 POST/DELETE 的 ``ValueError`` 映射为 400 单键。

本模块只接收仓储 / 服务对象作为参数，不自己 import 仓储实现（除 benchmark
的 provider 由调用方注入），方便 TestClient 用 ``dependency_overrides`` 替换。
"""

from __future__ import annotations

import json
from typing import Optional


# ─── 参数解析（逐字复用旧实现） ────────────────────────────────────────────


def _first(params, key: str) -> Optional[str]:
    if params is None:
        return None
    values = params.get(key)
    if isinstance(values, (list, tuple)):
        return values[0] if values else None
    return values


def parse_source(params, default: str = "v2") -> str:
    src = (_first(params, "source") or default or "v2").lower().strip()
    return src if src in {"v1", "v2"} else "v2"


def normalize_source_value(raw: str) -> str:
    src = (raw or "v2").lower().strip()
    return src if src in {"v1", "v2"} else "v2"


def normalize_api_date(raw: str) -> str:
    value = (raw or "").strip()
    if len(value) == 8 and value.isdigit():
        value = f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    if not value:
        return ""
    import datetime as dt

    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD or YYYYMMDD") from exc
    return parsed.strftime("%Y-%m-%d")


def parse_run_id(params) -> int:
    raw = _first(params, "run_id")
    if not raw:
        raise ValueError("missing run_id")
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError("invalid run_id") from exc


def parse_filters(params) -> dict:
    f: dict = {}

    code = _first(params, "code")
    if code:
        f["code_like"] = code.strip()

    name = _first(params, "name")
    if name:
        f["name_like"] = name.strip()

    df = _first(params, "date_from")
    if df:
        f["date_from"] = df.strip()

    dt = _first(params, "date_to")
    if dt:
        f["date_to"] = dt.strip()

    for key in ("score_min", "score_max", "return_min", "return_max",
                "drawdown_min", "drawdown_max"):
        raw = _first(params, key)
        if raw:
            try:
                f[key] = float(raw)
            except ValueError:
                pass

    status = _first(params, "status")
    if status:
        f["status"] = [s.strip() for s in status.split(",") if s.strip()]

    vmin = _first(params, "version_min")
    if vmin:
        f["version_min"] = vmin.strip()

    vmax = _first(params, "version_max")
    if vmax:
        f["version_max"] = vmax.strip()

    return f


# ─── dragons ───────────────────────────────────────────────────────────────


def dragons_payload(dragons, params, default_source: str = "v2") -> dict:
    filters = parse_filters(params)
    sort_by = _first(params, "sort_by") or "composite_score"
    sort_dir = _first(params, "sort_dir") or "desc"
    source = parse_source(params, default_source)
    caliber = dragons.resolve_caliber(_first(params, "review_caliber"), source)
    rows = dragons.query_dragons(
        filters, sort_by=sort_by, sort_dir=sort_dir, source=source, caliber=caliber,
    )
    # summary 必须基于筛选后的完整 rows（分页在客户端进行）。
    summary = dragons.summarize_review_rows(
        rows, source=source, include_derived_counts=True, caliber=caliber,
    )
    return {"data": rows, "count": len(rows), "summary": summary}


def legacy_summary(dragons, params, default_source: str = "v2") -> dict:
    source = parse_source(params, default_source)
    return dragons.get_review_summary(source=source)


# ─── account ───────────────────────────────────────────────────────────────


def account_runs(account, params, default_source: str = "v2") -> dict:
    source = parse_source(params, default_source)
    try:
        limit = int(_first(params, "limit") or "20")
    except ValueError:
        limit = 20
    rows = account.query_review_account_runs(limit=limit, source=source)
    return {"data": rows, "count": len(rows)}


def create_account_run(payload: dict, *, default_source: str,
                       run_account, account) -> dict:
    payload = payload if isinstance(payload, dict) else {}

    date_from = normalize_api_date(str(payload.get("date_from") or "").strip())
    date_to = normalize_api_date(str(payload.get("date_to") or "").strip())
    if not date_from or not date_to:
        raise ValueError("date_from and date_to are required")
    if date_from > date_to:
        raise ValueError("date_from must be earlier than date_to")

    source = normalize_source_value(
        str(payload.get("source") or default_source or "v2")
    )
    strategy_name = str(payload.get("strategy_name") or "dragon_pullback_daily").strip()
    if not strategy_name:
        strategy_name = "dragon_pullback_daily"

    display_name = str(payload.get("name") or payload.get("display_name") or "").strip()
    if not display_name:
        display_name = f"{date_from} ~ {date_to}"

    raw_cash = payload.get("initial_cash", 100000.0)
    if raw_cash in (None, ""):
        raw_cash = 100000.0
    try:
        initial_cash = float(raw_cash)
    except (TypeError, ValueError) as exc:
        raise ValueError("initial_cash must be a number") from exc
    if initial_cash <= 0:
        raise ValueError("initial_cash must be greater than 0")

    result = run_account(
        date_from=date_from,
        date_to=date_to,
        initial_cash=initial_cash,
        source=source,
        strategy_name=strategy_name,
        verbose=False,
        display_name=display_name,
        strategy_params=payload.get("strategy_params"),
    )
    run = account.get_review_account_run(int(result["run_id"]))
    return {"data": run, "run_id": result["run_id"]}


def delete_account_run(account, params) -> dict:
    run_id = parse_run_id(params)
    deleted = account.delete_review_account_run(run_id)
    if not deleted:
        return {"error": "run not found"}
    return {"deleted": True, "run_id": run_id}


def account_snapshots(account, params) -> dict:
    run_id = parse_run_id(params)
    return {"data": account.query_review_account_snapshots(run_id)}


def account_trades(account, params) -> dict:
    run_id = parse_run_id(params)
    return {"data": account.query_review_account_trades(run_id)}


def account_events(account, params) -> dict:
    run_id = parse_run_id(params)
    return {"data": account.query_review_account_events(run_id)}


def account_positions(account, evaluation, params) -> dict:
    run_id = parse_run_id(params)
    rows = account.query_review_account_positions(run_id)
    run = account.get_review_account_run(run_id)
    stats = evaluation.entry_reason_stats(rows, (run or {}).get("initial_cash") or 0.0)
    return {"data": rows, "stats": stats}


def account_benchmark(account, provider, params) -> dict:
    run_id = parse_run_id(params)
    code = (_first(params, "code") or "SH000001").upper()
    run = account.get_review_account_run(run_id)
    if not run:
        return {"error": "run not found"}
    snapshots = account.query_review_account_snapshots(run_id)
    rows = build_benchmark_series(
        provider,
        code=code,
        dates=[s["trade_date"] for s in snapshots],
        initial_cash=run["initial_cash"] or 0,
    )
    return {"data": rows, "code": code, "name": "上证指数"}


def build_benchmark_series(provider, code: str, dates: list[str],
                           initial_cash: float) -> list[dict]:
    """按账户快照日期生成指数等效权益曲线（原样搬运）。"""
    if not dates or initial_cash <= 0:
        return []
    from datetime import datetime

    days = max(60, len(dates) + 30)
    klines = provider.get_kline(code, days=days, fq_type="normal")
    close_by_date = {
        datetime.fromtimestamp(k.timestamp / 1000).strftime("%Y-%m-%d"): k.close
        for k in klines
    }
    first_close = None
    rows = []
    last_close = None
    for d in dates:
        close = close_by_date.get(d) or last_close
        if close is None:
            continue
        if first_close is None:
            first_close = close
        last_close = close
        cumulative_return = (close / first_close - 1) * 100 if first_close else 0.0
        rows.append({
            "trade_date": d,
            "close": close,
            "total_equity": initial_cash * close / first_close if first_close else initial_cash,
            "cumulative_return": cumulative_return,
        })
    return rows


def dump_json(payload) -> str:
    """兼容测试 / 调试的稳定 JSON 文本。"""
    return json.dumps(payload, ensure_ascii=False)
