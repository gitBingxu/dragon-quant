"""Per-case sandbox: isolated environment, path validation, cleanup.

Safety invariants (P0):

* Every child gets its own ``DQ_DATA_DIR`` / ``HOME`` / ``TMPDIR`` / ``APPDATA``
  / ``XDG_DATA_HOME`` inside one temporary root.
* The environment is rebuilt from a whitelist; credentials, cookies, proxies,
  ``VIRTUAL_ENV`` and ``DQ_TEST_ALLOW_REAL_DATA`` are never inherited.
* Any path that escapes the root, is relative, symlink-escapes, or points at the
  real data directory aborts before a child process can be launched.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

REAL_DATA_ENV_EXCEPTION = "DQ_TEST_ALLOW_REAL_DATA"


class E2EHarnessError(RuntimeError):
    """Harness precondition failed; no child process was launched."""


def _resolved(path: str | os.PathLike[str]) -> Path:
    return Path(path).resolve()


def _is_within(child: Path, parent: Path) -> bool:
    child = child.resolve()
    parent = parent.resolve()
    return child == parent or parent in child.parents


def _same_existing_path(left: Path, right: Path) -> bool:
    """Filesystem identity check (catches case-insensitive/alias bypasses)."""

    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _touches_existing_path(child: Path, parent: Path) -> bool:
    """True if ``child`` or any existing ancestor is the same file as ``parent``."""

    current = Path(child)
    while True:
        if _same_existing_path(current, parent):
            return True
        if current.parent == current:
            return False
        current = current.parent


def _require_absolute(value: object, label: str) -> Path:
    if value is None or not str(value).strip():
        raise E2EHarnessError(f"{label} 必须显式给出且非空")
    path = Path(str(value))
    if not path.is_absolute():
        raise E2EHarnessError(f"{label} 必须是绝对路径: {path}")
    return path


def default_real_paths(environ: Mapping[str, str] | None = None) -> dict[str, Path]:
    """Platform default locations *without importing application code*.

    ``DQ_DATA_DIR`` is deliberately ignored: this is the real default the
    sentinel watches, not the sandbox override.
    """

    env = os.environ if environ is None else environ
    home = Path(env.get("HOME") or Path.home()).resolve()
    if sys.platform == "win32":
        appdata = Path(env.get("APPDATA") or home / "AppData" / "Roaming").resolve()
        xdg_data = appdata
        data = appdata / "dragon-quant"
    elif sys.platform == "darwin":
        appdata = (home / "Library" / "Application Support").resolve()
        xdg_data = (home / ".local" / "share").resolve()
        data = appdata / "dragon-quant"
    else:
        xdg_data = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share").resolve()
        appdata = xdg_data
        data = xdg_data / "dragon-quant"
    return {
        "data": data,
        "home": home,
        "appdata": appdata,
        "xdg_data": xdg_data,
        "tmp": Path(tempfile.gettempdir()).resolve(),
    }


def validate_environment(
    *,
    data_dir: object,
    home: object,
    tmp: object,
    appdata: object,
    xdg_data: object,
    root: object,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Path]:
    """Validate one sandbox environment; raise ``E2EHarnessError`` on any risk.

    Returns the resolved paths so callers never re-derive them (that would
    reopen a symlink-race window).
    """

    env = dict(os.environ if environ is None else environ)
    if env.get(REAL_DATA_ENV_EXCEPTION) == "1":
        raise E2EHarnessError(
            f"{REAL_DATA_ENV_EXCEPTION}=1 不可继承到 E2E；真实目录豁免必须在父进程清理"
        )

    root_path = _require_absolute(root, "sandbox root").resolve()
    if not root_path.exists():
        raise E2EHarnessError(f"sandbox root 不存在: {root_path}")

    real = default_real_paths(env)

    data = _require_absolute(data_dir, "DQ_DATA_DIR").resolve()
    real_data = real["data"].resolve()
    if (
        data == real_data
        or real_data in data.parents
        or _touches_existing_path(data, real_data)
    ):
        raise E2EHarnessError(f"DQ_DATA_DIR 指向真实数据目录: {data}")
    if not _is_within(data, root_path):
        raise E2EHarnessError(f"DQ_DATA_DIR 越界（不在 sandbox root 内）: {data}")
    if not data.parent.exists():
        raise E2EHarnessError(f"DQ_DATA_DIR 父目录不存在: {data.parent}")

    checked: dict[str, Path] = {"root": root_path, "data": data}
    for key, label, value, real_key in (
        ("home", "HOME", home, "home"),
        ("appdata", "APPDATA", appdata, "appdata"),
        ("xdg_data", "XDG_DATA_HOME", xdg_data, "xdg_data"),
    ):
        path = _require_absolute(value, label).resolve()
        real_path = real[real_key].resolve()
        if (
            path == real_path
            or real_path in path.parents
            or _touches_existing_path(path, real_path)
        ):
            raise E2EHarnessError(f"{label} 指向真实用户目录: {path}")
        if not _is_within(path, root_path):
            raise E2EHarnessError(f"{label} 越界（不在 sandbox root 内）: {path}")
        checked[key] = path

    # TMPDIR: require containment in the sandbox.  Do not compare with the
    # system temp default: on macOS/Linux mkdtemp legitimately lives *inside*
    # that default, so such a comparison would reject every valid sandbox.
    tmp_path = _require_absolute(tmp, "TMPDIR").resolve()
    if not _is_within(tmp_path, root_path):
        raise E2EHarnessError(f"TMPDIR 越界（不在 sandbox root 内）: {tmp_path}")
    checked["tmp"] = tmp_path
    return checked


def _canonical_now(now: str) -> str:
    try:
        moment = datetime.fromisoformat(now)
    except ValueError as exc:
        raise E2EHarnessError(f"DQ_E2E_NOW 不是合法 ISO 时间: {now}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone(timedelta(hours=8)))
    return moment.isoformat()


def _safe_case_id(case_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", case_id).strip("._")
    return (cleaned or "case")[:48]


@dataclass
class EnvSandbox:
    """One isolated temporary root with a whitelist-derived environment."""

    root: Path
    now: str
    data_dir: Path
    home: Path
    tmp: Path
    appdata: Path
    xdg_data: Path
    keep: bool = False
    parent_env: Mapping[str, str] | None = None
    _cleaned: bool = False

    @classmethod
    def for_case(
        cls,
        case_id: str,
        *,
        now: str,
        environ: Mapping[str, str] | None = None,
    ) -> "EnvSandbox":
        env = dict(os.environ if environ is None else environ)
        if env.get(REAL_DATA_ENV_EXCEPTION) == "1":
            raise E2EHarnessError(
                f"{REAL_DATA_ENV_EXCEPTION}=1 不可继承到 E2E；真实目录豁免必须在父进程清理"
            )
        if not env.get("PATH"):
            raise E2EHarnessError("PATH 缺失，无法安全启动子进程")

        root = _resolved(tempfile.mkdtemp(prefix=f"dq-e2e-{os.getpid()}-{_safe_case_id(case_id)}-"))
        data_dir = root / "data"
        home = root / "home"
        appdata = home / "AppData" / "Roaming"
        xdg_data = home / ".local" / "share"
        tmp = root / "tmp"
        for directory in (data_dir, home, appdata, xdg_data, tmp, root / "remote", root / "out"):
            directory.mkdir(parents=True, exist_ok=True)

        paths = validate_environment(
            data_dir=data_dir,
            home=home,
            tmp=tmp,
            appdata=appdata,
            xdg_data=xdg_data,
            root=root,
            environ=env,
        )
        box = cls(
            root=paths["root"],
            now=_canonical_now(now),
            data_dir=paths["data"],
            home=paths["home"],
            tmp=paths["tmp"],
            appdata=paths["appdata"],
            xdg_data=paths["xdg_data"],
            keep=env.get("DQ_E2E_KEEP") == "1",
            parent_env=env,
        )
        box.meta_path.write_text(
            json_text({"case": case_id, "now": box.now, "pid": os.getpid()}),
            encoding="utf-8",
        )
        return box

    # ─── paths ────────────────────────────────────────────────────────────

    @property
    def repo_root(self) -> Path:
        return Path(__file__).resolve().parents[3]

    @property
    def injection_dir(self) -> Path:
        return self.repo_root / "tests" / "e2e" / "injection"

    @property
    def remote(self) -> Path:
        return self.root / "remote"

    @property
    def out(self) -> Path:
        return self.root / "out"

    @property
    def call_log(self) -> Path:
        return self.root / "calls.jsonl"

    @property
    def meta_path(self) -> Path:
        return self.root / "meta.json"

    def assert_output_path(self, path: str | os.PathLike[str]) -> Path:
        if path is None or not str(path).strip():
            raise E2EHarnessError("输出路径必须显式给出")
        candidate = Path(str(path))
        if not candidate.is_absolute():
            raise E2EHarnessError(f"输出路径必须是绝对路径: {candidate}")
        resolved = candidate.resolve()
        if resolved == self.root or self.root not in resolved.parents:
            raise E2EHarnessError(f"输出路径越界（不在 sandbox root 内）: {resolved}")
        return resolved

    # ─── environment ──────────────────────────────────────────────────────

    def env(self, extra: Mapping[str, object] | None = None) -> dict[str, str]:
        parent = dict(self.parent_env or os.environ)
        env = {
            "PATH": parent.get("PATH", ""),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "Asia/Shanghai",
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "TMPDIR": str(self.tmp),
            "TEMP": str(self.tmp),
            "TMP": str(self.tmp),
            "APPDATA": str(self.appdata),
            "XDG_DATA_HOME": str(self.xdg_data),
            "DQ_DATA_DIR": str(self.data_dir),
            "DQ_E2E_ROOT": str(self.root),
            "DQ_E2E_NOW": self.now,
            "DQ_E2E_CALL_LOG": str(self.call_log),
            "DQ_E2E_REQUIRE_INJECTION": "1",
            "PLAYWRIGHT_BROWSERS_PATH": str(self.root / "browsers"),
            "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
            "PYTHONPATH": os.pathsep.join(
                (str(self.injection_dir), str(self.repo_root))
            ),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        if sys.platform == "win32" and parent.get("SYSTEMROOT"):
            env["SYSTEMROOT"] = parent["SYSTEMROOT"]
        if extra:
            for key, value in extra.items():
                if value is None:
                    env.pop(str(key), None)
                else:
                    env[str(key)] = str(value)
        return env

    # ─── lifecycle ────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        if self.keep:
            print(f"DQ_E2E_KEEP=1：保留沙箱 {self.root}")
            return
        if self.root.exists():
            shutil.rmtree(self.root)


def json_text(payload: object) -> str:
    """Deterministic JSON used for sandbox metadata."""

    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
