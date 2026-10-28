"""复盘分（review）包公共面。

对外稳定符号：配置（唯一数字真源）、池解析、编排与六维评分。本包不做任何 I/O：
导入只加载纯 Python 模块（不读文件 / 环境变量 / 数据库，不发请求）。
"""

from __future__ import annotations

from .config import CONFIG, CONFIG_VERSION, ReviewConfig, config_hash
from .context import build_context
from .feedback import STATE_ACTIONS, compute_feedback, compute_plan_review
from .gates import evaluate_gates
from .orchestrator import REVIEW_VERSION, compute_review
from .pool import derive_anchors, derive_pool_codes
from .scoring import score_candidate
from .sizing import portfolio_summary, size_position

__all__ = [
    "CONFIG",
    "CONFIG_VERSION",
    "REVIEW_VERSION",
    "STATE_ACTIONS",
    "ReviewConfig",
    "build_context",
    "compute_feedback",
    "compute_plan_review",
    "compute_review",
    "config_hash",
    "derive_anchors",
    "derive_pool_codes",
    "evaluate_gates",
    "portfolio_summary",
    "score_candidate",
    "size_position",
]
