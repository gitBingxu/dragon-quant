"""Workspace：用户策略目录、选择持久化与冻结（阶段 3）。

公共门面在 :mod:`dragon_quant.workspace.main`。本包导入只解析路径/类型，
不创建目录、不导入用户代码。
"""

from __future__ import annotations

from dragon_quant.workspace.config import WorkspaceConflict, WorkspaceError
from dragon_quant.workspace.main import (
    StrategySelection,
    check_workspace,
    current_selection,
    freeze_strategy,
    init_workspace,
    list_strategies,
    resolve_selection,
    select_strategy,
    validate_strategy,
)

__all__ = [
    "StrategySelection",
    "WorkspaceConflict",
    "WorkspaceError",
    "check_workspace",
    "current_selection",
    "freeze_strategy",
    "init_workspace",
    "list_strategies",
    "resolve_selection",
    "select_strategy",
    "validate_strategy",
]
