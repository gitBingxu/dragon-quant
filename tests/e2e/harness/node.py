"""Node/npm subprocess helper for the installer E2E cases (N1-N7).

Every ``node`` invocation runs with the sandbox environment (so any Python the
installer probes inherits the M0 injection and the temporary data directory)
plus an npm environment that can never reach a registry: ``offline`` is forced
and ``npm_config_registry`` points at a closed local port.  A preflight runs
before each node launch, proving the inherited Python environment is active.

Only stdlib is imported at module level (see ``test_architecture_rules``).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

from .runner import preflight
from .sandbox import EnvSandbox

REPO_ROOT = Path(__file__).resolve().parents[3]
BIN = REPO_ROOT / "bin" / "dragon-quant-skills.mjs"

#: Forced for every npm/node invocation in this suite.
OFFLINE_NPM_ENV: dict[str, str] = {
    "npm_config_offline": "true",
    "npm_config_prefer_offline": "true",
    "npm_config_audit": "false",
    "npm_config_fund": "false",
    "npm_config_update_notifier": "false",
    "npm_config_registry": "http://127.0.0.1:9/",
}


class NodeUnavailable(RuntimeError):
    """``node`` / ``npm`` is missing; the installer cases cannot run."""


def node_bin() -> str | None:
    return shutil.which("node")


def npm_bin() -> str | None:
    return shutil.which("npm")


def _require(executable: str | None, name: str) -> str:
    if not executable:
        raise NodeUnavailable(f"{name} 未安装")
    return executable


def _env(
    sandbox: EnvSandbox,
    env_override: Mapping[str, object] | None,
) -> dict[str, str]:
    extra: dict[str, object] = dict(OFFLINE_NPM_ENV)
    extra["npm_config_cache"] = str(sandbox.root / "npm-cache")
    if env_override:
        extra.update(env_override)
    return sandbox.env(extra=extra)


def _run(
    executable: str,
    argv: Sequence[str],
    *,
    sandbox: EnvSandbox,
    cwd: Path | str | None,
    timeout: float,
    env_override: Mapping[str, object] | None,
) -> subprocess.CompletedProcess:
    preflight(sandbox)
    return subprocess.run(
        [executable, *(str(item) for item in argv)],
        cwd=str(cwd or sandbox.root),
        env=_env(sandbox, env_override),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_node(
    argv: Sequence[str],
    *,
    sandbox: EnvSandbox,
    cwd: Path | str | None = None,
    timeout: float = 60.0,
    env_override: Mapping[str, object] | None = None,
) -> subprocess.CompletedProcess:
    return _run(
        _require(node_bin(), "node"), argv, sandbox=sandbox, cwd=cwd,
        timeout=timeout, env_override=env_override,
    )


def run_installer(
    args: Sequence[str],
    *,
    sandbox: EnvSandbox,
    cwd: Path | str | None = None,
    timeout: float = 120.0,
    env_override: Mapping[str, object] | None = None,
) -> subprocess.CompletedProcess:
    """Run ``node bin/dragon-quant-skills.mjs <args>`` in the sandbox."""

    return run_node(
        [str(BIN), *args], sandbox=sandbox, cwd=cwd, timeout=timeout,
        env_override=env_override,
    )


def run_npm(
    args: Sequence[str],
    *,
    sandbox: EnvSandbox,
    cwd: Path | str | None = None,
    timeout: float = 300.0,
    env_override: Mapping[str, object] | None = None,
) -> subprocess.CompletedProcess:
    """Run ``npm <args>`` offline (registry unreachable by construction)."""

    return _run(
        _require(npm_bin(), "npm"), args, sandbox=sandbox, cwd=cwd,
        timeout=timeout, env_override=env_override,
    )
