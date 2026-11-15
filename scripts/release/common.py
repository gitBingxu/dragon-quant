"""Shared, dependency-free primitives for the unified release flow.

The release entry (``main.py``) is a Facade: argument parsing and interaction
live there, the use case lives in ``orchestrator.py``, and PyPI / npm live
behind small adapters.  This module holds only leaf helpers (errors, versions,
hashing, atomic writes, subprocess execution, secret scrubbing) so nobody
imports back up the stack.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import nullcontext
from pathlib import Path

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SCHEMA_VERSION = 2

# Conservative ceilings for subprocess work, in seconds.  They are not targets:
# a step that reaches one is terminated, fails its stage and stays resumable
# instead of waiting forever.
GIT_PROBE_TIMEOUT = 60.0       # read-only/local git probes (status, ls-remote, …)
REGISTRY_PROBE_TIMEOUT = 60.0  # remote registry / GitHub Release reads
BUILD_TIMEOUT = 20 * 60.0      # offline tests, frontend build, pack, install smoke
UPLOAD_TIMEOUT = 15 * 60.0     # registry upload, git push, GitHub Release create
DEFAULT_HEARTBEAT_SECONDS = 10.0

# Environment variables that carry credentials or one-time passwords.  Build
# and test subprocesses must never inherit them.
SECRET_ENV_KEYS = (
    "TWINE_PASSWORD",
    "NPM_TOKEN",
    "DQ_PUBLISH_PASSWORD",
    "NPM_OTP",
    "npm_config_otp",
)


class ReleaseError(RuntimeError):
    """A release step failed; ``stage`` says where."""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def now() -> str:
    return _now()


def validate_version(version: str | None) -> str:
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise ReleaseError("arguments", f"版本号格式错误（期望 x.y.z）：{version!r}")
    return version


def version_tuple(version: str) -> tuple[int, int, int]:
    validate_version(version)
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def compare_versions(left: str, right: str) -> int:
    """Return -1 / 0 / 1 comparing two ``x.y.z`` versions."""
    a, b = version_tuple(left), version_tuple(right)
    return (a > b) - (a < b)


def suggest_patch(version: str) -> str:
    try:
        major, minor, patch = version_tuple(version)
    except ReleaseError:
        return "0.0.1"
    return f"{major}.{minor}.{patch + 1}"


def redact(text: str, secrets: list[str] | tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha512_integrity(path: Path) -> str:
    """npm's ``dist.integrity`` value for a tarball: ``sha512-<base64>``."""
    digest = hashlib.sha512()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha512-" + base64.b64encode(digest.digest()).decode("ascii")


def atomic_write_text(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(raw)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data, mode: int = 0o644) -> None:
    atomic_write_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        mode=mode,
    )


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def plan_hash(plan: dict) -> str:
    payload = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ─── progress (stderr only) ────────────────────────────────────────────────


def _format_seconds(seconds: float) -> str:
    return f"{seconds:.1f}s"


class ProgressReporter:
    """Line-oriented progress for long release steps; writes only to a stream.

    Production uses ``sys.stderr`` so the JSON document on stdout stays
    machine-clean.  Every event is one line (no spinners/ANSI), readable in a
    terminal and in CI logs alike.  A task starts a daemon heartbeat thread
    that is always stopped and joined before the task returns, so nothing
    survives success, failure, timeout or Ctrl-C.
    """

    def __init__(self, stream=None, *,
                 heartbeat_interval: float = DEFAULT_HEARTBEAT_SECONDS,
                 clock=time.monotonic):
        self.stream = sys.stderr if stream is None else stream
        self.heartbeat_interval = float(heartbeat_interval)
        self._clock = clock
        self._lock = threading.Lock()

    def emit(self, text: str) -> None:
        with self._lock:
            self.stream.write(text + "\n")
            self.stream.flush()

    def task(self, label: str) -> "ProgressTask":
        return ProgressTask(self, label)


def progress_task(progress: ProgressReporter | None, label: str):
    """A reporter task when progress is configured, otherwise a no-op."""
    return progress.task(label) if progress is not None else nullcontext()


class ProgressTask:
    """``with reporter.task("label")``: start line, heartbeats, outcome line."""

    def __init__(self, reporter: ProgressReporter, label: str):
        self.reporter = reporter
        self.label = label
        self._started = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "ProgressTask":
        self._started = self.reporter._clock()
        self.reporter.emit(f"▶ {self.label} 开始")
        if self.reporter.heartbeat_interval > 0:
            self._thread = threading.Thread(
                target=self._heartbeat, name="dq-release-progress", daemon=True)
            self._thread.start()
        return self

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.reporter.heartbeat_interval):
            elapsed = self.reporter._clock() - self._started
            self.reporter.emit(
                f"… {self.label} 仍在运行（已耗时 {_format_seconds(elapsed)}）")

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        elapsed = _format_seconds(self.reporter._clock() - self._started)
        if exc_type is None:
            self.reporter.emit(f"✅ {self.label}（{elapsed}）")
        else:
            self.reporter.emit(f"❌ {self.label} 失败（{elapsed}）")
        return False


