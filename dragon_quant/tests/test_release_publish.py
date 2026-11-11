"""Release-flow safety tests (fake commands, no network, no real publishing).

Covers KR1: version injection rejection, dry-run isolation, ordering (build and
check before any git mutation), exact release files, atomic push without
fallback, credential handling, artifact identity and resume.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "scripts" / "release" / "publish_pypi.py"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


FAKE_PYTHON = r"""#!/usr/bin/env python3
import json, os, sys, tarfile, zipfile
from pathlib import Path

log = os.environ.get("FAKE_PY_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": sys.argv[1:], "env_password": os.environ.get("TWINE_PASSWORD")}) + "\n")
if os.environ.get("FAKE_PY_FAIL_TEST") and sys.argv[1:3] == ["-m", "pytest"]:
    print("test gate failed", file=sys.stderr)
    raise SystemExit(1)
if os.environ.get("FAKE_PY_FAIL_BUILD") and sys.argv[1:3] == ["-m", "build"]:
    print("boom", file=sys.stderr)
    raise SystemExit(1)
if sys.argv[1:3] == ["-m", "build"]:
    version = "0.0.0"
    for line in Path("pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            version = line.split('"')[1]
    dist = Path("dist"); dist.mkdir(exist_ok=True)
    wheel = dist / f"dragon_quant-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("dragon_quant/__init__.py", "")
        zf.writestr(f"dragon_quant-{version}.dist-info/METADATA", "Name: dragon-quant\n")
        zf.writestr("web_ui/dist/index.html", "<!doctype html>")
    sdist = dist / f"dragon_quant-{version}.tar.gz"
    with tarfile.open(sdist, "w:gz") as tf:
        for name, text in (("dragon_quant-0/PKG-INFO", "x"),):
            data = text.encode()
            info = tarfile.TarInfo(name); info.size = len(data)
            import io; tf.addfile(info, io.BytesIO(data))
raise SystemExit(0)
"""

FAKE_NPM = r"""#!/bin/sh
set -eu
if [ -n "${FAKE_NPM_LOG:-}" ]; then
  printf '%s\n' "$@" >> "$FAKE_NPM_LOG"
fi
case "$1" in
  --prefix)
    mkdir -p web_ui/dist
    printf '<!doctype html>\n' > web_ui/dist/index.html
    ;;
esac
exit 0
"""


class ReleaseFixture:
    def __init__(self, tmp_path: Path, version: str = "1.2.3"):
        self.tmp = tmp_path
        self.version = version
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.logs = tmp_path / "logs"
        self.logs.mkdir()
        self.fake_python = self.bin / "fake-python"
        _write_executable(self.fake_python, FAKE_PYTHON)
        _write_executable(self.bin / "npm", FAKE_NPM)
        self.py_log = self.logs / "python.jsonl"
        self.npm_log = self.logs / "npm.log"
        self.gh_log = self.logs / "gh.log"
        _write_executable(self.bin / "gh", f"""#!/bin/sh
