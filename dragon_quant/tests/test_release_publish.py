"""Unified release-flow safety tests (fake commands, no network, no real publishing).

Covers the residual PyPI-flow safety contract after the unified entry replaced
``publish_pypi.py``'s own orchestration: version injection rejection, dry-run
isolation, build-before-git ordering, exact release files, atomic push without
fallback, credential handling, artifact identity, resume reconciliation and
state tamper rejection.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ENTRY = REPO / "scripts" / "release" / "main.py"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


FAKE_PYTHON = r"""#!/usr/bin/env python3
import hashlib, io, json, os, re, sys, tarfile, zipfile
from pathlib import Path

args = sys.argv[1:]
order = os.environ.get("FAKE_ORDER_LOG")
if order:
    with open(order, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"tool": "py", "argv": args}) + "\n")
log = os.environ.get("FAKE_PY_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "argv": args,
            "env_password": os.environ.get("TWINE_PASSWORD"),
            "env_publish_password": os.environ.get("DQ_PUBLISH_PASSWORD"),
            "env_npm_token": os.environ.get("NPM_TOKEN"),
        }) + "\n")


def _fail(env, prefix):
    if os.environ.get(env) and args[:len(prefix)] == list(prefix):
        print("boom", file=sys.stderr)
        raise SystemExit(1)


_fail("FAKE_PY_FAIL_TEST", ["-m", "pytest"])
_fail("FAKE_PY_FAIL_BUILD", ["-m", "build"])
_fail("FAKE_PY_FAIL_TWINE", ["-m", "twine", "upload"])

if args[:2] == ["-m", "build"] and os.environ.get("FAKE_PY_SLEEP_BUILD"):
    import time
    time.sleep(float(os.environ["FAKE_PY_SLEEP_BUILD"]))

if args[:2] == ["-m", "build"]:
    version = "0.0.0"
    for line in Path("pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            version = line.split('"')[1]
    dist = Path("dist"); dist.mkdir(exist_ok=True)
    wheel = dist / f"dragon_quant-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("dragon_quant/__init__.py", "")
        zf.writestr("web_ui/dist/index.html", "<!doctype html>")
    sdist = dist / f"dragon_quant-{version}.tar.gz"
    with tarfile.open(sdist, "w:gz") as tf:
        data = b"x"; info = tarfile.TarInfo("dragon_quant-0/PKG-INFO"); info.size = 1
        tf.addfile(info, io.BytesIO(data))
    raise SystemExit(0)

if args[:3] == ["-m", "twine", "upload"]:
    url = os.environ.get("DQ_RELEASE_PYPI_URL", "")
    target = os.environ.get("FAKE_PY_PYPI_PATH")
    if not target and url.startswith("file:"):
        from urllib.parse import urlparse
        from urllib.request import url2pathname
        target = url2pathname(urlparse(url).path)
    if target and os.environ.get("FAKE_PY_UPDATE_PYPI") == "1":
        path = Path(target)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"releases": {}}
        for item in args[3:]:
            artifact = Path(item)
            match = re.match(r"dragon_quant-([0-9]+\.[0-9]+\.[0-9]+)", artifact.name)
            version = match.group(1) if match else artifact.name.split("-")[1]
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            data.setdefault("releases", {}).setdefault(version, []).append(
                {"filename": artifact.name, "digests": {"sha256": digest}})
        path.write_text(json.dumps(data), encoding="utf-8")
        # Optional lag simulation: the "CDN" serves 404 for this many reads.
        lag_path = os.environ.get("FAKE_PY_LAG_PATH")
        if lag_path:
            Path(lag_path).write_text(os.environ.get("FAKE_PY_LAG", "0"), encoding="utf-8")
    raise SystemExit(0)

raise SystemExit(0)
"""

FAKE_NPM = r"""#!/usr/bin/env python3
import base64, hashlib, json, os, re, sys
from pathlib import Path

args = sys.argv[1:]
order = os.environ.get("FAKE_ORDER_LOG")
if order:
    with open(order, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"tool": "npm", "argv": args}) + "\n")
log = os.environ.get("FAKE_NPM_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(args) + "\n")
env_log = os.environ.get("FAKE_NPM_ENV_LOG")
if env_log:
    with open(env_log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "argv": args,
            "otp": os.environ.get("npm_config_otp"),
            "npm_otp": os.environ.get("NPM_OTP"),
            "password": os.environ.get("DQ_PUBLISH_PASSWORD"),
        }) + "\n")

state_path = os.environ.get("FAKE_NPM_STATE")
state = {"published": {}, "dist_tags": {}}
if state_path and Path(state_path).exists():
    state = json.loads(Path(state_path).read_text(encoding="utf-8"))


def save():
    if state_path:
        Path(state_path).write_text(json.dumps(state), encoding="utf-8")


for arg in args:
    if arg.startswith("--userconfig=") and os.environ.get("FAKE_NPM_CONFIG_COPY"):
        cfg = Path(arg.split("=", 1)[1])
        Path(os.environ["FAKE_NPM_CONFIG_COPY"]).write_text(cfg.read_text(), encoding="utf-8")

if "--prefix" in args and "run" in args and "build" in args:
    Path("web_ui/dist").mkdir(parents=True, exist_ok=True)
    Path("web_ui/dist/index.html").write_text("<!doctype html>", encoding="utf-8")
    raise SystemExit(0)

if args[:1] == ["test"]:
    raise SystemExit(1 if os.environ.get("FAKE_NPM_FAIL_TEST") else 0)

if args[:1] == ["pack"]:
    staging = args[1]
    if "--dry-run" in args:
        # Real npm returns a one-element list with a nested files array.
        version = json.loads(
            Path(staging, "package.json").read_text(encoding="utf-8"))["version"]
        print(json.dumps([{
            "id": f"dragon-quant-skills@{version}",
            "name": "dragon-quant-skills",
            "version": version,
            "size": 1,
            "unpackedSize": 5,
            "filename": f"dragon-quant-skills-{version}.tgz",
            "files": [
                {"path": "bin/dragon-quant-skills.mjs", "size": 1, "mode": 493},
                {"path": "lib/cli.mjs", "size": 1, "mode": 420},
                {"path": "skills/dragon-quant/SKILL.md", "size": 1, "mode": 420},
                {"path": "README.md", "size": 1, "mode": 420},
                {"path": "LICENSE", "size": 1, "mode": 420},
            ],
            "entryCount": 5,
            "bundled": [],
        }]))
        raise SystemExit(0)
    dest = "."
    for index, arg in enumerate(args):
        if arg == "--pack-destination":
            dest = args[index + 1]
    version = json.loads(Path(staging, "package.json").read_text(encoding="utf-8"))["version"]
    target = Path(dest); target.mkdir(parents=True, exist_ok=True)
    name = f"dragon-quant-skills-{version}.tgz"
    (target / name).write_bytes(f"tarball-{version}".encode())
    print(name)
    raise SystemExit(0)

