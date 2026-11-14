"""workspace 用户因子：加载、命名空间、版本与组合注册表（方案 §13.3）。

- 因子模块只在显式执行路径导入（build / eval / query / check）；
- 注册名 ``ws_<短 id>.<local>``，禁止静默覆盖内建或其他 workspace；
- 版本至少纳入规范化 spec、所在模块与 workspace helpers 内容哈希；
- 重复、悬空依赖、循环整体拒绝；计算次序拓扑排序。
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Iterable, Optional

from dragon_quant.infra.factor.registry import (
    FACTOR_SPECS,
    FactorRegistryError,
    compute_version,
    validate_registry,
)
from dragon_quant.infra.factor.types import FactorSpec
from dragon_quant.workspace.config import (
    WorkspaceConfig,
    WorkspaceError,
    short_workspace_id,
)


def _hash_paths(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


def _helpers_hash(config: WorkspaceConfig) -> str:
    helpers = Path(config.root) / "helpers"
    if not helpers.is_dir():
        return hashlib.sha256(b"").hexdigest()
    return _hash_paths(helpers.rglob("*.py"))


def _import_factor_module(config: WorkspaceConfig, rel: str, index: int):
    path = Path(config.root) / rel
    if not path.is_file():
        raise WorkspaceError(f"因子模块不存在：{rel}")
    name = f"dq_ws_{short_workspace_id(config.workspace_id)}_factor_{index}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise WorkspaceError(f"无法加载因子模块：{rel}")
    module = importlib.util.module_from_spec(spec)
    root = str(config.root)
    added = root not in sys.path
    if added:
        sys.path.insert(0, root)
    try:
        sys.modules[name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(name, None)
        raise WorkspaceError(f"因子模块 {rel} 导入失败：{type(exc).__name__}: {exc}") from exc
    finally:
        if added:
            try:
                sys.path.remove(root)
            except ValueError:
                pass
    return module, path


def load_workspace_factor_specs(config: WorkspaceConfig) -> list[FactorSpec]:
    """加载并命名空间化用户因子（显式执行路径）。"""
    if not config.factors:
        return []
    namespace = f"ws_{short_workspace_id(config.workspace_id)}"
    helpers_hash = _helpers_hash(config)
    specs: list[FactorSpec] = []
    local_names: set[str] = set()
    raw_specs: list[tuple[FactorSpec, str, Path]] = []  # (spec, module_hash, path)
    for index, rel in enumerate(config.factors):
        module, path = _import_factor_module(config, rel, index)
        declared = getattr(module, "SPECS", None)
        if declared is None:
            raise WorkspaceError(f"因子模块 {rel} 缺少 SPECS 列表")
        if not isinstance(declared, (list, tuple)):
            raise WorkspaceError(f"因子模块 {rel} 的 SPECS 必须是列表")
        module_hash = _hash_paths([path])
        for spec in declared:
            if not isinstance(spec, FactorSpec):
                raise WorkspaceError(f"因子模块 {rel} 的 SPECS 含非 FactorSpec 元素")
            if spec.name in local_names:
                raise WorkspaceError(f"workspace 因子重名：{spec.name}")
            local_names.add(spec.name)
            raw_specs.append((spec, module_hash, path))

    builtin_names = {s.name for s in FACTOR_SPECS}
    renamed: dict[str, str] = {name: f"{namespace}.{name}" for name in local_names}
    for spec, module_hash, module_path in raw_specs:
        full_name = renamed[spec.name]
        depends = []
        for dep in spec.depends_on:
            if dep in renamed:
                depends.append(renamed[dep])
            elif dep in builtin_names:
                depends.append(dep)
            else:
                raise WorkspaceError(
                    f"用户因子 {spec.name} 依赖未知因子 {dep!r}（既不在本 workspace 也非内建）")
        renamed_spec = dataclasses.replace(spec, name=full_name,
                                           depends_on=tuple(depends),
                                           owner=spec.owner or "workspace")
        static = {entry["name"]: entry
                  for entry in literal_factor_metadata(module_path)}
        if spec.name in static:
            version = _static_version(static[spec.name], helpers_hash, module_hash)
        else:
            base_version = compute_version(renamed_spec)
            digest = hashlib.sha256(
                f"{base_version}|{helpers_hash}|{module_hash}".encode("utf-8")
            ).hexdigest()[:12]
            version = f"v{digest}"
        specs.append(dataclasses.replace(renamed_spec, version_override=version))
    return specs


def combined_specs(config: Optional[WorkspaceConfig]) -> list[FactorSpec]:
    """内建 + 用户因子组合注册表；校验重复/悬空/环路。"""
    specs = list(FACTOR_SPECS)
    if config is not None:
        specs.extend(load_workspace_factor_specs(config))
    try:
        validate_registry(specs)
        _topological_order(specs)
    except (FactorRegistryError, WorkspaceError) as exc:
        raise WorkspaceError(f"因子注册表校验失败：{exc}") from exc
    return specs


def _topological_order(specs: list[FactorSpec]) -> list[str]:
    """依赖拓扑排序（同级保持稳定 name 升序）；检测环路。"""
    by_name = {spec.name: spec for spec in specs}
    visiting: set[str] = set()
    done: set[str] = set()
    order: list[str] = []

    def visit(name: str, stack: tuple[str, ...]) -> None:
        if name in done:
            return
        if name in visiting:
            cycle = " -> ".join((*stack, name))
            raise WorkspaceError(f"因子依赖成环：{cycle}")
        visiting.add(name)
        for dep in by_name[name].depends_on:
            if dep in by_name:
                visit(dep, (*stack, name))
        visiting.discard(name)
        done.add(name)
        order.append(name)

    for name in sorted(by_name):
        visit(name, ())
    return order


def workspace_factor_names(config: Optional[WorkspaceConfig]) -> tuple[str, ...]:
    if config is None:
        return ()
    return tuple(sorted(s.name for s in load_workspace_factor_specs(config)))


def resolve_required_factors(config: WorkspaceConfig, declared: Iterable[str],
                             *, specs: Optional[Iterable[FactorSpec]] = None) -> dict:
    """把 descriptor 的本地因子名映射到注册名（用户因子加 workspace 命名空间）。

    允许声明：已注册的内建名 / 已命名空间化的 ``ws_<short>.<name>`` / 本 workspace
    的本地名。未注册 → 明确失败（不静默当作缺失）。
    """
    source = list(specs) if specs is not None else combined_specs(config)
    registered = {spec.name for spec in source}
    short = short_workspace_id(config.workspace_id)
    mapping: dict[str, str] = {}
    for name in declared:
        if name in registered:
            mapping[name] = name
            continue
        namespaced = f"ws_{short}.{name}"
        if namespaced in registered:
            mapping[name] = namespaced
            continue
        raise WorkspaceError(
            f"策略声明的必要因子 {name!r} 未注册（已尝试 {namespaced}）")
    return mapping


def factor_version(spec: FactorSpec) -> str:
    return compute_version(spec)


def literal_factor_metadata(path: Path) -> list[dict]:
    """仅解析 AST 提取 ``SPECS = [FactorSpec(name=..., ...)]`` 的常量元数据。

    ``list`` / ``status`` 等只读查询用它展示用户因子清单，**不导入用户 Python**。
    仅识别关键字字面量；动态构造的字段忽略（不作为执行依据）。
    """
    import ast

    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return []
    entries: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "SPECS" for t in node.targets):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        for item in node.value.elts:
            if not (isinstance(item, ast.Call)
                    and isinstance(item.func, ast.Name)
                    and item.func.id == "FactorSpec"):
                continue
            meta: dict = {}
            for kw in item.keywords:
                if kw.arg is None:
                    continue
                try:
                    meta[kw.arg] = ast.literal_eval(kw.value)
                except (ValueError, SyntaxError):
                    continue
            if meta.get("name"):
                entered = {
                    "name": meta.get("name"),
                    "label": meta.get("label", ""),
                    "frequency": meta.get("frequency", "1d"),
                    "source": meta.get("source", "computed"),
                    "depends_on": list(meta.get("depends_on") or ()),
                    "available_phase": meta.get("available_phase", "close"),
                    "pit_rule": meta.get("pit_rule", "recorded"),
                    "lookback": meta.get("lookback", 0),
                    "direction": meta.get("direction", "higher_better"),
                    "unit": meta.get("unit", "score"),
                    "default_enabled": bool(meta.get("default_enabled", False)),
                }
                entries.append(entered)
    return entries


def _static_version(entry: dict, helpers_hash: str, module_hash: str) -> str:
    from dragon_quant.infra.factor.registry import canonical_json

    payload = {k: v for k, v in entry.items() if k != "version"}
    digest = hashlib.sha256(
        (canonical_json(payload) + "|" + helpers_hash + "|" + module_hash)
        .encode("utf-8")).hexdigest()[:12]
    return f"v{digest}"


def metadata_only_specs(config: WorkspaceConfig) -> list[dict]:
    """workspace 用户因子的元数据清单（含命名空间与静态版本），不执行用户代码。

    版本 = 规范化元数据 + 模块文件哈希 + helpers 哈希 的内容哈希，与执行版本同一
    冻结源码来源；供 ``freeze_strategy`` 在父进程（Web/CLI）冻结因子版本，避免
    导入用户因子模块。
    """
    short = short_workspace_id(config.workspace_id)
    helpers_hash = _helpers_hash(config)
    result: list[dict] = []
    for rel in config.factors:
        path = Path(config.root) / rel
        module_hash = _hash_paths([path])
        for entry in literal_factor_metadata(path):
            # ``owner`` 只进展示元数据，不参与 ``_static_version`` 的哈希输入：
            # 版本公式必须与执行路径（``load_workspace_factor_specs``）逐位一致。
            result.append({**entry,
                           "name": f"ws_{short}.{entry['name']}",
                           "module": rel,
                           "owner": "workspace",
                           "version": _static_version(entry, helpers_hash, module_hash)})
    return result


def _parse_available_at(value: Optional[str]) -> Optional[int]:
    """available_at → epoch 毫秒；解析失败返回 None（按未知处理，不当作已可知）。"""
    if not value:
        return None
    from datetime import datetime, timezone, timedelta

    text = str(value).strip().replace(" ", "T")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone(timedelta(hours=8)))
    return int(moment.timestamp() * 1000)


class FactorReader:
    """按 as-of 读取 workspace/内建因子值；未来不可用与缺失严格区分（不补零）。

    ``versions`` 为任务开始时冻结的 ``{name: version}``；读取显式传版本，绝不取
    latest。存储读取故障与「无值」严格区分：故障向上抛，不缓存成缺失。
    """

    def __init__(self, names, *, db_path=None, versions=None):
        self.names = tuple(n for n in names if n)
        self.db_path = db_path
        self.versions = {str(k): str(v) for k, v in (versions or {}).items() if v}
        self._cache: dict[tuple, dict] = {}

    def _day(self, trade_date: str) -> dict:
        versions = dict(self.versions)
        key = (trade_date, tuple(sorted(versions.items())))
        if key in self._cache:
            return self._cache[key]
        from dragon_quant.infra.factor import storage as fstore

        payload = fstore.get_day_values(
            trade_date, names=list(self.names) or None,
            versions=versions or None, db_path=self.db_path)
        self._cache[key] = payload
        return payload

    def values_for(self, *, trade_date: str, code: Optional[str],
                   as_of_ms: int, phase: str = "", versions=None) -> dict:
        from dragon_quant.strategy_sdk import (
            FACTOR_STATUS_MISSING, FACTOR_STATUS_OK, FACTOR_STATUS_UNAVAILABLE,
            FactorValue,
        )

        if versions is not None:
            # 显式冻结版本优先；调用方不传时用实例冻结版本。
            self.versions = {str(k): str(v) for k, v in versions.items() if v} or self.versions
        if not code:
            return {name: FactorValue(name=name, status=FACTOR_STATUS_MISSING,
                                      reason="无标的代码") for name in self.names}
        payload = self._day(trade_date)
        values = (payload.get("values") or {}).get(code, {})
        available = (payload.get("available_at") or {}).get(code, {})
        resolved = payload.get("resolved_versions") or {}
        out: dict = {}
        for name in self.names:
            if name not in values:
                out[name] = FactorValue(name=name, status=FACTOR_STATUS_MISSING,
                                        reason="该时点无值")
                continue
            at = available.get(name)
            at_ms = _parse_available_at(at)
            if at_ms is None:
                out[name] = FactorValue(name=name, status=FACTOR_STATUS_MISSING,
                                        available_at=at, reason="available_at 无法解析")
                continue
            if at_ms > int(as_of_ms or 0):
                out[name] = FactorValue(name=name, status=FACTOR_STATUS_UNAVAILABLE,
                                        available_at=at, version=resolved.get(name, ""),
                                        reason="数据在该决策时点尚不可知")
                continue
            out[name] = FactorValue(name=name, value=float(values[name]),
                                    version=resolved.get(name, ""), available_at=at,
                                    status=FACTOR_STATUS_OK)
        return out
