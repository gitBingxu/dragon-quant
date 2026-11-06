"""可点击判定（纯函数，无 I/O）。

输入服务端事实（当前时间 / 交易日 / 当日是否已完成 / 运行中的作业），输出
``enabled`` 与逐条 ``blockers``。前端只用这份结果做按钮状态与 hover 文案，
**不自己复算时间与交易日**；POST 时服务端会用同一函数复检。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..run_specs import JobSpec


def _clock_text(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def in_window(now: datetime, spec: JobSpec) -> bool:
    current = (now.hour, now.minute)
    return spec.window_start <= current < spec.window_end


def _non_trading_text(source: str) -> str:
    if source == "weekend":
        return "今天不是交易日（周末）"
    if source == "weekday_fallback":
        return "今天不是交易日（交易日历不可用，按工作日口径判定）"
    return "今天不是交易日"


def decide(
    *,
    spec: JobSpec,
    now: datetime,
    trading_day: bool,
    trading_day_source: str,
    already_done: bool,
    scan_done: bool,
    running_job: str | None,
    running_label: str | None,
) -> dict[str, Any]:
    """返回 ``{enabled, blockers, window}``；``blockers`` 是用户可读的中文原因。"""
    blockers: list[dict[str, str]] = []

    if not trading_day:
        blockers.append({
            "code": "non_trading_day",
            "text": _non_trading_text(trading_day_source),
        })

    if not in_window(now, spec):
        start = _clock_text(*spec.window_start)
        end = _clock_text(*spec.window_end)
        current = _clock_text(now.hour, now.minute)
        blockers.append({
            "code": "outside_window",
            "text": f"仅可在交易日 {start}–{end} 之间执行（当前 {current}）",
        })

    if spec.requires_scan and not scan_done:
        blockers.append({
            "code": "missing_scan",
            "text": "今日尚未执行龙头扫描，请先点击「龙头扫描」",
        })

    if already_done:
        blockers.append({"code": "already_done", "text": spec.done_text})

    if running_job:
        label = running_label or running_job
        blockers.append({
            "code": "job_running",
            "text": f"有其他任务正在运行：{label}；请等待完成后再试",
        })

    return {
        "enabled": not blockers,
        "blockers": blockers,
        "window": {
            "start": _clock_text(*spec.window_start),
            "end": _clock_text(*spec.window_end),
        },
    }


__all__ = ["decide", "in_window"]
