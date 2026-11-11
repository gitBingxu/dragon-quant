"""workspace 公共门面：初始化、检查、选择、解析、策略清单与校验。

CLI 与 Web 都从本模块进入，避免两端各写一套参数合并/加载规则（方案 §13.1）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dragon_quant.workspace import snapshots
from dragon_quant.workspace.catalog import (
    StrategyDescriptor,
    builtin_descriptor,
    find_descriptor,
    list_descriptors,
)
from dragon_quant.workspace.config import (
    BUILTIN_STRATEGY_ID,
    WorkspaceConfig,
    WorkspaceError,
    create_workspace_config,
    load_workspace,
    resolve_workspace_path,
    workspace_file,
    write_active_workspace,
    write_workspace,
)
from dragon_quant.workspace.loader import (
    create_strategy,
    describe_strategy,
    merge_parameters,
)

#: 模板 slug → (json 资源名, py 资源名)
_TEMPLATES = {
    "example_momentum": ("example_momentum.strategy.json",
                         "example_momentum.strategy.py"),
    "example_meanrev": ("example_meanrev.strategy.json",
                        "example_meanrev.strategy.py"),
}

_AGENTS_MD = """# Workspace · 策略开发入口（agent）

这是一个 dragon-quant 策略 workspace。入口命令：

```bash
dragon-quant strategy list --workspace . --json
dragon-quant strategy validate user:example_momentum --workspace .
dragon-quant strategy use user:example_momentum --workspace .
dragon-quant review-account --workspace . --strategy user:example_momentum --from YYYYMMDD --to YYYYMMDD
```

- `strategies/<slug>/strategy.json` + `strategy.py`：策略定义，导出 `create_strategy(params)`。
- `helpers/`：共享代码；修改会让策略版本变化。
- `factors/`：自定义因子（`SPECS = [FactorSpec, ...]`）。
- `snapshots/`：运行时自动生成的冻结快照（不要手工编辑）。
- 详见 `dragon_quant.strategy_sdk` 打包说明（`python -c "from dragon_quant.strategy_sdk
  import read_resource; print(read_resource('README.md'))"`）。

只读上下文、结构化 Intent、研究闭环见 SDK README。实验成功不会自动切换活动策略。
"""

_README_MD = """# dragon-quant workspace

用 `dragon-quant workspace check --workspace .` 检查状态，
用 `dragon-quant strategy list --workspace .` 查看策略。

