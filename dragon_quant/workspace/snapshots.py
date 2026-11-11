"""策略冻结快照：内容哈希、原子发布、历史只读校验（方案 §13.4）。

快照覆盖策略 descriptor、strategy.py、workspace helpers 与声明因子源码，
而不是只 ``inspect.getsource`` 某个方法——helper 修改必须产生新版本。
查看历史只验证 manifest，**不导入历史代码**。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from dragon_quant.infra import clock
from dragon_quant.strategy_sdk import SDK_VERSION
from dragon_quant.workspace.catalog import StrategyDescriptor
from dragon_quant.workspace.config import WorkspaceConfig, WorkspaceError

SNAPSHOT_SCHEMA_VERSION = 1


def hash_file(path: Path) -> str:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()


def canonical_json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def params_hash(params: dict) -> str:
    return hashlib.sha256(canonical_json(dict(params or {})).encode("utf-8")).hexdigest()


def _toolkit_version() -> str:
    try:
        from dragon_quant._version import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - 冻结环境兜底
        return "unknown"


def _safe_relative(root: Path, rel: str) -> Path:
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise WorkspaceError(f"快照路径非法（绝对路径或逃逸）：{rel}")
    resolved = (Path(root) / candidate).resolve()
    if Path(root).resolve() not in resolved.parents and resolved != Path(root).resolve():
        raise WorkspaceError(f"快照路径逃逸 workspace：{rel}")
    return resolved


def collect_snapshot_files(config: WorkspaceConfig,
                           descriptor: StrategyDescriptor) -> dict[str, str]:
    """收集策略及相关本地代码的相对路径 → sha256。"""
    files: dict[str, str] = {}

    def add(path: Path) -> None:
        if path.is_file():
            rel = path.relative_to(config.root).as_posix()
            files[rel] = hash_file(path)

    if descriptor.path is not None:
        add(descriptor.path)
        add(descriptor.path.parent / "strategy.py")
    helpers = Path(config.root) / "helpers"
    if helpers.is_dir():
        for path in sorted(helpers.rglob("*.py")):
            add(path)
    for rel in config.factors:
        add(Path(config.root) / rel)
    return files


def compute_snapshot_id(files: dict[str, str], params: dict) -> str:
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "sdk_version": SDK_VERSION,
        "files": files,
        "params_hash": params_hash(params),
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return digest[:16]


def snapshot_dir(config: WorkspaceConfig, snapshot_id: str) -> Path:
    if not snapshot_id or "/" in snapshot_id or "\\" in snapshot_id or snapshot_id in {".", ".."}:
        raise WorkspaceError(f"非法快照 ID：{snapshot_id!r}")
    return Path(config.root) / "snapshots" / snapshot_id


def publish_snapshot(config: WorkspaceConfig, descriptor: StrategyDescriptor,
                     params: dict) -> dict:
    """冻结当前策略源码到 ``snapshots/<id>/``；原子发布并回读校验。

    返回 ``strategy_ref`` 片段：snapshot_id / params / params_hash / files /
    toolkit_version / sdk_version 等。
    """
    files = collect_snapshot_files(config, descriptor)
    snapshot_id = compute_snapshot_id(files, params)
    target = snapshot_dir(config, snapshot_id)
    if target.is_dir():
        return _manifest_ref(_read_manifest(target))
    root = Path(config.root)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=snapshot_id + ".", dir=str(target.parent)))
    try:
        for rel in files:
            src = _safe_relative(root, rel)
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        manifest = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "workspace_id": config.workspace_id,
            "strategy_id": descriptor.id,
            "sdk_version": SDK_VERSION,
            "params": dict(params or {}),
            "params_hash": params_hash(params),
            "files": files,
            "python_version": sys.version.split()[0],
            "toolkit_version": _toolkit_version(),
            "dependencies": {},
            "created_at": clock.stamp("%Y-%m-%dT%H:%M:%S"),
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        # 回读校验：写入内容哈希必须与 manifest 一致，否则不发布。
        for rel, expected in files.items():
            if hash_file(staging / rel) != expected:
                raise WorkspaceError(f"快照写入校验失败：{rel}")
        os.replace(staging, target)  # 原子发布（同盘 rename）
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return _manifest_ref(manifest)


def publish_parameter_variant(ref: dict, params: dict) -> dict:
    """基于**已冻结快照**创建参数变体引用（新 params_hash/snapshot_id）。

    不从当前 workspace 源码读取；父进程不导入用户 Python。用于研究参数变体：
    provider 会按新的 (snapshot_id, params_hash) 构造独立实例。
    """
    workspace = Path(str(ref.get("workspace") or ""))
    snapshot_id = ref.get("snapshot_id")
    if not workspace.is_dir() or not snapshot_id:
        raise WorkspaceError("参数变体需要基准冻结快照的 workspace/snapshot_id")
    base = workspace / "snapshots" / str(snapshot_id)
    manifest = _read_manifest(base)
    files = manifest.get("files") or {}
    variant_params = dict(params or {})
    new_id = compute_snapshot_id(files, variant_params)
    target = workspace / "snapshots" / new_id
    if target.is_dir():
        existing = _read_manifest(target)
        result = _manifest_ref(existing)
    else:
        staging = Path(tempfile.mkdtemp(prefix=new_id + ".", dir=str(target.parent)))
        try:
            for rel in files:
                src = base / rel
                dest = staging / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
            variant_manifest = {
                **manifest,
                "snapshot_id": new_id,
                "params": variant_params,
                "params_hash": params_hash(variant_params),
                "created_at": clock.stamp("%Y-%m-%dT%H:%M:%S"),
            }
            (staging / "manifest.json").write_text(
                json.dumps(variant_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            for rel, expected in files.items():
                if hash_file(staging / rel) != expected:
                    raise WorkspaceError(f"参数变体快照写入校验失败：{rel}")
            os.replace(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        result = _manifest_ref(variant_manifest)
    result["workspace"] = str(workspace)
    for key in ("workspace_id", "required_factors", "factor_versions"):
        if key in ref:
            result[key] = ref[key]
    return result


def _manifest_ref(manifest: dict) -> dict:
    return {
        "schema_version": 1,
        "snapshot_id": manifest["snapshot_id"],
        "workspace_id": manifest.get("workspace_id"),
        "strategy_id": manifest.get("strategy_id"),
        "sdk_version": manifest.get("sdk_version"),
        "params": manifest.get("params") or {},
        "params_hash": manifest.get("params_hash"),
        "files": manifest.get("files") or {},
        "python_version": manifest.get("python_version"),
        "toolkit_version": manifest.get("toolkit_version"),
        "reproducibility": "frozen",
    }


def _read_manifest(path: Path) -> dict:
    manifest_path = path / "manifest.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"快照 manifest 无法解析：{exc}") from exc
    if data.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise WorkspaceError("快照 schema_version 不兼容")
    return data


def read_snapshot(config: WorkspaceConfig, snapshot_id: str) -> dict:
    """只读校验历史快照 manifest（不导入历史代码）。"""
    return _read_manifest(snapshot_dir(config, snapshot_id))


def verify_snapshot(config: WorkspaceConfig, snapshot_id: str) -> dict:
    """校验快照文件仍与 manifest 哈希一致；返回 manifest + 校验结论。"""
    manifest = read_snapshot(config, snapshot_id)
    missing, mismatched = [], []
    for rel, expected in (manifest.get("files") or {}).items():
        path = snapshot_dir(config, snapshot_id) / rel
        if not path.is_file():
            missing.append(rel)
        elif hash_file(path) != expected:
            mismatched.append(rel)
    return {
        "snapshot_id": snapshot_id,
        "manifest": manifest,
        "ok": not missing and not mismatched,
        "missing": missing,
        "mismatched": mismatched,
    }


def snapshot_compatible(manifest: dict, *, sdk_version: int = SDK_VERSION) -> tuple[bool, str]:
    """判断历史快照能否在当前环境续算；返回 (兼容, 原因)。"""
    if manifest.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        return False, "快照 schema 版本不兼容"
    if manifest.get("sdk_version") != sdk_version:
        return False, (f"策略快照 SDK 版本 {manifest.get('sdk_version')} "
                       f"与当前 {sdk_version} 不兼容")
    return True, ""


def builtin_ref(*, strategy_id: str, params: dict) -> dict:
    """内建策略冻结身份（用工具版本 + 参数哈希识别，不复制整个仓库源码）。"""
    return {
        "schema_version": 1,
        "snapshot_id": f"builtin:{_toolkit_version()}:{params_hash(params)[:8]}",
        "workspace_id": None,
        "strategy_id": strategy_id,
        "sdk_version": SDK_VERSION,
        "params": dict(params or {}),
        "params_hash": params_hash(params),
        "files": {},
        "python_version": sys.version.split()[0],
        "toolkit_version": _toolkit_version(),
        "reproducibility": "toolkit_fingerprint",
    }
