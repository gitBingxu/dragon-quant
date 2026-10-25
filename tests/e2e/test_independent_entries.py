"""Independent entry points.

* I1-I4 (M1A): ``python -m dragon_quant.providers.cookie`` status / set /
  fetch (ok, empty, error) / unknown action.  The cookie module is *not*
  shadowed by the injection layer: the real code path runs, and only the
  object-storage/playwright/network seams are faked, so these tests cover the
  actual entry point without a browser or a socket.
* N1-N7 (M1F): the npm installer binary driven through a real ``node``
  subprocess with an isolated HOME/TMPDIR/npm cache and an unreachable
  ``npm_config_registry`` so any real registry access fails instead of leaking
  to the network.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tests.e2e.harness.sandbox import EnvSandbox

NOW = "2026-09-11T15:30:00+08:00"
SYNTHETIC_COOKIE = "xq_a_token=e2e-synthetic-token; u=e2e-synthetic-user"


def runner():
    from tests.e2e.harness import runner as module

    return module


@pytest.fixture
def sandbox():
    box = EnvSandbox.for_case("independent", now=NOW)
    try:
        yield box
    finally:
        box.cleanup()


def _cookie_module(args, sandbox, **kwargs):
    return runner().run_module(
        "dragon_quant.providers.cookie", args, sandbox=sandbox, **kwargs
    )


def _cookie_file(sandbox: EnvSandbox):
    return sandbox.data_dir / "cookies" / "xueqiu"


def test_i1_status_reports_empty_and_present_cookie(sandbox):
    empty = _cookie_module(["status"], sandbox)
    assert empty.returncode == 0, empty.stderr
    assert empty.stdout.strip() == "雪球: ❌ (0字符)"

    _cookie_file(sandbox).parent.mkdir(parents=True, exist_ok=True)
    _cookie_file(sandbox).write_text("xq_a_token=abc; xq_is_login=1", encoding="utf-8")
    present = _cookie_module(["status"], sandbox)
    assert present.returncode == 0, present.stderr
    assert present.stdout.strip() == "雪球: ✅ (29字符)"


def test_i2_set_writes_cookie_only_when_value_is_given(sandbox):
    missing_value = _cookie_module(["set"], sandbox)
    assert missing_value.returncode == 0, missing_value.stderr
    assert missing_value.stdout == ""
    assert not _cookie_file(sandbox).exists()

    written = _cookie_module(["set", "--cookie", "tok=1; u=2"], sandbox)
    assert written.returncode == 0, written.stderr
    assert "✅ 雪球 Cookie ->" in written.stdout
    assert _cookie_file(sandbox).read_text(encoding="utf-8") == "tok=1; u=2"

    status = _cookie_module(["status"], sandbox)
    assert status.stdout.strip() == "雪球: ✅ (10字符)"


def test_i3_fetch_ok_uses_fake_playwright_without_network(sandbox):
    result = _cookie_module(
        ["fetch"], sandbox, env_override={"DQ_E2E_COOKIE_FETCH": "ok"}
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert _cookie_file(sandbox).read_text(encoding="utf-8") == SYNTHETIC_COOKIE

    status = _cookie_module(["status"], sandbox)
    assert status.stdout.strip() == f"雪球: ✅ ({len(SYNTHETIC_COOKIE)}字符)"

    calls = [
        json.loads(line)
        for line in sandbox.call_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(
        record["provider"] == "playwright" and record["method"] == "chromium.launch"
        for record in calls
    )


def test_i3_fetch_empty_and_error_are_offline_and_write_nothing(sandbox):
    empty = _cookie_module(
        ["fetch"], sandbox, env_override={"DQ_E2E_COOKIE_FETCH": "empty"}
    )
    assert empty.returncode == 0, empty.stderr
    assert "⚠️ 雪球 Cookie 获取失败" in empty.stdout
    assert not _cookie_file(sandbox).exists()

    broken = _cookie_module(
        ["fetch"], sandbox, env_override={"DQ_E2E_COOKIE_FETCH": "error"}
    )
    assert broken.returncode == 0, broken.stderr
    assert "E2E fake playwright failure" in broken.stderr
    assert not _cookie_file(sandbox).exists()


def test_i4_unknown_action_exits_two(sandbox):
    result = _cookie_module(["bogus"], sandbox)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "invalid choice" in result.stderr
    assert "bogus" in result.stderr


# ─── N1-N7: npm installer over a real node subprocess ───────────────────────


def node_module():
    from tests.e2e.harness import node as module

    return module


def _installer(sandbox: EnvSandbox, args: list[str], **kwargs):
    module = node_module()
    assert module.node_bin(), "node 未安装，无法覆盖 N1-N7"
    return module.run_installer(args, sandbox=sandbox, **kwargs)


def _payload(result) -> dict:
    return json.loads(result.stdout)


def _project(sandbox: EnvSandbox, name: str = "project") -> Path:
    path = sandbox.root / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _env_recording_python(sandbox: EnvSandbox) -> tuple[Path, Path]:
    """A ``--python`` wrapper that records the inherited env then execs the venv."""

    dump = sandbox.root / "python-env.txt"
    script = sandbox.root / "python-wrapper"
    script.write_text(
        "#!/bin/sh\n"
        f"env > '{dump}'\n"
        f"exec '{sys.executable}' \"$@\"\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, dump


def _fake_version_python(sandbox: EnvSandbox, version: str) -> Path:
    script = sandbox.root / "fake-python"
    script.write_text(f"#!/bin/sh\necho 'dragon-quant {version}'\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def test_n1_version_and_help_are_offline(sandbox):
    version = _installer(sandbox, ["version", "--json"])
    assert version.returncode == 0, version.stderr
    payload = _payload(version)
    assert payload["ok"] is True
    assert payload["command"] == "version"
    assert payload["version"].count(".") == 2

    plain = _installer(sandbox, ["version"])
    assert plain.returncode == 0, plain.stderr
    assert plain.stdout.strip() == f"dragon-quant-skills {payload['version']}"

    help_result = _installer(sandbox, ["-h"])
    assert help_result.returncode == 0
    assert "用法" in help_result.stdout


def test_n2_unknown_command_exits_two(sandbox):
    result = _installer(sandbox, ["bogus", "--json"])
    assert result.returncode == 2
    assert _payload(result)["error"]["code"] == "usage"


def test_n3_install_dry_run_writes_nothing(sandbox):
    project = _project(sandbox)
    result = _installer(
        sandbox, ["install", "--target-dir", str(project), "--dry-run", "--json"])
    assert result.returncode == 0, result.stderr
    payload = _payload(result)
    reported = payload["agents"][0]["path"]
    assert os.path.isabs(reported)
    assert Path(reported) == (project / ".claude" / "skills" / "dragon-quant").resolve()
    assert payload["agents"][0]["state"] == "installed"
    assert not (project / ".claude").exists()


def test_n4_install_doctor_uninstall_lifecycle(sandbox):
    project = _project(sandbox)
    python, env_dump = _env_recording_python(sandbox)

    installed = _installer(
        sandbox, ["install", "--target-dir", str(project),
                  "--python", str(python), "--json"])
    assert installed.returncode == 0, installed.stderr
    payload = _payload(installed)
    skill = project / ".claude" / "skills" / "dragon-quant"
    assert (skill / "SKILL.md").is_file()
    assert (skill / ".dragon-quant-skill.json").is_file()
    assert payload["agents"][0]["state"] == "installed"
    assert payload["warnings"] == []

    # The Python probe inherits the sandbox environment, not the real one.
    env_text = env_dump.read_text(encoding="utf-8")
    assert f"DQ_DATA_DIR={sandbox.data_dir}" in env_text
    assert sandbox.injection_dir.as_posix() in env_text
    assert "DQ_TEST_ALLOW_REAL_DATA" not in env_text

    doctor = _installer(
        sandbox, ["doctor", "--target-dir", str(project),
                  "--python", str(python), "--json"])
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    report = _payload(doctor)
    assert report["ok"] is True
    checks = {check["id"]: check["status"] for check in report["checks"]}
    assert checks["install.present"] == "pass"
    assert checks["install.integrity"] == "pass"
    assert checks["cli.present"] == "pass"
    assert checks["cli.version"] == "pass"
    assert checks["reload.hint"] == "pass"

    uninstalled = _installer(
        sandbox, ["uninstall", "--target-dir", str(project), "--json"])
    assert uninstalled.returncode == 0, uninstalled.stderr
    assert _payload(uninstalled)["agents"][0]["state"] == "removed"
    assert not skill.exists()
    assert (project / ".claude" / "skills").is_dir()


def test_n5_modified_install_rejected_then_force_backs_up(sandbox):
    project = _project(sandbox)
    python = _fake_version_python(sandbox, "0.7.2")
    args = ["install", "--target-dir", str(project), "--python", str(python), "--json"]
    assert _installer(sandbox, args).returncode == 0

    skill = project / ".claude" / "skills" / "dragon-quant"
    original = (skill / "SKILL.md").read_text(encoding="utf-8")
    (skill / "SKILL.md").write_text(original + "\nuser edit\n", encoding="utf-8")

    rejected = _installer(sandbox, args)
    assert rejected.returncode == 1
    assert _payload(rejected)["error"]["code"] == "conflict"
    assert "user edit" in (skill / "SKILL.md").read_text(encoding="utf-8")

    forced = _installer(
        sandbox, ["install", "--target-dir", str(project),
                  "--python", str(python), "--force", "--json"])
    assert forced.returncode == 0, forced.stdout + forced.stderr
    forced_payload = _payload(forced)
    backup = Path(forced_payload["backup_path"])
    assert backup.is_dir()
    assert "user edit" in (backup / "SKILL.md").read_text(encoding="utf-8")
    assert (skill / "SKILL.md").read_text(encoding="utf-8") == original


def test_n6_missing_and_too_low_cli(sandbox):
    empty_path = sandbox.root / "empty-path"
    empty_path.mkdir()

    missing_project = _project(sandbox, "missing")
    missing = _installer(
        sandbox, ["install", "--target-dir", str(missing_project),
                  "--python", "/nonexistent/python", "--json"],
        env_override={"PATH": str(empty_path)})
    assert missing.returncode == 0, missing.stderr
    assert any("未找到 dragon-quant CLI" in warning
               for warning in _payload(missing)["warnings"])

    doctor = _installer(
        sandbox, ["doctor", "--target-dir", str(missing_project),
                  "--python", "/nonexistent/python", "--json"],
        env_override={"PATH": str(empty_path)})
    assert doctor.returncode == 1
    report = _payload(doctor)
    assert report["ok"] is False
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["cli.present"]["status"] == "fail"
    assert "venv" in checks["cli.present"]["fix"]

    low_project = _project(sandbox, "low")
    low_python = _fake_version_python(sandbox, "0.0.1")
    low_install = _installer(
        sandbox, ["install", "--target-dir", str(low_project),
                  "--python", str(low_python), "--json"])
    assert low_install.returncode == 0, low_install.stderr
    assert any("低于 skill 要求" in warning
               for warning in _payload(low_install)["warnings"])

    low_doctor = _installer(
        sandbox, ["doctor", "--target-dir", str(low_project),
                  "--python", str(low_python), "--json"])
    assert low_doctor.returncode == 1
    low_checks = {check["id"]: check for check in _payload(low_doctor)["checks"]}
    assert low_checks["cli.version"]["status"] == "fail"
    assert ">= 0.7.1" in low_checks["cli.version"]["detail"]


def test_n7_low_node_version_exits_two(sandbox):
    preload = sandbox.root / "node-preload.mjs"
    preload.write_text(
        "Object.defineProperty(process.versions, \"node\", "
        "{ value: \"22.19.0\", configurable: true });\n",
        encoding="utf-8",
    )
    module = node_module()
    assert module.node_bin(), "node 未安装，无法覆盖 N7"
    result = module.run_node(
        ["--import", str(preload), str(module.BIN), "version"],
        sandbox=sandbox)
    assert result.returncode == 2
    assert "低于最低要求 22.20.0" in result.stderr
