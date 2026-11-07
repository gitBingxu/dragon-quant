#!/usr/bin/env python3
"""Safe PyPI release orchestrator for dragon-quant (stdlib only).

Design (scheme §7):

* version is data passed via argv, never interpolated into source code;
* every check and the build happen in an isolated staging tree *before* any git
  mutation or remote write;
* only the release files (``pyproject.toml`` / ``_version.py`` / built
  ``web_ui/dist``) enter the release commit, and only ``HEAD:<branch>`` plus the
  ``v<version>`` tag are pushed (atomically when supported);
* the exact validated artifacts are uploaded; the same artifacts are hashed and
  recorded so ``--resume`` can verify them instead of rebuilding;
* credentials never reach argv or logs (openssl receives the passphrase through
  the environment, twine through ``TWINE_PASSWORD``).

Exit codes: ``0`` success (including a complete dry-run), ``1`` failure,
``2`` invalid invocation.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
DEFAULT_PYPI_JSON = "https://pypi.org/pypi/dragon-quant/json"
RELEASE_FILES = ("pyproject.toml", "dragon_quant/_version.py")
COPY_EXCLUDE = {
    ".git", ".venv", "build", "dist", "node_modules", "__pycache__",
    ".release", ".pytest_cache", ".ruff_cache", ".codebase-memory",
}
STAGES = ("preflight", "prepare", "verify", "commit", "tag", "push", "upload", "release")


class ReleaseError(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


@dataclass
class ReleaseState:
    path: Path
    data: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path, version: str, repo: Path | None = None
             ) -> "ReleaseState":
        state = cls(path=path)
        if path.is_file():
            try:
                state.data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ReleaseError("resume", f"无法读取发布状态 {path}: {exc}")
        if state.data:
            if state.data.get("version") != version:
                raise ReleaseError(
                    "resume",
                    f"状态目录属于 {state.data.get('version')}，与本次 {version} 不一致；"
                    "请人工核对后再继续",
                )
            recorded_repo = state.data.get("repo")
            if repo is not None and recorded_repo and Path(recorded_repo) != repo.resolve():
                raise ReleaseError(
                    "resume",
                    f"状态目录属于仓库 {recorded_repo}，与当前 {repo.resolve()} 不一致",
                )
        state.data.setdefault("version", version)
        if repo is not None:
            state.data.setdefault("repo", str(repo.resolve()))
        state.data.setdefault("stages", {})
        state.data.setdefault("artifacts", {})
        state.data.setdefault("notes", [])
        return state

    @property
    def stages(self) -> dict:
        return self.data["stages"]

    def done(self, name: str) -> bool:
        return bool(self.stages.get(name, {}).get("done"))

    def mark(self, name: str, **facts) -> None:
        entry = dict(self.stages.get(name) or {})
        entry.update(facts)
        entry["done"] = True
        entry["finished_at"] = _now()
        self.stages[name] = entry
        self.save()

    def fail(self, name: str, message: str) -> None:
        entry = dict(self.stages.get(name) or {})
        entry["done"] = False
        entry["error"] = message
        entry["failed_at"] = _now()
        self.stages[name] = entry
        self.save()

    def skip(self, name: str, message: str, **facts) -> None:
        entry = dict(self.stages.get(name) or {})
        entry.update(facts)
        entry["done"] = False
        entry["skipped"] = True
        entry["note"] = message
        entry["updated_at"] = _now()
        self.stages[name] = entry
        self.save()

    def note(self, message: str) -> None:
        self.data["notes"].append({"at": _now(), "message": message})
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = _now()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, self.path)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


class Runner:
    """Thin subprocess adapter; tests inject fake executables through PATH."""

    def __init__(self, *, cwd: Path, env: dict | None = None):
        self.cwd = cwd
        self.env = env or os.environ.copy()
        self.secrets: list[str] = []

    def run(self, argv: list[str], *, cwd: Path | None = None,
            env: dict | None = None, stage: str = "run",
            capture: bool = True, allow_failure: bool = False) -> subprocess.CompletedProcess:
        merged = dict(self.env)
        if env:
            merged.update(env)
        try:
            result = subprocess.run(
                argv, cwd=str(cwd or self.cwd), env=merged,
                text=True, capture_output=capture, check=False,
            )
        except FileNotFoundError as exc:
            raise ReleaseError(stage, f"缺少命令：{argv[0]}") from exc
        if result.returncode != 0 and capture and not allow_failure:
            detail = _redact((result.stderr or result.stdout or "").strip(), self.secrets)
            raise ReleaseError(
                stage,
                f"命令失败（exit {result.returncode}）：{' '.join(argv[:4])}\n{detail}",
            )
        return result


# ─── preflight ─────────────────────────────────────────────────────────────


def validate_version(version: str) -> str:
    if not VERSION_RE.match(version or ""):
        raise ReleaseError("arguments", f"版本号格式错误（期望 x.y.z）：{version!r}")
    return version


def changelog_notes(repo: Path, version: str) -> str:
    path = repo / "CHANGELOG.md"
    if not path.is_file():
        raise ReleaseError("preflight", f"缺少 {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(r"^## \[" + re.escape(version) + r"\](\s|$)")
    start = next((i for i, line in enumerate(lines) if pattern.match(line.strip())), None)
    if start is None:
        raise ReleaseError("preflight", f"CHANGELOG.md 缺少 [{version}] 段")
    body: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## ["):
            break
        if line.strip().startswith(("版本依据", "合入记录")):
            continue
        body.append(line)
    text = "\n".join(body).strip()
    if not text:
        raise ReleaseError("preflight", f"CHANGELOG.md [{version}] 段为空")
    return text + "\n"


def _git(repo: Path, runner: Runner, *args: str, stage: str = "preflight",
         check: bool = True) -> subprocess.CompletedProcess:
    return runner.run(["git", "-C", str(repo), *args], stage=stage, capture=True) if check \
        else subprocess.run(["git", "-C", str(repo), *args], cwd=str(repo),
                            env=runner.env, text=True, capture_output=True, check=False)


def check_clean_worktree(repo: Path, runner: Runner) -> None:
    status = _git(repo, runner, "status", "--porcelain").stdout.strip()
    if status:
        raise ReleaseError(
            "preflight",
            "工作区不干净（含暂存/未跟踪文件），发布前请先提交或清理：\n" + status,
        )
    top = _git(repo, runner, "rev-parse", "--show-toplevel").stdout.strip()
    if Path(top).resolve() != repo.resolve():
        raise ReleaseError("preflight", f"--repo-root 不是 git 工作区根：{top}")


def _current_upstream(repo: Path, runner: Runner) -> str | None:
    result = _git(repo, runner, "rev-parse", "--abbrev-ref",
                  "--symbolic-full-name", "@{u}", check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _normalize_push_url(url: str) -> str:
    """Strip http(s) userinfo before hashing; never persist the raw URL."""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    authority, sep, path = rest.partition("/")
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    return f"{scheme}://{authority}{sep}{path}"


def _push_target_fingerprint(repo: Path, runner: Runner) -> str:
    result = _git(repo, runner, "remote", "get-url", "--push", "origin",
                  check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise ReleaseError(
            "preflight", "未找到 origin 的 push URL；发布固定推送到 origin")
    normalized = _normalize_push_url(result.stdout.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def resolve_identity(repo: Path, runner: Runner) -> dict:
    """Current immutable release identity; fixed origin/<branch> contract."""
    branch = _git(repo, runner, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if not branch or branch == "HEAD":
        raise ReleaseError(
            "preflight", "当前处于 detached HEAD；发布必须基于有 upstream 的命名分支")
    upstream = _current_upstream(repo, runner)
    if not upstream:
        raise ReleaseError(
            "preflight", f"分支 {branch} 没有 upstream；请先设置 origin/{branch} 跟踪分支")
    expected = f"origin/{branch}"
    if upstream != expected:
        raise ReleaseError(
            "preflight",
            f"当前 upstream 为 {upstream}，与固定发布目标 {expected} 不一致；"
            "请在 git 中显式调整跟踪分支或分支名后再发布（脚本不会修改 git 配置）")
    return {
        "branch": branch,
        "upstream": upstream,
        "push_target_fingerprint": _push_target_fingerprint(repo, runner),
    }


def verify_recorded_identity(repo: Path, runner: Runner,
                             state: "ReleaseState") -> dict:
    """Read-only precheck: recorded upstream/push target must still match."""
    recorded_upstream = state.data.get("upstream")
    recorded_target = state.data.get("push_target_fingerprint")
    if not recorded_upstream or not recorded_target:
        raise ReleaseError(
            "resume",
            "旧状态缺少 upstream / push target 身份，无法证明同一发布目标；"
            "请人工核对后使用新的状态目录重新发布，脚本不会补写当前值")
    current = resolve_identity(repo, runner)
    if current["branch"] != state.data.get("branch"):
        raise ReleaseError(
            "resume",
            f"分支已变化：记录 {state.data.get('branch')}，当前 {current['branch']}")
    if current["upstream"] != recorded_upstream:
        raise ReleaseError(
            "resume",
            f"upstream 已变化：记录 {recorded_upstream}，当前 {current['upstream']}；"
            "拒绝继续，也不会覆盖原始记录")
    if current["push_target_fingerprint"] != recorded_target:
        raise ReleaseError(
            "resume", "origin push URL 指纹与记录不一致；拒绝继续")
    return current


def check_tags(repo: Path, runner: Runner, version: str, *,
               skip_local: bool = False, skip_remote: bool = False) -> None:
    if not skip_local:
        local = _git(repo, runner, "tag", "--list", f"v{version}").stdout.strip()
        if local:
            raise ReleaseError("preflight", f"本地已存在标签 v{version}")
    if skip_remote:
        return
    remote = _git(repo, runner, "ls-remote", "--exit-code", "--tags", "origin",
                  f"refs/tags/v{version}", check=False)
    if remote.returncode == 0 and remote.stdout.strip():
        raise ReleaseError("preflight", f"远端已存在标签 v{version}")
    if remote.returncode not in (0, 2):
        raise ReleaseError(
            "preflight",
            f"无法确认远端标签状态（不能当作不存在）：{_redact(remote.stderr, runner.secrets)}")


def check_remote_version(version: str) -> None:
    """Fail closed: an unreachable registry is not evidence the version is new."""
    url = os.environ.get("DQ_RELEASE_PYPI_URL", DEFAULT_PYPI_JSON)
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return
        raise ReleaseError("preflight", f"查询 PyPI 版本失败（HTTP {exc.code}）") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ReleaseError(
            "preflight", f"无法连接 PyPI 校验版本（网络失败不能当作版本不存在）：{exc}") from exc
    releases = payload.get("releases") or {}
    if version in releases:
        raise ReleaseError("preflight", f"PyPI 已存在版本 {version}")


def check_tools(repo: Path, python: str, runner: Runner) -> None:
    if not Path(python).exists():
        raise ReleaseError("preflight", f"发布解释器不存在：{python}")
    runner.run([python, "-c", "import sys; assert sys.version_info >= (3, 10)"],
               stage="preflight")
    for module in ("build", "twine"):
        result = subprocess.run(
            [python, "-c", f"import {module}"], text=True, capture_output=True, check=False)
        if result.returncode != 0:
            raise ReleaseError(
                "preflight",
                f"发布解释器缺少 {module}；请在该虚拟环境中安装（脚本不会自动安装）："
                f" {python} -m pip install build twine",
            )
    if shutil.which("npm", path=runner.env.get("PATH")) is None:
        raise ReleaseError("preflight", "缺少 npm（前端 dist 必须由发布者预先安装依赖）")
    frontend = repo / "web_ui" / "frontend"
    if not (frontend / "node_modules").is_dir():
        raise ReleaseError(
            "preflight",
            "web_ui/frontend/node_modules 缺失；请先在该目录 npm install（脚本不会自动安装）",
        )


def resolve_python(repo: Path) -> str:
    explicit = os.environ.get("DQ_RELEASE_PYTHON", "").strip()
    if explicit:
        return explicit
    venv = repo / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    raise ReleaseError(
        "preflight",
        "未找到发布解释器：请设置 DQ_RELEASE_PYTHON，或在仓库创建 .venv 并安装 build/twine。",
    )


# ─── isolated preparation ──────────────────────────────────────────────────


def _tracked_files(repo: Path, runner: Runner) -> list[str]:
    raw = runner.run(["git", "-C", str(repo), "ls-files", "-z"], stage="prepare").stdout
    return [item for item in raw.split("\0") if item]


def copy_source_tree(repo: Path, dest: Path, runner: Runner) -> None:
    """Copy the tracked tree (working tree is proven clean) without VCS/venv junk."""
    for relative in _tracked_files(repo, runner):
        source = repo / relative
        if not source.is_file():
            continue
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    modules = repo / "web_ui" / "frontend" / "node_modules"
    link = dest / "web_ui" / "frontend" / "node_modules"
    if modules.is_dir() and not link.exists():
        try:
            link.symlink_to(modules, target_is_directory=True)
        except OSError:
            shutil.copytree(modules, link, symlinks=True)


def write_version(staging: Path, version: str) -> None:
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
        path.write_text(updated, encoding="utf-8")        # Prove the only change is the intended line.
        changed = [
            (a, b) for a, b in zip(original.splitlines(), updated.splitlines()) if a != b
        ]
        if len(original.splitlines()) != len(updated.splitlines()) or len(changed) > 1:
            raise ReleaseError("prepare", f"{relative} 版本替换波及了意外行")


def build_frontend(staging: Path, python: str, runner: Runner, repo: Path) -> None:
    runner.run(
        ["npm", "--prefix", "web_ui/frontend", "run", "build"],
        cwd=staging, stage="prepare",
        env={"PATH": runner.env.get("PATH", ""), "HOME": runner.env.get("HOME", "")},
    )
    dist_index = staging / "web_ui" / "dist" / "index.html"
    if not dist_index.is_file():
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
            cwd=staging, stage="prepare",
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
    runner.run([python, "-m", "build", "--no-isolation"], cwd=staging, stage="prepare")
    artifacts = sorted(
        path for path in (staging / "dist").iterdir()
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )
    if not any(p.suffix == ".whl" for p in artifacts) or not any(
            p.name.endswith(".tar.gz") for p in artifacts):
        raise ReleaseError("prepare", f"构建产物不完整：{[p.name for p in artifacts]}")
    runner.run([python, "-m", "twine", "check", *[str(p) for p in artifacts]],
               cwd=staging, stage="verify")
    return artifacts


def _artifact_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _forbidden_member(name: str) -> bool:
    lowered = name.lower()
    return (
        "/tests/" in lowered
        or lowered.startswith("tests/")
        or "/docs/" in lowered
        or lowered.startswith("docs/")
        or "publish_token" in lowered
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


def prepare(repo: Path, version: str, python: str, runner: Runner,
            state: ReleaseState) -> tuple[Path, list[Path]]:
    staging = Path(tempfile.mkdtemp(prefix="dq-release-"))
    state.data["staging"] = str(staging)
    state.save()
    copy_source_tree(repo, staging, runner)
    write_version(staging, version)
    run_release_tests(staging, python, runner)
    build_frontend(staging, python, runner, repo)
    artifacts = build_artifacts(staging, python, runner)
    digests = {path.name: _artifact_digest(path) for path in artifacts}
    state.data["artifacts"] = digests
    state.save()
    return staging, artifacts


# ─── credentials / upload ──────────────────────────────────────────────────


def remote_artifacts(version: str) -> dict[str, str] | None:
    """Return ``{filename: sha256}`` already on the registry, or ``{}``.

    A version present with no/partial metadata is not assumed complete; callers
    compare names+hashes and stop on mismatch rather than skip-existing.
    """
    url = os.environ.get("DQ_RELEASE_PYPI_URL", DEFAULT_PYPI_JSON)
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise ReleaseError("upload", f"查询 PyPI 工件失败（HTTP {exc.code}）") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ReleaseError(
            "upload", f"无法核验远端工件（网络失败不能当作未上传）：{exc}") from exc
    releases = payload.get("releases") or {}
    if version not in releases:
        return None
    result: dict[str, str] = {}
    for item in releases.get(version) or []:
        name = item.get("filename")
        digest = (item.get("digests") or {}).get("sha256")
        if not (isinstance(name, str) and isinstance(digest, str)):
            raise ReleaseError(
                "upload", "远端已有版本但工件元数据不完整；无法核验，请人工处理")
        result[name] = digest
    if not result:
        raise ReleaseError(
            "upload", "远端已有版本但没有可核验工件；无法确认上传完整性，请人工处理")
    return result


def obtain_token(repo: Path, runner: Runner) -> str:
    direct = os.environ.get("TWINE_PASSWORD", "")
    if direct:
        runner.secrets.append(direct)
        return direct
    password = os.environ.get("DQ_PUBLISH_PASSWORD", "")
    if not password:
        if not sys.stdin.isatty():
            raise ReleaseError(
                "upload", "非交互环境缺少 DQ_PUBLISH_PASSWORD 或 TWINE_PASSWORD")
        password = getpass.getpass("发布密码（解密 scripts/publish_token.enc）: ")
    if not password:
        raise ReleaseError("upload", "发布密码为空")
    runner.secrets.append(password)
    enc = repo / "scripts" / "publish_token.enc"
    if not enc.is_file():
        raise ReleaseError("upload", f"缺少 {enc}")
    result = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-d", "-in", str(enc),
         "-pass", "env:DQ_PUBLISH_PASSWORD"],
        text=True, capture_output=True, check=False,
        env={**runner.env, "DQ_PUBLISH_PASSWORD": password},
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ReleaseError("upload", "解密失败（密码错误或加密文件损坏）")
    token = result.stdout.strip()
    runner.secrets.append(token)
    return token


def upload(artifacts: list[Path], python: str, runner: Runner, token: str) -> None:
    runner.run(
        [python, "-m", "twine", "upload", *[str(p) for p in artifacts]],
        stage="upload",
        env={"TWINE_USERNAME": "__token__", "TWINE_PASSWORD": token},
    )


# ─── git mutation ──────────────────────────────────────────────────────────


def _swap_dist(repo: Path, staging: Path) -> Path:
    """Replace ``web_ui/dist`` with the staged build; return backup path."""
    target = repo / "web_ui" / "dist"
    incoming = repo / "web_ui" / "dist.__release_new__"
    backup = repo / "web_ui" / "dist.__release_backup__"
    shutil.rmtree(incoming, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    shutil.copytree(staging / "web_ui" / "dist", incoming)
    if target.exists():
        target.rename(backup)
    try:
        incoming.rename(target)
    except BaseException:
        if not target.exists() and backup.exists():
            backup.rename(target)
        raise
    return backup


def commit_release(repo: Path, staging: Path, version: str,
                   runner: Runner) -> str:
    """Stage exactly the release files; restore the old tree on any failure."""
    status = runner.run(["git", "-C", str(repo), "status", "--porcelain"],
                        stage="commit").stdout.strip()
    if status:
        raise ReleaseError(
            "commit", "准备期间工作区发生变化；拒绝夹带其他改动：\n" + status)
    allowed = {"pyproject.toml", "dragon_quant/_version.py"}
    backup_dir = Path(tempfile.mkdtemp(prefix="dq-release-backup-"))
    backup_dist: Path | None = None
    try:
        for relative in RELEASE_FILES:
            original = repo / relative
            saved = backup_dir / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, saved)
            shutil.copy2(staging / relative, original)
        backup_dist = _swap_dist(repo, staging)
        runner.run(["git", "-C", str(repo), "add", "--", "pyproject.toml",
                    "dragon_quant/_version.py", "web_ui/dist"], stage="commit")
        staged = runner.run(
            ["git", "-C", str(repo), "diff", "--cached", "--name-only"],
            stage="commit").stdout.splitlines()
        extra = [name for name in staged
                 if name not in allowed
                 and not name.startswith("web_ui/dist/")]
        if extra:
            raise ReleaseError(
                "commit", f"暂存区出现非发布文件，已中止：{extra[:5]}")
        if not staged:
            # 版本文件与 dist 已是目标状态（例如手工准备过版本）；复用 HEAD，
            # 不创建空的 bump 提交。
            return runner.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              stage="commit").stdout.strip()
        runner.run(["git", "-C", str(repo), "commit", "-m", f"🔖 bump: {version}"],
                   stage="commit")
        return runner.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          stage="commit").stdout.strip()
    except BaseException as exc:
        # Best-effort rollback: unstage exact paths, restore files and dist.
        runner.run(["git", "-C", str(repo), "reset", "-q", "--",
                    "pyproject.toml", "dragon_quant/_version.py", "web_ui/dist"],
                   stage="commit", allow_failure=True)
        for relative in RELEASE_FILES:
            saved = backup_dir / relative
            if saved.exists():
                shutil.copy2(saved, repo / relative)
        target = repo / "web_ui" / "dist"
        if backup_dist and backup_dist.exists():
            shutil.rmtree(target, ignore_errors=True)
            backup_dist.rename(target)
        if isinstance(exc, ReleaseError):
            raise
        raise ReleaseError(
            "commit", f"发布文件替换失败，已回滚旧版本文件与 dist：{exc}") from exc
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)
        if backup_dist:
            shutil.rmtree(backup_dist, ignore_errors=True)


def tag_release(repo: Path, version: str, runner: Runner) -> None:
    runner.run(["git", "-C", str(repo), "tag", f"v{version}"], stage="tag")


def push_release(repo: Path, branch: str, version: str, commit: str,
                 runner: Runner) -> None:
    """Push exactly the recorded release object and tag (atomic, no fallback)."""
    result = runner.run(
        ["git", "-C", str(repo), "push", "--atomic", "origin",
         f"{commit}:refs/heads/{branch}", f"refs/tags/v{version}"],
        stage="push", capture=True, allow_failure=True,
    )
    if result.returncode == 0:
        return
    detail = _redact((result.stderr or result.stdout or "").strip(), runner.secrets)
    raise ReleaseError(
        "push",
        "原子推送失败（服务器不支持 --atomic 时不会退化为全标签推送）：\n" + detail,
    )


def _ls_remote(repo: Path, runner: Runner, ref: str) -> dict[str, str]:
    result = runner.run(["git", "-C", str(repo), "ls-remote", "origin", ref],
                        stage="resume", capture=True, allow_failure=True)
    if result.returncode != 0:
        detail = _redact((result.stderr or result.stdout or "").strip(),
                         runner.secrets)
        raise ReleaseError("resume", f"无法核验远端引用 {ref}：{detail}")
    mapping: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            mapping[parts[1]] = parts[0]
    return mapping


def _remote_tag_commit(repo: Path, runner: Runner, version: str) -> str | None:
    refs = _ls_remote(repo, runner, f"refs/tags/v{version}*")
    # annotated tag: peeled ``^{}`` is the commit; lightweight tag points directly.
    return (refs.get(f"refs/tags/v{version}^{{}}")
            or refs.get(f"refs/tags/v{version}"))


def create_github_release(repo: Path, version: str, notes: str, runner: Runner) -> bool:
    if shutil.which("gh", path=runner.env.get("PATH")) is None:
        return False
    handle, raw_path = tempfile.mkstemp(prefix="dq-release-notes-", suffix=".md")
    os.close(handle)
    notes_path = Path(raw_path)
    try:
        notes_path.write_text(notes, encoding="utf-8")
        runner.run(["gh", "release", "create", f"v{version}", "--title", f"v{version}",
                    "--notes-file", str(notes_path)], cwd=repo, stage="release")
    finally:
        notes_path.unlink(missing_ok=True)
    return True


# ─── resume verification ───────────────────────────────────────────────────


def resume_artifacts(state: ReleaseState) -> tuple[Path, list[Path]]:
    staging = Path(state.data.get("staging") or "")
    digests = state.data.get("artifacts") or {}
    artifacts: list[Path] = []
    if not staging.is_dir() or not digests:
        raise ReleaseError(
            "resume", "发布状态缺少可验证的暂存工件；无法确认远端工件身份，请人工处理")
    for name, expected in digests.items():
        path = staging / "dist" / name
        if not path.is_file():
            raise ReleaseError("resume", f"暂存工件缺失：{name}；请人工核对已发布内容")
        if _artifact_digest(path) != expected:
            raise ReleaseError("resume", f"暂存工件哈希变化：{name}；请人工核对")
        artifacts.append(path)
    return staging, artifacts


def verify_remote_facts(repo: Path, runner: Runner, version: str,
                        state: ReleaseState) -> None:
    """Before any mutation on resume: prove local/remote identity still matches."""
    commit_done = state.done("commit")
    tag_done = state.done("tag")
    push_done = state.done("push")
    expected_head = ((state.stages.get("commit") or {}).get("commit")
                     if commit_done else state.data.get("base_commit"))
    if not expected_head:
        raise ReleaseError("resume", "状态缺少 base/release commit，无法核验身份")
    current_head = runner.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              stage="resume").stdout.strip()
    if current_head != expected_head:
        raise ReleaseError(
            "resume",
            f"HEAD 已变化：记录 {expected_head}，当前 {current_head}；"
            "拒绝在新提交上继续发布")
    current_branch = runner.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        stage="resume").stdout.strip()
    if state.data.get("branch") and current_branch != state.data["branch"]:
        raise ReleaseError(
            "resume",
            f"分支已变化：记录 {state.data['branch']}，当前 {current_branch}")
    release_commit = state.stages.get("commit", {}).get("commit")
    if tag_done:
        tagged = runner.run(
            ["git", "-C", str(repo), "rev-parse", f"v{version}^{{}}"],
            stage="resume", allow_failure=True).stdout.strip()
        if not tagged or (release_commit and tagged != release_commit):
            raise ReleaseError(
                "resume", f"本地标签 v{version} 缺失或目标不一致，拒绝继续")
    if push_done:
        if not release_commit:
            raise ReleaseError("resume", "状态缺少 release commit，无法核验远端")
        branch = state.data.get("branch")
        remote_branch = _ls_remote(repo, runner, f"refs/heads/{branch}")
        if remote_branch.get(f"refs/heads/{branch}") != release_commit:
            raise ReleaseError(
                "resume",
                f"远端分支 {branch} 与记录 commit 不一致或不存在")
        remote_tag = _remote_tag_commit(repo, runner, version)
        if remote_tag != release_commit:
            raise ReleaseError(
                "resume",
                f"远端标签 v{version} 缺失或指向 {remote_tag}，与记录 {release_commit} 不一致")


def parse_args(argv: list[str], repo_default: Path) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="publish.sh",
        description="dragon-quant PyPI 发布（默认 dry-run 之外的所有步骤都先验证后执行）",
    )
    parser.add_argument("--v", "--version", dest="version", required=False,
                        help="目标版本 x.y.z")
    parser.add_argument("--dry-run", action="store_true",
                        help="只做预检/准备/构建/校验，零凭据、零注册表访问、零 git 变更")
    parser.add_argument("--resume", action="store_true",
                        help="按发布状态继续未完成步骤，不重打标签/不自动重置")
    parser.add_argument("--status-dir", default=None,
                        help="发布状态目录（默认 <repo>/.release/publish）")
    parser.add_argument("--repo-root", default=str(repo_default),
                        help=argparse.SUPPRESS)
    parser.add_argument("--passwd", dest="legacy_passwd", default=None,
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.legacy_passwd is not None:
        parser.error(
            "--passwd 已移除（会暴露在进程参数与 shell 历史中）；"
            "请改用 DQ_PUBLISH_PASSWORD 环境变量或交互式隐藏输入。")
    if not args.version:
        parser.error("缺少 --v <version>")
    return args


def _print_stage(name: str, message: str) -> None:
    print(f"==> [{name}] {message}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    repo_default = Path(__file__).resolve().parents[2]
    args = parse_args(argv, repo_default)
    try:
        validate_version(args.version)
    except ReleaseError as exc:
        print(f"错误（{exc.stage}）：{exc}", file=sys.stderr)
        return 2
    repo = Path(args.repo_root).resolve()
    version = args.version
    dry_run = args.dry_run
    default_status = (repo / ".release" /
                      ("publish-dry-run" if dry_run else "publish"))
    status_dir = (Path(args.status_dir) if args.status_dir
                  else Path(os.environ.get("DQ_RELEASE_STATUS_DIR")
                            or default_status))
    runner = Runner(cwd=repo)
    current = "preflight"
    try:
        if not dry_run and not args.resume and (status_dir / "state.json").exists():
            raise ReleaseError(
                "preflight",
                f"发布状态已存在：{status_dir}；请使用 --resume 继续，或人工清理状态目录")
        state = ReleaseState.load(status_dir / "state.json", version, repo)
        if args.resume:
            # Read-only identity checks BEFORE any state save or mutation.
            if not state.data.get("base_commit"):
                raise ReleaseError("resume", "状态缺少 base_commit，无法核验发布身份")
            identity = verify_recorded_identity(repo, runner, state)
            state.data["mode"] = "resume"
            verify_remote_facts(repo, runner, version, state)
            if state.done("prepare"):
                resume_artifacts(state)
        else:
            identity = resolve_identity(repo, runner)
            state.data["branch"] = identity["branch"]
            state.data["upstream"] = identity["upstream"]
            state.data["push_target_fingerprint"] = identity["push_target_fingerprint"]
        branch = identity["branch"]
        if not dry_run:
            check_clean_worktree(repo, runner)
            check_tags(repo, runner, version,
                       skip_local=args.resume and state.done("tag"),
                       skip_remote=args.resume and state.done("push"))
            if not args.resume:
                # Resume 由 upload 阶段按文件名+sha256 核验远端，不用「版本已存在」一刀切。
                check_remote_version(version)
        base_commit = runner.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                 stage="preflight").stdout.strip()
        if args.resume and base_commit != (state.stages.get("commit") or {}).get(
                "commit", state.data.get("base_commit")):
            raise ReleaseError("resume", "HEAD 与发布状态不一致，拒绝继续")
        state.data.setdefault("base_commit", base_commit)
        state.data["mode"] = state.data.get("mode") or "release"
        notes = changelog_notes(repo, version)
        python = resolve_python(repo)
        check_tools(repo, python, runner)
        state.mark("preflight", dry_run=dry_run, branch=branch,
                   base_commit=state.data["base_commit"],
                   upstream=state.data.get("upstream"),
                   push_target_fingerprint=state.data.get(
                       "push_target_fingerprint"))
        _print_stage("preflight", "通过")

        current = "prepare"
        if args.resume and state.done("prepare"):
            staging, artifacts = resume_artifacts(state)
        else:
            staging, artifacts = prepare(repo, version, python, runner, state)
            state.mark("prepare", staging=str(staging))
        _print_stage("prepare", f"测试、构建与打包完成：{[p.name for p in artifacts]}")

        current = "verify"
        if args.resume and state.done("verify"):
            resume_artifacts(state)
        else:
            verify_package_boundary(artifacts)
            state.mark("verify", artifacts=state.data["artifacts"])
        _print_stage("verify", "包边界与产物校验通过")

        if dry_run:
            print(json.dumps({
                "dry_run": True,
                "version": version,
                "pipeline": "pypi",
                "artifacts": state.data["artifacts"],
                "remote_writes": 0,
                "network_calls": 0,
                "notes": "dry-run 不读取凭据、不连接注册表、不改动调用仓库；"
                         "构建使用 --no-isolation",
            }, ensure_ascii=False, indent=2))
            return 0

        current = "commit"
        if state.done("commit"):
            commit = state.stages["commit"]["commit"]
        else:
            head_now = runner.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                  stage="commit").stdout.strip()
            if head_now != state.data["base_commit"]:
                raise ReleaseError(
                    "commit", "准备期间 HEAD 变化；拒绝在新提交上发布")
            commit = commit_release(repo, staging, version, runner)
            state.mark("commit", commit=commit, base_commit=state.data["base_commit"])
        _print_stage("commit", commit)

        current = "tag"
        if not state.done("tag"):
            tag_release(repo, version, runner)
            state.mark("tag", tag=f"v{version}")
        _print_stage("tag", f"v{version}")

        current = "push"
        if not state.done("push"):
            verify_remote_facts(repo, runner, version, state)
            push_release(repo, branch, version, commit, runner)
            state.mark("push", branch=branch, tag=f"v{version}", commit=commit)
        _print_stage("push", f"{commit}:{branch} + v{version}")

        current = "upload"
        if state.done("upload"):
            _print_stage("upload", "已在先前运行完成，跳过重复上传")
        else:
            remote = remote_artifacts(version)
            expected = dict(state.data["artifacts"])
            if remote is not None:
                extra = sorted(set(remote) - set(expected))
                if extra:
                    raise ReleaseError(
                        "upload", f"远端版本含状态外的工件，需人工核对：{extra}")
                mismatch = [name for name, digest in remote.items()
                            if expected.get(name) != digest]
                if mismatch:
                    raise ReleaseError(
                        "upload",
                        f"远端已有同名工件但哈希不一致，停止重传：{mismatch}")
            missing = {name: path for name, path in
                       ((p.name, p) for p in artifacts)
                       if name not in (remote or {})}
            if missing:
                token = obtain_token(repo, runner)
                upload(list(missing.values()), python, runner, token)
            else:
                _print_stage("upload", "远端工件与本地哈希一致，无需重复上传")
            state.mark("upload", artifacts=expected)
            _print_stage("upload", "PyPI 已上传")

        current = "release"
        if not state.done("release"):
            created = create_github_release(repo, version, notes, runner)
            if created:
                state.mark("release", created=True)
            else:
                state.skip("release", "gh CLI 缺失，GitHub Release 保持 pending",
                           created=False)
                state.note("未安装 gh CLI，GitHub Release 保持 pending；"
                           "安装 gh 后可用 --resume 补建")
        if state.done("release"):
            _print_stage("release", "完成")
            print(f"✅ v{version} 发布完成")
        else:
            _print_stage("release", "pending（gh CLI 缺失；PyPI 已完成）")
            print(f"⚠️ v{version} PyPI 已完成，GitHub Release pending；"
                  "安装 gh 后 --resume 补建", file=sys.stderr)
        return 0
    except ReleaseError as exc:
        try:
            if 'state' in locals():
                state.fail(current, str(exc))
        except OSError:
            pass
        print(f"错误（{exc.stage}）：{_redact(str(exc), runner.secrets)}", file=sys.stderr)
        completed = ([name for name in STAGES if state.done(name)]
                     if 'state' in locals() else [])
        print(f"已完成阶段：{completed or ['无']}；失败阶段：{current}", file=sys.stderr)
        print(f"状态目录：{status_dir}（可用 --resume 继续，不会重打标签）",
              file=sys.stderr)
        return 2 if exc.stage == "arguments" else 1


if __name__ == "__main__":
    raise SystemExit(main())
