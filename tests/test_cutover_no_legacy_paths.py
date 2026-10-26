"""Task 16 cutover gate: the legacy import paths are gone for good.

There is no old-path exception left: the historical ``dragon_quant/providers``
package and the ``infra/provider/cookie_cli.py`` adapter are both removed (the
Cookie CLI is the formal ``data cookie-status|cookie-set|cookie-fetch``
command).  Everything else (the migration shims and ``infra/compat.py``) must
not exist, must not be imported by production code, and must fail to import in
a **fresh process** (so that a stale ``sys.modules`` entry from an earlier test
cannot fake success).
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "dragon_quant"

FORBIDDEN_PATHS = [
    "orchestrator.py", "data.py", "rate_limit.py", "sectors.py", "review.py",
    "cache", "logging", "scorers", "storage", "auction", "factors",
    "assets_sync", "live_trade", "models", "runtime", "utils",
    "review_account/strategy.py", "review_account/execution.py",
    "review_account/priority.py", "review_account/risk.py", "infra/compat.py",
    "providers", "infra/provider/cookie_cli.py",
]

# Module prefixes that may no longer be imported anywhere in production.
FORBIDDEN_PREFIXES = (
    "dragon_quant.orchestrator",
    "dragon_quant.data",
    "dragon_quant.rate_limit",
    "dragon_quant.sectors",
    "dragon_quant.cache",
    "dragon_quant.logging",
    "dragon_quant.scorers",
    "dragon_quant.storage",
    "dragon_quant.auction",
    "dragon_quant.factors",
    "dragon_quant.assets_sync",
    "dragon_quant.live_trade",
    "dragon_quant.models",
    "dragon_quant.runtime",
    "dragon_quant.utils",
    "dragon_quant.review_account.execution",
    "dragon_quant.review_account.priority",
    "dragon_quant.review_account.risk",
    "dragon_quant.infra.compat",
    "dragon_quant.providers",
    "dragon_quant.infra.provider.cookie_cli",
)


def _matches(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _iter_python(base: Path):
    for path in sorted(base.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _module_parts(path: Path, root: Path) -> list[str]:
    """Dotted module parts for ``path`` relative to ``root`` (``__init__`` folded)."""
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return parts


def _imported_names(tree: ast.AST, path: Path, root: Path) -> set[str]:
    """All absolute dotted names touched by ``tree``, resolving relative imports.

    ``from ..storage import db`` inside ``dragon_quant/scan/x.py`` resolves to
    ``dragon_quant.storage`` / ``dragon_quant.storage.db``; a legal sibling import
    such as ``from . import storage`` resolves to ``dragon_quant.scan.storage``.
    """
    parts = _module_parts(path, root)
    package = parts if path.name == "__init__.py" else parts[:-1]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                up = node.level - 1
                if up > len(package):
                    continue
                base_parts = package[: len(package) - up] if up else package
                base = ".".join(base_parts)
                if node.module:
                    base = f"{base}.{node.module}" if base else node.module
                if base:
                    names.add(base)
                names.update(
                    f"{base}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*" and base
                )
            elif node.module:
                names.add(node.module)
                names.update(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )
    return names


def _legacy_names_in_source(source: str, path: Path, root: Path) -> list[str]:
    """Forbidden names in ``source`` (exported for the synthetic AST test)."""
    tree = ast.parse(source, filename=str(path))
    return sorted(
        name for name in _imported_names(tree, path, root)
        if any(_matches(name, prefix) for prefix in FORBIDDEN_PREFIXES)
    )


def _ast_legacy_imports(*bases: Path) -> dict[str, list[str]]:
    """``{relative_path: [offending module, ...]}`` for every production import."""
    offenders: dict[str, list[str]] = {}
    for base in bases:
        for path in _iter_python(base):
            hits = _legacy_names_in_source(
                path.read_text(encoding="utf-8"), path, ROOT)
            if hits:
                offenders[path.relative_to(ROOT).as_posix()] = hits
    return offenders


def _run_isolated(code: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Fresh interpreter with an isolated data root (never touches real DBs)."""
    home = tmp_path / "home"
    data = tmp_path / "data"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "DQ_DATA_DIR": str(data),
        "HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120,
    )


# ─── filesystem ────────────────────────────────────────────────────────────


def test_legacy_paths_are_gone():
    remaining = [p for p in FORBIDDEN_PATHS if (PACKAGE / p).exists()]
    assert remaining == [], f"legacy paths still exist: {remaining}"


# ─── production imports ────────────────────────────────────────────────────


def test_no_production_import_of_legacy_paths():
    assert _ast_legacy_imports(PACKAGE, ROOT / "web_ui") == {}


def test_relative_legacy_imports_are_detected_synthetically(tmp_path):
    """Prove the resolver catches relative legacy imports (not just zero hits).

    Synthetic package tree, no application import:
    ``dragon_quant/scan/probe.py`` with ``from ..storage import db`` must resolve
    to ``dragon_quant.storage``; the legal sibling ``from . import sectors``
    resolves to ``dragon_quant.scan.sectors`` and must **not** be flagged.
    """
    package = tmp_path / "dragon_quant" / "scan"
    package.mkdir(parents=True)
    probe = package / "probe.py"

    found = _legacy_names_in_source(
        "from ..storage import db\n"
        "from .. import factors\n",
        probe, tmp_path,
    )
    assert "dragon_quant.storage" in found
    assert "dragon_quant.storage.db" in found
    assert "dragon_quant.factors" in found

    legal = _legacy_names_in_source(
        "from . import storage\n"
        "from .storage import x\n"
        "from dragon_quant.scan.storage import dragons\n",
        probe, tmp_path,
    )
    assert legal == [], legal


# ─── fresh-process importability ───────────────────────────────────────────


def test_legacy_modules_are_unimportable_in_fresh_process(tmp_path):
    code = """
import importlib
names = (
    "dragon_quant.infra.compat",
    "dragon_quant.orchestrator",
    "dragon_quant.data",
    "dragon_quant.rate_limit",
    "dragon_quant.sectors",
    "dragon_quant.storage",
    "dragon_quant.factors",
    "dragon_quant.runtime",
    "dragon_quant.auction",
    "dragon_quant.live_trade",
    "dragon_quant.assets_sync",
    "dragon_quant.models",
    "dragon_quant.utils",
    "dragon_quant.scorers",
    "dragon_quant.cache",
    "dragon_quant.logging",
    "dragon_quant.review_account.execution",
    "dragon_quant.review_account.priority",
    "dragon_quant.review_account.risk",
    "dragon_quant.providers",
    "dragon_quant.providers.cookie",
    "dragon_quant.infra.provider.cookie_cli",
)
for name in names:
    try:
        importlib.import_module(name)
    except ModuleNotFoundError:
        continue
    raise AssertionError(f"{name} still importable")
print("ok")
"""
    result = _run_isolated(code, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