if args[:1] == ["install"]:
    prefix = "."
    for index, arg in enumerate(args):
        if arg == "--prefix":
            prefix = args[index + 1]
    entry = Path(prefix) / "node_modules" / "dragon-quant-skills" / "bin"
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "dragon-quant-skills.mjs").write_text("", encoding="utf-8")
    raise SystemExit(0)

if args[:1] == ["view"]:
    field = args[2]
    package, _, version = args[1].partition("@")
    if field == "dist-tags":
        print(json.dumps(state.get("dist_tags", {})))
        raise SystemExit(0)
    # Optional lag simulation: the read replica keeps returning E404 for the
    # first N reads after a publish.
    lag = state.get("lag", {}).get(version, 0)
    if lag:
        state.setdefault("lag", {})[version] = lag - 1
        save()
        print("npm error code E404", file=sys.stderr)
        raise SystemExit(1)
    facts = state.get("published", {}).get(version)
    if not facts:
        print("npm error code E404", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(facts))
    raise SystemExit(0)

if args[:1] == ["publish"]:
    if os.environ.get("FAKE_NPM_SLEEP_PUBLISH"):
        import time
        Path(os.environ["FAKE_NPM_SLEEP_MARKER"]).write_text("publishing")
        time.sleep(2)
    if os.environ.get("FAKE_NPM_FAIL_PUBLISH"):
        print("publish failed", file=sys.stderr)
        raise SystemExit(1)
    tarball = Path(args[1])
    match = re.match(r"dragon-quant-skills-(.+)\.tgz", tarball.name)
    version = match.group(1)
    integrity = "sha512-" + base64.b64encode(
        hashlib.sha512(tarball.read_bytes()).digest()).decode()
    state.setdefault("published", {})[version] = {
        "integrity": integrity, "tarball": str(tarball)}
    tag = "latest"
    for arg in args:
        if arg.startswith("--tag="):
            tag = arg.split("=", 1)[1]
    state.setdefault("dist_tags", {})[tag] = version
    state.setdefault("lag", {})[version] = int(os.environ.get("FAKE_NPM_LAG", "0"))
    save()
    raise SystemExit(0)

if args[:2] == ["dist-tag", "add"]:
    version = args[2].split("@")[-1]
    tag = args[3]
    state.setdefault("dist_tags", {})[tag] = version
    save()
    raise SystemExit(0)

raise SystemExit(0)
"""

FAKE_GH = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
if os.environ.get("FAKE_GH_LOG"):
    with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(args) + "\n")
state_path = os.environ.get("FAKE_GH_STATE")
state = {"releases": {}}
if state_path and Path(state_path).exists():
    state = json.loads(Path(state_path).read_text(encoding="utf-8"))

if args[:2] == ["release", "view"]:
    if os.environ.get("FAKE_GH_FAIL_VIEW"):
        print("dial tcp: network unreachable", file=sys.stderr)
        raise SystemExit(1)
    tag = args[2]
    release = state.get("releases", {}).get(tag)
    if not release:
        print("release not found", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({"tagName": tag, "isDraft": False, "body": release["body"]}))
    raise SystemExit(0)

if args[:2] == ["release", "create"]:
    tag = args[2]
    notes = ""
    for index, arg in enumerate(args):
        if arg == "--notes-file":
            notes = Path(args[index + 1]).read_text(encoding="utf-8")
    state.setdefault("releases", {})[tag] = {
        "body": notes, "verify_tag": "--verify-tag" in args}
    if state_path:
        Path(state_path).write_text(json.dumps(state), encoding="utf-8")
    raise SystemExit(0)

raise SystemExit(0)
"""


