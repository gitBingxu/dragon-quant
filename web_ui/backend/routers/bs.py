"""BS 端点：只读 ``/api/bs/overview|buys|sells`` + 生成任务 ``jobs/status`` / ``rebuild``。

只读端点只经 ``deps`` 白名单取仓储/聚合/文案；不新建连接、不写 SQL。无 run /
无行返回 200 空数据；参数非法 400；仓储异常 500 单键。

``POST /api/bs/rebuild`` 是 Web 层唯一写操作例外：HTTP 处理函数不写库，只委派
``BsRunner`` 启动 ``python -m dragon_quant bs-eval --force`` 子进程，写入发生在
子进程内。
"""

from __future__ import annotations

from functools import wraps
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .. import params
from ..bs_runner import BsJobBusy
from ..deps import (
    bs_aggregate,
    bs_repository,
    bs_runner,
    bs_strategy_doc,
    dashboard_settings,
)
from ..errors import ApiError
from ..presenters import bs_view

router = APIRouter(tags=["bs"])

# 只放行本机 Origin；``Origin: null`` / 外部站点一律 403。
_ALLOWED_ORIGIN_HOSTS = {"localhost", "127.0.0.1", "::1"}


def stable_errors(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - 单键 500，沿用既有错误处理器
            return JSONResponse(status_code=500, content={"error": str(exc)})

    return wrapper


def _iso(raw: str | None, field: str) -> str | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return params.normalize_date(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", f"{field}: {exc}") from exc


def _common_filters(date_from, date_to, code, name, partial) -> dict:
    return {
        "date_from": _iso(date_from, "date_from"),
        "date_to": _iso(date_to, "date_to"),
        "code_like": (code or "").strip() or None,
        "name_like": (name or "").strip() or None,
        "exclude_partial": str(partial or "").strip().lower() == "exclude",
    }


def _strategy_payload(doc) -> dict:
    return {
        "version": doc.STRATEGY_DOC_VERSION,
        "buy_signals": doc.BUY_SIGNALS,
        "sell_rules": doc.SELL_RULES,
        "thresholds": doc.THRESHOLDS,
    }


def _origin_allowed(origin: str) -> bool:
    try:
        host = urlsplit(origin).hostname
    except ValueError:
        return False
    return bool(host) and host.lower() in _ALLOWED_ORIGIN_HOSTS


def _job_payload(runner, settings) -> dict:
    snapshot = runner.status()
    return {
        "state": snapshot["state"],
        "autogen_enabled": bool(getattr(settings, "bs_autogen", False)),
        "last_run": snapshot.get("last_run"),
    }


@router.get("/api/bs/jobs/status", response_model=None)
@stable_errors
def bs_jobs_status(runner=Depends(bs_runner),
                   settings=Depends(dashboard_settings)):
    return _job_payload(runner, settings)


@router.post("/api/bs/rebuild", response_model=None)
@stable_errors
def bs_rebuild(request: Request, runner=Depends(bs_runner),
               settings=Depends(dashboard_settings)):
    origin = request.headers.get("origin")
    if origin is not None and not _origin_allowed(origin):
        return JSONResponse(status_code=403, content={"error": "invalid_origin"})
    try:
        runner.start_force(trigger="manual")
    except BsJobBusy:
        payload = _job_payload(runner, settings)
        return JSONResponse(status_code=409, content={
            "error": "job_running",
            "detail": "BS 数据生成已在进行中",
            "state": payload["state"],
            "last_run": payload["last_run"],
        })
    except Exception as exc:  # noqa: BLE001 - spawn 同步失败（状态已置 failed）
        return JSONResponse(status_code=500, content={
            "error": "spawn_failed",
            "detail": f"{type(exc).__name__}: {exc}",
        })
    return JSONResponse(status_code=202, content=_job_payload(runner, settings))


@router.get("/api/bs/overview", response_model=None)
@stable_errors
def bs_overview(repo=Depends(bs_repository),
                doc=Depends(bs_strategy_doc),
                aggregate=Depends(bs_aggregate)):
    latest = repo.latest_run()
    if latest is None:
        return bs_view.present_overview(
            None, _strategy_payload(doc), aggregate.empty_summary(), 0)
    current_hash = latest["strategy_hash"]
    version = doc.EVALUATOR_VERSION
    rows = repo.list_current(current_hash, version)
    stale = repo.count_stale(current_hash, version)
    return bs_view.present_overview(
        latest, _strategy_payload(doc), aggregate.summarize(rows), stale)


@router.get("/api/bs/buys", response_model=None)
@stable_errors
def bs_buys(date_from: str | None = None, date_to: str | None = None,
            code: str | None = None, name: str | None = None,
            rule: str | None = None, partial: str | None = None,
            sort_by: str = "buy_date", sort_dir: str = "desc",
            repo=Depends(bs_repository),
            doc=Depends(bs_strategy_doc),
            aggregate=Depends(bs_aggregate)):
    latest = repo.latest_run()
    if latest is None:
        return {"data": [], "count": 0, "summary": aggregate.buy_summary([])}

    base = _common_filters(date_from, date_to, code, name, partial)
    rule_codes = []
    if rule and rule in doc.BUY_SIGNAL_LABELS:
        rule_codes = [rule]
    rows = repo.query_signals(
        side="buy", filters={**base, "rule_codes": rule_codes},
        sort_by=sort_by, sort_dir=sort_dir,
        strategy_hash=latest["strategy_hash"],
        evaluator_version=doc.EVALUATOR_VERSION,
    )
    base_rows = repo.query_signals(
        side="buy", filters=base, sort_by=sort_by, sort_dir=sort_dir,
        strategy_hash=latest["strategy_hash"],
        evaluator_version=doc.EVALUATOR_VERSION,
    )
    labels = bs_view.labels(doc)
    return {
        "data": [bs_view.present_buy_row(row, labels) for row in rows],
        "count": len(rows),
        "summary": aggregate.buy_summary(base_rows),
    }


@router.get("/api/bs/sells", response_model=None)
@stable_errors
def bs_sells(date_from: str | None = None, date_to: str | None = None,
             code: str | None = None, name: str | None = None,
             rule: str | None = None, partial: str | None = None,
             sort_by: str = "sell_date", sort_dir: str = "desc",
             repo=Depends(bs_repository),
             doc=Depends(bs_strategy_doc),
             aggregate=Depends(bs_aggregate)):
    latest = repo.latest_run()
    if latest is None:
        return {"data": [], "count": 0}

    base = _common_filters(date_from, date_to, code, name, partial)
    rule_codes = []
    if rule and rule in doc.SELL_RULE_LABELS:
        rule_codes = [
            reason for reason, rule_name in doc.SELL_REASON_TO_RULE.items()
            if rule_name == rule
        ]
    rows = repo.query_signals(
        side="sell", filters={**base, "rule_codes": rule_codes},
        sort_by=sort_by, sort_dir=sort_dir,
        strategy_hash=latest["strategy_hash"],
        evaluator_version=doc.EVALUATOR_VERSION,
    )
    labels = bs_view.labels(doc)
    return {
        "data": [
            bs_view.present_sell_row(row, labels, aggregate.sell_verdict(row))
            for row in rows
        ],
        "count": len(rows),
    }