printf '%s\\n' "$@" >> "{self.gh_log}"
exit 0
""")
        self._init_repo()

    def _init_repo(self):
        (self.repo / "pyproject.toml").write_text(
            '[project]\nname = "dragon-quant"\nversion = "0.0.1"\n', encoding="utf-8")
        (self.repo / "dragon_quant").mkdir()
        (self.repo / "dragon_quant" / "_version.py").write_text(
            '__version__ = "0.0.1"\n', encoding="utf-8")
        (self.repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [{self.version}]\n\n### Added\n- research facade\n\n## [0.0.1]\n- old\n",
            encoding="utf-8")
        frontend = self.repo / "web_ui" / "frontend"
        (frontend / "node_modules").mkdir(parents=True)
        (frontend / "node_modules" / ".keep").write_text("", encoding="utf-8")
        (self.repo / "web_ui" / "dist").mkdir(parents=True)
        (self.repo / "web_ui" / "dist" / "index.html").write_text("old", encoding="utf-8")
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

    def pypi_json(self, existing: bool) -> str:
        path = self.tmp / "pypi.json"
        releases = {self.version: []} if existing else {}
        path.write_text(json.dumps({"releases": releases}), encoding="utf-8")
        return path.as_uri()

    def env(self, *, existing_version: bool = False, extra: dict | None = None,
            fake_path: str | None = None) -> dict:
        env = {
            **os.environ,
            "PATH": fake_path or f"{self.bin}:{os.environ['PATH']}",
            "DQ_RELEASE_PYTHON": str(self.fake_python),
            "DQ_RELEASE_PYPI_URL": self.pypi_json(existing_version),
            "DQ_RELEASE_STATUS_DIR": str(self.tmp / "state"),
            "FAKE_PY_LOG": str(self.py_log),
            "FAKE_NPM_LOG": str(self.npm_log),
            "HOME": str(self.tmp / "home"),
        }
        (self.tmp / "home").mkdir(exist_ok=True)
        env.update(extra or {})
        return env

    def run(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(HELPER), "--repo-root", str(self.repo), *args],
            text=True, capture_output=True, env=env or self.env())


def test_version_injection_is_rejected_before_any_command(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    result = fixture.run('--v', '1.2.3"; import os; #')
    assert result.returncode == 2
    assert not fixture.py_log.exists()
    assert not fixture.npm_log.exists()


def test_legacy_passwd_is_rejected_with_replacement(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    result = fixture.run("--v", "1.2.3", "--passwd", "hunter2")
    assert result.returncode == 2
    assert "DQ_PUBLISH_PASSWORD" in result.stderr
    assert "hunter2" not in result.stdout + result.stderr


def test_dry_run_is_credential_free_and_does_not_mutate_repo(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    before_head = fixture.git("rev-parse", "HEAD").stdout.strip()
    before_pyproject = (fixture.repo / "pyproject.toml").read_text(encoding="utf-8")
    before_version = (fixture.repo / "dragon_quant" / "_version.py").read_text(encoding="utf-8")

    result = fixture.run("--v", fixture.version, "--dry-run", env=fixture.env(extra={
        "TWINE_PASSWORD": "must-not-be-used", "NPM_TOKEN": "must-not-be-used"}))

    assert result.returncode == 0, result.stderr
    assert '"dry_run": true' in result.stdout
    assert "must-not-be-used" not in result.stdout + result.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == before_head
    assert fixture.git("tag", "--list").stdout.strip() == ""
    assert (fixture.repo / "pyproject.toml").read_text(encoding="utf-8") == before_pyproject
    assert (fixture.repo / "dragon_quant" / "_version.py").read_text(encoding="utf-8") == before_version
    assert (fixture.repo / "web_ui" / "dist" / "index.html").read_text(encoding="utf-8") == "old"
    # 关键顺序：dry-run 未触发任何 git 写命令与上传（twine check 允许）。
    py_calls = [json.loads(line) for line in fixture.py_log.read_text().splitlines()]
    assert not any(call["argv"][:3] == ["-m", "twine", "upload"] for call in py_calls)


def test_dirty_worktree_blocks_release_before_build(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    (fixture.repo / "scratch.txt").write_text("x", encoding="utf-8")
    result = fixture.run("--v", fixture.version, env=fixture.env())
    assert result.returncode == 1
    assert "工作区不干净" in result.stderr
    assert not fixture.py_log.exists()


def test_existing_registry_version_blocks_release(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    result = fixture.run("--v", fixture.version, env=fixture.env(existing_version=True))
    assert result.returncode == 1
    assert "已存在版本" in result.stderr
    assert fixture.git("tag", "--list").stdout.strip() == ""


def test_build_failure_leaves_zero_git_mutation(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    before_head = fixture.git("rev-parse", "HEAD").stdout.strip()
    result = fixture.run("--v", fixture.version, env=fixture.env(
        extra={"FAKE_PY_FAIL_BUILD": "1"}))
    assert result.returncode == 1
    assert "prepare" in result.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == before_head
    assert fixture.git("tag", "--list").stdout.strip() == ""
    assert (fixture.repo / "pyproject.toml").read_text(encoding="utf-8").count("0.0.1") == 1


def test_success_commits_only_release_files_and_uploads_same_artifacts(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    result = fixture.run("--v", fixture.version, env=fixture.env(extra={
        "TWINE_PASSWORD": "secret-token-value"}))
    assert result.returncode == 0, result.stderr

    changed = fixture.git("show", "--name-only", "--pretty=format:", "HEAD").stdout.split()
    assert "pyproject.toml" in changed
    assert "dragon_quant/_version.py" in changed
    assert any(name.startswith("web_ui/dist/") for name in changed)
    assert "CHANGELOG.md" not in changed
    assert "secret-token-value" not in result.stdout + result.stderr

    assert fixture.git("tag", "--list", f"v{fixture.version}").stdout.strip() == f"v{fixture.version}"
    state = json.loads((fixture.tmp / "state" / "state.json").read_text())
    assert state["upstream"] == "origin/release/test"
    assert len(state["push_target_fingerprint"]) == 64
    remote_tags = subprocess.run(
        ["git", "ls-remote", "--tags", str(fixture.origin), f"refs/tags/v{fixture.version}"],
        text=True, capture_output=True, check=True).stdout
    assert remote_tags.strip()

    calls = [json.loads(line) for line in fixture.py_log.read_text().splitlines()]
    builds = [call for call in calls if call["argv"][:2] == ["-m", "build"]]
    assert builds and "--no-isolation" in builds[0]["argv"]
    uploads = [call for call in calls if call["argv"][:3] == ["-m", "twine", "upload"]]
    assert uploads, calls
    assert uploads[0]["env_password"] == "secret-token-value"
    assert "secret-token-value" not in " ".join(uploads[0]["argv"])
    artifact_names = {Path(arg).name for arg in uploads[0]["argv"][3:]}
    assert any(name.endswith(".whl") for name in artifact_names)
    assert any(name.endswith(".tar.gz") for name in artifact_names)


def test_release_succeeds_when_version_already_prepared(tmp_path):
    """手工准备过版本时（pyproject/_version/dist 已是目标状态）复用 HEAD，不建空提交。"""
    fixture = ReleaseFixture(tmp_path)
    (fixture.repo / "pyproject.toml").write_text(
        f'[project]\nname = "dragon-quant"\nversion = "{fixture.version}"\n',
        encoding="utf-8")
    (fixture.repo / "dragon_quant" / "_version.py").write_text(
        f'__version__ = "{fixture.version}"\n', encoding="utf-8")
    (fixture.repo / "web_ui" / "dist" / "index.html").write_text(
        "<!doctype html>\n", encoding="utf-8")
    fixture.git("add", "-A")
    fixture.git("commit", "-q", "-m", "prepare release version")
    head = fixture.git("rev-parse", "HEAD").stdout.strip()

    result = fixture.run("--v", fixture.version,
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 0, result.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == head
    assert fixture.git("tag", "--list", f"v{fixture.version}").stdout.strip() == (
        f"v{fixture.version}")
    calls = [json.loads(line) for line in fixture.py_log.read_text().splitlines()]
    assert any(call["argv"][:3] == ["-m", "twine", "upload"] for call in calls)


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
    result = fixture.run("--v", fixture.version, env=fixture.env(
        fake_path=f"{fixture.bin}:{wrapper_dir}:{os.environ['PATH']}",
        extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "原子推送失败" in result.stderr
    calls = (tmp_path / "git-calls.log").read_text().splitlines()
    assert any("push --atomic" in call for call in calls)
    assert not any(call.startswith("push ") and "--atomic" not in call for call in calls)
    assert not any("twine upload" in call for call in fixture.py_log.read_text().splitlines())


def test_resume_after_upload_failure_reuses_commit_tag_and_artifacts(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    fail_env = fixture.env(extra={"TWINE_PASSWORD": "x", "FAKE_PY_FAIL_TWINE": "1"})
    # First attempt fails at upload because twine exits non-zero for upload.
    helper = Path(fixture.fake_python)
    _write_executable(helper, FAKE_PYTHON.replace(
        'if sys.argv[1:3] == ["-m", "build"]:',
        'if os.environ.get("FAKE_PY_FAIL_TWINE") and sys.argv[1:4] == ["-m", "twine", "upload"]:\n'
        '    raise SystemExit(1)\n'
        'if sys.argv[1:3] == ["-m", "build"]:'))
    first = fixture.run("--v", fixture.version, env=fail_env)
    assert first.returncode == 1
    head = fixture.git("rev-parse", "HEAD").stdout.strip()
    tags = fixture.git("tag", "--list").stdout.strip()
    assert tags == f"v{fixture.version}"
    state = json.loads((fixture.tmp / "state" / "state.json").read_text())
    digests = dict(state["artifacts"])

    second = fixture.run("--v", fixture.version, "--resume", env=fixture.env(
        extra={"TWINE_PASSWORD": "x"}))
    assert second.returncode == 0, second.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == head
    assert fixture.git("tag", "--list").stdout.strip() == tags
    final_state = json.loads((fixture.tmp / "state" / "state.json").read_text())
    assert final_state["artifacts"] == digests
    assert final_state["stages"]["upload"]["done"] is True


def test_package_boundary_rejects_private_files(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    _write_executable(Path(fixture.fake_python), FAKE_PYTHON.replace(
        'zf.writestr("web_ui/dist/index.html", "<!doctype html>")',
        'zf.writestr("web_ui/dist/index.html", "<!doctype html>")\n'
        '        zf.writestr("dragon_quant/tests/test_secret.py", "")'))
    result = fixture.run("--v", fixture.version, env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "不该发布" in result.stderr
    assert fixture.git("tag", "--list").stdout.strip() == ""


def test_distribution_versions_are_consistent():
    import json as _json
    import re as _re

    package = _json.loads((REPO / "package.json").read_text(encoding="utf-8"))
    skill = (REPO / "skills" / "dragon-quant" / "SKILL.md").read_text(encoding="utf-8")
    front = skill.split("---\n", 2)[1]
    skill_version = _re.search(r'skill-version:\s*"([^"]+)"', front).group(1)
    min_cli = _re.search(r'min-cli-version:\s*"([^"]+)"', front).group(1)
    pyproject_version = _re.search(
        r'^version = "([^"]+)"$', (REPO / "pyproject.toml").read_text(encoding="utf-8"),
        _re.MULTILINE).group(1)
    module_version = _re.search(
        r'^__version__ = "([^"]+)"$',
        (REPO / "dragon_quant" / "_version.py").read_text(encoding="utf-8"),
        _re.MULTILINE).group(1)
    assert package["version"] == skill_version
    assert min_cli == pyproject_version == module_version


def test_resume_after_github_release_failure_does_not_reupload(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    gh = fixture.bin / "gh"
    _write_executable(gh, "#!/bin/sh\nexit 1\n")
    first = fixture.run("--v", fixture.version, env=fixture.env(
        extra={"TWINE_PASSWORD": "x"}))
    assert first.returncode == 1
    calls = [json.loads(line) for line in fixture.py_log.read_text().splitlines()]
    uploads = [c for c in calls if c["argv"][:3] == ["-m", "twine", "upload"]]
    assert len(uploads) == 1
    _write_executable(gh, "#!/bin/sh\nexit 0\n")
    second = fixture.run("--v", fixture.version, "--resume",
                         env=fixture.env(extra={}))
    assert second.returncode == 0, second.stderr
    calls = [json.loads(line) for line in fixture.py_log.read_text().splitlines()]
    uploads = [c for c in calls if c["argv"][:3] == ["-m", "twine", "upload"]]
    assert len(uploads) == 1, "resume must not upload the same version twice"


def _staged_artifacts(tmp_path: Path) -> dict:
    state = json.loads((tmp_path / "state" / "state.json").read_text())
    return state


def _fail_upload_env(fixture: ReleaseFixture, tmp_path: Path) -> dict:
    helper = Path(fixture.fake_python)
    _write_executable(helper, globals()["FAKE_PYTHON"].replace(
        'if sys.argv[1:3] == ["-m", "build"]:',
        'if os.environ.get("FAKE_PY_FAIL_TWINE") and sys.argv[1:4] == '
        '["-m", "twine", "upload"]:\n    raise SystemExit(1)\n'
        'if sys.argv[1:3] == ["-m", "build"]:'))
    # First attempt reaches upload; twine fails after commit/tag/push.
    return fixture.env(extra={"TWINE_PASSWORD": "x", "FAKE_PY_FAIL_TWINE": "1"})


def test_release_tests_failure_blocks_before_any_git_mutation(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    before_head = fixture.git("rev-parse", "HEAD").stdout.strip()
    result = fixture.run("--v", fixture.version, env=fixture.env(
        extra={"FAKE_PY_FAIL_TEST": "1", "TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "prepare" in result.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == before_head
    assert fixture.git("tag", "--list").stdout.strip() == ""
    calls = [json.loads(line) for line in fixture.py_log.read_text().splitlines()]
    assert any(c["argv"][:2] == ["-m", "pytest"] for c in calls)


def test_non_resume_refuses_existing_state(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    first = fixture.run("--v", fixture.version, env=fixture.env(
        extra={"TWINE_PASSWORD": "x", "FAKE_PY_FAIL_BUILD": "1"}))
    assert first.returncode == 1
    second = fixture.run("--v", fixture.version, env=fixture.env(
        extra={"TWINE_PASSWORD": "x"}))
    assert second.returncode == 1
    assert "--resume" in second.stderr


def test_resume_refuses_head_change(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run("--v", fixture.version, env=_fail_upload_env(fixture, tmp_path)).returncode == 1
    fixture.git("commit", "--allow-empty", "-m", "new commit")
    result = fixture.run("--v", fixture.version, "--resume", env=fixture.env())
    assert result.returncode == 1
    assert "HEAD 已变化" in result.stderr


def test_resume_refuses_missing_remote_tag(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run("--v", fixture.version, env=_fail_upload_env(fixture, tmp_path)).returncode == 1
    fixture.git("push", "origin", f":refs/tags/v{fixture.version}")
    result = fixture.run("--v", fixture.version, "--resume", env=fixture.env())
    assert result.returncode == 1
    assert "远端标签" in result.stderr


def test_resume_refuses_changed_artifacts(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run("--v", fixture.version, env=_fail_upload_env(fixture, tmp_path)).returncode == 1
    state = _staged_artifacts(tmp_path)
    staging = Path(state["staging"])
    artifact = next((staging / "dist").glob("*.whl"))
    artifact.write_bytes(artifact.read_bytes() + b"tamper")
    result = fixture.run("--v", fixture.version, "--resume", env=fixture.env())
    assert result.returncode == 1
    assert "哈希变化" in result.stderr


def _write_pypi_artifacts(tmp_path: Path, version: str, artifacts: dict,
                          *, tamper: str | None = None) -> None:
    entries = []
    for name, digest in artifacts.items():
        entries.append({
            "filename": name,
            "digests": {"sha256": ("0" * 64 if tamper == name else digest)},
        })
    path = tmp_path / "pypi-partial.json"
    path.write_text(json.dumps({"releases": {version: entries}}), encoding="utf-8")


def test_resume_skips_already_uploaded_identical_artifacts(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run("--v", fixture.version, env=_fail_upload_env(fixture, tmp_path)).returncode == 1
    state = _staged_artifacts(tmp_path)
    _write_pypi_artifacts(tmp_path, fixture.version, state["artifacts"])
    env = fixture.env()
    env["DQ_RELEASE_PYPI_URL"] = (tmp_path / "pypi-partial.json").as_uri()
    env.pop("TWINE_PASSWORD", None)
    result = fixture.run("--v", fixture.version, "--resume", env=env)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in fixture.py_log.read_text().splitlines()]
    uploads = [c for c in calls if c["argv"][:3] == ["-m", "twine", "upload"]]
    assert len(uploads) == 1, "identical remote artifacts must not be re-uploaded"


def test_resume_refuses_remote_artifact_hash_mismatch(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run("--v", fixture.version, env=_fail_upload_env(fixture, tmp_path)).returncode == 1
    state = _staged_artifacts(tmp_path)
    _write_pypi_artifacts(tmp_path, fixture.version, state["artifacts"],
                          tamper=next(iter(state["artifacts"])))
    env = fixture.env()
    env["DQ_RELEASE_PYPI_URL"] = (tmp_path / "pypi-partial.json").as_uri()
    env["TWINE_PASSWORD"] = "x"
    result = fixture.run("--v", fixture.version, "--resume", env=env)
    assert result.returncode == 1
    assert "哈希不一致" in result.stderr


def test_real_wheel_and_sdist_contain_runtime_research_and_dist(tmp_path):
    pytest.importorskip("build", reason="release build backend not installed")

    out = tmp_path / "dist"
    result = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(out)],
        cwd=REPO, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stderr[-2000:]
    wheel = next(out.glob("*.whl"))
    sdist = next(out.glob("*.tar.gz"))
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


def _write_state(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "state" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def test_resume_refuses_corrupt_cross_version_and_cross_repo_state(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    before_head = fixture.git("rev-parse", "HEAD").stdout.strip()
    _write_state(tmp_path, "{broken")
    bad = fixture.run("--v", fixture.version, "--resume", env=fixture.env())
    assert bad.returncode == 1
    assert "无法读取发布状态" in bad.stderr

    _write_state(tmp_path, json.dumps({
        "version": "9.9.9", "repo": str(fixture.repo.resolve()),
        "base_commit": "x", "stages": {}}))
    wrong_version = fixture.run("--v", fixture.version, "--resume",
                                env=fixture.env())
    assert wrong_version.returncode == 1
    assert "不一致" in wrong_version.stderr

    _write_state(tmp_path, json.dumps({
        "version": fixture.version, "repo": str(tmp_path / "other-repo"),
        "base_commit": "x", "stages": {}}))
    wrong_repo = fixture.run("--v", fixture.version, "--resume", env=fixture.env())
    assert wrong_repo.returncode == 1
    assert "仓库" in wrong_repo.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == before_head
    assert fixture.git("tag", "--list").stdout.strip() == ""


def _remote_refs(fixture: ReleaseFixture) -> str:
    return fixture.git("ls-remote", str(fixture.origin)).stdout


def _assert_identity_failure_left_no_traces(fixture, *, head: str, tags: str,
                                            remote: str, state: dict,
                                            uploads: int) -> None:
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == head
    assert fixture.git("tag", "--list").stdout.strip() == tags
    assert _remote_refs(fixture) == remote
    current = json.loads((fixture.tmp / "state" / "state.json").read_text())
    assert current.get("upstream") == state.get("upstream")
    assert current.get("push_target_fingerprint") == state.get(
        "push_target_fingerprint")
    calls = [json.loads(line) for line in fixture.py_log.read_text().splitlines()]
    upload_count = sum(1 for c in calls
                       if c["argv"][:3] == ["-m", "twine", "upload"])
    assert upload_count == uploads
    assert not fixture.gh_log.exists() or not fixture.gh_log.read_text().strip()


def test_resume_refuses_upstream_change_without_writing(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run("--v", fixture.version, env=_fail_upload_env(
        fixture, tmp_path)).returncode == 1
    state = _staged_artifacts(tmp_path)
    head = fixture.git("rev-parse", "HEAD").stdout.strip()
    tags = fixture.git("tag", "--list").stdout.strip()
    remote = _remote_refs(fixture)
    fixture.git("update-ref", "refs/remotes/origin/other", "HEAD")
    fixture.git("config", "branch.release/test.merge", "refs/heads/other")
    result = fixture.run("--v", fixture.version, "--resume", env=fixture.env())
    assert result.returncode == 1
    assert "upstream" in result.stderr
    _assert_identity_failure_left_no_traces(
        fixture, head=head, tags=tags, remote=remote, state=state, uploads=1)


def test_resume_refuses_missing_upstream(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run("--v", fixture.version, env=_fail_upload_env(
        fixture, tmp_path)).returncode == 1
    state = _staged_artifacts(tmp_path)
    head = fixture.git("rev-parse", "HEAD").stdout.strip()
    tags = fixture.git("tag", "--list").stdout.strip()
    remote = _remote_refs(fixture)
    fixture.git("config", "--unset", "branch.release/test.merge")
    result = fixture.run("--v", fixture.version, "--resume", env=fixture.env())
    assert result.returncode == 1
    assert "upstream" in result.stderr
    _assert_identity_failure_left_no_traces(
        fixture, head=head, tags=tags, remote=remote, state=state, uploads=1)


def test_resume_refuses_push_target_change(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run("--v", fixture.version, env=_fail_upload_env(
        fixture, tmp_path)).returncode == 1
    state = _staged_artifacts(tmp_path)
    head = fixture.git("rev-parse", "HEAD").stdout.strip()
    tags = fixture.git("tag", "--list").stdout.strip()
    remote = _remote_refs(fixture)
    other = tmp_path / "other-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(other)], check=True)
    fixture.git("remote", "set-url", "--push", "origin", str(other))
    result = fixture.run("--v", fixture.version, "--resume", env=fixture.env())
    assert result.returncode == 1
    assert "push URL" in result.stderr
    _assert_identity_failure_left_no_traces(
        fixture, head=head, tags=tags, remote=remote, state=state, uploads=1)


def test_resume_refuses_legacy_state_without_identity(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    assert fixture.run("--v", fixture.version, env=_fail_upload_env(
        fixture, tmp_path)).returncode == 1
    state = _staged_artifacts(tmp_path)
    state.pop("upstream", None)
    state.pop("push_target_fingerprint", None)
    (tmp_path / "state" / "state.json").write_text(json.dumps(state),
                                                   encoding="utf-8")
    head = fixture.git("rev-parse", "HEAD").stdout.strip()
    tags = fixture.git("tag", "--list").stdout.strip()
    remote = _remote_refs(fixture)
    result = fixture.run("--v", fixture.version, "--resume", env=fixture.env())
    assert result.returncode == 1
    assert "旧状态缺少 upstream" in result.stderr
    current = json.loads((tmp_path / "state" / "state.json").read_text())
    assert "upstream" not in current
    assert "push_target_fingerprint" not in current
    assert fixture.git("rev-parse", "HEAD").stdout.strip() == head
    assert fixture.git("tag", "--list").stdout.strip() == tags
    assert _remote_refs(fixture) == remote
    calls = [json.loads(line) for line in fixture.py_log.read_text().splitlines()]
    assert sum(1 for c in calls if c["argv"][:3] == ["-m", "twine", "upload"]) == 1


def test_fresh_release_refuses_non_origin_tracking_branch(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    fixture.git("update-ref", "refs/remotes/origin/other", "HEAD")
    fixture.git("config", "branch.release/test.remote", "origin")
    fixture.git("config", "branch.release/test.merge", "refs/heads/other")
    result = fixture.run("--v", fixture.version, env=fixture.env(
        extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "固定发布目标" in result.stderr
    assert not (fixture.repo / ".release" / "publish" / "state.json").exists()


def _load_release_module():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("release_publish_pypi", HELPER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_publish_pypi"] = module
    spec.loader.exec_module(module)
    return module


def test_wrong_publish_password_reports_password_error_not_traceback(tmp_path):
    """输错发布密码：控制台打印「密码错误」并 exit 1，不得让异常裸 traceback 冒泡。

    真实 .enc + 错误密码：openssl 解出垃圾字节，subprocess 的严格 UTF-8 解码
    会抛 UnicodeDecodeError（ValueError 子类），旧实现直接冒泡成 traceback。
    """
    fixture = ReleaseFixture(tmp_path)
    enc = fixture.repo / "scripts" / "publish_token.enc"
    enc.parent.mkdir()
    enc.write_bytes((REPO / "scripts" / "publish_token.enc").read_bytes())
    fixture.git("add", "scripts/publish_token.enc")
    fixture.git("commit", "-q", "-m", "add encrypted token")
    result = fixture.run("--v", fixture.version, env=fixture.env(
        extra={"DQ_PUBLISH_PASSWORD": "definitely-not-the-passphrase"}))
    assert result.returncode == 1
    assert "密码错误" in result.stderr
    assert "Traceback" not in result.stderr


def test_obtain_token_wrong_password_raises_clean_release_error(tmp_path, monkeypatch):
    """解密失败必须转成含「密码错误」的 ReleaseError，不得让 UnicodeDecodeError 冒泡。

    用已知口令加密垃圾明文：错误口令解密时 openssl 无论报错还是碰巧解出
    垃圾（1/256 padding 巧合），都必须收敛为 ReleaseError（密码错误）。
    """
    mod = _load_release_module()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2",
         "-pass", "pass:correct-horse",
         "-out", str(scripts / "publish_token.enc")],
        input=b"garbage-not-a-token\n", check=True, capture_output=True)
    runner = mod.Runner(cwd=tmp_path)
    monkeypatch.setenv("DQ_PUBLISH_PASSWORD", "wrong-battery")
    with pytest.raises(mod.ReleaseError) as exc_info:
        mod.obtain_token(tmp_path, runner)
    assert exc_info.value.stage == "upload"
    assert "密码错误" in str(exc_info.value)


def test_unexpected_internal_error_is_reported_not_bubbled(tmp_path, monkeypatch, capsys):
    """main 顶层兜底：未预期异常打印阶段与错误信息 + resume 提示，不裸 traceback。"""
    mod = _load_release_module()
    monkeypatch.setenv("DQ_RELEASE_STATUS_DIR", str(tmp_path / "state"))

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "resolve_identity", _boom)
    rc = mod.main(["--v", "1.2.3", "--repo-root", str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "未预期异常" in err
    assert "RuntimeError" in err
    assert "boom" in err
    assert "--resume" in err