class ReleaseFixture:
    def __init__(self, tmp_path: Path, *, pypi_current: str = "0.0.1",
                 npm_current: str = "1.0.0"):
        self.tmp = tmp_path
        self.pypi_current = pypi_current
        self.npm_current = npm_current
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.logs = tmp_path / "logs"
        self.logs.mkdir()
        self.fake_python = self.bin / "fake-python"
        _write_executable(self.fake_python, FAKE_PYTHON)
        _write_executable(self.bin / "npm", FAKE_NPM)
        _write_executable(self.bin / "gh", FAKE_GH)
        self.py_log = self.logs / "python.jsonl"
        self.npm_log = self.logs / "npm.log"
        self.gh_log = self.logs / "gh.log"
        self.npm_state = self.logs / "npm-state.json"
        self.gh_state = self.logs / "gh-state.json"
        self.pypi_json_path = tmp_path / "pypi.json"
        self.pypi_json_path.write_text(json.dumps({"releases": {}}), encoding="utf-8")
        self._init_repo()

    def _init_repo(self):
        (self.repo / "pyproject.toml").write_text(
            f'[project]\nname = "dragon-quant"\nversion = "{self.pypi_current}"\n',
            encoding="utf-8")
        (self.repo / "dragon_quant").mkdir()
        (self.repo / "dragon_quant" / "_version.py").write_text(
            f'__version__ = "{self.pypi_current}"\n', encoding="utf-8")
        (self.repo / "package.json").write_text(json.dumps({
            "name": "dragon-quant-skills", "version": self.npm_current,
        }, indent=2) + "\n", encoding="utf-8")
        (self.repo / "skills" / "dragon-quant").mkdir(parents=True)
        (self.repo / "skills" / "dragon-quant" / "SKILL.md").write_text(
            "---\nname: dragon-quant\nlicense: MIT\nmetadata:\n"
            f'  skill-version: "{self.npm_current}"\n'
            f'  min-cli-version: "{self.pypi_current}"\n---\n\n# skill\n',
            encoding="utf-8")
        (self.repo / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [1.2.3]\n\n### Added\n- unified release\n\n"
            f"## [{self.pypi_current}]\n- old\n", encoding="utf-8")
        frontend = self.repo / "web_ui" / "frontend"
        (frontend / "node_modules").mkdir(parents=True)
        (frontend / "node_modules" / ".keep").write_text("", encoding="utf-8")
        (self.repo / "web_ui" / "dist").mkdir(parents=True)
        (self.repo / "web_ui" / "dist" / "index.html").write_text(
            "old", encoding="utf-8")
        self.git("init", "-q", "-b", "release/test")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.origin = self.tmp / "origin.git"
        self.git("init", "-q", "--bare", str(self.origin))
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "-q", "-u", "origin", "release/test")

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.repo), *args], text=True,
                              capture_output=True, check=check)

    def set_pypi_releases(self, releases: dict) -> None:
        self.pypi_json_path.write_text(json.dumps({"releases": releases}),
                                       encoding="utf-8")

    def env(self, *, extra: dict | None = None, fake_path: str | None = None,
            with_gh: bool = True) -> dict:
        if with_gh:
            path = f"{fake_path or self.bin}:{os.environ['PATH']}"
        else:
            no_gh = self.tmp / "no-gh-bin"
            no_gh.mkdir(exist_ok=True)
            for name in ("npm", "fake-python"):
                link = no_gh / name
                if not link.exists():
                    link.symlink_to(self.bin / name)
            path = f"{fake_path or no_gh}:/usr/bin:/bin"
        env = {
            **os.environ,
            "PATH": path,
            "DQ_RELEASE_PYTHON": str(self.fake_python),
            "DQ_RELEASE_PYPI_URL": self.pypi_json_path.as_uri(),
            "DQ_RELEASE_STATUS_DIR": str(self.tmp / "state"),
            "FAKE_PY_LOG": str(self.py_log),
            "FAKE_PY_UPDATE_PYPI": "1",
            "FAKE_NPM_LOG": str(self.npm_log),
            "FAKE_NPM_ENV_LOG": str(self.logs / "npm-env.jsonl"),
            "FAKE_NPM_STATE": str(self.npm_state),
            "FAKE_ORDER_LOG": str(self.logs / "order.jsonl"),
            "TMPDIR": str(self.tmp / "tmpdir"),
            "FAKE_GH_LOG": str(self.gh_log),
            "FAKE_GH_STATE": str(self.gh_state),
            "HOME": str(self.tmp / "home"),
            # Keep propagation waits short by default: a test that deliberately
            # exercises lag overrides these in ``extra``.
            "DQ_RELEASE_PROPAGATION_BUDGET": "1.0",
            "DQ_RELEASE_PROPAGATION_INTERVAL": "0.05",
        }
        (self.tmp / "home").mkdir(exist_ok=True)
        (self.tmp / "tmpdir").mkdir(exist_ok=True)
        env.update(extra or {})
        return env

    def run(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ENTRY), "--repo-root", str(self.repo), *args],
            text=True, capture_output=True, env=env or self.env())

    def state_file(self) -> Path:
        paths = sorted((self.tmp / "state" / "batches").glob("*/state.json"))
        assert len(paths) == 1, paths
        return paths[0]

    def state(self) -> dict:
        return json.loads(self.state_file().read_text(encoding="utf-8"))

    def py_calls(self) -> list[dict]:
        if not self.py_log.exists():
            return []
        return [json.loads(line) for line in self.py_log.read_text().splitlines()]

    def npm_calls(self) -> list[list[str]]:
        if not self.npm_log.exists():
            return []
        return [json.loads(line) for line in self.npm_log.read_text().splitlines()]


def _pypi_args(version: str = "1.2.3", *extra: str) -> list[str]:
    return ["--packages", "pypi", "--pypi-version", version, *extra]


