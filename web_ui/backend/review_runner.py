"""龙头回测增量作业：命令工厂 + 复用 ``BsRunner`` 状态机（标准库 only）。

Web 层**不 import** ``dragon_quant.review``、不建连接、不写库：所有写入都发生在
``python -m dragon_quant review --source v2 --recompute-all`` 子进程里。本模块
只组合既有单飞状态机（``BsRunner``），不复制锁 / 线程 / 输出截断实现。

语义：``--recompute-all`` 不带 ``--force`` —— 成熟 pending、partial 与严格侧表
缺失的记录会被补算，已有非 partial 严格结果跳过；未成熟记录保留，等待后续
补算。不会把全量强制重算伪装成增量。
"""

from __future__ import annotations

import re
import sys
from typing import Any, Callable, Optional, Sequence

from .bs_runner import BsJobBusy, BsRunner, JobMode, JobSnapshot, JobTrigger

__all__ = [
    "ReviewJobBusy",
    "ReviewJobRunner",
    "default_command",
    "parse_review_summary",
]

#: 复用既有作业忙异常（HTTP 层按 review 文案转 409）。
ReviewJobBusy = BsJobBusy


def default_command(mode: JobMode = "incremental") -> list[str]:
    """``python -m dragon_quant review --source v2 --recompute-all``。

    ``mode`` 由状态机保留（仅记录 incremental / force）；本作业**只有增量一种
    模式**，命令固定不带 ``--force``，避免覆盖既有严格结果。
    """
    _ = mode  # 作业语义固定为增量；保留签名以复用命令工厂协议。
    return [
        sys.executable, "-m", "dragon_quant", "review",
        "--source", "v2", "--recompute-all",
    ]


_SUMMARY_KEYS = ("processed", "completed", "cannot_evaluate", "failed",
                 "skipped")


def parse_review_summary(output: str | None) -> Optional[dict[str, int]]:
    """从既有 CLI 输出尾部解析 ``--recompute-all`` 的业务结果汇总。

    只读取 **已有** 输出契约（``📊 重算完成: processed=…`` 块），不新增命令
    参数或退出码语义。解析不到（旧版本 / 输出被截断）返回 ``None``，调用方
    必须展示中性结果而不是伪报全部成功。
    """
    if not output:
        return None
    marker = output.rfind("重算完成")
    if marker < 0:
        # 没有候选 / 全部未成熟时，CLI 提前返回而不打印数字汇总。
        # 只识别既有明确空结果文案，不把任意 exit 0 当作零失败。
        if ("没有需要重算的龙头记录" in output
                or "没有待回测的龙头记录" in output):
            summary = {key: 0 for key in _SUMMARY_KEYS if key != "skipped"}
            existing = re.search(r"跳过已有严格 (\d+) 条", output)
            if existing:
                summary["skipped"] = int(existing.group(1))
            return summary
        return None
    block = output[marker:]
    summary: dict[str, int] = {}
    for key in _SUMMARY_KEYS:
        match = re.search(rf"(?<![a-z_]){key}=(\d+)", block)
        if match:
            summary[key] = int(match.group(1))
    skipped = re.search(r"已有严格 (\d+) / 未成熟 (\d+)", block)
    if skipped:
        summary["skipped_existing"] = int(skipped.group(1))
        summary["skipped_immature"] = int(skipped.group(2))
    return summary if "processed" in summary else None


class ReviewJobRunner:
    """单飞龙头回测增量作业；``command_factory`` / ``popener`` 可注入（测试）。"""
    def __init__(
        self,
        *,
        command_factory: Callable[[JobMode], Sequence[str]] | None = None,
        popener: Callable[..., Any] | None = None,
        output_lines: int = 80,
        output_chars: int = 6000,
    ) -> None:
        # 组合既有状态机：单飞锁、running → succeeded/failed、输出尾部截断、
        # terminate 幂等语义全部复用，不在此复制。
        self._job = BsRunner(
            command_factory=command_factory or default_command,
            popener=popener,
            output_lines=output_lines,
            output_chars=output_chars,
        )

    def start_incremental(self, trigger: JobTrigger = "manual") -> JobSnapshot:
        return self._job.start_incremental(trigger)

    def status(self) -> dict[str, Any]:
        return self._job.status()

    def terminate(self, timeout: float = 3.0) -> None:
        self._job.terminate(timeout=timeout)
