"""Subprocess runner with a mandatory, identical-environment preflight.

Contract: every business command is preceded by a harmless ``python -c``
preflight using the *same* interpreter, environment and ``PYTHONPATH``.  If the
preflight cannot prove the injection is active, the business process is never
launched.  The post-run marker check is only a second line of defence.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Mapping, Sequence  # noqa: E402

if __package__ in (None, ""):  # pragma: no cover - script-mode bootstrap
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from sandbox import E2EHarnessError, EnvSandbox  # type: ignore
    from sentinel import RealDataSentinel  # type: ignore
else:
    from .sandbox import E2EHarnessError, EnvSandbox
    from .sentinel import RealDataSentinel

REPO_ROOT = Path(__file__).resolve().parents[3]
INJECTION_DIR = REPO_ROOT / "tests" / "e2e" / "injection"
EXPECTED_PROVIDERS = ("tdx", "ths", "xueqiu", "tencent")
PATH_OPTIONS = ("--out", "--target-dir", "--target_dir", "--config")
BUSINESS_DB_FILES = ("dragon.db", "assets.db", "prep.db")
CLOCK_MODES = ("runtime",)

PREFLIGHT_SNIPPET = r"""
import json
import os
import socket
import sys
from pathlib import Path

root = Path(os.environ["DQ_E2E_ROOT"]).resolve()
data = Path(os.environ["DQ_DATA_DIR"]).resolve()
result = {"pid": os.getpid(), "root": str(root)}

module = sys.modules.get("sitecustomize")
result["sitecustomize"] = getattr(module, "__file__", None)

marker_path = root / "injection.json"
result["marker_exists"] = marker_path.is_file()
try:
    result["injection"] = json.loads(marker_path.read_text(encoding="utf-8"))
except Exception as exc:
    result["injection"] = None
    result["injection_error"] = f"{type(exc).__name__}: {exc}"

try:
    import netguard
except Exception:
    netguard = None
result["netguard_installed"] = bool(
    netguard is not None and getattr(netguard, "INSTALLED", False)
)
result["netguard_patched"] = bool(
    netguard is not None
    and getattr(netguard, "is_patched", lambda: False)()
)

result["fake_provider_finder"] = any(
    getattr(finder, "E2E_FAKE_PROVIDER_FINDER", False) for finder in sys.meta_path
)
result["fake_backend_finder"] = any(
    getattr(finder, "E2E_FAKE_BACKEND_FINDER", False) for finder in sys.meta_path
)
try:
    import subprocess

    result["subprocess_popen_blocked"] = bool(
        getattr(getattr(subprocess, "Popen", None), "E2E_BLOCKED", False)
    )
    import procguard

    result["subprocess_guard"] = bool(getattr(procguard, "INSTALLED", False))
except Exception:
    result["subprocess_popen_blocked"] = False
    result["subprocess_guard"] = False
try:
    import importlib.util

    top_spec = importlib.util.find_spec("playwright")
    top_origin = getattr(top_spec, "origin", None)
    result["fake_playwright_origin"] = top_origin
    looks_fake = bool(
        top_origin
        and Path(top_origin).name == "__init__.py"
        and Path(top_origin).parent.name == "playwright"
        and Path(top_origin).parent.parent.name == "injection"
    )
    if looks_fake:
        result["fake_playwright_sync_origin"] = getattr(
            importlib.util.find_spec("playwright.sync_api"), "origin", None
        )
    else:
        result["fake_playwright_sync_origin"] = None
except Exception:
    result["fake_playwright_origin"] = None
    result["fake_playwright_sync_origin"] = None

result["app_imported"] = any(
    name == "dragon_quant" or name.startswith("dragon_quant.") for name in sys.modules
)
result["db_exists"] = any((data / name).exists() for name in ("dragon.db", "assets.db", "prep.db"))

result["external_blocked"] = False
result["dns_blocked"] = False
if result["netguard_patched"]:
    try:
        socket.create_connection(("e2e.invalid.example", 80), 0.2)
    except BaseException as exc:
        result["external_blocked"] = isinstance(exc, netguard.NetworkBlocked)
        result["external_error"] = type(exc).__name__
    try:
        socket.getaddrinfo("e2e.invalid.example", 80)
    except BaseException as exc:
        result["dns_blocked"] = isinstance(exc, netguard.NetworkBlocked)
        result["dns_error"] = type(exc).__name__
