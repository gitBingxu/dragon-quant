"""Single, thin injection entry point for E2E child processes.

Loaded by Python's normal ``site`` initialization through ``PYTHONPATH``.  It
never imports the application: its only job is to validate sandbox paths,
install the network guard and the fake seams, and write an auditable marker.

Any validation failure raises before the child's ``-c`` / ``-m`` target runs,
which is the strongest guarantee the harness can offer.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import sys
from pathlib import Path

EXPECTED_PROVIDERS = ("tdx", "ths", "xueqiu", "tencent")
INJECTION_DIR = Path(__file__).resolve().parent


def _fail(message: str) -> None:
    raise RuntimeError(f"E2E injection aborted: {message}")


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        _fail(f"缺少环境变量 {name}")
    return value


def _within(child: Path, parent: Path) -> bool:
    child = child.resolve()
    parent = parent.resolve()
    return child == parent or parent in child.parents


def _real_home() -> Path | None:
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    except Exception:
        return None


def _real_data_dir() -> Path | None:
    home = _real_home()
    if home is None:
        return None
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        return (appdata / "dragon-quant").resolve()
    if sys.platform == "darwin":
        return (home / "Library" / "Application Support" / "dragon-quant").resolve()
    xdg = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return (xdg / "dragon-quant").resolve()


def _assert_sandbox_paths(root: Path, data_dir: Path) -> None:
    if not root.is_absolute():
        _fail("DQ_E2E_ROOT 必须是绝对路径")
    root = root.resolve()
    checks = {
        "DQ_DATA_DIR": os.environ.get("DQ_DATA_DIR"),
        "HOME": os.environ.get("HOME"),
        "TMPDIR": os.environ.get("TMPDIR"),
        "APPDATA": os.environ.get("APPDATA"),
        "XDG_DATA_HOME": os.environ.get("XDG_DATA_HOME"),
    }
    for name, raw in checks.items():
        if not raw:
            _fail(f"{name} 未设置")
        path = Path(raw)
        if not path.is_absolute():
            _fail(f"{name} 必须是绝对路径: {path}")
        if not _within(path, root):
            _fail(f"{name} 越界（不在 DQ_E2E_ROOT 内）: {path}")
    real = _real_data_dir()
    if real is not None and (data_dir.resolve() == real or real in data_dir.resolve().parents):
        _fail(f"DQ_DATA_DIR 指向真实数据目录: {data_dir}")
    if os.environ.get("DQ_TEST_ALLOW_REAL_DATA") == "1":
        _fail("DQ_TEST_ALLOW_REAL_DATA 不可继承")


class _ProviderLoader(importlib.abc.Loader):
    def __init__(self, fullname: str) -> None:
        self.fullname = fullname

    def create_module(self, spec):  # type: ignore[no-untyped-def]
        return None

    def exec_module(self, module) -> None:  # type: ignore[no-untyped-def]
        import fake_providers

        short = self.fullname.rsplit(".", 1)[-1]
        cls = fake_providers.PROVIDER_CLASSES[short]
        module.__dict__[cls.__name__] = cls
        module.__dict__["E2E_FAKE"] = True
        if short == "tdx":
            module.__dict__["warn_if_unavailable"] = fake_providers.warn_if_unavailable
            module.__dict__["is_available"] = fake_providers.is_available
        if short == "ths":
            module.__dict__["drain_fetch_failures"] = fake_providers.drain_fetch_failures


class _FakeProviderFinder(importlib.abc.MetaPathFinder):
    E2E_FAKE_PROVIDER_FINDER = True

    def find_spec(self, fullname, path=None, target=None):  # type: ignore[no-untyped-def]
        if not fullname.startswith("dragon_quant.providers."):
            return None
        short = fullname.rsplit(".", 1)[-1]
        if short not in EXPECTED_PROVIDERS:
            return None
        return importlib.util.spec_from_loader(fullname, _ProviderLoader(fullname))


class _BackendLoader(importlib.abc.Loader):
    def __init__(self, loader) -> None:  # type: ignore[no-untyped-def]
        self._loader = loader

    def create_module(self, spec):  # type: ignore[no-untyped-def]
        create = getattr(self._loader, "create_module", None)
        return create(spec) if create is not None else None

    def exec_module(self, module) -> None:  # type: ignore[no-untyped-def]
        self._loader.exec_module(module)
        import fake_assets_backend

        fake_assets_backend.patch_s3_module(module)


class _FakeBackendFinder(importlib.abc.MetaPathFinder):
    E2E_FAKE_BACKEND_FINDER = True
    TARGET = "dragon_quant.assets_sync.backends.s3"

    def find_spec(self, fullname, path=None, target=None):  # type: ignore[no-untyped-def]
        if fullname != self.TARGET:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _BackendLoader(spec.loader)
        return spec


def _import_required(name: str):  # type: ignore[no-untyped-def]
    try:
        return importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 - any failure must abort the child
        _fail(f"必需注入模块 {name} 不可用: {type(exc).__name__}: {exc}")


def _verify_fake_playwright() -> tuple[Path, Path]:
    expected_init = (INJECTION_DIR / "playwright" / "__init__.py").resolve()
    expected_sync = (INJECTION_DIR / "playwright" / "sync_api.py").resolve()

    spec = importlib.util.find_spec("playwright")
    origin = Path(spec.origin).resolve() if spec is not None and spec.origin else None
    if origin != expected_init:
        _fail(f"fake playwright 未遮蔽真实包: origin={origin} expected={expected_init}")

    sync_spec = importlib.util.find_spec("playwright.sync_api")
    sync_origin = (
        Path(sync_spec.origin).resolve()
        if sync_spec is not None and sync_spec.origin
        else None
    )
    if sync_origin != expected_sync:
        _fail(f"fake playwright.sync_api 不可用: origin={sync_origin} expected={expected_sync}")
    return expected_init, origin


def _install_required_seams() -> dict[str, object]:
    """Every seam is mandatory; a missing seam aborts before business code."""

    fake_clock = _import_required("fake_clock")
    clock_mode = fake_clock.install(os.environ["DQ_E2E_NOW"])
    clock_impl = "runtime.clock"

    fake_providers = _import_required("fake_providers")
    sys.meta_path.insert(0, _FakeProviderFinder())

    _import_required("fake_assets_backend")
    sys.meta_path.insert(0, _FakeBackendFinder())

    playwright_path, playwright_origin = _verify_fake_playwright()

    return {
        "providers": list(EXPECTED_PROVIDERS),
        "fake_classes": [
            fake_providers.PROVIDER_CLASSES[name].__name__ for name in EXPECTED_PROVIDERS
        ],
        "clock_mode": clock_mode,
        "clock_impl": clock_impl,
        "fake_assets_backend": True,
        "fake_playwright": True,
        "fake_playwright_path": str(playwright_path),
        "fake_playwright_origin": str(playwright_origin),
        "subprocess_guard": True,
    }


def _write_marker(root: Path, clock: str, allow_localhost: bool, capabilities: dict) -> None:
    marker = {
        "pid": os.getpid(),
        "root": str(root),
        "clock": clock,
        "sitecustomize": str(Path(__file__).resolve()),
        "netguard": True,
        "netguard_mode": "localhost" if allow_localhost else "block_all",
    }
    marker.update(capabilities)
    target = root / "injection.json"
    temporary = root / "injection.json.tmp"
    temporary.write_text(
        json.dumps(marker, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, target)


def _main() -> None:
    if os.environ.get("DQ_E2E_REQUIRE_INJECTION") != "1":
        _fail("DQ_E2E_REQUIRE_INJECTION != 1，拒绝在非 harness 环境注入")

    # Make sibling injection modules importable even if PYTHONPATH only lists
    # the parent directory (defence in depth).
    if str(INJECTION_DIR) not in sys.path:
        sys.path.insert(0, str(INJECTION_DIR))

    root = Path(_require("DQ_E2E_ROOT"))
    clock = _require("DQ_E2E_NOW")
    data_dir = Path(_require("DQ_DATA_DIR"))
    _assert_sandbox_paths(root, data_dir)

    import netguard

    allow_localhost = os.environ.get("DQ_E2E_ALLOW_LOCALHOST") == "1"
    netguard.install(allow_localhost=allow_localhost)
    if not netguard.is_patched():
        _fail("netguard 安装后 socket 入口未被接管")

    procguard = _import_required("procguard")
    procguard.install()

    capabilities = _install_required_seams()
    _write_marker(root.resolve(), clock, allow_localhost, capabilities)


_main()
