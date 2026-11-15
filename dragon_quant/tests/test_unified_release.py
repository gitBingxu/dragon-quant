"""Unified-release integration tests for dual-package, interaction and secrets.

Complements ``test_release_publish.py`` (PyPI-flow safety) with the behaviours
unique to the unified entry: package selection, PyPI→npm ordering, min-cli
gating, dist-tag repair, one-password credentials and zero-side-effect cancel.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import shutil
import signal
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from dragon_quant.tests.test_release_publish import (
    ENTRY,
    REPO,
    ReleaseFixture,
    _common_dir,
    _pypi_args,
    _write_executable,
)

sys.path.insert(0, str(REPO / "scripts"))


def _both_args(*extra: str) -> list[str]:
    return ["--packages", "both", "--pypi-version", "1.2.3",
            "--npm-version", "1.0.3", "--min-cli-version", "1.2.3", *extra]


def _npm_args(*extra: str) -> list[str]:
    return ["--packages", "npm", "--npm-version", "1.0.3",
            "--min-cli-version", "1.0.0", *extra]


def _order_log(fixture: ReleaseFixture) -> list[dict]:
    path = fixture.logs / "order.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_dual_package_order_metadata_tags_and_single_release(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    env = fixture.env(extra={"TWINE_PASSWORD": "pypi-secret",
                             "NPM_TOKEN": "npm-secret"})
    result = fixture.run(*_both_args(), "--yes", env=env)
    assert result.returncode == 0, result.stderr

    changed = set(fixture.git("show", "--name-only", "--pretty=format:",
                              "HEAD").stdout.split())
    assert {"pyproject.toml", "dragon_quant/_version.py", "package.json",
            "skills/dragon-quant/SKILL.md"} <= changed
    assert any(name.startswith("web_ui/dist/") for name in changed)
    assert "CHANGELOG.md" not in changed

    commit = fixture.git("rev-parse", "HEAD").stdout.strip()
    for tag in ("v1.2.3", "skills-v1.0.3"):
        assert fixture.git("rev-parse", f"refs/tags/{tag}^{{}}").stdout.strip() == commit
    remote = fixture.git("ls-remote", "--tags", str(fixture.origin)).stdout
    assert "refs/tags/v1.2.3" in remote and "refs/tags/skills-v1.0.3" in remote

    skill = (fixture.repo / "skills" / "dragon-quant" / "SKILL.md").read_text(
        encoding="utf-8")
    assert 'skill-version: "1.0.3"' in skill
    assert 'min-cli-version: "1.2.3"' in skill
    package = json.loads((fixture.repo / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == "1.0.3"

    gh = json.loads(fixture.gh_state.read_text(encoding="utf-8"))
    assert list(gh["releases"]) == ["v1.2.3"], "one Release, PyPI tag preferred"

    order = _order_log(fixture)
    pypi_upload = next(index for index, call in enumerate(order)
                       if call["tool"] == "py"
                       and call["argv"][:3] == ["-m", "twine", "upload"])
    npm_publish = next(index for index, call in enumerate(order)
                       if call["tool"] == "npm" and call["argv"][:1] == ["publish"])
    assert pypi_upload < npm_publish, "PyPI must upload before npm"

    npm_state = json.loads(fixture.npm_state.read_text(encoding="utf-8"))
    artifact = fixture.state()["artifacts"]["npm"]
    assert npm_state["published"]["1.0.3"]["integrity"] == artifact["integrity"]


def test_dry_run_both_defaults_min_cli_to_pypi_version(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    result = fixture.run("--packages", "both", "--pypi-version", "1.2.3",
                         "--npm-version", "1.0.3", "--dry-run",
                         env=fixture.env())
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout[result.stdout.index("{"):])
    assert summary["min_cli_version"] == "1.2.3"
    assert summary["tags"] == ["v1.2.3", "skills-v1.0.3"]
    assert summary["network_calls"] == 0
    assert json.loads(fixture.pypi_json_path.read_text()) == {"releases": {}}
    assert not any(call[:1] == ["view"] for call in fixture.npm_calls())


def test_pypi_failure_blocks_npm_upload(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    result = fixture.run(*_both_args(), "--yes", env=fixture.env(extra={
        "TWINE_PASSWORD": "x", "NPM_TOKEN": "y", "FAKE_PY_FAIL_TWINE": "1"}))
    assert result.returncode == 1
    assert not any(call[:1] == ["publish"] for call in fixture.npm_calls()), \
        "npm must not publish when PyPI upload failed"
    assert not fixture.gh_state.exists() or not json.loads(
        fixture.gh_state.read_text())["releases"]


def test_unavailable_min_cli_blocks_npm_upload(tmp_path):
    fixture = ReleaseFixture(tmp_path, pypi_current="1.0.0")
    # Present but with no artifacts: not installable, so still unavailable.
    fixture.set_pypi_releases({"1.0.0": []})
    result = fixture.run(*_npm_args(), "--yes", env=fixture.env(extra={
        "NPM_TOKEN": "npm-secret"}))
    assert result.returncode == 1
    assert "不在 PyPI" in result.stderr
    assert not any(call[:1] == ["publish"] for call in fixture.npm_calls())


def test_npm_only_preserves_python_versions_and_uses_skill_tag(tmp_path):
    fixture = ReleaseFixture(tmp_path, pypi_current="1.0.0")
    fixture.set_pypi_releases({"1.0.0": [{
        "filename": "dragon_quant-1.0.0-py3-none-any.whl",
        "digests": {"sha256": "0" * 64}}]})
    result = fixture.run(*_npm_args(), "--yes", env=fixture.env(extra={
        "NPM_TOKEN": "npm-secret"}))
    assert result.returncode == 0, result.stderr

    assert not any(call["argv"][:3] == ["-m", "twine", "upload"]
                   for call in fixture.py_calls())
    changed = set(fixture.git("show", "--name-only", "--pretty=format:",
                              "HEAD").stdout.split())
    assert changed == {"package.json", "skills/dragon-quant/SKILL.md"}
    assert "1.0.0" in (fixture.repo / "pyproject.toml").read_text(encoding="utf-8")
    assert fixture.git("tag", "--list", "skills-v1.0.3").stdout.strip() == (
        "skills-v1.0.3")
    assert fixture.git("tag", "--list", "v1.2.3").stdout.strip() == ""
    gh = json.loads(fixture.gh_state.read_text(encoding="utf-8"))
    assert list(gh["releases"]) == ["skills-v1.0.3"]


def test_pypi_only_preserves_npm_metadata(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    before_package = (fixture.repo / "package.json").read_text(encoding="utf-8")
    before_skill = (fixture.repo / "skills" / "dragon-quant" / "SKILL.md").read_text(
        encoding="utf-8")
    result = fixture.run(*_pypi_args(), "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 0, result.stderr
    assert (fixture.repo / "package.json").read_text(encoding="utf-8") == before_package
    assert (fixture.repo / "skills" / "dragon-quant" / "SKILL.md").read_text(
        encoding="utf-8") == before_skill
    assert not any(call[:1] == ["publish"] for call in fixture.npm_calls())


def test_resume_repairs_dist_tag_without_republishing(tmp_path):
    fixture = ReleaseFixture(tmp_path, pypi_current="1.0.0")
    fixture.set_pypi_releases({"1.0.0": [{
        "filename": "dragon_quant-1.0.0-py3-none-any.whl",
        "digests": {"sha256": "0" * 64}}]})
    assert fixture.run(*_npm_args(), "--yes", env=fixture.env(extra={
        "NPM_TOKEN": "npm-secret"})).returncode == 0

    npm_state = json.loads(fixture.npm_state.read_text(encoding="utf-8"))
    npm_state["dist_tags"] = {}
    fixture.npm_state.write_text(json.dumps(npm_state), encoding="utf-8")
    state_path = fixture.state_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["complete"] = False
    state["steps"]["upload_npm"]["done"] = False
    state_path.write_text(json.dumps(state), encoding="utf-8")

    publishes_before = sum(1 for call in fixture.npm_calls() if call[:1] == ["publish"])
    result = fixture.run("--resume", "--yes", env=fixture.env(extra={
        "NPM_TOKEN": "npm-secret",
        "FAKE_NPM_CONFIG_COPY": str(fixture.logs / "npmrc-copy")}))
    assert result.returncode == 0, result.stderr
    assert sum(1 for call in fixture.npm_calls() if call[:1] == ["publish"]) == publishes_before
    assert any(call[:2] == ["dist-tag", "add"] for call in fixture.npm_calls())
    assert "_authToken=npm-secret" in (
        fixture.logs / "npmrc-copy").read_text(encoding="utf-8")


def test_publish_uses_0600_temp_npmrc_and_cleans_it(tmp_path):
    fixture = ReleaseFixture(tmp_path, pypi_current="1.0.0")
    fixture.set_pypi_releases({"1.0.0": [{
        "filename": "dragon_quant-1.0.0-py3-none-any.whl",
        "digests": {"sha256": "0" * 64}}]})
    copy_path = fixture.logs / "npmrc-copy"
    result = fixture.run(*_npm_args(), "--yes", env=fixture.env(extra={
        "NPM_TOKEN": "npm-secret", "FAKE_NPM_CONFIG_COPY": str(copy_path)}))
    assert result.returncode == 0, result.stderr
    assert "_authToken=npm-secret" in copy_path.read_text(encoding="utf-8")
    leftovers = [p for p in (fixture.tmp / "tmpdir").glob("dq-npm-auth-*")]
    assert leftovers == [], leftovers


def test_sigterm_cleans_temporary_npm_publish_dir(tmp_path):
    fixture = ReleaseFixture(tmp_path, pypi_current="1.0.0")
    fixture.set_pypi_releases({"1.0.0": [{
        "filename": "dragon_quant-1.0.0-py3-none-any.whl",
        "digests": {"sha256": "0" * 64}}]})
    marker = tmp_path / "publishing"
    env = fixture.env(extra={
        "NPM_TOKEN": "npm-secret", "FAKE_NPM_SLEEP_PUBLISH": "1",
        "FAKE_NPM_SLEEP_MARKER": str(marker)})
    child = subprocess.Popen(
        [sys.executable, str(ENTRY), "--repo-root", str(fixture.repo),
         *_npm_args(), "--yes"],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 20
    while not marker.exists():
        if time.time() > deadline:
            child.kill()
            raise AssertionError("publish never started: " + child.stderr.read())
        time.sleep(0.02)
    child.send_signal(signal.SIGTERM)
    child.wait(timeout=20)
    leftovers = [p for p in (fixture.tmp / "tmpdir").glob("dq-npm-auth-*")]
    assert leftovers == [], "temporary npmrc dir must be cleaned on SIGTERM"


def test_concurrent_release_is_rejected(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    git_dir = Path(fixture.git("rev-parse", "--absolute-git-dir").stdout.strip())
    lock_path = git_dir / "dq-release.lock"
    handle = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = fixture.run(*_pypi_args(), "--dry-run", env=fixture.env())
        assert result.returncode == 1
        assert "仓库锁" in result.stderr
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)


def test_resume_refuses_changing_the_frozen_plan(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    first = fixture.run(*_pypi_args(), "--yes", env=fixture.env(extra={
        "TWINE_PASSWORD": "x", "FAKE_PY_FAIL_BUILD": "1"}))
    assert first.returncode == 1
    result = fixture.run("--packages", "pypi", "--pypi-version", "9.9.9",
                         "--resume", "--yes", env=fixture.env())
    assert result.returncode == 1
    assert "不允许改变发布计划" in result.stderr


# ─── interaction ──────────────────────────────────────────────────────────


def test_interactive_cancel_has_zero_side_effects(tmp_path, monkeypatch, capsys):
    fixture = ReleaseFixture(tmp_path)
    from release import main as entry

    answers = iter(["3", "", "", "", "n"])  # both / suggestions / final "no"
    monkeypatch.setattr(entry, "_is_tty", lambda: True)
    monkeypatch.setattr(entry, "_INPUT", lambda prompt: next(answers))
    monkeypatch.setenv("DQ_RELEASE_STATUS_DIR", str(fixture.tmp / "state"))
    monkeypatch.setenv("DQ_RELEASE_PYTHON", str(fixture.fake_python))
    monkeypatch.setenv("PATH", f"{fixture.bin}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(fixture.tmp / "home"))

    rc = entry.main(["--dry-run", "--repo-root", str(fixture.repo)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "发布计划" in out
    assert "0.0.2" in out and "1.0.1" in out  # patch suggestions shown
    assert "已取消" in out
    assert not (fixture.tmp / "state" / "batches").exists()
    assert not fixture.py_log.exists()


def test_real_release_final_confirmation_defaults_to_no(tmp_path, monkeypatch, capsys):
    fixture = ReleaseFixture(tmp_path)
    from release import main as entry

    monkeypatch.setattr(entry, "_is_tty", lambda: True)
    monkeypatch.setattr(entry, "_INPUT", lambda prompt: "")  # accept default No
    monkeypatch.setenv("DQ_RELEASE_STATUS_DIR", str(fixture.tmp / "state"))

    rc = entry.main(["--packages", "pypi", "--pypi-version", "1.2.3",
                     "--yes", "--repo-root", str(fixture.repo)])
    # --yes still shows the confirmation in a TTY; empty input means No.
    assert rc == 0
    assert "已取消" in capsys.readouterr().out
    assert not (fixture.tmp / "state" / "batches").exists()


# ─── credentials ──────────────────────────────────────────────────────────


def _encrypt(path: Path, token: str, password: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "DQ_PUBLISH_PASSWORD": password}
    subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2",
         "-pass", "env:DQ_PUBLISH_PASSWORD", "-out", str(path)],
        input=token.encode(), env=env, check=True, capture_output=True)
    path.chmod(0o600)


def _credentials(tmp_path: Path):
    from release.credentials import Credentials
    from release.common import Runner

    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home")}
    return Credentials(tmp_path, Runner(cwd=tmp_path, env=env)), env


def test_one_password_decrypts_both_tokens(tmp_path, monkeypatch):
    from release import credentials as module

    _encrypt(tmp_path / "scripts" / "publish_token.enc", "pypi-abc", "shared-pass")
    _encrypt(tmp_path / "scripts" / "npm_token.enc", "npm_abc.def", "shared-pass")
    creds, _ = _credentials(tmp_path)
    prompts = []

    def fake_getpass(prompt=""):
        prompts.append(prompt)
        return "shared-pass"

    monkeypatch.setattr(module.getpass, "getpass", fake_getpass)
    monkeypatch.setattr(module.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    assert creds.pypi_token() == "pypi-abc"
    assert creds.npm_token() == "npm_abc.def"
    assert len(prompts) == 1, "one password prompt must serve both tokens"


def test_wrong_password_is_a_clean_error_without_leak(tmp_path, monkeypatch):
    from release import credentials as module

    _encrypt(tmp_path / "scripts" / "publish_token.enc", "pypi-secret-token",
             "shared-pass")
    creds, _ = _credentials(tmp_path)
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt="": "wrong-pass")
    monkeypatch.setattr(module.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    with pytest.raises(module.ReleaseError) as excinfo:
        creds.pypi_token()
    message = str(excinfo.value)
    assert "密码错误" in message
    assert "pypi-secret-token" not in message and "wrong-pass" not in message


def test_empty_control_and_corrupt_tokens_are_rejected(tmp_path, monkeypatch):
    from release import credentials as module

    _encrypt(tmp_path / "scripts" / "npm_token.enc", "", "p")
    creds, _ = _credentials(tmp_path)
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt="": "p")
    monkeypatch.setattr(module.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    with pytest.raises(module.ReleaseError) as excinfo:
        creds.npm_token()
    assert "为空" in str(excinfo.value)

    _encrypt(tmp_path / "scripts" / "npm_token.enc", "npm\tbad", "p")
    creds, _ = _credentials(tmp_path)
    with pytest.raises(module.ReleaseError) as excinfo:
        creds.npm_token()
    assert "控制字符" in str(excinfo.value)

    (tmp_path / "scripts" / "npm_token.enc").write_bytes(b"not-a-ciphertext")
    creds, _ = _credentials(tmp_path)
    with pytest.raises(module.ReleaseError) as excinfo:
        creds.npm_token()
    assert "密码错误" in str(excinfo.value)


def test_unselected_token_is_never_decrypted(tmp_path, monkeypatch):
    from release import credentials as module

    _encrypt(tmp_path / "scripts" / "publish_token.enc", "pypi-abc", "p")
    creds, _ = _credentials(tmp_path)
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt="": "p")
    monkeypatch.setattr(module.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    calls = []
    original = creds._decrypt

    def tracked(enc, package):
        calls.append(package)
        return original(enc, package)

    monkeypatch.setattr(creds, "_decrypt", tracked)
    assert creds.pypi_token() == "pypi-abc"
    assert calls == ["PyPI"]


def test_env_token_overrides_skip_password_prompt(tmp_path, monkeypatch):
    from release import credentials as module
    from release.common import Runner

    runner = Runner(cwd=tmp_path, env={"PATH": os.environ["PATH"],
                                       "TWINE_PASSWORD": "env-pypi",
                                       "NPM_TOKEN": "env-npm"})
    creds = module.Credentials(tmp_path, runner)
    monkeypatch.setattr(module.getpass, "getpass",
                        lambda prompt="": pytest.fail("must not prompt"))
    assert creds.pypi_token() == "env-pypi"
    assert creds.npm_token() == "env-npm"


# ─── encrypt_token.sh ─────────────────────────────────────────────────────


def _copy_encrypt_script(tmp_path: Path) -> Path:
    import shutil

    target = tmp_path / "repo" / "scripts" / "encrypt_token.sh"
    target.parent.mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "encrypt_token.sh", target)
    return target


def _decrypt_with_openssl(path: Path, password: str) -> str:
    result = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-d", "-in", str(path),
         "-pass", "env:DQ_TEST_PASSWORD"],
        env={**os.environ, "DQ_TEST_PASSWORD": password},
        capture_output=True, check=True)
    return result.stdout.decode("utf-8").strip()


def test_encrypt_token_script_roundtrips_both_tokens_with_one_password(tmp_path):
    script = _copy_encrypt_script(tmp_path)
    answers = "3\npypi-fake-token\nnpm-fake-token\nshared-pass\nshared-pass\n"
    result = subprocess.run(["bash", str(script)], input=answers, text=True,
                            capture_output=True)
    assert result.returncode == 0, result.stderr
    pypi = script.parent / "publish_token.enc"
    npm = script.parent / "npm_token.enc"
    assert pypi.is_file() and npm.is_file()
    assert _decrypt_with_openssl(pypi, "shared-pass") == "pypi-fake-token"
    assert _decrypt_with_openssl(npm, "shared-pass") == "npm-fake-token"


def test_encrypt_token_script_rejects_argv_secrets_and_keeps_unselected(tmp_path):
    script = _copy_encrypt_script(tmp_path)
    pypi = script.parent / "publish_token.enc"
    pypi.write_bytes(b"keep-me")
    result = subprocess.run(["bash", str(script), "--token", "x"],
                            text=True, capture_output=True)
    assert result.returncode == 2
    assert pypi.read_bytes() == b"keep-me"

    answers = "1\npypi-new\npw\npw\n"
    result = subprocess.run(["bash", str(script)], input=answers, text=True,
                            capture_output=True)
    assert result.returncode == 0, result.stderr
    assert not (script.parent / "npm_token.enc").exists()
    assert _decrypt_with_openssl(pypi, "pw") == "pypi-new"


def test_encrypt_token_script_mismatched_passwords_change_nothing(tmp_path):
    script = _copy_encrypt_script(tmp_path)
    pypi = script.parent / "publish_token.enc"
    pypi.write_bytes(b"old-ciphertext")
    answers = "1\npypi-new\npw-one\npw-two\n"
    result = subprocess.run(["bash", str(script)], input=answers, text=True,
                            capture_output=True)
    assert result.returncode == 1
    assert pypi.read_bytes() == b"old-ciphertext"


# ─── rework: real npm pack document + tarball (M1) ────────────────────────


def test_pack_whitelist_matches_real_npm_pack_json(tmp_path):
    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("npm not installed")
    from release import publish_npm
    from release.common import Runner

    staging = tmp_path / "pkg"
    (staging / "bin").mkdir(parents=True)
    (staging / "skills" / "dragon-quant").mkdir(parents=True)
    (staging / "bin" / "dragon-quant-skills.mjs").write_text(
        "#!/usr/bin/env node\n", encoding="utf-8")
    (staging / "skills" / "dragon-quant" / "SKILL.md").write_text(
        "---\nname: dragon-quant\nmetadata:\n"
        '  skill-version: "1.0.3"\n  min-cli-version: "1.0.3"\n---\n\n# skill\n',
        encoding="utf-8")
    (staging / "package.json").write_text(json.dumps({
        "name": "dragon-quant-skills",
        "version": "1.0.3",
        "bin": {"dragon-quant-skills": "bin/dragon-quant-skills.mjs"},
        "files": ["bin/", "skills/"],
    }, indent=2) + "\n", encoding="utf-8")
    cache = tmp_path / "npm-cache"
    runner = Runner(cwd=staging, env={
        "PATH": os.environ["PATH"], "HOME": str(tmp_path / "home"),
        "DQ_RELEASE_NPM_CACHE": str(cache),
    })
    # Real npm returns [{..., "files": [{path, size, mode}]}]; this must pass.
    publish_npm._pack_whitelist(staging, runner)
    tarball = publish_npm._pack(staging, runner)
    import tarfile
    with tarfile.open(tarball) as archive:
        names = archive.getnames()
    assert "package/skills/dragon-quant/SKILL.md" in names, names
    assert "package/bin/dragon-quant-skills.mjs" in names
    assert publish_npm.artifact(staging, tarball)["integrity"].startswith("sha512-")


# ─── rework: interaction interrupts (M4) ──────────────────────────────────


def _entry_env(fixture: ReleaseFixture, monkeypatch) -> None:
    monkeypatch.setenv("DQ_RELEASE_STATUS_DIR", str(fixture.tmp / "state"))
    monkeypatch.setenv("DQ_RELEASE_PYTHON", str(fixture.fake_python))
    monkeypatch.setenv("PATH", f"{fixture.bin}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(fixture.tmp / "home"))
    monkeypatch.setenv("DQ_RELEASE_PYPI_URL", fixture.pypi_json_path.as_uri())
    monkeypatch.setenv("FAKE_PY_LOG", str(fixture.py_log))
    monkeypatch.setenv("FAKE_PY_UPDATE_PYPI", "1")


def test_resume_confirmation_defaults_to_no_and_eof_cancels(
        tmp_path, monkeypatch, capsys):
    fixture = ReleaseFixture(tmp_path)
    first = fixture.run(*_pypi_args(), "--yes", env=fixture.env(extra={
        "TWINE_PASSWORD": "x", "FAKE_PY_FAIL_BUILD": "1"}))
    assert first.returncode == 1
    builds = sum(1 for call in fixture.py_calls()
                 if call["argv"][:2] == ["-m", "build"])
    from release import main as entry

    _entry_env(fixture, monkeypatch)
    monkeypatch.setattr(entry, "_is_tty", lambda: True)
    monkeypatch.setattr(entry, "_INPUT", lambda prompt: "")  # accept default No
    rc = entry.main(["--resume", "--repo-root", str(fixture.repo)])
    assert rc == 0
    assert "已取消" in capsys.readouterr().out
    assert sum(1 for call in fixture.py_calls()
               if call["argv"][:2] == ["-m", "build"]) == builds

    def raise_eof(prompt=""):
        raise EOFError

    monkeypatch.setattr(entry, "_INPUT", raise_eof)
    rc = entry.main(["--resume", "--repo-root", str(fixture.repo)])
    assert rc == 130
    assert sum(1 for call in fixture.py_calls()
               if call["argv"][:2] == ["-m", "build"]) == builds


def test_eof_at_package_selection_cancels_without_looping(
        tmp_path, monkeypatch, capsys):
    fixture = ReleaseFixture(tmp_path)
    from release import main as entry

    _entry_env(fixture, monkeypatch)
    monkeypatch.setattr(entry, "_is_tty", lambda: True)

    def raise_eof(prompt=""):
        raise EOFError

    monkeypatch.setattr(entry, "_INPUT", raise_eof)
    rc = entry.main(["--dry-run", "--repo-root", str(fixture.repo)])
    assert rc == 130
    err = capsys.readouterr().err
    assert "已取消" in err and "EOF" in err
    assert not (fixture.tmp / "state" / "batches").exists()


def test_encrypt_script_rolls_back_when_second_replacement_fails(tmp_path):
    script = _copy_encrypt_script(tmp_path)
    pypi = script.parent / "publish_token.enc"
    npm = script.parent / "npm_token.enc"
    pypi.write_bytes(b"old-pypi")
    npm.write_bytes(b"old-npm")

    mvbin = tmp_path / "mvbin"
    mvbin.mkdir()
    _write_executable(mvbin / "mv", f"""#!/bin/sh
