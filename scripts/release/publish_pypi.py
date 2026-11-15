"""PyPI adapter: build, verify, reconcile and upload Python artifacts.

This module owns *only* Python-artifact concerns.  It never touches Git,
never bumps the caller repository, never asks for a password and never creates
a GitHub Release — the unified orchestrator does those.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from release.common import ReleaseError, Runner, sha256_file

NAME = "pypi"
RELEASE_FILES = ("pyproject.toml", "dragon_quant/_version.py")
DEFAULT_PYPI_JSON = "https://pypi.org/pypi/dragon-quant/json"


def current_version(repo: Path) -> str:
    text = (Path(repo) / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', text, re.MULTILINE)
    if not match:
        raise ReleaseError("preflight", "pyproject.toml 缺少版本行")
    return match.group(1)


# ─── isolated preparation ──────────────────────────────────────────────────


def apply_versions(staging: Path, version: str) -> list[str]:
    targets = {
        "pyproject.toml": (re.compile(r'^version = "[^"]*"$', re.MULTILINE),
                           f'version = "{version}"'),
        "dragon_quant/_version.py": (re.compile(r'^__version__ = "[^"]*"$', re.MULTILINE),
                                     f'__version__ = "{version}"'),
    }
    for relative, (pattern, replacement) in targets.items():
        path = staging / relative
        original = path.read_text(encoding="utf-8")
        updated, count = pattern.subn(replacement, original)
        if count != 1:
            raise ReleaseError("prepare", f"{relative} 中未找到唯一的版本行（匹配 {count} 次）")
        path.write_text(updated, encoding="utf-8")
        changed = [
            (a, b) for a, b in zip(original.splitlines(), updated.splitlines()) if a != b
        ]
        if len(original.splitlines()) != len(updated.splitlines()) or len(changed) > 1:
            raise ReleaseError("prepare", f"{relative} 版本替换波及了意外行")
    return list(RELEASE_FILES)


def build_frontend(staging: Path, runner: Runner) -> None:
    runner.run(
        ["npm", "--prefix", "web_ui/frontend", "run", "build"],
        cwd=staging, stage="prepare", scrub=True,
        env={"PATH": runner.env_for().get("PATH", ""),
             "HOME": runner.env_for().get("HOME", "")},
    )
    if not (staging / "web_ui" / "dist" / "index.html").is_file():
        raise ReleaseError("prepare", "前端构建未产出 web_ui/dist/index.html")


def run_release_tests(staging: Path, python: str, runner: Runner) -> None:
    """Release-necessary tests, run inside the staging tree (not the caller repo)."""
    scratch = Path(tempfile.mkdtemp(prefix="dq-release-tests-"))
    try:
        runner.run(
            [python, "-m", "pytest", "-q",
             "dragon_quant/tests/test_architecture_rules.py",
             "dragon_quant/tests/test_skill_package.py",
             "dragon_quant/tests/test_research_facade.py"],
            cwd=staging, stage="prepare", scrub=True,
            env={"PYTHONPATH": str(staging), "HOME": str(scratch / "home"),
                 "TMPDIR": str(scratch), "TMP": str(scratch),
                 "DQ_DATA_DIR": str(scratch / "data")},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def build_artifacts(staging: Path, python: str, runner: Runner) -> list[Path]:
    shutil.rmtree(staging / "dist", ignore_errors=True)
    # ``--no-isolation`` keeps the build offline: missing backends are a clear
    # preflight failure, never an implicit network install.
    runner.run([python, "-m", "build", "--no-isolation"], cwd=staging,
               stage="prepare", scrub=True)
    artifacts = sorted(
        path for path in (staging / "dist").iterdir()
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )
    if not any(p.suffix == ".whl" for p in artifacts) or not any(
            p.name.endswith(".tar.gz") for p in artifacts):
        raise ReleaseError("prepare", f"构建产物不完整：{[p.name for p in artifacts]}")
    runner.run([python, "-m", "twine", "check", *[str(p) for p in artifacts]],
               cwd=staging, stage="verify", scrub=True)
    return artifacts


def _forbidden_member(name: str) -> bool:
    lowered = name.lower()
    return (
        "/tests/" in lowered
        or lowered.startswith("tests/")
        or "/docs/" in lowered
        or lowered.startswith("docs/")
        or "publish_token" in lowered
        or "npm_token" in lowered
        or lowered.endswith(".enc")
        or lowered.startswith(".git/")
    )


def verify_package_boundary(artifacts: list[Path]) -> None:
    for artifact in artifacts:
        if artifact.suffix == ".whl":
            with zipfile.ZipFile(artifact) as archive:
                names = archive.namelist()
        else:
            with tarfile.open(artifact, "r:gz") as archive:
                names = archive.getnames()
        bad = [name for name in names if _forbidden_member(name)]
        if bad:
            raise ReleaseError("verify", f"{artifact.name} 含不该发布的文件：{bad[:5]}")
        if artifact.suffix == ".whl":
            if not any(name.startswith("dragon_quant/") for name in names):
                raise ReleaseError("verify", f"{artifact.name} 缺少 dragon_quant 包")
            if not any(name.startswith("web_ui/dist/") for name in names):
                raise ReleaseError("verify", f"{artifact.name} 缺少 web_ui/dist 构建产物")


def prepare(repo: Path, staging: Path, version: str, python: str,
            runner: Runner) -> list[Path]:
    apply_versions(staging, version)
    run_release_tests(staging, python, runner)
    build_frontend(staging, runner)
    artifacts = build_artifacts(staging, python, runner)
    verify_package_boundary(artifacts)
    return artifacts


def digests(artifacts: list[Path]) -> dict[str, str]:
    return {path.name: sha256_file(path) for path in artifacts}


def verify_local(staging: Path, expected: dict[str, str]) -> list[Path]:
    artifacts: list[Path] = []
    for name, digest in expected.items():
        path = Path(staging) / "dist" / name
        if not path.is_file():
            raise ReleaseError("resume", f"暂存工件缺失：{name}；请人工核对已发布内容")
        if sha256_file(path) != digest:
            raise ReleaseError("resume", f"暂存工件哈希变化：{name}；请人工核对")
        artifacts.append(path)
    return artifacts


# ─── registry reconciliation ──────────────────────────────────────────────


def _payload(stage: str = "upload_pypi") -> dict:
    url = os.environ.get("DQ_RELEASE_PYPI_URL", DEFAULT_PYPI_JSON)
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"releases": {}}
        raise ReleaseError(stage, f"查询 PyPI 失败（HTTP {exc.code}）") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ReleaseError(
            stage, f"无法连接 PyPI 核验（网络失败不能当作不存在）：{exc}") from exc


def remote_artifacts(version: str, stage: str = "upload_pypi") -> dict[str, str] | None:
    """Return ``{filename: sha256}`` already on the registry, or ``None``."""
    releases = _payload(stage).get("releases") or {}
    if version not in releases:
        return None
    result: dict[str, str] = {}
    for item in releases.get(version) or []:
        name = item.get("filename")
        digest = (item.get("digests") or {}).get("sha256")
        if not (isinstance(name, str) and isinstance(digest, str)):
            raise ReleaseError(
                "upload_pypi", "远端已有版本但工件元数据不完整；无法核验，请人工处理")
        result[name] = digest
    if not result:
        raise ReleaseError(
            "upload_pypi",
            "远端已有版本但没有可核验工件；无法确认上传完整性，请人工处理",
        )
    return result


def version_exists(version: str, stage: str = "preflight") -> bool:
    """True only when the version has at least one usable, non-yanked artifact.

    A version whose every file is yanked is not installable, so it cannot
    satisfy the npm package's minimum-CLI dependency.
    """
    releases = _payload(stage).get("releases") or {}
    entries = releases.get(version) or []
    for item in entries:
        if not isinstance(item, dict):
            continue
        if item.get("yanked"):
            continue
        if item.get("filename"):
            return True
    return False


def reconcile(version: str, expected: dict[str, str]) -> list[str]:
    """Return filenames that still need an upload; reject any mismatch."""
    remote = remote_artifacts(version)
    if remote is None:
        return sorted(expected)
    extra = sorted(set(remote) - set(expected))
    if extra:
        raise ReleaseError(
            "upload_pypi", f"远端版本含状态外的工件，需人工核对：{extra}")
    mismatch = [name for name, digest in remote.items() if expected.get(name) != digest]
    if mismatch:
        raise ReleaseError(
            "upload_pypi", f"远端已有同名工件但哈希不一致，停止重传：{mismatch}")
    return sorted(name for name in expected if name not in remote)


def upload(artifacts: list[Path], python: str, runner: Runner, token: str) -> None:
    runner.run(
        [python, "-m", "twine", "upload", *[str(p) for p in artifacts]],
        stage="upload_pypi", scrub=True,
        env={"TWINE_USERNAME": "__token__", "TWINE_PASSWORD": token},
    )


def changelog_notes(repo: Path, version: str) -> str | None:
    """Return the CHANGELOG body for ``version``, or ``None`` when absent.

    A missing (or empty) section is *not* an error: the orchestrator falls back
    to a deterministic git change list and never rewrites CHANGELOG.md itself.
    """
    path = Path(repo) / "CHANGELOG.md"
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(r"^## \[" + re.escape(version) + r"\](\s|$)")
    start = next((i for i, line in enumerate(lines) if pattern.match(line.strip())), None)
    if start is None:
        return None
    body: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## ["):
            break
        if line.strip().startswith(("版本依据", "合入记录")):
            continue
        body.append(line)
    text = "\n".join(body).strip()
    return text + "\n" if text else None