def test_version_injection_is_rejected_before_any_command(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    result = fixture.run("--packages", "pypi", "--pypi-version", '1.2.3"; import os; #')
    assert result.returncode == 2
    assert not fixture.py_log.exists()
    assert not fixture.npm_log.exists()


def test_legacy_secret_and_otp_args_are_rejected(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    for args in (["--passwd", "hunter2"], ["--token", "pypi-secret"],
                 ["--otp", "123456"]):
        result = fixture.run(*_pypi_args(), *args)
        assert result.returncode == 2
        assert "hunter2" not in result.stdout + result.stderr
    assert not fixture.py_log.exists()


def test_non_tty_real_release_requires_yes_and_has_zero_side_effects(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    before = fixture.git("rev-parse", "HEAD").stdout.strip()
    result = fixture.run(*_pypi_args())
    assert result.returncode == 2
    assert "--yes" in result.stderr
    assert not (fixture.tmp / "state").exists()
    assert not fixture.py_log.exists()
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == before


def test_legacy_v_alias_selects_pypi(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    result = fixture.run("--v", "1.2.3", "--dry-run", env=fixture.env(extra={
        "TWINE_PASSWORD": "must-not-be-used"}))
    assert result.returncode == 0, result.stderr
    assert '"packages": [' in result.stdout
    assert "pypi" in result.stdout


def test_dry_run_is_credential_free_and_does_not_mutate_repo(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    before_head = fixture.git("rev-parse", "HEAD").stdout.strip()
    before_pyproject = (fixture.repo / "pyproject.toml").read_text(encoding="utf-8")
    before_version = (fixture.repo / "dragon_quant" / "_version.py").read_text(
        encoding="utf-8")

    result = fixture.run(*_pypi_args("1.2.3", "--dry-run"), env=fixture.env(extra={
        "TWINE_PASSWORD": "must-not-be-used",
        "DQ_PUBLISH_PASSWORD": "must-not-be-used"}))

    assert result.returncode == 0, result.stderr
    assert '"dry_run": true' in result.stdout
    assert "must-not-be-used" not in result.stdout + result.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == before_head
    assert fixture.git("tag", "--list").stdout.strip() == ""
    assert (fixture.repo / "pyproject.toml").read_text(encoding="utf-8") == before_pyproject
    assert (fixture.repo / "dragon_quant" / "_version.py").read_text(
        encoding="utf-8") == before_version
    assert (fixture.repo / "web_ui" / "dist" / "index.html").read_text(
        encoding="utf-8") == "old"
    calls = fixture.py_calls()
    assert not any(call["argv"][:3] == ["-m", "twine", "upload"] for call in calls)
    # Build/test subprocesses must not inherit credentials.
    assert all(call["env_publish_password"] is None for call in calls)
    assert all(call["env_password"] is None for call in calls)
    # No registry access: the fake PyPI file is untouched.
    assert json.loads(fixture.pypi_json_path.read_text()) == {"releases": {}}


def test_dirty_worktree_blocks_release_before_build(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    (fixture.repo / "scratch.txt").write_text("x", encoding="utf-8")
    result = fixture.run(*_pypi_args(), "--yes")
    assert result.returncode == 1
    assert "工作区不干净" in result.stderr
    assert not fixture.py_log.exists()


def test_existing_registry_version_blocks_release(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    fixture.set_pypi_releases({
        "1.2.3": [{"filename": "dragon_quant-1.2.3-py3-none-any.whl",
                   "digests": {"sha256": "0" * 64}}]})
    result = fixture.run(*_pypi_args(), "--yes")
    assert result.returncode == 1
    assert "已存在版本" in result.stderr
    assert fixture.git("tag", "--list").stdout.strip() == ""


def test_build_failure_leaves_zero_git_mutation(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    before_head = fixture.git("rev-parse", "HEAD").stdout.strip()
    result = fixture.run(*_pypi_args(), "--yes", env=fixture.env(
        extra={"FAKE_PY_FAIL_BUILD": "1", "TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == before_head
    assert fixture.git("tag", "--list").stdout.strip() == ""
    assert "0.0.1" in (fixture.repo / "pyproject.toml").read_text(encoding="utf-8")


def test_success_commits_only_release_files_and_uploads_same_artifacts(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    result = fixture.run(*_pypi_args(), "--yes", env=fixture.env(extra={
        "TWINE_PASSWORD": "secret-token-value"}))
    assert result.returncode == 0, result.stderr

    changed = fixture.git("show", "--name-only", "--pretty=format:", "HEAD").stdout.split()
    assert "pyproject.toml" in changed
    assert "dragon_quant/_version.py" in changed
    assert any(name.startswith("web_ui/dist/") for name in changed)
    assert "CHANGELOG.md" not in changed
    assert "secret-token-value" not in result.stdout + result.stderr

    assert fixture.git("tag", "--list", "v1.2.3").stdout.strip() == "v1.2.3"
    state = fixture.state()
    assert state["upstream"] == "origin/release/test"
    assert len(state["push_target_fingerprint"]) == 64
    assert state["complete"] is True
    remote_tags = subprocess.run(
        ["git", "ls-remote", "--tags", str(fixture.origin), "refs/tags/v1.2.3"],
        text=True, capture_output=True, check=True).stdout
    assert remote_tags.strip()

    calls = fixture.py_calls()
    builds = [call for call in calls if call["argv"][:2] == ["-m", "build"]]
    assert builds and "--no-isolation" in builds[0]["argv"]
    uploads = [call for call in calls if call["argv"][:3] == ["-m", "twine", "upload"]]
    assert uploads, calls
    assert uploads[0]["env_password"] == "secret-token-value"
    assert "secret-token-value" not in " ".join(uploads[0]["argv"])
    artifact_names = {Path(arg).name for arg in uploads[0]["argv"][3:]}
    assert any(name.endswith(".whl") for name in artifact_names)
    assert any(name.endswith(".tar.gz") for name in artifact_names)
    # GitHub Release created once with verify-tag.
    gh_calls = fixture.gh_log.read_text().splitlines()
    assert any('"release", "create"' in line for line in gh_calls)


def test_release_succeeds_when_version_already_prepared(tmp_path):
    """手工准备过版本时（pyproject/_version/dist 已是目标状态）复用 HEAD。"""
    fixture = ReleaseFixture(tmp_path)
    (fixture.repo / "pyproject.toml").write_text(
        '[project]\nname = "dragon-quant"\nversion = "1.2.3"\n', encoding="utf-8")
    (fixture.repo / "dragon_quant" / "_version.py").write_text(
        '__version__ = "1.2.3"\n', encoding="utf-8")
    (fixture.repo / "web_ui" / "dist" / "index.html").write_text(
        "<!doctype html>", encoding="utf-8")
    fixture.git("add", "-A")
    fixture.git("commit", "-q", "-m", "prepare release version")
    head = fixture.git("rev-parse", "HEAD").stdout.strip()

    result = fixture.run(*_pypi_args(), "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 0, result.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == head
    assert fixture.git("tag", "--list", "v1.2.3").stdout.strip() == "v1.2.3"
    assert any(call["argv"][:3] == ["-m", "twine", "upload"]
               for call in fixture.py_calls())


def test_atomic_push_failure_does_not_fall_back(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    wrapper_dir = tmp_path / "gitwrap"
    wrapper_dir.mkdir()
    real_git = subprocess.run(["which", "git"], text=True, capture_output=True,
                              check=True).stdout.strip()
    _write_executable(wrapper_dir / "git", f"""#!/bin/sh
printf '%s\\n' "$*" >> "{tmp_path}/git-calls.log"
for arg in "$@"; do
  if [ "$arg" = "--atomic" ]; then echo "atomic unsupported" >&2; exit 1; fi
done
exec "{real_git}" "$@"
""")
    result = fixture.run(*_pypi_args(), "--yes", env=fixture.env(
        fake_path=f"{fixture.bin}:{wrapper_dir}:{os.environ['PATH']}",
        extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "原子推送失败" in result.stderr
    calls = (tmp_path / "git-calls.log").read_text().splitlines()
    assert any("push --atomic" in call for call in calls)
    assert not any(call.startswith("push ") and "--atomic" not in call for call in calls)
    assert not any(call["argv"][:3] == ["-m", "twine", "upload"]
                   for call in fixture.py_calls())


def _fail_upload_env(fixture: ReleaseFixture) -> dict:
    return fixture.env(extra={"TWINE_PASSWORD": "x", "FAKE_PY_FAIL_TWINE": "1"})


def test_resume_after_upload_failure_reuses_commit_tag_and_artifacts(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    first = fixture.run(*_pypi_args(), "--yes", env=_fail_upload_env(fixture))
    assert first.returncode == 1
    head = fixture.git("rev-parse", "HEAD").stdout.strip()
    tags = fixture.git("tag", "--list").stdout.strip()
    assert tags == "v1.2.3"
    digests = dict(fixture.state()["artifacts"]["pypi"])

    second = fixture.run(*_pypi_args(), "--resume", "--yes", env=fixture.env(
        extra={"TWINE_PASSWORD": "x"}))
    assert second.returncode == 0, second.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == head
    assert fixture.git("tag", "--list").stdout.strip() == tags
    final = fixture.state()
    assert final["artifacts"]["pypi"] == digests
    assert final["steps"]["upload_pypi"]["done"] is True


def test_package_boundary_rejects_private_files(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    _write_executable(Path(fixture.fake_python), FAKE_PYTHON.replace(
        'zf.writestr("web_ui/dist/index.html", "<!doctype html>")',
        'zf.writestr("web_ui/dist/index.html", "<!doctype html>")\n'
        '        zf.writestr("dragon_quant/tests/test_secret.py", "")'))
    result = fixture.run(*_pypi_args(), "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "不该发布" in result.stderr
    assert fixture.git("tag", "--list").stdout.strip() == ""


def test_release_tests_failure_blocks_before_any_git_mutation(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    before_head = fixture.git("rev-parse", "HEAD").stdout.strip()
    result = fixture.run(*_pypi_args(), "--yes", env=fixture.env(
        extra={"FAKE_PY_FAIL_TEST": "1", "TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == before_head
    assert fixture.git("tag", "--list").stdout.strip() == ""
    assert any(call["argv"][:2] == ["-m", "pytest"] for call in fixture.py_calls())


def test_non_resume_refuses_unfinished_batch(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    first = fixture.run(*_pypi_args(), "--yes", env=fixture.env(
        extra={"TWINE_PASSWORD": "x", "FAKE_PY_FAIL_BUILD": "1"}))
    assert first.returncode == 1
    second = fixture.run(*_pypi_args(), "--yes", env=fixture.env(
        extra={"TWINE_PASSWORD": "x"}))
    assert second.returncode == 1
    assert "未完成发布批次" in second.stderr


def test_resume_refuses_head_change(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes",
                       env=_fail_upload_env(fixture)).returncode == 1
    fixture.git("commit", "--allow-empty", "-m", "new commit")
    result = fixture.run(*_pypi_args(), "--resume", "--yes", env=fixture.env())
    assert result.returncode == 1
    assert "HEAD 已变化" in result.stderr


def test_resume_refuses_missing_remote_tag(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes",
                       env=_fail_upload_env(fixture)).returncode == 1
    fixture.git("push", "origin", ":" + "refs/tags/v1.2.3")
    result = fixture.run(*_pypi_args(), "--resume", "--yes", env=fixture.env())
    assert result.returncode == 1
    assert "远端" in result.stderr


def test_resume_refuses_changed_artifacts(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes",
                       env=_fail_upload_env(fixture)).returncode == 1
    staging = Path(fixture.state()["staging"])
    artifact = next((staging / "dist").glob("*.whl"))
    artifact.write_bytes(artifact.read_bytes() + b"tamper")
    result = fixture.run(*_pypi_args(), "--resume", "--yes", env=fixture.env())
    assert result.returncode == 1
    assert "哈希变化" in result.stderr


def _remote_entries(artifacts: dict, *, tamper: str | None = None) -> list[dict]:
    return [
        {"filename": name,
         "digests": {"sha256": ("0" * 64 if tamper == name else digest)}}
        for name, digest in artifacts.items()
    ]


def test_resume_skips_already_uploaded_identical_artifacts(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes",
                       env=_fail_upload_env(fixture)).returncode == 1
    artifacts = fixture.state()["artifacts"]["pypi"]
    wheel = next(name for name in artifacts if name.endswith(".whl"))
    fixture.set_pypi_releases({"1.2.3": _remote_entries(
        {wheel: artifacts[wheel]})})
    env = fixture.env(extra={"TWINE_PASSWORD": "x"})
    result = fixture.run(*_pypi_args(), "--resume", "--yes", env=env)
    assert result.returncode == 0, result.stderr
    uploads = [c for c in fixture.py_calls()
               if c["argv"][:3] == ["-m", "twine", "upload"]]
    assert len(uploads) == 2  # failed first attempt + one retry
    names = {Path(arg).name for arg in uploads[-1]["argv"][3:]}
    assert wheel not in names and len(names) == 1


def test_resume_refuses_remote_artifact_hash_mismatch(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes",
                       env=_fail_upload_env(fixture)).returncode == 1
    artifacts = fixture.state()["artifacts"]["pypi"]
    fixture.set_pypi_releases({"1.2.3": _remote_entries(
        artifacts, tamper=next(iter(artifacts)))})
    result = fixture.run(*_pypi_args(), "--resume", "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "哈希不一致" in result.stderr


def test_resume_refuses_third_content_in_release_file(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    first = fixture.run(*_pypi_args(), "--yes", env=fixture.env(
        extra={"FAKE_PY_FAIL_BUILD": "1", "TWINE_PASSWORD": "x"}))
    assert first.returncode == 1
    # A content that is neither the old (0.0.1) nor the target (1.2.3) value.
    (fixture.repo / "pyproject.toml").write_text(
        '[project]\nname = "dragon-quant"\nversion = "9.9.9"\n', encoding="utf-8")
    result = fixture.run(*_pypi_args(), "--resume", "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "拒绝覆盖" in result.stderr
    assert fixture.git("tag", "--list").stdout.strip() == ""


def test_resume_after_commit_requires_clean_worktree(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes",
                       env=_fail_upload_env(fixture)).returncode == 1
    (fixture.repo / "pyproject.toml").write_text("tampered\n", encoding="utf-8")
    result = fixture.run(*_pypi_args(), "--resume", "--yes", env=fixture.env())
    assert result.returncode == 1
    assert "工作区不干净" in result.stderr


def test_resume_refuses_tampered_plan(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes",
                       env=_fail_upload_env(fixture)).returncode == 1
    state_path = fixture.state_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["plan"]["pypi_version"] = "9.9.9"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = fixture.run("--resume", "--yes", env=fixture.env())
    assert result.returncode == 1
    assert "哈希不一致" in result.stderr


def test_legacy_schema_state_is_rejected(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    legacy = fixture.tmp / "state" / "state.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"version": "1.2.3", "stages": {
        "commit": {"done": True}}}), encoding="utf-8")
    result = fixture.run("--resume", "--yes", env=fixture.env())
    assert result.returncode == 1
    assert "不是统一发布状态" in result.stderr or "旧发布状态" in result.stderr


def test_resume_refuses_corrupt_and_foreign_batches(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    batch = fixture.tmp / "state" / "batches" / "20260101T000000Z-aaaaaa"
    batch.mkdir(parents=True)
    (batch / "state.json").write_text("{broken", encoding="utf-8")
    result = fixture.run("--resume", "--yes", env=fixture.env())
    # Corrupt batch is skipped by discovery; no resumable batch is reported.
    assert result.returncode == 1
    assert "没有可恢复" in result.stderr or "无法读取发布状态" in result.stderr

    (batch / "state.json").write_text(json.dumps({
        "schema": 2, "batch_id": batch.name, "repo": str(tmp_path / "other"),
        "plan": {}, "plan_hash": "", "complete": False, "steps": {},
    }), encoding="utf-8")
    result = fixture.run("--resume", "--yes", env=fixture.env())
    assert result.returncode == 1


def test_resume_refuses_upstream_change_without_writing(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes",
                       env=_fail_upload_env(fixture)).returncode == 1
    state = fixture.state()
    head = fixture.git("rev-parse", "HEAD").stdout.strip()
    tags = fixture.git("tag", "--list").stdout.strip()
    remote = fixture.git("ls-remote", str(fixture.origin)).stdout
    fixture.git("update-ref", "refs/remotes/origin/other", "HEAD")
    fixture.git("config", "branch.release/test.merge", "refs/heads/other")
    result = fixture.run(*_pypi_args(), "--resume", "--yes", env=fixture.env())
    assert result.returncode == 1
    assert "upstream" in result.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == head
    assert fixture.git("tag", "--list").stdout.strip() == tags
    assert fixture.git("ls-remote", str(fixture.origin)).stdout == remote
    current = fixture.state()
    assert current["upstream"] == state["upstream"]
    assert current["push_target_fingerprint"] == state["push_target_fingerprint"]


def test_fresh_release_refuses_non_origin_tracking_branch(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    fixture.git("update-ref", "refs/remotes/origin/other", "HEAD")
    fixture.git("config", "branch.release/test.merge", "refs/heads/other")
    result = fixture.run(*_pypi_args(), "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "固定发布目标" in result.stderr
    assert not (fixture.tmp / "state" / "batches").exists() or not list(
        (fixture.tmp / "state" / "batches").glob("*/state.json"))


def test_wrong_publish_password_reports_clean_error(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    enc = fixture.repo / "scripts" / "publish_token.enc"
    enc.parent.mkdir()
    enc.write_bytes((REPO / "scripts" / "publish_token.enc").read_bytes())
    fixture.git("add", "scripts/publish_token.enc")
    fixture.git("commit", "-q", "-m", "add encrypted token")
    result = fixture.run(*_pypi_args(), "--yes", env=fixture.env(
        extra={"DQ_PUBLISH_PASSWORD": "definitely-not-the-passphrase"},
        with_gh=False))
    assert result.returncode == 1
    assert "密码错误" in result.stderr
    assert "Traceback" not in result.stderr
    assert fixture.git("tag", "--list").stdout.strip() == ""


def test_distribution_versions_satisfy_min_cli_contract():
    import re

    package = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
    skill = (REPO / "skills" / "dragon-quant" / "SKILL.md").read_text(encoding="utf-8")
    front = skill.split("---\n", 2)[1]
    skill_version = re.search(r'skill-version:\s*"([^"]+)"', front).group(1)
    min_cli = re.search(r'min-cli-version:\s*"([^"]+)"', front).group(1)
    pyproject_version = re.search(
        r'^version = "([^"]+)"$',
        (REPO / "pyproject.toml").read_text(encoding="utf-8"), re.MULTILINE).group(1)
    module_version = re.search(
        r'^__version__ = "([^"]+)"$',
        (REPO / "dragon_quant" / "_version.py").read_text(encoding="utf-8"),
        re.MULTILINE).group(1)
    assert package["version"] == skill_version
    assert pyproject_version == module_version
    # min-cli is a *minimum dependency* contract, not an equality with the
    # current CLI version.
    def _tuple(value):
        return tuple(int(part) for part in value.split("."))
    assert _tuple(min_cli) <= _tuple(pyproject_version)


def test_resume_after_github_release_failure_does_not_reupload(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    first = fixture.run(*_pypi_args(), "--yes", env=fixture.env(
        extra={"TWINE_PASSWORD": "x", "FAKE_GH_FAIL_VIEW": "1"}))
    assert first.returncode == 1
    assert "pending" in first.stderr or "未整体完成" in first.stderr
    uploads = [c for c in fixture.py_calls()
               if c["argv"][:3] == ["-m", "twine", "upload"]]
    assert len(uploads) == 1

    second = fixture.run(*_pypi_args(), "--resume", "--yes", env=fixture.env())
    assert second.returncode == 0, second.stderr
    uploads = [c for c in fixture.py_calls()
               if c["argv"][:3] == ["-m", "twine", "upload"]]
    assert len(uploads) == 1, "resume must not upload the same version twice"


def test_missing_gh_keeps_release_pending(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    result = fixture.run(*_pypi_args(), "--yes", env=fixture.env(
        extra={"TWINE_PASSWORD": "x"}, with_gh=False))
    assert result.returncode == 1
    assert "pending" in result.stderr
    state = fixture.state()
    assert state["complete"] is False
    assert state["steps"]["release"].get("skipped") is True


def test_real_wheel_and_sdist_contain_runtime_research_and_dist(tmp_path):
    pytest.importorskip("build", reason="release build backend not installed")

    out = tmp_path / "dist"
    result = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(out)],
        cwd=REPO, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stderr[-2000:]
    wheel = next(out.glob("*.whl"))
    sdist = next(out.glob("*.tar.gz"))
    import tarfile
    import zipfile
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
    assert any(name.startswith("dragon_quant/research/") for name in names)
    assert any(name.startswith("web_ui/dist/") for name in names)
    assert not [name for name in names
                if "/tests/" in name or name.endswith(".enc")]
    with tarfile.open(sdist) as archive:
        sdist_names = archive.getnames()
    assert any("/dragon_quant/research/" in name for name in sdist_names)
    assert not [name for name in sdist_names
                if "/tests/" in name or name.endswith(".enc")]


def test_unexpected_internal_error_is_reported_not_bubbled(tmp_path, monkeypatch, capsys):
    fixture = ReleaseFixture(tmp_path)
    sys.path.insert(0, str(REPO / "scripts"))
    import release.orchestrator as orchestrator

    monkeypatch.setattr(orchestrator.git_ops, "resolve_identity",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setenv("DQ_RELEASE_STATUS_DIR", str(fixture.tmp / "state"))
    from release import main as entry

    rc = entry.main(["--packages", "pypi", "--pypi-version", "1.2.3", "--yes",
                     "--repo-root", str(fixture.repo)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "未预期异常" in err and "boom" in err and "--resume" in err


# ─── rework: recorded success is authoritative (M2) ───────────────────────


def _upload_calls(fixture: ReleaseFixture) -> list[dict]:
    return [call for call in fixture.py_calls()
            if call["argv"][:3] == ["-m", "twine", "upload"]]


def _no_credentials_env(fixture: ReleaseFixture) -> dict:
    env = fixture.env()
    env.pop("TWINE_PASSWORD", None)
    env.pop("DQ_PUBLISH_PASSWORD", None)
    env.pop("NPM_TOKEN", None)
    return env


def test_resume_refuses_reupload_when_recorded_upload_vanished(tmp_path):
    """M2: a recorded successful upload must fail closed if the remote vanished."""
    fixture = ReleaseFixture(tmp_path)
    first = fixture.run(*_pypi_args(), "--yes", env=fixture.env(extra={
        "TWINE_PASSWORD": "x", "FAKE_GH_FAIL_VIEW": "1"}))
    assert first.returncode == 1
    assert fixture.state()["steps"]["upload_pypi"]["done"] is True
    uploads = len(_upload_calls(fixture))

    fixture.set_pypi_releases({})
    second = fixture.run("--resume", "--yes", env=fixture.env())
    assert second.returncode == 1
    assert "拒绝重传" in second.stderr
    assert len(_upload_calls(fixture)) == uploads, "must not upload twice"


def test_resume_adopts_unrecorded_upload_without_reuploading(tmp_path):
    """Crash after a real upload but before its result was recorded."""
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes", env=fixture.env(extra={
        "TWINE_PASSWORD": "x", "FAKE_GH_FAIL_VIEW": "1"})).returncode == 1
    state_path = fixture.state_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    del state["steps"]["upload_pypi"]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    uploads = len(_upload_calls(fixture))

    second = fixture.run("--resume", "--yes", env=fixture.env())
    assert second.returncode == 0, second.stderr
    assert len(_upload_calls(fixture)) == uploads
    assert fixture.state()["steps"]["upload_pypi"]["done"] is True


def _common_dir(fixture: ReleaseFixture) -> Path:
    raw = fixture.git("rev-parse", "--git-common-dir").stdout.strip()
    path = Path(raw)
    if not path.is_absolute():
        path = (fixture.repo / path).resolve()
    return path


def _git_wrapper(tmp_path: Path) -> tuple[Path, Path]:
    wrapper_dir = tmp_path / "gitwrap-unrecorded"
    wrapper_dir.mkdir()
    log = tmp_path / "git-unrecorded.log"
    real_git = subprocess.run(["which", "git"], text=True, capture_output=True,
                              check=True).stdout.strip()
    _write_executable(wrapper_dir / "git", f"""#!/bin/sh
printf '%s\\n' "$*" >> "{log}"
exec "{real_git}" "$@"
""")
    return wrapper_dir, log


def test_resume_adopts_unrecorded_push_without_repushing(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes", env=fixture.env(extra={
        "TWINE_PASSWORD": "x", "FAKE_GH_FAIL_VIEW": "1"})).returncode == 1
    state_path = fixture.state_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    del state["steps"]["push"]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    wrapper_dir, log = _git_wrapper(tmp_path)

    second = fixture.run("--resume", "--yes", env=fixture.env(
        fake_path=f"{fixture.bin}:{wrapper_dir}:{os.environ['PATH']}"))
    assert second.returncode == 0, second.stderr
    pushes = [line for line in log.read_text().splitlines()
              if line.startswith("push ")]
    assert pushes == [], pushes
    assert fixture.state()["steps"]["push"]["done"] is True


def test_resume_adopts_unrecorded_release_without_recreating(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes",
                       env=fixture.env(extra={"TWINE_PASSWORD": "x"})).returncode == 0
    state_path = fixture.state_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["complete"] = False
    del state["steps"]["release"]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    creates = sum(1 for line in fixture.gh_log.read_text().splitlines()
                  if '"release", "create"' in line)

    second = fixture.run("--resume", "--yes", env=fixture.env())
    assert second.returncode == 0, second.stderr
    creates_after = sum(1 for line in fixture.gh_log.read_text().splitlines()
                        if '"release", "create"' in line)
    assert creates_after == creates, "must not create the Release twice"
    assert fixture.state()["steps"]["release"]["done"] is True


# ─── rework: staged content identity is frozen (M3) ───────────────────────


def _stop_before_commit(fixture: ReleaseFixture) -> None:
    """Prepare/build succeed, then credentials fail (no Git write happened)."""
    head = fixture.git("rev-parse", "HEAD").stdout.strip()
    first = fixture.run(*_pypi_args(), "--yes",
                        env=_no_credentials_env(fixture))
    assert first.returncode == 1
    assert "token" in first.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == head
    assert fixture.git("tag", "--list").stdout.strip() == ""


def test_resume_refuses_tampered_staging_source(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    _stop_before_commit(fixture)
    staging = Path(fixture.state()["staging"])
    pyproject = staging / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace('"1.2.3"', '"9.9.9"'),
        encoding="utf-8")
    head = fixture.git("rev-parse", "HEAD").stdout.strip()

    result = fixture.run("--resume", "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "冻结身份" in result.stderr and "pyproject.toml" in result.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == head
    assert fixture.git("tag", "--list").stdout.strip() == ""


def test_resume_refuses_tampered_staging_dist(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    _stop_before_commit(fixture)
    staging = Path(fixture.state()["staging"])
    index = staging / "web_ui" / "dist" / "index.html"
    index.write_text("tampered", encoding="utf-8")

    result = fixture.run("--resume", "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "冻结身份" in result.stderr and "web_ui/dist/index.html" in result.stderr


def test_resume_refuses_missing_staging_file(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    _stop_before_commit(fixture)
    staging = Path(fixture.state()["staging"])
    (staging / "dragon_quant" / "_version.py").unlink()

    result = fixture.run("--resume", "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "冻结身份" in result.stderr and "_version.py" in result.stderr


def test_resume_refuses_foreign_commit_with_same_subject(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    _stop_before_commit(fixture)
    # Same subject and parent as the release commit, but different content.
    fixture.git("commit", "--allow-empty", "-m", "🔖 发布：PyPI 1.2.3")
    result = fixture.run("--resume", "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "不一致" in result.stderr or "拒绝继续" in result.stderr
    assert fixture.git("tag", "--list").stdout.strip() == ""


# ─── rework: notes frozen before external writes (M5) ─────────────────────


def test_missing_changelog_section_uses_git_fallback_and_releases(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    changelog = fixture.repo / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## [0.0.1]\n- old\n", encoding="utf-8")
    before = changelog.read_text(encoding="utf-8")
    fixture.git("add", "-A")
    fixture.git("commit", "-q", "-m", "changelog without 1.2.3")

    result = fixture.run(*_pypi_args(), "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 0, result.stderr
    assert changelog.read_text(encoding="utf-8") == before, "CHANGELOG must not be rewritten"
    notes = fixture.state()["release_notes"]
    assert "git 变更列表" in notes["text"]
    gh = json.loads(fixture.gh_state.read_text(encoding="utf-8"))
    assert gh["releases"]["v1.2.3"]["body"] == notes["text"]
    changed = set(fixture.git("show", "--name-only", "--pretty=format:",
                              "HEAD").stdout.split())
    assert "CHANGELOG.md" not in changed


def test_existing_release_body_conflict_is_not_overwritten(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    fixture.gh_state.write_text(json.dumps({"releases": {
        "v1.2.3": {"body": "someone else's notes", "verify_tag": True}}}),
        encoding="utf-8")
    result = fixture.run(*_pypi_args(), "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "正文与记录不一致" in result.stderr
    gh = json.loads(fixture.gh_state.read_text(encoding="utf-8"))
    assert gh["releases"]["v1.2.3"]["body"] == "someone else's notes"
    assert fixture.state()["complete"] is False


def test_resume_refuses_tampered_release_notes(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    _stop_before_commit(fixture)
    state_path = fixture.state_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["release_notes"]["text"]
    state["release_notes"]["text"] += "\n- injected\n"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = fixture.run("--resume", "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "notes 内容哈希" in result.stderr


# ─── rework: completed batches and dry-run recovery (M6) ──────────────────


def test_resume_reports_completed_batch_without_side_effects(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes",
                       env=fixture.env(extra={"TWINE_PASSWORD": "x"})).returncode == 0
    head = fixture.git("rev-parse", "HEAD").stdout.strip()
    uploads = len(_upload_calls(fixture))

    result = fixture.run("--resume", "--yes", env=fixture.env())
    assert result.returncode == 0, result.stderr
    assert "已完成" in result.stdout and "未执行任何新步骤" in result.stdout
    assert len(_upload_calls(fixture)) == uploads
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == head


def test_failed_dry_run_can_be_restarted(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    first = fixture.run(*_pypi_args("1.2.3", "--dry-run"),
                        env=fixture.env(extra={"FAKE_PY_FAIL_BUILD": "1"}))
    assert first.returncode == 1
    second = fixture.run(*_pypi_args("1.2.3", "--dry-run"),
                         env=fixture.env())
    assert second.returncode == 0, second.stderr
    summary = json.loads(second.stdout[second.stdout.index("{"):])
    assert len(summary["abandoned_dry_batches"]) == 1
    batches = sorted((fixture.tmp / "state" / "batches").glob("*/state.json"))
    assert len(batches) == 2, "history is kept, not deleted"


def test_dry_run_resume_continues_failed_preview(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    first = fixture.run(*_pypi_args("1.2.3", "--dry-run"),
                        env=fixture.env(extra={"FAKE_PY_FAIL_BUILD": "1"}))
    assert first.returncode == 1
    batch = fixture.state()["batch_id"]

    second = fixture.run("--dry-run", "--resume", "--yes",
                         env=fixture.env())
    assert second.returncode == 0, second.stderr
    summary = json.loads(second.stdout[second.stdout.index("{"):])
    assert summary["batch"] == batch
    assert summary["artifacts"]


# ─── rework: persistent staging + canonical active pointer (M9) ───────────


def test_persistent_staging_lives_in_batch_dir_not_tmpdir(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    _stop_before_commit(fixture)
    staging = Path(fixture.state()["staging"])
    assert staging.is_dir()
    assert str(staging).startswith(str(fixture.tmp / "state"))
    assert (staging / "pyproject.toml").is_file()
    # Scratch under TMPDIR may be cleaned; the frozen staging must remain.
    import shutil as _shutil
    _shutil.rmtree(fixture.tmp / "tmpdir", ignore_errors=True)
    result = fixture.run("--resume", "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 0, result.stderr


def test_unfinished_release_blocks_other_status_dir_and_pointer_resumes(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    first = fixture.run(*_pypi_args(), "--yes", env=fixture.env(extra={
        "TWINE_PASSWORD": "x", "FAKE_PY_FAIL_BUILD": "1"}))
    assert first.returncode == 1
    other_root = fixture.tmp / "other-state"

    blocked = fixture.run(*_pypi_args(), "--yes", env=fixture.env(extra={
        "TWINE_PASSWORD": "x", "DQ_RELEASE_STATUS_DIR": str(other_root)}))
    assert blocked.returncode == 1
    assert "未完成发布批次" in blocked.stderr
    assert not (other_root / "batches").exists()

    resumed = fixture.run("--resume", "--yes", env=fixture.env(extra={
        "TWINE_PASSWORD": "x", "DQ_RELEASE_STATUS_DIR": str(other_root)}))
    assert resumed.returncode == 0, resumed.stderr
    assert fixture.state()["complete"] is True
    common = _common_dir(fixture)
    assert not (common / "dq-release-active.json").exists(), "pointer released"


def test_completed_batch_releases_pointer(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes",
                       env=fixture.env(extra={"TWINE_PASSWORD": "x"})).returncode == 0
    common = _common_dir(fixture)
    assert not (common / "dq-release-active.json").exists()


def test_resume_fails_closed_when_recorded_release_vanished(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes",
                       env=fixture.env(extra={"TWINE_PASSWORD": "x"})).returncode == 0
    state_path = fixture.state_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["complete"] = False
    state_path.write_text(json.dumps(state), encoding="utf-8")
    fixture.gh_state.write_text(json.dumps({"releases": {}}), encoding="utf-8")
    creates = sum(1 for line in fixture.gh_log.read_text().splitlines()
                  if '"release", "create"' in line)

    result = fixture.run("--resume", "--yes", env=fixture.env())
    assert result.returncode == 1
    assert "拒绝重建" in result.stderr
    assert "pending" not in result.stderr
    current = fixture.state()
    assert current["steps"]["release"]["done"] is True
    assert current["complete"] is False
    creates_after = sum(1 for line in fixture.gh_log.read_text().splitlines()
                        if '"release", "create"' in line)
    assert creates_after == creates


def test_resume_refuses_local_tag_loss_when_recorded_done(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run(*_pypi_args(), "--yes", env=_fail_upload_env(
        fixture)).returncode == 1
    assert fixture.state()["steps"]["tag"]["done"] is True
    fixture.git("tag", "-d", "v1.2.3")

    result = fixture.run("--resume", "--yes", env=fixture.env())
    assert result.returncode == 1
    assert "标签" in result.stderr
    assert fixture.git("tag", "--list").stdout.strip() == "", "must not recreate"
