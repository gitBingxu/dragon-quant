"""M0 offline E2E safety canaries (C1-C17).

Every test here is a *negative* safety proof: it must fail before any business
process, network call, or real-data access can happen.  Business E2E scenarios
begin in M1; M0 only proves the harness itself.

Tests import the harness eagerly (stdlib-only) and the runner lazily so that
early TDD stages can run while later stages are still unimplemented.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dragon_quant.tests.e2e.harness.sandbox import (
    E2EHarnessError,
    EnvSandbox,
    default_real_paths,
    validate_environment,
)
from dragon_quant.tests.e2e.harness.sentinel import RealDataSentinel

NOW = "2026-09-11T15:30:00+08:00"
EXPECTED_DATE = "2026-09-11"
EXPECTED_CLOCK = "2026-09-11 15:30"


def runner():
    from dragon_quant.tests.e2e.harness import runner as module

    return module


@pytest.fixture
def sandbox():
    box = EnvSandbox.for_case("safety", now=NOW)
    try:
        yield box
    finally:
        box.cleanup()


def _validate_kwargs(root: Path, data_dir):
    return {
        "data_dir": data_dir,
        "home": root / "home",
        "tmp": root / "tmp",
        "appdata": root / "home" / "AppData" / "Roaming",
        "xdg_data": root / "home" / ".local" / "share",
        "root": root,
        "environ": dict(os.environ),
    }


# ─── C1-C4 / C16: path and environment rejection ────────────────────────────


def test_c1_missing_data_dir_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(E2EHarnessError, match="DQ_DATA_DIR"):
        validate_environment(**_validate_kwargs(root, None))


def test_c2_real_default_data_dir_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    real = default_real_paths(environ=dict(os.environ))["data"]
    with pytest.raises(E2EHarnessError, match="真实"):
        validate_environment(**_validate_kwargs(root, real))


def test_c3_relative_and_symlink_escape_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(E2EHarnessError, match="绝对"):
        validate_environment(**_validate_kwargs(root, "relative/data"))

    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "data-link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(E2EHarnessError, match="越界"):
        validate_environment(**_validate_kwargs(root, link))


def test_c3_case_insensitive_real_path_variant_rejected(tmp_path):
    fake_home = tmp_path / "fake-home"
    real_data = fake_home / "Library" / "Application Support" / "dragon-quant"
    real_data.mkdir(parents=True)
    variant = Path(str(real_data).upper())
    try:
        same = os.path.samefile(real_data, variant)
    except OSError:
        same = False
    if not same:
        pytest.skip("filesystem is case-sensitive; case-variant bypass not applicable")
    environ = {**os.environ, "HOME": str(fake_home)}
    with pytest.raises(E2EHarnessError, match="真实"):
        validate_environment(
            data_dir=variant,
            home=fake_home / "sandbox-home",
            tmp=tmp_path / "tmp",
            appdata=fake_home / "sandbox-home" / "AppData",
            xdg_data=fake_home / "sandbox-home" / ".local",
            root=tmp_path,
            environ=environ,
        )


def test_c4_output_outside_root_rejected_before_launch(sandbox, tmp_path, monkeypatch):
    outside = tmp_path / "outside.txt"
    with pytest.raises(E2EHarnessError, match="越界"):
        sandbox.assert_output_path(outside)

    launches = []
    real_spawn = runner()._spawn

    def spy(command, env, timeout, cwd=None):
        launches.append(tuple(command))
        return real_spawn(command, env, timeout, cwd=cwd)

    monkeypatch.setattr(runner(), "_spawn", spy)
    with pytest.raises(E2EHarnessError, match="越界"):
        runner().run_cli(["scan", "--out", str(outside)], sandbox=sandbox)
    assert launches == [], "argument validation must happen before any child launch"


def test_c16_inherited_real_data_exception_rejected():
    env = dict(os.environ)
    env["DQ_TEST_ALLOW_REAL_DATA"] = "1"
    with pytest.raises(E2EHarnessError, match="DQ_TEST_ALLOW_REAL_DATA"):
        EnvSandbox.for_case("inherited", now=NOW, environ=env)


def test_environment_is_whitelisted(sandbox):
    env = sandbox.env()
    for key in (
        "DQ_ASSETS_ENDPOINT",
        "DQ_ASSETS_BUCKET",
        "DQ_ASSETS_ACCESS_KEY_ID",
        "DQ_ASSETS_SECRET_ACCESS_KEY",
        "DQ_ASSETS_PROFILE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "XUEQIU_COOKIE",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "DQ_TEST_ALLOW_REAL_DATA",
    ):
        assert key not in env, f"{key} must not leak into the child environment"

    assert env["DQ_DATA_DIR"] == str(sandbox.data_dir)
    assert env["HOME"] == str(sandbox.home)
    assert env["TMPDIR"] == str(sandbox.tmp)
    assert env["APPDATA"] == str(sandbox.appdata)
    assert env["XDG_DATA_HOME"] == str(sandbox.xdg_data)
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["DQ_E2E_REQUIRE_INJECTION"] == "1"
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(sandbox.root / "browsers")
    assert env["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] == "1"
    assert str(sandbox.injection_dir) in env["PYTHONPATH"].split(os.pathsep)


# ─── C10: sentinel detects metadata changes on a fake production dir ────────


def test_c10_sentinel_detects_metadata_change(tmp_path):
    fake = tmp_path / "fake-real-data"
    fake.mkdir()
    for name in ("dragon.db", "assets.db", "prep.db"):
        (fake / name).write_bytes(b"x")

    sentinel = RealDataSentinel(data_dir=fake)
    before = sentinel.snapshot()
    sentinel.assert_unchanged(before, sentinel.snapshot())

    (fake / "dragon.db").write_bytes(b"changed-to-a-different-size")
    with pytest.raises(E2EHarnessError, match="dragon.db"):
        sentinel.assert_unchanged(before, sentinel.snapshot())


def test_c10_sentinel_detects_new_real_directory(tmp_path):
    missing = tmp_path / "not-created-yet"
    sentinel = RealDataSentinel(data_dir=missing)
    before = sentinel.snapshot()
    missing.mkdir()
    with pytest.raises(E2EHarnessError):
        sentinel.assert_unchanged(before, sentinel.snapshot())


# ─── C5 / C6 / C13: network guard ───────────────────────────────────────────


def test_c5_external_socket_blocked(sandbox):
    result = runner().run_python(
        "import socket\n"
        "socket.create_connection(('e2e.invalid.example', 80), 0.2)\n",
        sandbox=sandbox,
    )
    assert result.returncode != 0
    assert "NetworkBlocked" in result.stderr


def test_c6_dns_resolution_blocked(sandbox):
    result = runner().run_python(
        "import socket\n"
        "socket.getaddrinfo('e2e.invalid.example', 80)\n",
        sandbox=sandbox,
    )
    assert result.returncode != 0
    assert "NetworkBlocked" in result.stderr


def test_c13_localhost_only_when_explicitly_enabled(sandbox):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    code = (
        "import socket\n"
        "try:\n"
        f"    s = socket.create_connection(('127.0.0.1', {port}), 1.0)\n"
        "    print('LOCAL_OK')\n"
        "    s.close()\n"
        "except Exception as exc:\n"
        "    print('LOCAL_BLOCKED:' + type(exc).__name__)\n"
        "try:\n"
        "    socket.create_connection(('e2e.invalid.example', 80), 0.2)\n"
        "    print('EXTERNAL_OK')\n"
        "except Exception as exc:\n"
        "    print('EXTERNAL:' + type(exc).__name__)\n"
    )
    try:
        blocked = runner().run_python(code, sandbox=sandbox)
        assert "LOCAL_BLOCKED:NetworkBlocked" in blocked.stdout
        assert "EXTERNAL:NetworkBlocked" in blocked.stdout

        allowed = runner().run_python(code, sandbox=sandbox, allow_localhost=True)
        assert "LOCAL_OK" in allowed.stdout
        assert "EXTERNAL:NetworkBlocked" in allowed.stdout
    finally:
        server.close()


# ─── C7 / C8 / C17: provider and clock injection ────────────────────────────


def test_preflight_proves_every_required_seam(sandbox):
    payload = runner().preflight(sandbox)
    assert payload["app_feature_imported"] == []
    assert payload["netguard_installed"] is True
    assert payload["external_blocked"] is True
    assert payload["dns_blocked"] is True
    assert payload["subprocess_guard"] is True
    assert payload["subprocess_popen_blocked"] is True
    assert payload["fake_provider_finder"] is True
    assert payload["fake_backend_finder"] is True
    # M9C: the only clock seam is runtime.clock; datetime/time stay untouched.
    assert payload["datetime_is_replaced"] is False
    assert payload["datetime_class"] == "datetime.datetime"
    assert payload["time_is_replaced"] is False
    assert payload["time_function_name"] == "time"
    assert payload["datetime_isinstance_ok"] is True
    assert payload["runtime_clock_type"] == "FixedClock"
    assert payload["runtime_clock_now"].startswith("2026-09-11T15:30")
    expected_init = (sandbox.injection_dir / "playwright" / "__init__.py").resolve()
    expected_sync = (sandbox.injection_dir / "playwright" / "sync_api.py").resolve()
    assert Path(payload["fake_playwright_origin"]).resolve() == expected_init
    assert Path(payload["fake_playwright_sync_origin"]).resolve() == expected_sync
    marker = payload["injection"]
    assert marker["clock_mode"] == "runtime"
    assert marker["clock_impl"] == "runtime.clock"
    assert marker["providers"] == ["tdx", "ths", "xueqiu", "tencent"]
    assert marker["fake_assets_backend"] is True
    assert marker["fake_playwright"] is True
    assert Path(marker["fake_playwright_path"]).resolve() == expected_init
    assert marker["subprocess_guard"] is True


def test_subprocess_guard_blocks_every_launch_api(sandbox):
    code = (
        "import asyncio, os, subprocess, sys\n"
        "checks = []\n"
        "def attempt(name, fn):\n"
        "    try:\n"
        "        fn()\n"
        "    except BaseException as exc:\n"
        "        checks.append(name + ':' + type(exc).__name__)\n"
        "    else:\n"
        "        checks.append(name + ':ALLOWED')\n"
        "attempt('popen', lambda: subprocess.Popen([sys.executable, '-c', 'print(1)']))\n"
        "attempt('run', lambda: subprocess.run([sys.executable, '-c', 'print(1)']))\n"
        "attempt('call', lambda: subprocess.call([sys.executable, '-c', 'print(1)']))\n"
        "attempt('system', lambda: os.system('true'))\n"
        "attempt('fork', lambda: os.fork())\n"
        "if hasattr(os, 'posix_spawn'):\n"
        "    attempt('posix_spawn', lambda: os.posix_spawn('/bin/true', ['/bin/true'], {}))\n"
        "attempt('asyncio_exec', lambda: asyncio.run("
        "asyncio.create_subprocess_exec(sys.executable, '-c', 'print(1)'))) \n"
        "attempt('asyncio_shell', lambda: asyncio.run("
        "asyncio.create_subprocess_shell('true'))) \n"
        "attempt('playwright_install', lambda: subprocess.run("
        "[sys.executable, '-m', 'playwright', 'install', 'chromium']))\n"
        "print('\\n'.join(checks))\n"
    )
    result = runner().run_python(code, sandbox=sandbox)
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines
    for line in lines:
        assert line.endswith(":SubprocessBlocked"), line


def test_preflight_gate_rejects_tampered_proof(sandbox):
    payload = runner().preflight(sandbox)
    tampered = json.loads(json.dumps(payload))
    tampered["fake_playwright_origin"] = "/opt/real/playwright/__init__.py"
    tampered["fake_playwright_sync_origin"] = "/opt/real/playwright/sync_api.py"
    tampered["subprocess_guard"] = False
    tampered["injection"]["fake_playwright_origin"] = "/opt/real/playwright/__init__.py"
    tampered["injection"]["clock_impl"] = None
    problems = runner()._validate_preflight(
        tampered, sandbox, allow_localhost=False, expected_clock=sandbox.now
    )
    joined = "\n".join(problems)
    assert "playwright" in joined
    assert "clock_impl" in joined
    assert "subprocess" in joined


def test_fake_assets_backend_is_active(sandbox):
    code = (
        "import os\n"
        "from pathlib import Path\n"
        "from dragon_quant.infra.assets.sync.backends.s3 import S3Backend\n"
        "backend = S3Backend(object())\n"
        "backend.put('probe/key.txt', b'payload')\n"
        "print(S3Backend.__module__)\n"
        "print((Path(os.environ['DQ_E2E_ROOT']) / 'remote' / 'probe' / 'key.txt').read_text())\n"
    )
    result = runner().run_python(code, sandbox=sandbox)
    assert result.returncode == 0, result.stderr
    assert "fake_assets_backend" in result.stdout
    assert "payload" in result.stdout


def test_c7_fake_providers_are_active(sandbox):
    code = (
        "from dragon_quant.infra.provider.main import create_providers\n"
        "providers = create_providers()\n"
        "for name in ('tdx', 'ths', 'xueqiu', 'tencent'):\n"
        "    cls = type(providers[name])\n"
        "    print(f'{name}:{cls.__module__}:{cls.__name__}')\n"
    )
    result = runner().run_python(code, sandbox=sandbox)
    assert result.returncode == 0, result.stderr
    for name in ("tdx", "ths", "xueqiu", "tencent"):
        assert f"{name}:fake_providers:" in result.stdout


def test_c8_datetime_and_time_are_not_globally_replaced(sandbox):
    """M9C: neither ``datetime.datetime`` nor ``time.time`` is patched.

    The frozen instant lives only on ``dragon_quant.infra.clock``; every
    stdlib consumer keeps the real classes and functions.
    """

    code = (
        "import datetime\n"
        "import time\n"
        "from datetime import datetime as datetime_cls\n"
        "print('MODULE', datetime.datetime.__module__, datetime.datetime.__name__)\n"
        "print('SAME', datetime_cls is datetime.datetime)\n"
        "print('NOW', isinstance(datetime_cls.now(), datetime.datetime))\n"
        "print('TIME', time.time.__name__)\n"
        "print('PARSE',\n"
        "      datetime_cls.strptime('2026-09-11 15:30', '%Y-%m-%d %H:%M').year,\n"
        "      datetime_cls.fromtimestamp(0).year)\n"
    )
    result = runner().run_python(code, sandbox=sandbox)
    assert result.returncode == 0, result.stderr
    assert "MODULE datetime datetime" in result.stdout
    assert "SAME True" in result.stdout
    assert "NOW True" in result.stdout
    assert "TIME time" in result.stdout
    assert "PARSE 2026 1970" in result.stdout


def test_runtime_clock_is_frozen_and_isinstance_semantics_stay_intact(sandbox):
    """One seam: the runtime clock is frozen while plain classes stay normal.

    ``factors/evaluation.py`` checks ``isinstance(stamp, datetime)``.  With no
    global replacement that check is ordinary Python; the frozen moment is
    visible only through ``dragon_quant.infra.clock``.
    """

    code = (
        "import datetime\n"
        "from datetime import datetime as datetime_cls\n"
        "from dragon_quant.infra.factor.evaluation import _bar_date\n"
        "from dragon_quant.infra import clock\n"
        "print('CLOCK', clock.now().strftime('%Y-%m-%d %H:%M'),\n"
        "      type(clock.get_clock()).__name__)\n"
        "print('NOW', type(datetime_cls.now()).__module__,\n"
        "      isinstance(datetime_cls.now(), datetime_cls))\n"
        "print('MODULE', isinstance(datetime.datetime.now(), datetime.datetime))\n"
        "print('DATE', isinstance(datetime.date(2020, 1, 1), datetime_cls))\n"
        "print('FACTORY',\n"
        "      isinstance(datetime_cls.fromtimestamp(0), datetime_cls),\n"
        "      isinstance(datetime_cls.fromisoformat('2020-01-01T00:00:00'), datetime_cls),\n"
        "      isinstance(datetime_cls.strptime('2020-01-01', '%Y-%m-%d'), datetime_cls))\n"
        "class Bar:\n"
        "    timestamp = datetime_cls(2026, 9, 11, 15, 0)\n"
        "class DateBar:\n"
        "    timestamp = datetime.date(2026, 9, 11)\n"
        "print('BAR', _bar_date(Bar()), _bar_date(DateBar()))\n"
    )
    result = runner().run_python(code, sandbox=sandbox)
    assert result.returncode == 0, result.stderr
    assert f"CLOCK {EXPECTED_CLOCK} FixedClock" in result.stdout
    assert "NOW datetime True" in result.stdout
    assert "MODULE True" in result.stdout
    assert "DATE False" in result.stdout
    assert "FACTORY True True True" in result.stdout
    assert "BAR 2026-09-11 2026-09-11" in result.stdout


def test_legacy_clock_mode_is_rejected_before_any_child_launch(sandbox, monkeypatch):
    """M9C removed the legacy freeze; asking for it must never launch business code."""
    launches = _spy_launches(monkeypatch)
    with pytest.raises(runner().E2EInjectionError, match="DQ_E2E_CLOCK_MODE"):
        runner().run_cli(
            ["-h"], sandbox=sandbox,
            env_override={"DQ_E2E_CLOCK_MODE": "legacy"})
    assert launches == [], "unsupported clock mode must fail before any child"
    assert not sandbox.call_log.exists()


def test_legacy_clock_mode_is_rejected_inside_the_injection(sandbox):
    """The child-side seam refuses a non-runtime mode instead of freezing classes."""
    legacy_env = sandbox.env(extra={"DQ_E2E_CLOCK_MODE": "legacy"})
    with pytest.raises(runner().E2EInjectionError):
        runner().preflight(sandbox, env=legacy_env)


def test_c17_default_trade_date_flows_through_the_runtime_clock(sandbox):
    code = (
        "from dragon_quant.cli import _resolve_trade_date\n"
        "print(_resolve_trade_date(None))\n"
        "import datetime\n"
        "print(datetime.datetime.__name__)\n"
    )
    result = runner().run_python(code, sandbox=sandbox)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [EXPECTED_DATE, "datetime"]


# ─── C9 / C15: preflight gates business launch ──────────────────────────────


def _spy_launches(monkeypatch):
    launches = []
    real_spawn = runner()._spawn

    def spy(command, env, timeout, cwd=None):
        launches.append(tuple(command))
        return real_spawn(command, env, timeout, cwd=cwd)

    monkeypatch.setattr(runner(), "_spawn", spy)
    return launches


def test_c9_disabled_injection_prevents_business_launch(sandbox, monkeypatch):
    launches = _spy_launches(monkeypatch)
    with pytest.raises(runner().E2EInjectionError):
        runner().run_cli(["-h"], sandbox=sandbox, env_override={"PYTHONPATH": ""})

    assert len(launches) == 1, "only the harmless preflight may have run"
    preflight_command = launches[0]
    assert preflight_command[1] == "-c"
    assert "dragon_quant" not in preflight_command
    assert not (sandbox.data_dir / "dragon.db").exists()
    assert not (sandbox.data_dir / "assets.db").exists()
    assert not sandbox.call_log.exists()


def test_c15_marker_mismatch_prevents_business_launch(sandbox, monkeypatch):
    launches = _spy_launches(monkeypatch)
    with pytest.raises(runner().E2EInjectionError, match="clock|DQ_E2E_NOW|时间"):
        runner().run_cli(
            ["-h"],
            sandbox=sandbox,
            env_override={"DQ_E2E_NOW": "2000-01-01T00:00:00+08:00"},
        )
    assert len(launches) == 1
    assert launches[0][1] == "-c"
    assert "dragon_quant" not in launches[0]
    assert not sandbox.call_log.exists()


# ─── C11: help is side-effect free for the real data dir ────────────────────


def test_c11_help_does_not_touch_real_data(sandbox):
    sentinel = RealDataSentinel()
    before = sentinel.snapshot()
    result = runner().run_cli(["-h"], sandbox=sandbox)
    after = sentinel.snapshot()
    sentinel.assert_unchanged(before, after)
    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout
    assert "dragon_quant" not in result.stderr


# ─── C14: destructive storage command is sandbox-only ───────────────────────


def test_c14_storage_clear_only_touches_sandbox(sandbox):
    victims = ("cache/a.json", "results/r.txt", "logs/l.jsonl", "shared/s.json")
    for rel in victims:
        path = sandbox.data_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    for name in ("dragon.db", "assets.db", "prep.db"):
        (sandbox.data_dir / name).write_bytes(b"")

    sentinel = RealDataSentinel()
    before = sentinel.snapshot()
    result = runner().run_cli(["storage", "clear", "--all"], sandbox=sandbox)
    sentinel.assert_unchanged(before, sentinel.snapshot())

    assert result.returncode == 0, result.stderr
    assert "清理 cache" in result.stdout
    for rel in victims:
        assert not (sandbox.data_dir / rel).exists(), rel
    for name in ("dragon.db", "assets.db", "prep.db"):
        assert (sandbox.data_dir / name).exists(), name


# ─── Task 4: timeout, cleanup, standalone self-check ────────────────────────


def test_timeout_kills_child_process_group(sandbox):
    started = time.monotonic()
    with pytest.raises(runner().E2ETimeoutError) as caught:
        runner().run_python("import time\ntime.sleep(30)\n", sandbox=sandbox, timeout=1)
    assert time.monotonic() - started < 15
    pid = caught.value.pid
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"child pid {pid} still alive after timeout")


def test_cleanup_is_idempotent_and_keep_is_explicit():
    box = EnvSandbox.for_case("cleanup", now=NOW)
    root = box.root
    box.cleanup()
    assert not root.exists()
    box.cleanup()  # second call must be a no-op

    kept = EnvSandbox.for_case(
        "keep", now=NOW, environ={**os.environ, "DQ_E2E_KEEP": "1"}
    )
    try:
        kept_root = kept.root
        kept.cleanup()
        assert kept_root.exists(), "DQ_E2E_KEEP=1 keeps the root for diagnostics"
    finally:
        import shutil

        shutil.rmtree(kept.root, ignore_errors=True)


def test_standalone_self_check_exits_zero():
    repo = Path(__file__).resolve().parents[3]
    env = dict(os.environ)
    env.pop("DQ_TEST_ALLOW_REAL_DATA", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, str(repo / "dragon_quant/tests/e2e/harness/runner.py"), "--self-check"],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "SELF-CHECK OK" in proc.stdout


def test_minimal_fixture_is_pinned():
    fixture = Path(__file__).resolve().parent / "fixtures" / "scenario_minimal.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    assert data["now"] == NOW
    assert data["trade_date"] == EXPECTED_DATE