class Runner:
    """Thin subprocess adapter; tests inject fake executables through PATH.

    Every run is bounded by ``timeout`` (``GIT_PROBE_TIMEOUT`` unless a stage
    passes a larger one) and runs in its own session, so a timeout or Ctrl-C
    kills the whole child tree (git remote helpers, npm/node) instead of
    leaving it behind.  ``progress_label`` opts into stderr progress.

    ``sep`` env handling: ``scrub=True`` removes credential variables inherited
    from the caller before the command starts, then re-applies the explicit
    ``env`` values (e.g. this one upload's token).
    """

    def __init__(self, *, cwd: Path, env: dict | None = None,
                 progress: ProgressReporter | None = None):
        self.cwd = Path(cwd)
        self.base_env = dict(os.environ if env is None else env)
        self.secrets: list[str] = []
        self.progress = progress

    def env_for(self, extra: dict | None = None, *, scrub: bool = False) -> dict:
        env = dict(self.base_env)
        if scrub:
            for key in SECRET_ENV_KEYS:
                env.pop(key, None)
        if extra:
            env.update(extra)
        return env

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict | None = None,
        stage: str = "run",
        capture: bool = True,
        allow_failure: bool = False,
        scrub: bool = False,
        stdin_text: str | None = None,
        progress_label: str | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess:
        merged = self.env_for(env, scrub=scrub)
        limit = GIT_PROBE_TIMEOUT if timeout is None else timeout
        label = f"[{stage}] {progress_label}" if progress_label else None
        if label is not None and self.progress is not None:
            with self.progress.task(label):
                return self._run(argv, merged, cwd=cwd, stage=stage,
                                 capture=capture, allow_failure=allow_failure,
                                 stdin_text=stdin_text, timeout=limit)
        return self._run(argv, merged, cwd=cwd, stage=stage, capture=capture,
                         allow_failure=allow_failure, stdin_text=stdin_text,
                         timeout=limit)

    def _run(self, argv: list[str], merged: dict, *, cwd: Path | None,
             stage: str, capture: bool, allow_failure: bool,
             stdin_text: str | None, timeout: float) -> subprocess.CompletedProcess:
        try:
            result = self._execute(argv, merged, cwd=cwd, stdin_text=stdin_text,
                                   capture=capture, timeout=timeout)
        except FileNotFoundError as exc:
            raise ReleaseError(stage, f"缺少命令：{argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            command = redact(" ".join(argv[:4]), self.secrets)
            raise ReleaseError(
                stage,
                f"命令超时（>{timeout:g} 秒），已终止：{command}；"
                "该步骤未完成，可用 --resume 对账后继续",
            ) from exc
        if result.returncode != 0 and capture and not allow_failure:
            detail = redact((result.stderr or result.stdout or "").strip(), self.secrets)
            command = redact(" ".join(argv[:4]), self.secrets)
            raise ReleaseError(
                stage,
                f"命令失败（exit {result.returncode}）：{command}\n{detail}",
            )
        return result

    @staticmethod
    def _terminate_tree(process: subprocess.Popen) -> None:
        """Kill the child *and* everything it spawned in its session."""
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except OSError:
            process.kill()

    def _execute(self, argv: list[str], env: dict, *, cwd: Path | None,
                 stdin_text: str | None, capture: bool,
                 timeout: float) -> subprocess.CompletedProcess:
        streams: dict = {}
        if capture:
            streams = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
        process = subprocess.Popen(
            argv,
            cwd=str(cwd or self.cwd),
            env=env,
            text=True,
            errors="replace",
            stdin=subprocess.PIPE if stdin_text is not None else None,
            # Own session: a timeout or Ctrl-C terminates the whole child tree.
            start_new_session=True,
            **streams,
        )
        try:
            stdout, stderr = process.communicate(stdin_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            self._terminate_tree(process)
            self._reap(process)
            raise
        except BaseException:
            # Ctrl-C/SIGTERM: never leave the child running behind the exit.
            self._terminate_tree(process)
            self._reap(process)
            raise
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)

    @staticmethod
    def _reap(process: subprocess.Popen) -> None:
        try:
            process.communicate(timeout=5)
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
