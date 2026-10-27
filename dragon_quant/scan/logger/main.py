"""``scan.logger`` 公共门面（唯一批准入口）。

- ``ScanLogger`` / ``LogEntry`` — 结构化日志写入与内存查询；
- ``ReportBuilder`` — 自然语言报告（只读日志，不写库/文件）；
- ``tail_logs`` / ``query_logs`` / ``clear_logs`` / ``list_logs`` /
  ``log_summary`` — 面向 agent 的日志查询，统一经 ``scan.storage.logs``。

包 ``__init__`` 保持零 eager；新代码必须 import ``dragon_quant.scan.logger.main``。
"""

from __future__ import annotations

from dragon_quant.scan.logger.events import LogEntry, ScanLogger
from dragon_quant.scan.logger.query import (
    clear_logs,
    list_logs,
    log_summary,
    query_logs,
    tail_logs,
)
from dragon_quant.scan.logger.report import ReportBuilder

__all__ = [
    "ScanLogger",
    "LogEntry",
    "ReportBuilder",
    "tail_logs",
    "query_logs",
    "clear_logs",
    "list_logs",
    "log_summary",
]
