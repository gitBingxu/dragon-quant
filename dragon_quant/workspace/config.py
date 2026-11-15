"""Workspace 生命周期：元信息、选择持久化、稳定 ID、原子写入。

设计要点（方案 §13.1 / §13.4）：

- workspace 独立于 site-packages，显式 ``init`` 才创建；
- ``workspace.json`` 的修改使用内容 ``revision`` 比较并交换（CAS）：冲突时
  **保留原文件**并抛可操作错误，Web 层映射 409；
- 本机活动 workspace 只记在 ``DQ_DATA_DIR/workspace-selection.json``，仅在
  显式 ``init`` / ``use`` 时创建；导入/查询/help 不创建任何文件；
- 路径解析优先级：显式 ``--workspace`` > ``DQ_WORKSPACE`` > 本机登记。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dragon_quant.infra import clock

WORKSPACE_SCHEMA_VERSION = 1

#: 内建策略的 canonical ID（列表里只展示实际存在的实现）
BUILTIN_STRATEGY_ID = "builtin:dragon_pullback_daily"

WORKSPACE_FILE = "workspace.json"
SELECTION_FILE = "workspace-selection.json"


class WorkspaceError(ValueError):
    """workspace 未初始化 / 配置非法 / 选择冲突等域错误。"""


class WorkspaceConflict(WorkspaceError):
    """revision 冲突（并发修改）。Web 层映射 409。"""


def workspace_id_for(path: Path) -> str:
    """稳定 workspace_id（同一路径恒定，与目录内容无关）。"""
    resolved = str(Path(path).expanduser().resolve())
    return "ws_" + hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]


def short_workspace_id(workspace_id: str) -> str:
    """因子命名空间用的短标识（去掉 ``ws_`` 前缀）。"""
    return workspace_id[3:] if workspace_id.startswith("ws_") else workspace_id[:12]


def _relpath_safe(value: object, *, field_name: str) -> str:
    """校验 workspace.json 内的相对路径：拒绝绝对路径与 ``..`` 逃逸。"""
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceError(f"{field_name} 必须是非空字符串")
    candidate = Path(value)
    if candidate.is_absolute():
        raise WorkspaceError(f"{field_name} 必须是相对路径，拒绝绝对路径：{value}")
    if ".." in candidate.parts:
        raise WorkspaceError(f"{field_name} 不得逃逸 workspace：{value}")
    return value


@dataclass
class WorkspaceConfig:
    """``workspace.json`` 的内存表示。"""

    root: Path
    workspace_id: str
    revision: int
    selected_strategy_id: str = BUILTIN_STRATEGY_ID
    selected_parameters: dict = field(default_factory=dict)
    strategies: list[str] = field(default_factory=list)
    factors: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": WORKSPACE_SCHEMA_VERSION,
            "workspace_id": self.workspace_id,
            "revision": self.revision,
            "selected_strategy_id": self.selected_strategy_id,
            "selected_parameters": dict(self.selected_parameters),
            "strategies": list(self.strategies),
            "factors": list(self.factors),
        }

    def descriptor_paths(self) -> list[Path]:
        return [self.root / rel for rel in self.strategies]

    def factor_paths(self) -> list[Path]:
        return [self.root / rel for rel in self.factors]


def _parse_config(root: Path, data: object) -> WorkspaceConfig:
    if not isinstance(data, dict):
        raise WorkspaceError("workspace.json 必须是 JSON 对象")
    if data.get("schema_version") != WORKSPACE_SCHEMA_VERSION:
        raise WorkspaceError(
            f"workspace.json schema_version 必须为 {WORKSPACE_SCHEMA_VERSION}，"
            f"得到 {data.get('schema_version')!r}")
    workspace_id = data.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id.startswith("ws_"):
        raise WorkspaceError("workspace.json 缺少合法 workspace_id")
    if workspace_id != workspace_id_for(root):
        # 复制目录后 ID 与路径不一致会让跨 workspace 身份混淆；明确拒绝。
        raise WorkspaceError(
            "workspace_id 与目录路径不一致（疑似复制了其他 workspace）；"
            "请重新执行 workspace init 或修正 workspace_id")
    revision = data.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise WorkspaceError("workspace.json revision 必须是正整数")
    selected = data.get("selected_strategy_id", BUILTIN_STRATEGY_ID)
    if not isinstance(selected, str) or not selected.strip():
        raise WorkspaceError("selected_strategy_id 必须是非空字符串")
    parameters = data.get("selected_parameters") or {}
    if not isinstance(parameters, dict):
        raise WorkspaceError("selected_parameters 必须是对象")
    strategies = data.get("strategies") or []
    if not isinstance(strategies, list):
        raise WorkspaceError("strategies 必须是数组")
    factors = data.get("factors") or []
    if not isinstance(factors, list):
        raise WorkspaceError("factors 必须是数组")
    return WorkspaceConfig(
        root=root,
        workspace_id=workspace_id,
        revision=revision,
        selected_strategy_id=selected,
        selected_parameters=dict(parameters),
        strategies=[_relpath_safe(p, field_name="strategies[]") for p in strategies],
        factors=[_relpath_safe(p, field_name="factors[]") for p in factors],
        raw=dict(data),
    )


@contextmanager
def _exclusive_lock(path: Path):
    """跨进程互斥（锁文件 O_EXCL + 超时）；失败抛可操作错误，不静默丢写。"""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    deadline = time.monotonic() + 5.0
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise WorkspaceConflict(
                    f"无法获得写锁（{lock}）；另一个进程仍在写入，请稍后重试")
            time.sleep(0.02)
    try:
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(lock)
        except OSError:
            pass


def _atomic_write_json(path: Path, payload: dict) -> None:
    """原子写 JSON：同目录临时文件 + ``os.replace``；失败保留原文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def workspace_file(root: Path) -> Path:
    return Path(root) / WORKSPACE_FILE


