"""命令运行端点：spec / 可点击判定 / 单飞状态 / 显式 POST。

边界与既有 ``bs`` / ``review`` 作业一致：

  - GET 全部只读：不启动任务、不写库；
  - ``POST /api/runs/{job}`` 是显式写操作：HTTP 处理函数不写库，只委派
    ``RunJobRunner`` 启动 ``python -m dragon_quant <command>`` 子进程；
  - 参数先按注册表白名单校验（400），再复检可点击判定（409），
    拒绝跨站 ``Origin``（403）；单飞冲突 409；
  - 三个命令共享一个作业位，与 BS / 回测作业互不影响。
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from ..deps import (
    clock_module,
    run_done_probe,
    run_runner,
    trade_calendar_probe,
)
from ..errors import ApiError
from ..http_guards import origin_allowed
from ..presenters import run_plan
from ..run_runner import RunJobBusy
from ..run_specs import (
    JobSpec,
    ParamError,
    UnknownJob,
    build_argv,
    get_spec,
    spec_payload,
    validate_params,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
router = APIRouter(tags=["runs"])


def _spec(job: str) -> JobSpec:
    try:
        return get_spec(job)
    except UnknownJob:
        raise ApiError(
            404, "not_found",
            f"未知任务：{job}（可选 scan / prep / auction）",
        ) from None


def _running_job(status: dict) -> tuple[str | None, str | None]:
    if status.get("state") != "running":
        return None, None
    return status.get("job"), status.get("label")


def _evaluate(
    *,
    spec: JobSpec,
    clock,
    calendar,
    done_probe,
    runner,
) -> dict:
    """组装一次判定所需的全部服务端事实（GET 与 POST 共用）。"""
    now = clock.now(SHANGHAI)
    today = now.strftime("%Y-%m-%d")
    trading_day, source = calendar.lookup(now)
    already_done = bool(done_probe(spec.name, today))
    # prep 依赖当日扫描：扫描产出当日龙头池后复盘才有意义（CLI 会静默回退
    # 到最新龙头日，UI 必须有明确前置，避免复盘到昨天）。只有需要该前置的
    # job 才多查一次，竞价页的 30s 状态轮询不做无谓读库。
    if spec.name == "scan":
        scan_done = already_done
    elif spec.requires_scan:
        scan_done = bool(done_probe("scan", today))
    else:
        scan_done = False
    status = runner.status()
    running_job, running_label = _running_job(status)
    decision = run_plan.decide(
        spec=spec,
        now=now,
        trading_day=trading_day,
        trading_day_source=source,
        already_done=already_done,
        scan_done=scan_done,
        running_job=running_job,
        running_label=running_label,
    )
    return {
        "job": spec.name,
        "enabled": decision["enabled"],
        "blockers": decision["blockers"],
        "window": decision["window"],
        "trading_day": trading_day,
        "trading_day_source": source,
        "already_done": already_done,
        "scan_done": scan_done,
        "server_now": now.isoformat(timespec="seconds"),
        "trade_date": today,
        "job_state": status["state"],
    }


def _status_payload(runner) -> dict:
    return runner.status()


@router.get("/api/runs/{job}/spec", response_model=None)
def run_spec(job: str):
    _spec(job)  # 未注册 → 404
    return spec_payload(job)


@router.get("/api/runs/{job}/eligibility", response_model=None)
def run_eligibility(
    job: str,
    clock=Depends(clock_module),
    calendar=Depends(trade_calendar_probe),
    done_probe=Depends(run_done_probe),
    runner=Depends(run_runner),
):
    spec = _spec(job)
    return _evaluate(
        spec=spec, clock=clock, calendar=calendar,
        done_probe=done_probe, runner=runner,
    )


@router.get("/api/runs/{job}/status", response_model=None)
def run_status(job: str, runner=Depends(run_runner)):
    _spec(job)  # 未注册 → 404
    return _status_payload(runner)


@router.post("/api/runs/{job}", response_model=None)
def run_start(
    job: str,
    request: Request,
    payload: dict | None = Body(default=None),
    clock=Depends(clock_module),
    calendar=Depends(trade_calendar_probe),
    done_probe=Depends(run_done_probe),
    runner=Depends(run_runner),
):
    origin = request.headers.get("origin")
    if origin is not None and not origin_allowed(origin):
        return JSONResponse(status_code=403, content={
            "error": "invalid_origin",
            "detail": "只允许本机 dashboard 页面触发命令",
        })

    spec = _spec(job)
    try:
        clean = validate_params(job, payload)
    except ParamError as exc:
        raise ApiError(400, "invalid_request", exc.message) from exc

    state = _evaluate(
        spec=spec, clock=clock, calendar=calendar,
        done_probe=done_probe, runner=runner,
    )
    if not state["enabled"]:
        return JSONResponse(status_code=409, content={
            "error": "not_eligible",
            "detail": "；".join(item["text"] for item in state["blockers"]),
            "blockers": state["blockers"],
            "trade_date": state["trade_date"],
        })

    argv = build_argv(job, clean, trade_date=state["trade_date"])
    try:
        runner.start(
            job=spec.name, label=spec.label, argv=argv, params=clean)
    except RunJobBusy:
        current = _status_payload(runner)
        return JSONResponse(status_code=409, content={
            "error": "job_running",
            "detail": "已有任务在运行，请等待完成后再试",
            "job": current.get("job"),
            "label": current.get("label"),
            "state": current.get("state"),
        })
    except Exception as exc:  # noqa: BLE001 - spawn 同步失败（状态已置 failed）
        return JSONResponse(status_code=500, content={
            "error": "spawn_failed",
            "detail": f"{type(exc).__name__}: {exc}",
        })
    return JSONResponse(status_code=202, content=_status_payload(runner))


__all__ = ["router"]
