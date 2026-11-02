"""premarket — 只读盘前上下文（Facade / Adapter / 纯投影）。

阶段 3 实现；本包不构造 provider、不触网、不重算、不写库、不新增表。
"""

from __future__ import annotations

from .context import CONTEXT_VERSION, DISCLAIMER, build_context
from .main import PremarketError, run_premarket
from .report import render_report

__all__ = [
    "CONTEXT_VERSION",
    "DISCLAIMER",
    "PremarketError",
    "build_context",
    "render_report",
    "run_premarket",
]
