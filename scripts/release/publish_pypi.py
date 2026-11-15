"""PyPI adapter: build, verify, reconcile and upload Python artifacts.

This module owns *only* Python-artifact concerns.  It never touches Git,
never bumps the caller repository, never asks for a password and never creates
a GitHub Release — the unified orchestrator does those.
"""

from __future__ import annotations

import http.client
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

from release import consistency
from release.common import (
    BUILD_TIMEOUT,
    REGISTRY_PROBE_TIMEOUT,
    UPLOAD_TIMEOUT,
    ProgressReporter,
    ReleaseError,
    Runner,
    progress_task,
    sha256_file,
)

NAME = "pypi"
PACKAGE_NAME = "dragon-quant"
RELEASE_FILES = ("pyproject.toml", "dragon_quant/_version.py")
# Version-level endpoint: ``urls`` is exactly this version's files, so a fresh
# upload is not hidden behind the project-level document's 900 s cache.
DEFAULT_PYPI_VERSION_JSON = f"https://pypi.org/pypi/{PACKAGE_NAME}/{{version}}/json"
PYPI_USER_AGENT = (
    "dragon-quant-release (+https://github.com/gitBingxu/dragon-quant)"
)


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
        progress_label="构建前端产物（npm run build）", timeout=BUILD_TIMEOUT,
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
            progress_label="运行 Python 发布离线测试", timeout=BUILD_TIMEOUT,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def build_artifacts(staging: Path, python: str, runner: Runner) -> list[Path]:
    shutil.rmtree(staging / "dist", ignore_errors=True)
    # ``--no-isolation`` keeps the build offline: missing backends are a clear
    # preflight failure, never an implicit network install.
    runner.run([python, "-m", "build", "--no-isolation"], cwd=staging,
               stage="prepare", scrub=True,
               progress_label="构建 Python wheel/sdist", timeout=BUILD_TIMEOUT)
    artifacts = sorted(
        path for path in (staging / "dist").iterdir()
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )
    if not any(p.suffix == ".whl" for p in artifacts) or not any(
            p.name.endswith(".tar.gz") for p in artifacts):
        raise ReleaseError("prepare", f"构建产物不完整：{[p.name for p in artifacts]}")
    runner.run([python, "-m", "twine", "check", *[str(p) for p in artifacts]],
               cwd=staging, stage="verify", scrub=True,
               progress_label="twine check 校验工件", timeout=BUILD_TIMEOUT)
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


def version_url(version: str) -> str:
    """Version-level endpoint; a test fixture may override it wholesale.

    ``DQ_RELEASE_PYPI_URL`` keeps working for the local JSON fixtures used by
    the release tests.  A ``{version}`` placeholder is filled in; an override
    without one is used verbatim (project-level shape is still understood).
    """
    override = os.environ.get("DQ_RELEASE_PYPI_URL")
    if override:
        return override.replace("{version}", version)
    return DEFAULT_PYPI_VERSION_JSON.replace("{version}", version)


