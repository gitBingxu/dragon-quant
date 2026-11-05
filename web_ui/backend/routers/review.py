"""龙头回测增量作业端点：只读状态 + 显式 POST 触发（写操作委托子进程）。

边界：
  - ``GET /api/review/jobs/status`` 纯只读，不启动任何任务；
  - ``POST /api/review/jobs/incremental`` 是显式写操作：HTTP 处理函数不写库，
    只委派 ``ReviewJobRunner`` 启动 ``python -m dragon_quant review
    --source v2 --recompute-all`` 子进程（写入发生在子进程内）；
  - 单飞：运行中重复触发返回 409；拒绝跨站 ``Origin``（403）；
  - 错误一律中文，便于前端直接展示。
"""

from __future__ import annotations

from functools import wraps

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..deps import review_runner
from ..http_guards import origin_allowed
from ..review_runner import ReviewJobBusy, parse_review_summary

router = APIRouter(tags=["review"])


def stable_errors(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 单键 500，沿用既有错误处理器
            return JSONResponse(status_code=500, content={"error": str(exc)})

    return wrapper


def _job_payload(runner) -> dict:
    snapshot = runner.status()
    last_run = snapshot.get("last_run")
    # 作业状态（进程是否成功）与业务结果（逐条 processed/failed）分开：
    # CLI 对单条 error 仍正常退出，不能把 exit 0 当作业务全部成功。
    summary = (parse_review_summary(last_run.get("output_tail"))
               if last_run else None)
    return {
        "state": snapshot["state"],
        "last_run": last_run,
        "summary": summary,
    }


@router.get("/api/review/jobs/status", response_model=None)
@stable_errors
def review_job_status(runner=Depends(review_runner)):
    return _job_payload(runner)


@router.post("/api/review/jobs/incremental", response_model=None)
@stable_errors
def review_job_incremental(request: Request, runner=Depends(review_runner)):
    origin = request.headers.get("origin")
    if origin is not None and not origin_allowed(origin):
        return JSONResponse(status_code=403, content={
            "error": "invalid_origin",
            "detail": "只允许本机 dashboard 页面触发回测",
        })
    try:
        runner.start_incremental(trigger="manual")
    except ReviewJobBusy:
        payload = _job_payload(runner)
        return JSONResponse(status_code=409, content={
            "error": "job_running",
            "detail": "已有回测任务在运行，请稍候",
            "state": payload["state"],
            "last_run": payload["last_run"],
        })
    except Exception as exc:  # noqa: BLE001 - spawn 同步失败（状态已置 failed）
        return JSONResponse(status_code=500, content={
            "error": "spawn_failed",
            "detail": f"回测任务启动失败：{type(exc).__name__}: {exc}",
        })
    return JSONResponse(status_code=202, content=_job_payload(runner))


__all__ = ["router"]