两个示例策略仅演示 API，无有效性宣称。
"""


@dataclass
class StrategySelection:
    """一次任务启动时解析并冻结的策略选择。"""

    strategy_id: str
    descriptor: StrategyDescriptor
    workspace: Optional[WorkspaceConfig]
    parameters: dict = field(default_factory=dict)

    @property
    def source(self) -> str:
        return self.descriptor.source

    @property
    def workspace_path(self) -> Optional[Path]:
        return self.workspace.root if self.workspace else None

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "source": self.source,
            "label": self.descriptor.label,
            "workspace": str(self.workspace_path) if self.workspace_path else None,
            "workspace_id": self.workspace.workspace_id if self.workspace else None,
            "parameters": dict(self.parameters),
        }


def _copy_resource(name: str, dest: Path) -> None:
    from dragon_quant.strategy_sdk import read_resource

    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_text(read_resource("templates", name), encoding="utf-8")


def init_workspace(path: str, *, register: bool = True) -> dict:
    """初始化 workspace（不覆盖已有文件），并登记为本机活动 workspace。"""
    root = Path(path).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    for sub in ("strategies", "helpers", "factors", "snapshots", "experiments"):
        (root / sub).mkdir(exist_ok=True)

    config_path = workspace_file(root)
    created_config = False
    if not config_path.exists():
        config = create_workspace_config(root)
        for slug, (json_name, py_name) in _TEMPLATES.items():
            descriptor_rel = f"strategies/{slug}/strategy.json"
            config.strategies.append(descriptor_rel)
        write_workspace(config, expected_revision=None)
        created_config = True
    config = load_workspace(root)

    created: list[str] = []
    for slug, (json_name, py_name) in _TEMPLATES.items():
        dest_dir = root / "strategies" / slug
        for name, dest_name in ((json_name, "strategy.json"), (py_name, "strategy.py")):
            dest = dest_dir / dest_name
            if not dest.exists():
                _copy_resource(name, dest)
                created.append(str(dest.relative_to(root)))
    for name, content in (("AGENTS.md", _AGENTS_MD), ("README.md", _README_MD)):
        dest = root / name
        if not dest.exists():
            dest.write_text(content, encoding="utf-8")
            created.append(name)
    helper_readme = root / "helpers" / "README.md"
    if not helper_readme.exists():
        helper_readme.write_text(
            "# helpers\n\nworkspace 共享 Python 代码；修改会使策略快照版本变化。\n",
            encoding="utf-8")
        created.append("helpers/README.md")
    factor_readme = root / "factors" / "README.md"
    if not factor_readme.exists():
        factor_readme.write_text(
            "# factors\n\n自定义因子模块：定义 `SPECS = [FactorSpec, ...]`。\n",
            encoding="utf-8")
        created.append("factors/README.md")

    if register:
        write_active_workspace(root)
    return {
        "ok": True,
        "workspace": str(root.resolve()),
        "workspace_id": config.workspace_id,
        "created_config": created_config,
        "created_files": created,
        "current": config.selected_strategy_id,
        "revision": config.revision,
    }


def _load_config_or_none(path: Optional[str]) -> Optional[WorkspaceConfig]:
    resolved = resolve_workspace_path(path)
    if resolved is None:
        return None
    return load_workspace(resolved)


def check_workspace(path: Optional[str] = None) -> dict:
    """检查 workspace 配置与全部策略/因子能否加载（显式验证路径，可执行用户代码）。"""
    config = _load_config_or_none(path)
    if config is None:
        return {"ok": True, "workspace": None, "current": BUILTIN_STRATEGY_ID,
                "strategies": [builtin_descriptor().to_dict()], "errors": []}
    results, errors = [], []
    for desc in list_descriptors(config):
        entry = desc.to_dict()
        if desc.source == "user" and desc.valid:
            try:
                _instance, merged = create_strategy(config, desc, None)
                entry["parameters"] = merged
            except WorkspaceError as exc:
                entry["valid"] = False
                entry["errors"] = [str(exc)]
                errors.append(f"{desc.id}: {exc}")
        if not entry["valid"]:
            errors.append(f"{desc.id or '(未知)'}: " + "；".join(entry["errors"]))
        results.append(entry)
    factors = _check_factors(config)
    errors.extend(factors["errors"])
    return {
        "ok": not errors,
        "workspace": str(config.root),
        "workspace_id": config.workspace_id,
        "revision": config.revision,
        "current": config.selected_strategy_id,
        "strategies": results,
        "factors": factors,
        "errors": errors,
    }


def _check_factors(config: WorkspaceConfig) -> dict:
    """静态检查因子模块（存在性 + 语法编译），不执行用户代码。"""
    entries, errors = [], []
    for rel in config.factors:
        path = config.root / rel
        entry = {"module": rel, "exists": path.is_file(), "errors": []}
        if not path.is_file():
            entry["errors"].append("文件不存在")
        else:
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except SyntaxError as exc:
                entry["errors"].append(f"语法错误：{exc}")
        errors.extend(f"因子 {rel}: {e}" for e in entry["errors"])
        entries.append(entry)
    return {"modules": entries, "errors": errors}


def list_strategies(path: Optional[str] = None) -> dict:
    """策略清单（只读 descriptor，不导入用户 Python）。"""
    config = _load_config_or_none(path)
    current = config.selected_strategy_id if config else BUILTIN_STRATEGY_ID
    descriptors = list_descriptors(config)
    items = []
    for desc in descriptors:
        item = desc.to_dict()
        item["current"] = (desc.id == current)
        item["effective_parameters"] = _effective_parameters(desc, config, current)
        if desc.source == "user" and desc.path is not None:
            item["source_fingerprint"] = snapshots.hash_file(desc.path.parent / "strategy.py")
        else:
            item["source_fingerprint"] = ""
        items.append(item)
    return {
        "workspace": str(config.root) if config else None,
        "workspace_id": config.workspace_id if config else None,
        "revision": config.revision if config else None,
        "current": current,
        "strategies": items,
    }


def _effective_parameters(descriptor: StrategyDescriptor,
                          config: Optional[WorkspaceConfig],
                          current: str) -> dict:
    """有效参数值（schema 默认 + 当前选择的保存覆盖），供 Web 展示与编辑。"""
    schema = descriptor.parameters or {}
    effective = {name: spec.get("default") for name, spec in schema.items()
                 if isinstance(spec, dict) and "default" in spec}
    if config is not None and descriptor.id == current:
        effective.update(config.selected_parameters or {})
    return effective


def validate_strategy(strategy_id: str, path: Optional[str] = None) -> dict:
    """对单个策略做完整校验（用户策略会导入并构造实例）。"""
    if not strategy_id:
        raise WorkspaceError("必须指定策略 ID")
    config = _load_config_or_none(path)
    descriptor = find_descriptor(config, strategy_id)
    if descriptor.source == "builtin":
        return {"ok": True, "strategy": descriptor.to_dict(), "builtin": True}
    result = describe_strategy(config, descriptor)
    return {"ok": True, "strategy": descriptor.to_dict(), "detail": result}


def select_strategy(path: Optional[str], strategy_id: str,
                    parameters: Optional[dict] = None,
                    expected_revision: Optional[int] = None) -> dict:
    """持久切换 workspace 的策略选择（CAS + 登记活动 workspace）。"""
    config = _load_config_or_none(path)
    if config is None:
        raise WorkspaceError(
            "未找到 workspace；请先运行 workspace init <path> 或用 --workspace 指定")
    descriptor = find_descriptor(config, strategy_id)
    if descriptor.source == "user" and descriptor.valid:
        merged = merge_parameters(descriptor, parameters)
    else:
        merged = dict(parameters) if parameters else {}
    config.selected_strategy_id = strategy_id
    config.selected_parameters = merged
    updated = write_workspace(config, expected_revision=expected_revision)
    write_active_workspace(updated.root)
    return {
        "ok": True,
        "workspace": str(updated.root),
        "workspace_id": updated.workspace_id,
        "current": updated.selected_strategy_id,
        "parameters": dict(updated.selected_parameters),
        "revision": updated.revision,
    }


def resolve_selection(explicit_workspace: Optional[str] = None,
                      explicit_strategy_id: Optional[str] = None,
                      explicit_parameters: Optional[dict] = None) -> StrategySelection:
    """解析并冻结本次任务策略：显式 > 保存选择 > 内建默认。

    参数优先级：descriptor 默认 < workspace 保存值 < 本次显式参数。
    """
    config = _load_config_or_none(explicit_workspace)
    strategy_id = (explicit_strategy_id
                   or (config.selected_strategy_id if config else BUILTIN_STRATEGY_ID))
    descriptor = find_descriptor(config, strategy_id)
    saved = (config.selected_parameters
             if config and config.selected_strategy_id == strategy_id else {})
    if descriptor.source == "user":
        parameters = merge_parameters(descriptor, saved, explicit_parameters)
    else:
        parameters = {**saved, **(explicit_parameters or {})}
    return StrategySelection(
        strategy_id=strategy_id,
        descriptor=descriptor,
        workspace=config,
        parameters=parameters,
    )


def current_selection(path: Optional[str] = None) -> dict:
    config = _load_config_or_none(path)
    current = config.selected_strategy_id if config else BUILTIN_STRATEGY_ID
    return {
        "workspace": str(config.root) if config else None,
        "workspace_id": config.workspace_id if config else None,
        "revision": config.revision if config else None,
        "strategy_id": current,
        "parameters": dict(config.selected_parameters) if config else {},
    }


def freeze_strategy(selection: StrategySelection) -> dict:
    """冻结策略身份为 ``strategy_ref``（用户策略发布快照；内建用工具指纹）。

    用户策略额外冻结「映射后的必要因子名」与「因子版本」，运行期只能用这些版本
    读取，后续事件/增量不得切到 latest。
    """
    if selection.descriptor.source == "user":
        config = selection.workspace
        if config is None:
            raise WorkspaceError("用户策略缺少 workspace，无法冻结")
        ref = snapshots.publish_snapshot(config, selection.descriptor,
                                         selection.parameters)
        ref["workspace"] = str(Path(config.root).resolve())
        # 因子版本必须来自**同一份已冻结源码**：在快照目录上解析元数据/哈希，
        # 否则「先复制快照、再从当前因子文件取版本」会在源码竞态下错配实现版本。
        frozen_files = ref.get("files") or {}
        frozen_config = WorkspaceConfig(
            root=snapshots.snapshot_dir(config, str(ref["snapshot_id"])),
            workspace_id=config.workspace_id, revision=1,
            strategies=sorted(rel for rel in frozen_files
                              if rel == "strategy.json"
                              or rel.endswith("/strategy.json")),
            factors=sorted(rel for rel in frozen_files
                           if rel.startswith("factors/")))
        required, versions = _freeze_factor_versions(frozen_config,
                                                     selection.descriptor)
        ref["required_factors"] = list(required)
        ref["factor_versions"] = dict(versions)
        return ref
    return snapshots.builtin_ref(strategy_id=selection.strategy_id,
                                 params=selection.parameters)


def _freeze_factor_versions(config: WorkspaceConfig, descriptor: StrategyDescriptor):
    """把 descriptor 声明的因子名映射到注册名并冻结版本（静态元数据，不导入用户代码）。

    用户因子清单/版本来自 AST 元数据 + 源码/helpers 哈希；内建因子版本来自注册表。
    这样 Web/CLI 父进程在冻结阶段也不会 import 用户因子模块。
    """
    declared = list(descriptor.required_factors or ())
    if not declared:
        return [], {}
    from dragon_quant.infra.factor.registry import FACTOR_SPECS, compute_version
    from dragon_quant.workspace.factors import (
        metadata_only_specs, short_workspace_id,
    )

    metadata = {entry["name"]: entry for entry in metadata_only_specs(config)}
    builtin = {spec.name: spec for spec in FACTOR_SPECS}
    short = short_workspace_id(config.workspace_id)
    required: list[str] = []
    versions: dict[str, str] = {}
    for name in declared:
        if name in metadata:
            required.append(name)
            versions[name] = metadata[name]["version"]
        elif f"ws_{short}.{name}" in metadata:
            mapped = f"ws_{short}.{name}"
            required.append(mapped)
            versions[mapped] = metadata[mapped]["version"]
        elif name in builtin:
            required.append(name)
            versions[name] = compute_version(builtin[name])
        else:
            raise WorkspaceError(
                f"策略 {descriptor.id} 声明的必要因子 {name!r} 未注册"
                f"（用户因子需以字面量 SPECS 声明；已尝试 ws_{short}.{name}）")
    return required, versions