case "$*" in
  *npm_token.enc) exit 1 ;;
esac
exec "{shutil.which('mv')}" "$@"
""")
    answers = "3\npypi-new\nnpm-new\npw\npw\n"
    result = subprocess.run(
        ["bash", str(script)], input=answers, text=True, capture_output=True,
        env={**os.environ, "PATH": f"{mvbin}:{os.environ['PATH']}"})
    assert result.returncode == 1
    assert "回滚" in result.stderr
    assert pypi.read_bytes() == b"old-pypi"
    assert npm.read_bytes() == b"old-npm"


def test_encrypt_script_restores_terminal_echo_on_interrupt(tmp_path):
    script = _copy_encrypt_script(tmp_path)
    log = tmp_path / "stty.log"
    bindir = tmp_path / "sttybin"
    bindir.mkdir()
    _write_executable(bindir / "stty", f"""#!/bin/sh
printf '%s\\n' "$*" >> "{log}"
case "$1" in
  -g) printf '%s\\n' mockstate ;;
esac
exit 0
""")
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["bash", str(script)], stdin=slave, stdout=slave, stderr=slave,
        env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"},
        start_new_session=True)
    os.close(slave)

    def expect(token: bytes, timeout: float = 15) -> bytes:
        buffer = b""
        deadline = time.time() + timeout
        while token not in buffer:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise AssertionError(f"timeout waiting for {token!r}: {buffer[-400:]!r}")
            ready, _, _ = select.select([master], [], [], remaining)
            if not ready:
                raise AssertionError(f"timeout waiting for {token!r}: {buffer[-400:]!r}")
            buffer += os.read(master, 4096)
        return buffer

    try:
        expect("1=PyPI".encode())
        os.write(master, b"1\n")
        expect("PyPI token".encode())
        os.write(master, b"pypi-fake\n")
        expect("发布密码".encode())
        time.sleep(0.2)
        os.killpg(proc.pid, signal.SIGINT)
        proc.wait(timeout=15)
    finally:
        os.close(master)
        if proc.poll() is None:
            proc.kill()

    assert proc.returncode == 130
    entries = log.read_text().splitlines()
    assert "-echo" in entries, entries
    after = entries[entries.index("-echo") + 1:]
    assert any(entry == "mockstate" or entry == "echo" for entry in after), entries
    assert not (script.parent / "publish_token.enc").exists()


# ─── rework: min-cli yanked metadata (M7) ─────────────────────────────────


def test_all_yanked_min_cli_blocks_npm_upload(tmp_path):
    fixture = ReleaseFixture(tmp_path, pypi_current="1.0.0")
    fixture.set_pypi_releases({"1.0.0": [{
        "filename": "dragon_quant-1.0.0-py3-none-any.whl",
        "digests": {"sha256": "0" * 64}, "yanked": True}]})
    result = fixture.run(*_npm_args(), "--yes", env=fixture.env(extra={
        "NPM_TOKEN": "npm-secret"}))
    assert result.returncode == 1
    assert "不在 PyPI" in result.stderr
    assert not any(call[:1] == ["publish"] for call in fixture.npm_calls())
    assert fixture.git("tag", "--list").stdout.strip() == ""


# ─── rework: dist-tag downgrade guard before publish (M8) ─────────────────


def test_dist_tag_downgrade_is_rejected_before_publish(tmp_path):
    fixture = ReleaseFixture(tmp_path, pypi_current="1.0.0")
    fixture.set_pypi_releases({"1.0.0": [{
        "filename": "dragon_quant-1.0.0-py3-none-any.whl",
        "digests": {"sha256": "0" * 64}}]})
    fixture.npm_state.write_text(json.dumps({
        "published": {"1.0.9": {"integrity": "sha512-existing"}},
        "dist_tags": {"latest": "1.0.9"},
    }), encoding="utf-8")

    result = fixture.run(*_npm_args(), "--yes", env=fixture.env(extra={
        "NPM_TOKEN": "npm-secret"}))
    assert result.returncode == 1
    assert "拒绝在发布" in result.stderr and "dist-tag" in result.stderr
    assert not any(call[:1] == ["publish"] for call in fixture.npm_calls())
    npm_state = json.loads(fixture.npm_state.read_text(encoding="utf-8"))
    assert npm_state["dist_tags"]["latest"] == "1.0.9"


# ─── rework: Chinese commit subject / confirmation details (M10) ──────────


def test_release_commit_subject_is_chinese_with_trailer(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    result = fixture.run(*_pypi_args(), "--yes",
                         env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 0, result.stderr
    body = fixture.git("log", "-1", "--pretty=%B").stdout
    assert body.startswith("🔖 发布：PyPI 1.2.3"), body
    assert "Co-Authored-By: Codex <codex@openai.com>" in body


def test_final_confirmation_shows_branch_targets_and_actions(
        tmp_path, monkeypatch, capsys):
    fixture = ReleaseFixture(tmp_path)
    from release import main as entry

    _entry_env(fixture, monkeypatch)
    monkeypatch.setattr(entry, "_is_tty", lambda: True)
    monkeypatch.setattr(entry, "_INPUT", lambda prompt: "")  # default No
    rc = entry.main(["--packages", "pypi", "--pypi-version", "1.2.3",
                     "--repo-root", str(fixture.repo)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "分支：release/test -> origin/release/test" in out
    assert "原子 push" in out
    assert "上传 PyPI 1.2.3" in out
    assert "已取消" in out
    assert not fixture.py_log.exists()


# ─── rework: repository lock + pointer across linked worktrees (S2/M9) ────


def test_repository_lock_covers_linked_worktrees(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    common = _common_dir(fixture)
    worktree = tmp_path / "linked-wt"
    fixture.git("worktree", "add", "-q", str(worktree), "-b", "other")
    fixture.git("push", "-q", "-u", "origin", "other")

    handle = os.open(common / "dq-release.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            [sys.executable, str(ENTRY), "--repo-root", str(worktree),
             "--dry-run", "--packages", "pypi", "--pypi-version", "1.2.3"],
            text=True, capture_output=True, env=fixture.env())
        assert result.returncode == 1
        assert "仓库锁" in result.stderr
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)


def test_active_pointer_blocks_release_from_linked_worktree(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    first = fixture.run(*_pypi_args(), "--yes", env=fixture.env(extra={
        "TWINE_PASSWORD": "x", "FAKE_PY_FAIL_BUILD": "1"}))
    assert first.returncode == 1

    worktree = tmp_path / "linked-wt-pointer"
    fixture.git("worktree", "add", "-q", str(worktree), "-b", "other")
    fixture.git("push", "-q", "-u", "origin", "other")

    result = subprocess.run(
        [sys.executable, str(ENTRY), "--repo-root", str(worktree),
         "--packages", "pypi", "--pypi-version", "9.9.9", "--yes"],
        text=True, capture_output=True,
        env=fixture.env(extra={"TWINE_PASSWORD": "x"}))
    assert result.returncode == 1
    assert "未完成发布批次" in result.stderr


def test_resume_dist_tag_repair_passes_npm_otp(tmp_path):
    fixture = ReleaseFixture(tmp_path, pypi_current="1.0.0")
    fixture.set_pypi_releases({"1.0.0": [{
        "filename": "dragon_quant-1.0.0-py3-none-any.whl",
        "digests": {"sha256": "0" * 64}}]})
    assert fixture.run(*_npm_args(), "--yes", env=fixture.env(extra={
        "NPM_TOKEN": "npm-secret"})).returncode == 0
    npm_state = json.loads(fixture.npm_state.read_text(encoding="utf-8"))
    npm_state["dist_tags"] = {}
    fixture.npm_state.write_text(json.dumps(npm_state), encoding="utf-8")
    state_path = fixture.state_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["complete"] = False
    state["steps"]["upload_npm"]["done"] = False
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = fixture.run("--resume", "--yes", env=fixture.env(extra={
        "NPM_TOKEN": "npm-secret", "NPM_OTP": "654321"}))
    assert result.returncode == 0, result.stderr
    env_log = fixture.logs / "npm-env.jsonl"
    calls = [json.loads(line) for line in env_log.read_text().splitlines()]
    dist_tag_calls = [call for call in calls
                      if call["argv"][:2] == ["dist-tag", "add"]]
    assert dist_tag_calls and all(call["otp"] == "654321" for call in dist_tag_calls)
    # The OTP is passed explicitly; it must not leak into build/test subprocesses.
    assert all(call["otp"] is None for call in calls
               if call["argv"][:1] in (["test"], ["pack"], ["install"]))
