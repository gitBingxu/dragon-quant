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
import subprocess
import tempfile
from pathlib import Path

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SCHEMA_VERSION = 2

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


class Runner:
    """Thin subprocess adapter; tests inject fake executables through PATH.

    ``sep`` env handling: ``scrub=True`` removes credential variables inherited
    from the caller before the command starts, then re-applies the explicit
    ``env`` values (e.g. this one upload's token).
    """

    def __init__(self, *, cwd: Path, env: dict | None = None):
        self.cwd = Path(cwd)
        self.base_env = dict(os.environ if env is None else env)
        self.secrets: list[str] = []

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
    ) -> subprocess.CompletedProcess:
        merged = self.env_for(env, scrub=scrub)
        try:
            result = subprocess.run(
                argv,
                cwd=str(cwd or self.cwd),
                env=merged,
                text=True,
                capture_output=capture,
                check=False,
                input=stdin_text,
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise ReleaseError(stage, f"缺少命令：{argv[0]}") from exc
        if result.returncode != 0 and capture and not allow_failure:
            detail = redact((result.stderr or result.stdout or "").strip(), self.secrets)
            raise ReleaseError(
                stage,
                f"命令失败（exit {result.returncode}）：{' '.join(argv[:4])}\n{detail}",
            )
        return result