def _document(version: str, *, stage: str, timeout: float) -> dict | None:
    """Fetch the JSON document; ``None`` only for a genuine 404 (absent).

    Transient transport problems / 429 / 5xx raise :class:`PendingVisibility`
    (a ``ReleaseError``), so they can never be mistaken for "not published";
    401/403 and malformed content fail immediately.
    """
    request = urllib.request.Request(
        version_url(version),
        headers={
            "Accept": "application/json",
            # Sent as a courtesy; a CDN may still answer from its cache, so
            # nothing here *claims* no-cache bypasses propagation delay.
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": PYPI_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(timeout, 0.1)) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code == 429 or 500 <= exc.code < 600:
            raise consistency.PendingVisibility(
                stage, f"PyPI 查询暂时失败（HTTP {exc.code}），将按传播等待重试") from exc
        raise ReleaseError(
            stage, f"查询 PyPI 失败（HTTP {exc.code}）；鉴权或请求错误不重试") from exc
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        raise consistency.PendingVisibility(
            stage, f"无法连接 PyPI 核验（网络失败不能当作不存在）：{exc}") from exc
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ReleaseError(
            stage, f"PyPI 返回内容不是合法 JSON；不能当作不存在：{exc}") from exc
    if not isinstance(document, dict):
        raise ReleaseError(
            stage, "PyPI 响应不是 JSON 对象；无法确认状态，不能当作不存在")
    return document


def _view(document: dict | None, version: str, stage: str) -> dict | None:
    """Normalize a version-level (``urls``) or project-level (``releases``) doc.

    Returns ``{"present": bool, "artifacts": {name: {"sha256", "yanked"}}}`` or
    ``None`` for a genuine 404.  Structural problems are hard failures, never a
    silent "does not exist".
    """
    if document is None:
        return None
    if "urls" in document:
        entries = document.get("urls")
        info = document.get("info")
        if isinstance(info, dict):
            reported = info.get("version")
            if isinstance(reported, str) and reported != version:
                raise ReleaseError(
                    stage,
                    f"PyPI 版本级响应版本 {reported} 与请求 {version} 不符；拒绝继续")
        present = True
    elif "releases" in document:
        releases = document.get("releases")
        if not isinstance(releases, dict):
            raise ReleaseError(stage, "PyPI releases 字段结构异常；不能当作不存在")
        present = version in releases
        entries = releases.get(version) or []
    else:
        raise ReleaseError(
            stage, "PyPI 响应既无 urls 也无 releases；无法确认状态，不能当作不存在")
    if not isinstance(entries, list):
        raise ReleaseError(stage, "PyPI 工件列表结构异常；不能当作不存在")
    artifacts: dict[str, dict] = {}
    for item in entries:
        if not isinstance(item, dict):
            raise ReleaseError(stage, "PyPI 工件条目结构异常；不能当作不存在")
        name = item.get("filename")
        digest = (item.get("digests") or {}).get("sha256")
        if not (isinstance(name, str) and name and isinstance(digest, str) and digest):
            raise ReleaseError(
                stage, "远端版本工件元数据不完整（filename/sha256）；无法核验，请人工处理")
        artifacts[name] = {"sha256": digest, "yanked": bool(item.get("yanked"))}
    return {"present": present, "artifacts": artifacts}


def _fetch_view(version: str, *, stage: str, timeout: float) -> dict | None:
    return _view(_document(version, stage=stage, timeout=timeout), version, stage)


def _artifacts(view: dict | None) -> dict[str, str]:
    return {
        name: item["sha256"]
        for name, item in ((view or {}).get("artifacts") or {}).items()
    }


def _installable(view: dict | None) -> bool:
    return any(
        not item["yanked"]
        for item in ((view or {}).get("artifacts") or {}).values()
    )


def _missing(expected: dict[str, str], remote: dict[str, str], stage: str) -> list[str]:
    extra = sorted(set(remote) - set(expected))
    if extra:
        raise ReleaseError(stage, f"远端版本含状态外的工件，需人工核对：{extra}")
    mismatch = [name for name, digest in remote.items() if expected.get(name) != digest]
    if mismatch:
        raise ReleaseError(stage, f"远端已有同名工件但哈希不一致，停止重传：{mismatch}")
    return sorted(name for name in expected if name not in remote)


def remote_artifacts(version: str, stage: str = "upload_pypi",
                     progress: ProgressReporter | None = None) -> dict[str, str] | None:
    """Return ``{filename: sha256}`` already on the registry, or ``None``."""
    with progress_task(progress, f"[{stage}] 查询 PyPI 远端版本信息"):
        view = _fetch_view(version, stage=stage, timeout=REGISTRY_PROBE_TIMEOUT)
    if view is None or not view["present"]:
        return None
    remote = _artifacts(view)
    if not remote:
        raise ReleaseError(
            stage, "远端已有版本但没有可核验工件；无法确认上传完整性，请人工处理")
    return remote


def version_exists(version: str, stage: str = "preflight",
                   progress: ProgressReporter | None = None) -> bool:
    """True only when the version has at least one usable, non-yanked artifact.

    A version whose every file is yanked is not installable, so it cannot
    satisfy the npm package's minimum-CLI dependency.  This is a one-shot
    availability check: a genuinely absent version is a settled answer (there
    is no upload of it to wait for), while a transient read still fails closed
    through :class:`PendingVisibility`'s ``ReleaseError`` base.
    """
    with progress_task(progress, f"[{stage}] 查询 PyPI 版本 {version} 是否可安装"):
        view = _fetch_view(version, stage=stage, timeout=REGISTRY_PROBE_TIMEOUT)
    return bool(view and view["present"] and _installable(view))


def reconcile(version: str, expected: dict[str, str],
              progress: ProgressReporter | None = None, *,
              stage: str = "upload_pypi") -> list[str]:
    """One-shot: filenames that still need an upload; reject any mismatch."""
    with progress_task(progress, f"[{stage}] 对账 PyPI 工件"):
        view = _fetch_view(version, stage=stage, timeout=REGISTRY_PROBE_TIMEOUT)
    if view is None or not view["present"]:
        return sorted(expected)
    remote = _artifacts(view)
    if not remote:
        raise ReleaseError(
            stage, "远端已有版本但没有可核验工件；无法确认上传完整性，请人工处理")
    return _missing(expected, remote, stage)


def settle(version: str, expected: dict[str, str],
           progress: ProgressReporter | None = None, *,
           stage: str = "upload_pypi",
           policy: consistency.WaitPolicy | None = None) -> dict:
    """Bounded wait until *exactly* the expected artifacts are visible.

    Read-only: used after an upload and by every resume path that already
    recorded an accepted/done upload, so nothing is ever re-published.  A
    different hash or an extra unknown artifact fails immediately (a wait must
    not paper over a real conflict); an exhausted budget raises
    :class:`PropagationExhausted` and leaves the batch resumable.
    """
    def probe(remaining: float) -> dict:
        view = _fetch_view(version, stage=stage,
                           timeout=min(remaining, REGISTRY_PROBE_TIMEOUT))
        if view is None or not view["present"]:
            raise consistency.PendingVisibility(stage, f"版本 {version} 暂不可见")
        remote = _artifacts(view)
        if not remote:
            raise consistency.PendingVisibility(
                stage, f"版本 {version} 已出现但工件暂不可见")
        missing = _missing(expected, remote, stage)
        if missing:
            raise consistency.PendingVisibility(stage, f"部分工件暂不可见：{missing}")
        return {"artifacts": remote, "installable": _installable(view)}

    return consistency.wait_for_settled(
        probe, stage=stage, subject=f"PyPI {version} 工件", policy=policy,
        progress=progress)


def upload(artifacts: list[Path], python: str, runner: Runner, token: str) -> None:
    runner.run(
        [python, "-m", "twine", "upload", *[str(p) for p in artifacts]],
        stage="upload_pypi", scrub=True,
        env={"TWINE_USERNAME": "__token__", "TWINE_PASSWORD": token},
        progress_label="上传 PyPI 工件（twine upload）", timeout=UPLOAD_TIMEOUT,
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
