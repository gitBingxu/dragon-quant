"""用户策略加载：显式执行路径才导入 Python，唯一模块名避免跨 workspace 串缓存。

``list`` / ``-h`` / 普通查询永不经过本模块（方案 §13.2）。只有 ``validate``、
``buy``、``sell``、``review-account`` 等显式执行路径才导入用户代码。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Optional

from dragon_quant.strategy_sdk import SDK_VERSION, StrategyIntent
from dragon_quant.workspace.catalog import StrategyDescriptor
from dragon_quant.workspace.config import (
    WorkspaceConfig,
    WorkspaceError,
    short_workspace_id,
)


def _module_name(config: WorkspaceConfig, descriptor: StrategyDescriptor) -> str:
    slug = descriptor.path.parent.name if descriptor.path else "strategy"
    return f"dq_ws_{short_workspace_id(config.workspace_id)}_{slug}"


def _validate_parameter_value(descriptor_id: str, name: str, spec: dict, value) -> None:
    kind = spec.get("type")
    if kind == "bool":
        ok = isinstance(value, bool)
    elif kind == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "number":
        import math
        ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
              and math.isfinite(float(value)))
    elif kind == "string":
        ok = isinstance(value, str)
    else:
        ok = True  # schema 未声明类型时只做未知键校验
    if not ok:
        raise WorkspaceError(
            f"策略 {descriptor_id} 参数 {name} 类型必须是 {kind}，得到 {type(value).__name__}")
    if kind in ("number", "int"):
        if "min" in spec and value < spec["min"]:
            raise WorkspaceError(
                f"策略 {descriptor_id} 参数 {name} 不能小于 {spec['min']}")
        if "max" in spec and value > spec["max"]:
            raise WorkspaceError(
                f"策略 {descriptor_id} 参数 {name} 不能大于 {spec['max']}")
    enum = spec.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise WorkspaceError(
            f"策略 {descriptor_id} 参数 {name} 必须是 {enum} 之一，得到 {value!r}")


def merge_parameters(descriptor: StrategyDescriptor, *layers: Optional[dict]) -> dict:
    """参数优先级：descriptor 默认 < 各层覆盖；按 schema 校验类型/范围/枚举。"""
    schema = descriptor.parameters or {}
    merged = {}
    for name, spec in schema.items():
        if isinstance(spec, dict) and "default" in spec:
            _validate_parameter_value(descriptor.id, name, spec, spec["default"])
            merged[name] = spec["default"]
    for layer in layers:
        if not layer:
            continue
        if not isinstance(layer, dict):
            raise WorkspaceError("策略参数必须是 JSON 对象")
        for key, value in layer.items():
            if schema and key not in schema:
                raise WorkspaceError(
                    f"策略 {descriptor.id} 未知参数 {key!r}；"
                    f"可用参数：{', '.join(sorted(schema))}")
            if key in schema:
                _validate_parameter_value(descriptor.id, key, schema[key], value)
            merged[key] = value
    return merged


def load_strategy_module(config: WorkspaceConfig, descriptor: StrategyDescriptor,
                         *, keep_workspace_root: bool = False):
    """加载并返回用户策略模块（带唯一模块名，helpers 可在模块级/调用期 import）。

    ``keep_workspace_root=True`` 用于独立 worker 进程：在整个 worker 生命周期保留
    workspace 根，使 ``evaluate_*`` 内的 lazy ``import helpers`` 可用。HTTP/CLI 父
    进程不应使用该模式（父进程不执行用户代码）。
    """
    if descriptor.source != "user" or descriptor.path is None:
        raise WorkspaceError(f"{descriptor.id} 不是用户策略，不能按用户模块加载")
    module_path = descriptor.path.parent / "strategy.py"
    if not module_path.is_file():
        raise WorkspaceError(f"策略 {descriptor.id} 缺少 strategy.py")
    try:
        root_resolved = Path(descriptor.path).parent.parent.parent.resolve()
        resolved = module_path.resolve()
    except OSError:
        root_resolved = resolved = None
    if root_resolved is not None and resolved is not None and (
            resolved != root_resolved and root_resolved not in resolved.parents):
        raise WorkspaceError(f"策略 {descriptor.id} 的 strategy.py 路径逃逸 workspace")
    name = _module_name(config, descriptor)
    # 不跨执行复用 sys.modules 缓存：同一进程内用户改源码后，必须执行新代码，
    # 否则冻结快照哈希（读盘）与实际执行版本会不一致。
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise WorkspaceError(f"无法加载策略模块：{module_path}")
    module = importlib.util.module_from_spec(spec)
    root = str(config.root)
    added = root not in sys.path
    if added:
        sys.path.insert(0, root)  # 允许 strategy.py 在模块级 import workspace/helpers
    try:
        sys.modules[name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(name, None)
        raise WorkspaceError(
            f"策略 {descriptor.id} 导入失败：{type(exc).__name__}: {exc}") from exc
    finally:
        if added and not keep_workspace_root:
            try:
                sys.path.remove(root)
            except ValueError:
                pass
    return module


def create_strategy(config: WorkspaceConfig, descriptor: StrategyDescriptor,
                    parameters: Optional[dict] = None, *,
                    keep_workspace_root: bool = False):
    """构造用户策略实例并校验协议；任何异常带策略 ID 显式失败。"""
    if descriptor.sdk_version != SDK_VERSION:
        raise WorkspaceError(
            f"策略 {descriptor.id} SDK 版本 {descriptor.sdk_version} 与当前 "
            f"{SDK_VERSION} 不兼容")
    merged = merge_parameters(descriptor, parameters)
    module = load_strategy_module(config, descriptor,
                                  keep_workspace_root=keep_workspace_root)
    factory = getattr(module, descriptor.entrypoint or "create_strategy", None)
    if not callable(factory):
        raise WorkspaceError(
            f"策略 {descriptor.id} 缺少工厂函数 "
            f"{descriptor.entrypoint or 'create_strategy'}(params)")
    try:
        instance = factory(merged)
    except Exception as exc:
        raise WorkspaceError(
            f"策略 {descriptor.id} 工厂执行失败：{type(exc).__name__}: {exc}") from exc
    for method in ("evaluate_entry", "evaluate_exit"):
        if not callable(getattr(instance, method, None)):
            raise WorkspaceError(f"策略 {descriptor.id} 缺少方法 {method}(ctx)")
    return instance, merged


def describe_strategy(config: WorkspaceConfig, descriptor: StrategyDescriptor) -> dict:
    """构造实例并返回静态信息（validate 用；仍会执行用户模块）。"""
    instance, merged = create_strategy(config, descriptor)
    return {
        "id": descriptor.id,
        "label": descriptor.label,
        "sdk_version": descriptor.sdk_version,
        "parameters": merged,
        "required_factors": list(descriptor.required_factors),
        "data_requirements": list(descriptor.data_requirements),
        "explain": _safe_explain(instance),
    }


def _safe_explain(instance) -> list:
    explain = getattr(instance, "explain", None)
    if not callable(explain):
        return []
    try:
        result = explain(None)  # 允许 explain 在无 ctx 时返回静态说明
    except Exception:
        return []
    if isinstance(result, (list, tuple)):
        return [dict(item) for item in result if isinstance(item, dict)]
    return []


def coerce_intent(result, *, expected_action: str):
    """调用方包装：用户返回值若是 dict 自动转 StrategyIntent（宽松输入，严格校验）。"""
    if result is None:
        return None
    if isinstance(result, dict):
        allowed = {f for f in ("action", "reason_code", "reason_text", "priority",
                               "position_pct", "exit_fraction", "evidence")}
        unknown = set(result) - allowed
        if unknown:
            raise WorkspaceError(f"策略返回未知字段：{', '.join(sorted(unknown))}")
        result = StrategyIntent(**result)
    return result


def strategy_file_hash(config: WorkspaceConfig, descriptor: StrategyDescriptor) -> str:
    """策略源码内容哈希（含 helpers/因子由 snapshots 负责；此处用于展示）。"""
    from dragon_quant.workspace.snapshots import hash_file

    if descriptor.path is None:
        return ""
    return hash_file(descriptor.path.parent / "strategy.py")