def load_workspace(root: Path) -> WorkspaceConfig:
    """读取并校验 ``workspace.json``；缺失 → 未初始化错误。"""
    root = Path(root).expanduser()
    path = workspace_file(root)
    if not path.is_file():
        raise WorkspaceError(
            f"workspace 未初始化（缺少 {path}）；请先运行 workspace init")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"workspace.json 无法解析：{exc}") from exc
    return _parse_config(root, data)


def write_workspace(config: WorkspaceConfig, *, expected_revision: Optional[int] = None) -> WorkspaceConfig:
    """CAS 写入 ``workspace.json``：revision 不匹配 → 冲突且不改原文件。"""
    path = workspace_file(config.root)
    with _exclusive_lock(path):
        if not path.is_file():
            # 首次创建：调用方负责给出初版 revision；CAS 不允许对外声称已知版本。
            if expected_revision not in (None, 0):
                raise WorkspaceConflict("workspace 尚未创建，无法按 revision 更新")
            config.revision = config.revision or 1
            _atomic_write_json(path, config.to_dict())
            return config
        current = load_workspace(config.root)
        if expected_revision is not None and current.revision != expected_revision:
            raise WorkspaceConflict(
                f"workspace 已被其他操作修改（当前 revision={current.revision}，"
                f"期望 {expected_revision}）；请刷新后重试")
        config.revision = current.revision + 1
        _atomic_write_json(path, config.to_dict())
    return config


def create_workspace_config(root: Path) -> WorkspaceConfig:
    return WorkspaceConfig(
        root=Path(root).expanduser(),
        workspace_id=workspace_id_for(root),
        revision=1,
        selected_strategy_id=BUILTIN_STRATEGY_ID,
        selected_parameters={},
        strategies=[],
        factors=[],
    )


# ── 本机活动 workspace 登记（DQ_DATA_DIR/workspace-selection.json） ──


def selection_path() -> Path:
    from dragon_quant.infra.storage import paths

    return paths.DATA_DIR / SELECTION_FILE


def read_active_workspace() -> Optional[Path]:
    """读取本机登记的活动 workspace；文件缺失/损坏 → None（不创建任何文件）。"""
    path = selection_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("active_workspace")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value)


def write_active_workspace(root: Path) -> None:
    """登记本机活动 workspace（原子写 + 互斥）；只在显式 init/use 时调用。"""
    path = selection_path()
    resolved = str(Path(root).expanduser().resolve())
    with _exclusive_lock(path):
        _atomic_write_json(path, {
            "active_workspace": resolved,
            "updated_at": clock.stamp("%Y-%m-%dT%H:%M:%S"),
        })


def resolve_workspace_path(explicit: Optional[str] = None,
                           env: Optional[dict] = None) -> Optional[Path]:
    """路径解析优先级：显式 > ``DQ_WORKSPACE`` > 本机登记；都没有 → None。"""
    if explicit:
        return Path(explicit).expanduser()
    environ = os.environ if env is None else env
    from_env = environ.get("DQ_WORKSPACE")
    if from_env:
        return Path(from_env).expanduser()
    return read_active_workspace()
