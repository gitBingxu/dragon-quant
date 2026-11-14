"""策略目录：内建与用户策略的元数据清单与静态校验（不导入用户 Python）。

关键约束（方案 §13.2 / §13.6）：

- 清单只读 ``strategy.json`` 元数据；``list`` / ``-h`` / 普通查询**绝不导入**
  用户模块，也不创建存储；
- 用户策略 ID 固定 ``user:<slug>``，且与所在目录名一致；
- 内建策略目录只列**实际存在**的实现（当前仅 ``builtin:dragon_pullback_daily``）；
- 重名、SDK 版本不兼容、非法字段在此处明确失败，不做静默降级。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dragon_quant.strategy_sdk import SDK_VERSION
from dragon_quant.workspace.config import (
    BUILTIN_STRATEGY_ID,
    WorkspaceConfig,
    WorkspaceError,
)

#: 内建策略的展示元数据。参数 schema 只列常用可覆盖项（权威默认值仍在 StrategyConfig）。
BUILTIN_DESCRIPTOR = {
    "id": BUILTIN_STRATEGY_ID,
    "label": "龙头回踩日线（内建）",
    "description": "项目内建的四类买点 + 十条卖出规则（五维候选池）。",
    "sdk_version": SDK_VERSION,
    "source": "builtin",
    "entrypoint": "",
    "parameters": {
        "min_score": {
            "type": "number", "default": 50.0, "label": "最低综合分",
            "help": "低于该分数的候选不进入常规买入判断", "unit": "分", "step": 0.1,
        },
        "max_open_gap": {
            "type": "number", "default": 7.0, "label": "最高开盘涨幅",
            "help": "开盘涨幅超过该值时不再追入", "unit": "%", "step": 0.1,
        },
        "stop_loss_pct": {
            "type": "number", "default": -5.0, "label": "常规止损线",
            "help": "持仓亏损达到该幅度时执行硬止损（负数）", "unit": "%", "step": 0.1,
        },
        "take_profit_pct": {
            "type": "number", "default": 12.0, "label": "止盈参考值",
            "help": "兼容/参考参数；当前内建实现不直接读取该值，实际止盈由卖出链规则决定",
            "unit": "%", "step": 0.1,
        },
        "max_hold_days": {
            "type": "int", "default": 5, "label": "最大持有天数",
            "help": "到期仍未退出则触发时间止损（封板豁免）", "unit": "天", "step": 1,
        },
        "max_positions": {
            "type": "int", "default": 5, "label": "最大同时持仓",
            "help": "限制账户同时持有的股票数量", "unit": "只", "step": 1,
        },
    },
    "required_factors": [],
    "data_requirements": ["1d", "5m"],
}

_PARAM_TYPES = {"number", "int", "bool", "string"}


@dataclass
class StrategyDescriptor:
    id: str
    label: str
    source: str                      # "builtin" | "user"
    sdk_version: int = SDK_VERSION
    description: str = ""
    entrypoint: str = "create_strategy"
    parameters: dict = field(default_factory=dict)
    required_factors: list[str] = field(default_factory=list)
    data_requirements: list[str] = field(default_factory=list)
    path: Optional[Path] = None      # 用户策略的 strategy.json 路径
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "source": self.source,
            "sdk_version": self.sdk_version,
            "description": self.description,
            "entrypoint": self.entrypoint,
            "parameters": dict(self.parameters),
            "required_factors": list(self.required_factors),
            "data_requirements": list(self.data_requirements),
            "path": str(self.path) if self.path else None,
            "valid": self.valid,
            "errors": list(self.errors),
        }


def builtin_descriptor() -> StrategyDescriptor:
    return StrategyDescriptor(**BUILTIN_DESCRIPTOR)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _value_matches_type(kind: str, value: object) -> bool:
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return _is_number(value) and math.isfinite(float(value))
    if kind == "string":
        return isinstance(value, str)
    return True


def _validate_parameter_schema(parameters: object) -> list[str]:
    errors: list[str] = []
    if parameters is None:
        return errors
    if not isinstance(parameters, dict):
        return ["parameters 必须是对象"]
    for name, schema in parameters.items():
        if not isinstance(name, str) or not name.strip():
            errors.append("parameters 的键必须是非空字符串")
            continue
        if not isinstance(schema, dict):
            errors.append(f"参数 {name} 的 schema 必须是对象")
            continue
        kind = schema.get("type")
        if kind not in _PARAM_TYPES:
            errors.append(f"参数 {name} 的 type 必须是 {sorted(_PARAM_TYPES)} 之一")
            continue
        for field_name in ("label", "help", "unit"):
            if field_name in schema and not isinstance(schema[field_name], str):
                errors.append(f"参数 {name} 的 {field_name} 必须是字符串")
        if "step" in schema:
            step = schema["step"]
            if not _is_number(step) or not math.isfinite(float(step)) or step <= 0:
                errors.append(f"参数 {name} 的 step 必须是正数")
        if "default" in schema and not _value_matches_type(kind, schema["default"]):
            errors.append(f"参数 {name} 的默认值类型必须是 {kind}")
        bounds: dict[str, float] = {}
        for bound in ("min", "max"):
            if bound not in schema:
                continue
            value = schema[bound]
            if kind not in {"number", "int"} or not _is_number(value) \
                    or not math.isfinite(float(value)):
                errors.append(
                    f"参数 {name} 的 {bound} 只支持 number/int 参数且必须是数值")
            else:
                bounds[bound] = float(value)
        if len(bounds) == 2 and bounds["min"] > bounds["max"]:
            errors.append(f"参数 {name} 的 min 不能大于 max")
        enum = schema.get("enum")
        if enum is not None:
            if not isinstance(enum, list):
                errors.append(f"参数 {name} 的 enum 必须是列表")
            else:
                if any(not _value_matches_type(kind, value) for value in enum):
                    errors.append(f"参数 {name} 的 enum 值类型必须与 type 相容")
                elif "default" in schema and schema["default"] not in enum:
                    errors.append(f"参数 {name} 的默认值必须在 enum 内")
    return errors


def _validate_str_list(value: object, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        return [f"{name} 必须是字符串数组"]
    return []


def load_user_descriptor(root: Path, descriptor_rel: str) -> StrategyDescriptor:
    """读取单个用户策略 descriptor（只读 JSON，不导入 strategy.py）。"""
    descriptor_path = Path(root) / descriptor_rel
    errors: list[str] = []
    data: dict = {}
    try:
        root_resolved = Path(root).resolve()
        resolved = descriptor_path.resolve()
    except OSError:
        root_resolved = resolved = None
    if root_resolved is not None and resolved is not None and (
            resolved != root_resolved and root_resolved not in resolved.parents):
        return StrategyDescriptor(
            id="", label="", source="user", path=descriptor_path,
            errors=[f"descriptor 路径逃逸 workspace：{descriptor_rel}"])
    if not descriptor_path.is_file():
        return StrategyDescriptor(
            id="", label="", source="user", path=descriptor_path,
            errors=[f"descriptor 不存在：{descriptor_rel}"])
    try:
        data = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return StrategyDescriptor(
            id="", label="", source="user", path=descriptor_path,
            errors=[f"descriptor 无法解析：{exc}"])
    if not isinstance(data, dict):
        return StrategyDescriptor(
            id="", label="", source="user", path=descriptor_path,
            errors=["descriptor 必须是 JSON 对象"])

    strategy_id = data.get("id")
    slug = descriptor_path.parent.name
    expected_id = f"user:{slug}"
    if not isinstance(strategy_id, str) or not strategy_id.strip():
        errors.append("缺少 id")
    elif strategy_id != expected_id:
        errors.append(f"id 必须为 {expected_id!r}（与目录名一致），得到 {strategy_id!r}")
    elif ":" not in strategy_id or not strategy_id.split(":", 1)[1]:
        errors.append("id 必须是 user:<slug> 形式")

    label = data.get("label")
    if not isinstance(label, str) or not label.strip():
        errors.append("缺少 label")

    sdk_version = data.get("sdk_version")
    if sdk_version != SDK_VERSION:
        errors.append(
            f"sdk_version 不兼容：策略声明 {sdk_version!r}，当前 SDK 为 {SDK_VERSION}")

    entrypoint = data.get("entrypoint", "create_strategy")
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        errors.append("entrypoint 必须是非空字符串")
    elif not (descriptor_path.parent / "strategy.py").is_file():
        errors.append("缺少 strategy.py")

    parameters = data.get("parameters") or {}
    errors.extend(_validate_parameter_schema(parameters))
    errors.extend(_validate_str_list(data.get("required_factors"), "required_factors"))
    errors.extend(_validate_str_list(data.get("data_requirements"), "data_requirements"))

    return StrategyDescriptor(
        id=strategy_id if isinstance(strategy_id, str) else "",
        label=label if isinstance(label, str) else "",
        source="user",
        sdk_version=sdk_version if isinstance(sdk_version, int) else 0,
        description=data.get("description") or "",
        entrypoint=entrypoint if isinstance(entrypoint, str) else "create_strategy",
        parameters=dict(parameters) if isinstance(parameters, dict) else {},
        required_factors=list(data.get("required_factors") or []),
        data_requirements=list(data.get("data_requirements") or []),
        path=descriptor_path,
        errors=errors,
    )


def user_descriptors(config: WorkspaceConfig) -> list[StrategyDescriptor]:
    return [load_user_descriptor(config.root, rel) for rel in config.strategies]


def list_descriptors(config: Optional[WorkspaceConfig]) -> list[StrategyDescriptor]:
    """内建 + 用户策略（按 ID 排序，内建在前）。"""
    descriptors = [builtin_descriptor()]
    if config is not None:
        descriptors.extend(user_descriptors(config))
    # 重名检测：用户不得覆盖内建或彼此覆盖。
    seen: dict[str, StrategyDescriptor] = {}
    for desc in descriptors:
        if desc.id:
            if desc.id in seen:
                desc.errors.append(f"策略 ID 重复：{desc.id}")
            seen[desc.id] = desc
    return descriptors


def find_descriptor(config: Optional[WorkspaceConfig], strategy_id: str) -> StrategyDescriptor:
    """按 ID 解析 descriptor；未知或存在校验错误的策略带原因失败。"""
    for desc in list_descriptors(config):
        if desc.id == strategy_id:
            if desc.errors:
                raise WorkspaceError(
                    f"策略 {strategy_id} 校验失败：" + "；".join(desc.errors))
            return desc
    known = [d.id for d in list_descriptors(config) if d.id]
    raise WorkspaceError(
        f"未知策略 {strategy_id!r}；可用策略：{', '.join(known) or '（无）'}")
