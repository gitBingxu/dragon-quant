"""npm adapter: build, verify, reconcile and upload the Agent Skill package.

Like the PyPI adapter this owns only package concerns: version contract,
offline tests, ``npm pack`` whitelist, local install smoke, registry
reconciliation and upload.  It never touches Git, never bumps the repository
and never asks for a password.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from release.common import (
    BUILD_TIMEOUT,
    REGISTRY_PROBE_TIMEOUT,
    UPLOAD_TIMEOUT,
    ReleaseError,
    Runner,
    compare_versions,
    sha256_file,
    sha512_integrity,
    validate_version,
)

NAME = "npm"
PACKAGE_NAME = "dragon-quant-skills"
REGISTRY = "https://registry.npmjs.org"
RELEASE_FILES = ("package.json", "skills/dragon-quant/SKILL.md")
SKILL_PATH = "skills/dragon-quant/SKILL.md"


def _env(runner: Runner, *, offline: bool, extra: dict | None = None) -> dict:
    env = {"npm_config_offline": "true" if offline else "false"}
    cache = runner.env_for().get("DQ_RELEASE_NPM_CACHE")
    if cache:
        env["npm_config_cache"] = cache
    if extra:
        env.update(extra)
    return env


def _npm(runner: Runner, argv: list[str], *, stage: str, offline: bool,
         cwd: Path | None = None, allow_failure: bool = False,
         extra_env: dict | None = None, progress_label: str | None = None,
         timeout: float | None = None):
    return runner.run(
        ["npm", *argv],
        cwd=cwd,
        stage=stage,
        scrub=True,
        env=_env(runner, offline=offline, extra=extra_env),
        allow_failure=allow_failure,
        progress_label=progress_label,
        timeout=timeout,
    )


# ─── versions ─────────────────────────────────────────────────────────────


def current_version(repo: Path) -> str:
    data = json.loads((Path(repo) / "package.json").read_text(encoding="utf-8"))
    return str(data.get("version") or "")


def current_min_cli(repo: Path) -> str:
    return read_metadata(Path(repo))["min_cli_version"]


def read_metadata(root: Path) -> dict:
    package = json.loads((Path(root) / "package.json").read_text(encoding="utf-8"))
    skill = (Path(root) / SKILL_PATH).read_text(encoding="utf-8")
    front = skill.split("---\n", 2)[1]
    return {
        "package_name": package.get("name"),
        "package_version": package.get("version"),
        "skill_version": _front_value(front, "skill-version"),
        "min_cli_version": _front_value(front, "min-cli-version"),
    }


def _front_value(front: str, key: str) -> str:
    match = re.search(rf'{re.escape(key)}:\s*"([^"]+)"', front)
    if not match:
        raise ReleaseError("preflight", f"SKILL.md 缺少 {key}")
    return match.group(1)


def apply_versions(staging: Path, npm_version: str, min_cli_version: str) -> list[str]:
    package_path = Path(staging) / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["version"] = npm_version
    package_path.write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    skill_path = Path(staging) / SKILL_PATH
    text = skill_path.read_text(encoding="utf-8")
    for key, value in (("skill-version", npm_version),
                       ("min-cli-version", min_cli_version)):
        pattern = re.compile(rf'({re.escape(key)}:\s*)"[^"]*"')
        text, count = pattern.subn(rf'\g<1>"{value}"', text, count=1)
        if count != 1:
            raise ReleaseError("prepare", f"SKILL.md 缺少唯一的 {key}")
    skill_path.write_text(text, encoding="utf-8")
    return list(RELEASE_FILES)


def version_contract(staging: Path, npm_version: str, min_cli_version: str,
                     cli_version: str) -> None:
    metadata = read_metadata(staging)
    if metadata["package_name"] != PACKAGE_NAME:
        raise ReleaseError(
            "prepare", f"package name 必须是 {PACKAGE_NAME}，当前 {metadata['package_name']}")
    if metadata["package_version"] != npm_version:
        raise ReleaseError(
            "prepare", f"package.json {metadata['package_version']} != 目标 {npm_version}")
    if metadata["skill_version"] != npm_version:
        raise ReleaseError(
            "prepare", f"skill-version {metadata['skill_version']} != 目标 {npm_version}")
    if metadata["min_cli_version"] != min_cli_version:
        raise ReleaseError(
            "prepare",
            f"min-cli-version {metadata['min_cli_version']} != 目标 {min_cli_version}",
        )
    validate_version(min_cli_version)
    validate_version(cli_version)
    if compare_versions(min_cli_version, cli_version) > 0:
        raise ReleaseError(
            "prepare",
            f"min-cli-version {min_cli_version} 高于用于构建验证的 CLI {cli_version}",
        )


# ─── offline build / verify ───────────────────────────────────────────────


def _run_skill_tests(staging: Path, python: str, runner: Runner) -> None:
    scratch = Path(tempfile.mkdtemp(prefix="dq-npm-tests-"))
    try:
        runner.run(
            [python, "-m", "pytest", "-q",
             "dragon_quant/tests/test_skill_package.py",
             "dragon_quant/tests/test_skill_content.py",
             "dragon_quant/tests/test_skill_routes.py"],
            cwd=staging, stage="prepare", scrub=True,
            env={"PYTHONPATH": str(staging), "HOME": str(scratch / "home"),
                 "TMPDIR": str(scratch), "TMP": str(scratch),
                 "DQ_DATA_DIR": str(scratch / "data")},
            progress_label="运行 Skill 离线测试", timeout=BUILD_TIMEOUT,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _pack_whitelist(staging: Path, runner: Runner) -> None:
    """Validate the real ``npm pack --dry-run --json`` document shape.

    npm returns a one-element list whose entry carries a ``files`` array of
    ``{path, size, mode}`` objects (not a flat list of paths).  The structure
    is validated strictly: anything unexpected is a hard failure, never a
    silently empty whitelist.
    """
    result = _npm(runner, ["pack", str(staging), "--dry-run", "--json",
                           "--ignore-scripts"], stage="prepare", offline=True,
                  progress_label="校验 npm 打包白名单", timeout=BUILD_TIMEOUT)
    try:
        document = json.loads(result.stdout or "")
    except json.JSONDecodeError as exc:
        raise ReleaseError("prepare", f"npm pack --json 输出无法解析：{exc}") from exc
    if not isinstance(document, list) or len(document) != 1 \
            or not isinstance(document[0], dict):
        raise ReleaseError(
            "prepare", "npm pack --json 不是预期的单包数组结构；拒绝继续")
    entry = document[0]
    files = entry.get("files")
    if not isinstance(files, list) or not files:
        raise ReleaseError("prepare", "npm pack --json 缺少 files 列表；拒绝继续")
    paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) \
                or not item["path"]:
            raise ReleaseError(
                "prepare", "npm pack --json 的 files 条目缺少 path；拒绝继续")
        paths.add(item["path"])
    metadata = read_metadata(staging)
    if entry.get("name") not in (None, PACKAGE_NAME):
        raise ReleaseError(
            "prepare", f"npm pack 包名 {entry.get('name')!r} != {PACKAGE_NAME}")
    if entry.get("version") not in (None, metadata["package_version"]):
        raise ReleaseError(
            "prepare",
            f"npm pack 版本 {entry.get('version')!r} != 目标 {metadata['package_version']}")
    if SKILL_PATH not in paths:
        raise ReleaseError("prepare", f"npm 包缺少 {SKILL_PATH}")
    if "bin/dragon-quant-skills.mjs" not in paths:
        raise ReleaseError("prepare", "npm 包缺少 bin 入口")
    forbidden = [name for name in paths
                 if name.startswith(("docs/", "dragon_quant/", "test/"))
                 or name.startswith(".env") or str(name).endswith(".enc")]
    if forbidden:
        raise ReleaseError("prepare", f"npm 包包含不该发布的路径：{forbidden[:5]}")


def _pack(staging: Path, runner: Runner) -> Path:
    dist = Path(staging) / "npm-dist"
    shutil.rmtree(dist, ignore_errors=True)
    dist.mkdir(parents=True, exist_ok=True)
    result = _npm(runner, ["pack", str(staging), "--pack-destination", str(dist),
                           "--ignore-scripts", "--silent"], stage="prepare",
                  offline=True, progress_label="打包 npm tarball",
                  timeout=BUILD_TIMEOUT)
    name = (result.stdout or "").strip().splitlines()[-1] if result.stdout else ""
    tarball = dist / name
    if not name or not tarball.is_file():
        candidates = [p for p in dist.iterdir() if p.suffix == ".tgz"]
        if len(candidates) != 1:
            raise ReleaseError("prepare", f"npm pack 未生成唯一 tarball：{candidates}")
        tarball = candidates[0]
    return tarball


def _install_smoke(staging: Path, tarball: Path, runner: Runner) -> None:
    scratch = Path(tempfile.mkdtemp(prefix="dq-npm-smoke-"))
    try:
        smoke = scratch / "install"
        _npm(runner, ["install", "--prefix", str(smoke), "--ignore-scripts",
                      "--no-audit", "--no-fund", str(tarball)],
             stage="prepare", offline=True, cwd=staging,
             extra_env={"HOME": str(scratch / "home"), "TMPDIR": str(scratch)},
             progress_label="本地安装冒烟测试", timeout=BUILD_TIMEOUT)
        entry = smoke / "node_modules" / PACKAGE_NAME / "bin" / "dragon-quant-skills.mjs"
        if not entry.is_file():
            raise ReleaseError("prepare", "本地安装冒烟：安装产物缺少 bin 入口")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _node_tests(staging: Path, runner: Runner) -> None:
    _npm(runner, ["test", "--prefix", str(staging)], stage="prepare",
         offline=True, cwd=staging, progress_label="运行 Node 安装器测试",
         timeout=BUILD_TIMEOUT)


def prepare(repo: Path, staging: Path, npm_version: str, min_cli_version: str,
            python: str, runner: Runner) -> Path:
    cli_version = _staging_cli_version(staging)
    apply_versions(staging, npm_version, min_cli_version)
    version_contract(staging, npm_version, min_cli_version, cli_version)
    _run_skill_tests(staging, python, runner)
    _node_tests(staging, runner)
    _pack_whitelist(staging, runner)
    tarball = _pack(staging, runner)
    _install_smoke(staging, tarball, runner)
    return tarball


def _staging_cli_version(staging: Path) -> str:
    text = (Path(staging) / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', text, re.MULTILINE)
    if not match:
        raise ReleaseError("prepare", "pyproject.toml 缺少版本行")
    return match.group(1)


def artifact(staging: Path, tarball: Path) -> dict:
    return {
        "filename": tarball.name,
        "sha256": sha256_file(tarball),
        "integrity": sha512_integrity(tarball),
    }


def verify_local(staging: Path, expected: dict) -> Path:
    path = Path(staging) / "npm-dist" / expected["filename"]
    if not path.is_file():
        raise ReleaseError("resume", f"暂存 npm 工件缺失：{expected['filename']}")
    if sha256_file(path) != expected["sha256"]:
        raise ReleaseError("resume", f"暂存 npm 工件哈希变化：{expected['filename']}")
    if sha512_integrity(path) != expected["integrity"]:
        raise ReleaseError("resume", f"暂存 npm 工件 integrity 变化：{expected['filename']}")
    return path


# ─── registry ─────────────────────────────────────────────────────────────


def remote_facts(version: str, runner: Runner,
                 *, stage: str = "upload_npm") -> dict | None:
    """Return remote ``dist`` facts or ``None`` when the version is absent."""
    result = _npm(runner, ["view", f"{PACKAGE_NAME}@{version}", "dist", "--json",
                           f"--registry={REGISTRY}"],
                  stage=stage, offline=False, allow_failure=True,
                  progress_label=f"查询 npm 版本 {PACKAGE_NAME}@{version}",
                  timeout=REGISTRY_PROBE_TIMEOUT)
    if result.returncode != 0:
        detail = f"{result.stderr or ''}{result.stdout or ''}"
        if "E404" in detail or "404 Not Found" in detail:
            return None
        raise ReleaseError(
            stage,
            "无法确认 npm registry 版本状态（网络失败不能当作版本不存在）："
            + detail.strip()[:400],
        )
    text = (result.stdout or "").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReleaseError("upload_npm", f"无法解析 npm view dist 输出：{exc}") from exc
    if isinstance(data, str):
        data = {"integrity": data}
    if not isinstance(data, dict) or not data.get("integrity"):
        raise ReleaseError(
            "upload_npm", "远端版本缺少 dist.integrity；无法对账，请人工处理")
    return data


def dist_tags(runner: Runner) -> dict:
    result = _npm(runner, ["view", PACKAGE_NAME, "dist-tags", "--json",
                           f"--registry={REGISTRY}"],
                  stage="upload_npm", offline=False, allow_failure=True,
                  progress_label="查询 npm dist-tags",
                  timeout=REGISTRY_PROBE_TIMEOUT)
    if result.returncode != 0:
        detail = f"{result.stderr or ''}{result.stdout or ''}"
        if "E404" in detail or "404 Not Found" in detail:
            return {}
        raise ReleaseError(
            "upload_npm", "无法确认 npm dist-tags（网络失败不能当作不存在）："
            + detail.strip()[:400])
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ReleaseError("upload_npm", f"无法解析 npm dist-tags：{exc}") from exc
    return data if isinstance(data, dict) else {}


def reconcile(version: str, integrity: str, runner: Runner) -> bool:
    """True when the exact version already exists with the same integrity."""
    facts = remote_facts(version, runner)
    if facts is None:
        return False
    if facts.get("integrity") != integrity:
        raise ReleaseError(
            "upload_npm",
            f"远端已存在 {PACKAGE_NAME}@{version} 但 integrity 不一致，停止重传",
        )
    return True


def _auth_scratch(runner: Runner, token: str):
    """Create a 0700 scratch dir with a 0600 npmrc; caller must clean up."""
    scratch = Path(tempfile.mkdtemp(prefix="dq-npm-auth-"))
    npmrc = scratch / "npmrc"
    npmrc.write_text(
        f"registry={REGISTRY}/\n//registry.npmjs.org/:_authToken={token}\n",
        encoding="utf-8",
    )
    npmrc.chmod(0o600)
    extra = {"npm_config_cache": str(scratch / "cache"),
             "HOME": str(scratch / "home"),
             "TMPDIR": str(scratch)}
    return scratch, npmrc, extra


def guard_dist_tag(version: str, tag: str, runner: Runner) -> None:
    """Reject a publish that would move ``tag`` backwards.

    ``npm publish --tag`` reassigns the tag as part of the upload, so checking
    after the upload (or only on resume) is too late.  The guard runs before
    the irreversible publish and fails closed; the user can pick another tag.
    """
    tags = dist_tags(runner)
    current = tags.get(tag)
    if current and compare_versions(str(current), version) > 0:
        raise ReleaseError(
            "upload_npm",
            f"dist-tag {tag} 当前指向更高版本 {current}；拒绝在发布 {version} 前把"
            f"标签降级（如需发布请选择其他 --npm-tag）",
        )


def ensure_dist_tag(version: str, tag: str, runner: Runner,
                    token_provider, otp: str | None = None) -> bool:
    """Ensure ``tag`` points at ``version``; never overwrite a newer version."""
    tags = dist_tags(runner)
    current = tags.get(tag)
    if current == version:
        return False
    if current and compare_versions(str(current), version) > 0:
        raise ReleaseError(
            "upload_npm",
            f"dist-tag {tag} 指向更新的版本 {current}，拒绝覆盖外部新版本",
        )
    token = token_provider()
    scratch, npmrc, extra = _auth_scratch(runner, token)
    try:
        if otp:
            # ``_npm`` scrubs inherited NPM_OTP, so pass it explicitly.
            extra["npm_config_otp"] = otp
        _npm(runner, ["dist-tag", "add", f"{PACKAGE_NAME}@{version}", tag,
                      f"--registry={REGISTRY}", f"--userconfig={npmrc}"],
             stage="upload_npm", offline=False, extra_env=extra,
             progress_label=f"更新 npm dist-tag {tag}", timeout=UPLOAD_TIMEOUT)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return True


def upload(tarball: Path, runner: Runner, token: str, tag: str, access: str,
           otp: str | None) -> None:
    scratch, npmrc, extra = _auth_scratch(runner, token)
    try:
        if otp:
            extra["npm_config_otp"] = otp
        _npm(runner, ["publish", str(tarball), "--ignore-scripts",
                      f"--registry={REGISTRY}", f"--userconfig={npmrc}",
                      f"--tag={tag}", f"--access={access}"],
             stage="upload_npm", offline=False, extra_env=extra,
             progress_label="上传 npm 工件（npm publish）", timeout=UPLOAD_TIMEOUT)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