else:
    result["external_error"] = "netguard_not_patched"
    result["dns_error"] = "netguard_not_patched"

import datetime
import time

tz8 = datetime.timezone(datetime.timedelta(hours=8))
result["datetime_class"] = (
    f"{datetime.datetime.__module__}.{datetime.datetime.__name__}"
)
result["datetime_is_replaced"] = datetime.datetime.__name__ != "datetime"
result["time_function_name"] = getattr(time.time, "__name__", "")
result["time_is_replaced"] = result["time_function_name"] != "time"
result["datetime_isinstance_ok"] = bool(
    isinstance(datetime.datetime.now(), datetime.datetime)
    and isinstance(datetime.datetime(2020, 1, 1), datetime.datetime)
    and not isinstance(datetime.date(2020, 1, 1), datetime.datetime)
)
result["app_feature_imported"] = sorted(
    name for name in sys.modules
    if (name == "dragon_quant" or name.startswith("dragon_quant."))
    and name not in {
        "dragon_quant",
        "dragon_quant._version",
        "dragon_quant.infra",
        "dragon_quant.infra.clock",
    }
)
from dragon_quant.infra import clock as runtime_clock

result["runtime_clock_now"] = runtime_clock.now(tz8).isoformat()
result["runtime_clock_type"] = type(runtime_clock.get_clock()).__name__
payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
(root / "preflight.json").write_text(payload, encoding="utf-8")
print(payload)
"""


class E2EInjectionError(E2EHarnessError):
    """Injection could not be proven; the business command must not run."""


class E2ETimeoutError(E2EHarnessError):
    def __init__(self, message: str, *, pid: int, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.pid = pid
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True)
class CliResult:
    returncode: int
    stdout: str
    stderr: str
    command: tuple[str, ...] = ()
    timed_out: bool = False


# ─── process management ─────────────────────────────────────────────────────


def _terminate(process: subprocess.Popen) -> None:
    if os.name == "nt":  # pragma: no cover - CI is POSIX
        process.kill()
        return
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            pass


def _spawn(
    command: Sequence[str],
    env: Mapping[str, str],
    timeout: int,
    cwd: str | os.PathLike[str] | None = None,
) -> subprocess.CompletedProcess:
    kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": True,
    }
    if os.name == "nt":  # pragma: no cover - CI is POSIX
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(
        list(command),
        env=dict(env),
        cwd=str(cwd or REPO_ROOT),
        **kwargs,  # type: ignore[arg-type]
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate(process)
        stdout, stderr = process.communicate()
        raise E2ETimeoutError(
            f"子进程超时 {timeout}s: {' '.join(command)}",
            pid=process.pid,
            stdout=stdout or "",
            stderr=stderr or "",
        ) from None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


# ─── environment and argument validation ────────────────────────────────────


def _build_env(
    sandbox: EnvSandbox,
    *,
    allow_localhost: bool,
    env_override: Mapping[str, object] | None,
) -> dict[str, str]:
    extra = {"DQ_E2E_ALLOW_LOCALHOST": "1"} if allow_localhost else None
    env = sandbox.env(extra=extra)
    if env_override:
        for key, value in env_override.items():
            if value is None:
                env.pop(str(key), None)
            else:
                env[str(key)] = str(value)
    return env


def _validate_command_args(args: Sequence[str], sandbox: EnvSandbox) -> None:
    index = 0
    while index < len(args):
        token = str(args[index])
        matched = False
        for option in PATH_OPTIONS:
            if token == option:
                if index + 1 >= len(args):
                    raise E2EHarnessError(f"{option} 缺少路径参数")
                sandbox.assert_output_path(args[index + 1])
                index += 2
                matched = True
                break
            if token.startswith(option + "="):
                sandbox.assert_output_path(token.split("=", 1)[1])
                index += 1
                matched = True
                break
        if not matched:
            index += 1


# ─── preflight ──────────────────────────────────────────────────────────────


def _local_minute(iso_now: str) -> str:
    from datetime import datetime

    return datetime.fromisoformat(iso_now).strftime("%Y-%m-%dT%H:%M")


def _clock_mode(env: Mapping[str, object]) -> str:
    """Resolve the injection mode; M9C accepts the runtime seam only."""
    mode = str(env.get("DQ_E2E_CLOCK_MODE") or "runtime")
    if mode not in CLOCK_MODES:
        raise E2EInjectionError(
            f"未知 DQ_E2E_CLOCK_MODE: {mode!r}（M9C 只允许 {CLOCK_MODES}）")
    return mode


_CLOCK_IMPL = "runtime.clock"


def _read_preflight_payload(sandbox: EnvSandbox, stdout: str) -> dict:
    path = sandbox.root / "preflight.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise E2EInjectionError(f"preflight.json 不可读: {exc}") from exc
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise E2EInjectionError("preflight 未产出可解析的 JSON")


def _validate_preflight(
    payload: Mapping[str, object],
    sandbox: EnvSandbox,
    *,
    allow_localhost: bool,
    expected_clock: str,
    expected_clock_mode: str = "runtime",
) -> list[str]:
    problems: list[str] = []
    expected_sitecustomize = str((sandbox.injection_dir / "sitecustomize.py").resolve())
    expected_playwright = (sandbox.injection_dir / "playwright" / "__init__.py").resolve()
    expected_playwright_sync = (sandbox.injection_dir / "playwright" / "sync_api.py").resolve()
    actual_sitecustomize = payload.get("sitecustomize")
    if not actual_sitecustomize or str(Path(str(actual_sitecustomize)).resolve()) != expected_sitecustomize:
        problems.append(
            f"sitecustomize 路径不符: {actual_sitecustomize!r} != {expected_sitecustomize!r}"
        )

    marker = payload.get("injection")
    if not isinstance(marker, dict):
        problems.append(f"injection marker 缺失或不可读: {payload.get('injection_error')!r}")
    else:
        if str(marker.get("root")) != str(sandbox.root):
            problems.append(f"marker root 不符: {marker.get('root')!r} != {str(sandbox.root)!r}")
        if marker.get("clock") != expected_clock:
            problems.append(
                f"injection clock 不匹配: marker={marker.get('clock')!r} expected={expected_clock!r}"
            )
        if marker.get("netguard") is not True:
            problems.append("marker 未声明 netguard")
        expected_mode = "localhost" if allow_localhost else "block_all"
        if marker.get("netguard_mode") != expected_mode:
            problems.append(
                f"netguard_mode 不符: {marker.get('netguard_mode')!r} != {expected_mode!r}"
            )
        if marker.get("clock_mode") != expected_clock_mode:
            problems.append(
                f"clock_mode 不匹配: marker={marker.get('clock_mode')!r} "
                f"expected={expected_clock_mode!r}")
        if marker.get("clock_impl") != _CLOCK_IMPL:
            problems.append(
                f"clock_impl 缺失或错误: {marker.get('clock_impl')!r} "
                f"expected={_CLOCK_IMPL!r}")
        if marker.get("providers") != list(EXPECTED_PROVIDERS):
            problems.append(f"providers 声明不符: {marker.get('providers')!r}")
        expected_classes = {
            "TdxProvider",
            "THSProvider",
            "XueqiuProvider",
            "TencentProvider",
        }
        if set(marker.get("fake_classes") or []) != expected_classes:
            problems.append(f"fake_classes 不符: {marker.get('fake_classes')!r}")
        if marker.get("fake_assets_backend") is not True:
            problems.append("fake_assets_backend 未启用")
        if marker.get("fake_playwright") is not True:
            problems.append("fake_playwright 未启用")
        if str(Path(str(marker.get("fake_playwright_path"))).resolve()) != str(expected_playwright):
            problems.append(
                f"fake_playwright_path 不符: {marker.get('fake_playwright_path')!r}"
            )
        if str(Path(str(marker.get("fake_playwright_origin"))).resolve()) != str(expected_playwright):
            problems.append(
                f"marker fake_playwright_origin 不符: {marker.get('fake_playwright_origin')!r}"
            )
        if marker.get("subprocess_guard") is not True:
            problems.append("marker 未声明 subprocess_guard")

    if payload.get("app_feature_imported") != []:
        problems.append(
            f"preflight 不应 import 业务模块: {payload.get('app_feature_imported')!r}")
    if payload.get("netguard_installed") is not True:
        problems.append("netguard 未安装")
    if payload.get("netguard_patched") is not True:
        problems.append("netguard 未接管 socket 入口")
    if payload.get("subprocess_guard") is not True:
        problems.append("subprocess guard 未安装")
    if payload.get("subprocess_popen_blocked") is not True:
        problems.append("subprocess.Popen 未被 guard 接管")
    if payload.get("fake_provider_finder") is not True:
        problems.append("fake provider finder 未安装")
    if payload.get("fake_backend_finder") is not True:
        problems.append("fake assets backend finder 未安装")
    origin = payload.get("fake_playwright_origin")
    if not origin or str(Path(str(origin)).resolve()) != str(expected_playwright):
        problems.append(f"fake playwright 未遮蔽真实包: origin={origin!r}")
    sync_origin = payload.get("fake_playwright_sync_origin")
    if not sync_origin or str(Path(str(sync_origin)).resolve()) != str(expected_playwright_sync):
        problems.append(f"fake playwright.sync_api 来源不符: origin={sync_origin!r}")
    expected_prefix = _local_minute(expected_clock)
    if expected_clock_mode != "runtime":
        problems.append(
            f"M9C 只接受 runtime 时钟模式: {expected_clock_mode!r}")
    if payload.get("datetime_is_replaced") is not False:
        problems.append(
            f"datetime 不得被全局替换: {payload.get('datetime_is_replaced')!r}")
    if payload.get("datetime_class") != "datetime.datetime":
        problems.append(
            f"datetime.datetime 类被替换: {payload.get('datetime_class')!r}")
    if payload.get("time_is_replaced") is not False:
        problems.append(
            f"time.time 不得被全局替换: {payload.get('time_is_replaced')!r}")
    if payload.get("time_function_name") != "time":
        problems.append(
            f"time.time 名称异常: {payload.get('time_function_name')!r}")
    if payload.get("datetime_isinstance_ok") is not True:
        problems.append(
            f"datetime isinstance 语义被破坏: {payload.get('datetime_isinstance_ok')!r}")
    if payload.get("runtime_clock_type") != "FixedClock":
        problems.append(
            f"runtime clock 未固定: type={payload.get('runtime_clock_type')!r}")
    if not str(payload.get("runtime_clock_now") or "").startswith(expected_prefix):
        problems.append(
            f"runtime clock 未生效: runtime_clock_now="
            f"{payload.get('runtime_clock_now')!r} expected prefix={expected_prefix!r}")
    if payload.get("external_blocked") is not True:
        problems.append(f"外部网络未被拦截: {payload.get('external_error')!r}")
    if payload.get("dns_blocked") is not True:
        problems.append(f"DNS 未被拦截: {payload.get('dns_error')!r}")
    return problems


def _clear_artifacts(sandbox: EnvSandbox, *names: str) -> None:
    """Remove stale proof files so a previous run can never satisfy this one."""

    for name in names:
        try:
            (sandbox.root / name).unlink()
        except FileNotFoundError:
            pass


def preflight(
    sandbox: EnvSandbox,
    *,
    allow_localhost: bool = False,
    env: Mapping[str, object] | None = None,
    env_override: Mapping[str, object] | None = None,
    expected_clock: str | None = None,
    timeout: int = 30,
) -> dict:
    """Prove the injection with a harmless child before any business command."""

    _clear_artifacts(sandbox, "preflight.json", "injection.json")
    child_env = (
        dict(env)
        if env is not None
        else _build_env(
            sandbox, allow_localhost=allow_localhost, env_override=env_override
        )
    )
    if allow_localhost:
        child_env["DQ_E2E_ALLOW_LOCALHOST"] = "1"
    command = [sys.executable, "-c", PREFLIGHT_SNIPPET]
    completed = _spawn(command, child_env, timeout=timeout)
    if completed.returncode != 0:
        raise E2EInjectionError(
            "preflight 子进程失败（业务命令未启动）\n"
            f"exit={completed.returncode}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    payload = _read_preflight_payload(sandbox, completed.stdout)
    clock = expected_clock or sandbox.now
    clock_mode = _clock_mode(child_env)
    problems = _validate_preflight(
        payload, sandbox, allow_localhost=allow_localhost,
        expected_clock=clock, expected_clock_mode=clock_mode,
    )
    if problems:
        raise E2EInjectionError(
            "preflight 注入校验失败（业务命令未启动）:\n- " + "\n- ".join(problems)
            + f"\npayload: {json.dumps(payload, ensure_ascii=False, sort_keys=True)[:4000]}"
        )
    return payload


def _verify_marker(sandbox: EnvSandbox, expected_clock: str | None = None,
                   expected_clock_mode: str = "runtime") -> None:
    """Second line of defence after the business process has started."""

    path = sandbox.root / "injection.json"
    if not path.is_file():
        raise E2EInjectionError("业务进程结束后 injection marker 缺失")
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise E2EInjectionError(f"业务进程 marker 不可读: {exc}") from exc
    problems: list[str] = []
    if str(marker.get("root")) != str(sandbox.root):
        problems.append("marker root 不符")
    if marker.get("clock") != (expected_clock or sandbox.now):
        problems.append("marker clock 不符")
    if marker.get("clock_mode") != expected_clock_mode:
        problems.append("marker clock_mode 不符")
    if expected_clock_mode != "runtime":
        problems.append(f"M9C 只接受 runtime 时钟模式: {expected_clock_mode!r}")
    if marker.get("clock_impl") != _CLOCK_IMPL:
        problems.append("marker clock_impl 不符")
    if marker.get("netguard") is not True:
        problems.append("marker netguard 未声明")
    expected_sitecustomize = str((sandbox.injection_dir / "sitecustomize.py").resolve())
    if str(Path(str(marker.get("sitecustomize"))).resolve()) != expected_sitecustomize:
        problems.append("marker sitecustomize 路径不符")
    if problems:
        raise E2EInjectionError("业务进程注入校验失败: " + "; ".join(problems))


# ─── public runner API ──────────────────────────────────────────────────────


def run_module(
    module: str,
    args: Sequence[str] = (),
    *,
    sandbox: EnvSandbox,
    timeout: int = 60,
    allow_localhost: bool = False,
    env_override: Mapping[str, object] | None = None,
) -> CliResult:
    argv = list(args)
    _validate_command_args(argv, sandbox)
    env = _build_env(sandbox, allow_localhost=allow_localhost, env_override=env_override)
    clock_mode = _clock_mode(env)
    preflight(sandbox, allow_localhost=allow_localhost, env=env)
    _clear_artifacts(sandbox, "injection.json")
    command = [sys.executable, "-m", module, *argv]
    completed = _spawn(command, env, timeout=timeout)
    _verify_marker(sandbox, expected_clock_mode=clock_mode)
    return CliResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        command=tuple(command),
    )


def run_cli(
    args: Sequence[str],
    *,
    sandbox: EnvSandbox,
    timeout: int = 60,
    allow_localhost: bool = False,
    env_override: Mapping[str, object] | None = None,
) -> CliResult:
    return run_module(
        "dragon_quant",
        args,
        sandbox=sandbox,
        timeout=timeout,
        allow_localhost=allow_localhost,
        env_override=env_override,
    )


def run_python(
    code: str,
    *,
    sandbox: EnvSandbox,
    timeout: int = 30,
    allow_localhost: bool = False,
    env_override: Mapping[str, object] | None = None,
) -> CliResult:
    env = _build_env(sandbox, allow_localhost=allow_localhost, env_override=env_override)
    clock_mode = _clock_mode(env)
    preflight(sandbox, allow_localhost=allow_localhost, env=env)
    _clear_artifacts(sandbox, "injection.json")
    command = [sys.executable, "-c", code]
    completed = _spawn(command, env, timeout=timeout)
    _verify_marker(sandbox, expected_clock_mode=clock_mode)
    return CliResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        command=tuple(command),
    )


# ─── standalone self-check ─────────────────────────────────────────────────


def self_check() -> int:
    failures: list[str] = []
    sentinel = RealDataSentinel()
    before = sentinel.snapshot()
    sandbox = EnvSandbox.for_case("self-check", now="2026-09-11T15:30:00+08:00")
    try:
        payload = preflight(sandbox)
        print(
            f"OK preflight: netguard={payload.get('netguard_installed')} "
            f"clock={payload.get('runtime_clock_now')} "
            f"datetime_isinstance={payload.get('datetime_isinstance_ok')}")

        blocked = run_python(
            "import socket\nsocket.create_connection(('e2e.invalid.example', 80), 0.2)\n",
            sandbox=sandbox,
        )
        if blocked.returncode == 0 or "NetworkBlocked" not in blocked.stderr:
            failures.append("external network was not blocked")
        else:
            print("OK external network blocked")

        providers = run_python(
            "from dragon_quant.infra.provider.main import create_providers\n"
            "print(type(create_providers()['tdx']).__module__)\n",
            sandbox=sandbox,
        )
        if providers.returncode != 0 or "fake_providers" not in providers.stdout:
            failures.append(f"fake providers not active: {providers.stderr[-500:]}")
        else:
            print("OK fake providers active")

        clock = run_python(
            "from dragon_quant.cli import _resolve_trade_date\nprint(_resolve_trade_date(None))\n",
            sandbox=sandbox,
        )
        if clock.returncode != 0 or clock.stdout.strip() != "2026-09-11":
            failures.append(
                f"runtime clock does not drive the trade date: "
                f"{clock.stdout!r} {clock.stderr[-500:]}")
        else:
            print("OK runtime clock drives the trade date")

        no_freeze = run_python(
            "import datetime\n"
            "import time\n"
            "from datetime import datetime as cls\n"
            "print(datetime.datetime.__module__, datetime.datetime.__name__)\n"
            "print(time.time.__name__)\n"
            "print(isinstance(cls.now(), datetime.datetime),\n"
            "      isinstance(datetime.date(2020, 1, 1), cls))\n",
            sandbox=sandbox,
        )
        expected_no_freeze = ["datetime datetime", "time", "True False"]
        if no_freeze.returncode != 0 or no_freeze.stdout.splitlines() != expected_no_freeze:
            failures.append(
                f"datetime/time were replaced or isinstance broke: "
                f"{no_freeze.stdout!r} {no_freeze.stderr[-500:]}")
        else:
            print("OK datetime/time are not replaced")

        legacy_env = sandbox.env(extra={"DQ_E2E_CLOCK_MODE": "legacy"})
        try:
            preflight(sandbox, env=legacy_env)
        except E2EInjectionError:
            print("OK legacy clock mode rejected")
        else:
            failures.append("legacy clock mode was accepted")

        runtime_clock = run_python(
            "from dragon_quant.infra import clock\n"
            "print(clock.now().strftime('%Y-%m-%d %H:%M'))\n"
            "print(type(clock.get_clock()).__name__)\n",
            sandbox=sandbox,
        )
        runtime_lines = runtime_clock.stdout.splitlines()
        if (runtime_clock.returncode != 0
                or runtime_lines != ["2026-09-11 15:30", "FixedClock"]):
            failures.append(
                f"runtime clock not active: {runtime_clock.stdout!r} "
                f"{runtime_clock.stderr[-500:]}")
        else:
            print("OK runtime clock active")

        cookie = run_cli(
            ["data", "cookie-fetch"],
            sandbox=sandbox,
            env_override={"DQ_E2E_COOKIE_FETCH": "ok"},
        )
        cookie_file = sandbox.data_dir / "cookies" / "xueqiu"
        if cookie.returncode != 0 or not cookie_file.is_file():
            failures.append(f"fake playwright cookie fetch failed: {cookie.stderr[-500:]}")
        else:
            print("OK fake playwright cookie fetch")
    except Exception as exc:  # noqa: BLE001 - self-check reports every failure
        failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        sandbox.cleanup()
        try:
            sentinel.assert_unchanged(before, sentinel.snapshot())
        except Exception as exc:  # noqa: BLE001
            failures.append(f"real data sentinel: {exc}")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("SELF-CHECK OK")
    return 0


if __name__ == "__main__":
    if "--self-check" in sys.argv[1:]:
        raise SystemExit(self_check())
    print("usage: python tests/e2e/harness/runner.py --self-check", file=sys.stderr)
    raise SystemExit(2)
