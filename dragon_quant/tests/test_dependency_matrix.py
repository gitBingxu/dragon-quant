"""Dependency-direction gate for the final tree (Task 15).

The refactor fixes the navigation chain ``cli → commands → feature main → use
case → infra``.  This module pins the *module-level* import graph with a pure
AST scan (no application import, no network, no database):

* ``infra`` never imports a feature at module scope; the factor layer's
  read-only warehouse access is allowed only as **function-local**
  ``scan.storage`` / ``vpa.storage`` imports;
* the documented forbidden feature edges stay empty;
* ``scan`` / ``infra`` / ``review`` / ``review_account`` / ``vpa`` / ``commands``
  have no module-level import cycles;
* ``commands`` enters features only through their documented public entries and
  never through legacy migration paths.

Function-local (lazy) imports are deliberately excluded from the edge checks:
they are the approved cycle-breaking mechanism.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "dragon_quant"
FEATURES = ("scan", "review", "review_account", "vpa")


# ─── AST helpers ────────────────────────────────────────────────────────────


def _module_scope(body: list[ast.stmt]):
    """Yield module-scope statements, descending into If/Try/With but not functions."""
    for node in body:
        yield node
        if isinstance(node, ast.If):
            yield from _module_scope(node.body)
            yield from _module_scope(node.orelse)
        elif isinstance(node, ast.Try):
            yield from _module_scope(node.body)
            for handler in node.handlers:
                yield from _module_scope(handler.body)
            yield from _module_scope(node.orelse)
            yield from _module_scope(node.finalbody)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            yield from _module_scope(node.body)


def _imported_names(nodes) -> set[str]:
    """Absolute dotted names touched by ``nodes`` (``from X import y`` → X, X.y)."""
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            base = node.module or ""
            if base:
                names.add(base)
            names.update(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
                if alias.name != "*"
            )
    return names


def _module_level_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _imported_names(_module_scope(tree.body))


def _function_level_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            names |= _imported_names(ast.walk(node))
    return names


def _matches(name: str, prefix: str) -> bool:
    """Dotted-boundary match: ``a.b`` matches ``a`` and ``a.b`` but not ``a.bc``."""
    return name == prefix or name.startswith(prefix + ".")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _package_files(relative: str) -> list[Path]:
    base = PACKAGE / relative
    return sorted(base.rglob("*.py")) if base.is_dir() else [base]


# ─── public helpers (also used by the doc/architecture gates) ───────────────


def module_level_edges(package_name: str,
                       target_prefixes: tuple[str, ...]) -> list[tuple[str, str]]:
    """Module-level edges from ``dragon_quant.<package_name>`` into feature packages."""
    full_targets = tuple(f"dragon_quant.{name}" for name in target_prefixes)
    offenders: list[tuple[str, str]] = []
    for path in _package_files(package_name):
        for name in sorted(_module_level_names(path)):
            if any(_matches(name, target) for target in full_targets):
                offenders.append((_rel(path), name))
    return offenders


def forbidden_edges() -> list[tuple[str, str, str]]:
    """``(source_path, imported_name, rule)`` for every documented forbidden edge."""
    rules: dict[str, tuple[str, ...]] = {
        "scan/storage": (
            "dragon_quant.scan.logger",
            "dragon_quant.scan.orchestrator",
            "dragon_quant.scan.scores",
            "dragon_quant.scan.cache",
        ),
        "scan/logger": (
            "dragon_quant.scan.orchestrator",
            "dragon_quant.scan.scores",
            "dragon_quant.scan.cache",
        ),
        "scan/cache": (
            "dragon_quant.scan.orchestrator",
            "dragon_quant.review_account",
            "dragon_quant.review",
        ),
        "scan/scores": (
            "dragon_quant.scan.orchestrator",
            "dragon_quant.scan.storage",
            "dragon_quant.scan.logger",
        ),
        # ``review_account.market`` may read ``scan.storage.dragons`` for the v1
        # history exception; scores/orchestrator/cache stay off limits.
        "review_account": (
            "dragon_quant.scan.scores",
            "dragon_quant.scan.orchestrator",
            "dragon_quant.scan.cache",
        ),
        "review_account/live_trade": (
            "dragon_quant.scan",
            "dragon_quant.review",
        ),
    }
    offenders: list[tuple[str, str, str]] = []
    for relative, prefixes in rules.items():
        base = PACKAGE / relative
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            for name in sorted(_module_level_names(path)):
                if any(_matches(name, prefix) for prefix in prefixes):
                    offenders.append((_rel(path), name, relative))
    return offenders


def _package_modules(package_name: str) -> dict[str, Path]:
    base = PACKAGE / package_name.removeprefix("dragon_quant.")
    modules: dict[str, Path] = {}
    for path in sorted(base.rglob("*.py")):
        relative = (path.parent if path.name == "__init__.py" else path).relative_to(ROOT)
        modules[".".join(relative.with_suffix("").parts)] = path
    return modules


def _module_level_edges_within(path: Path, package_name: str,
                               modules: set[str]) -> set[str]:
    """Module-level edges inside one package (siblings only, lazy imports excluded)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    edges: set[str] = set()
    for node in _module_scope(tree.body):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                if node.module == package_name or node.module.startswith(package_name + "."):
                    edges.add(node.module)
                    for alias in node.names:
                        candidate = f"{node.module}.{alias.name}"
                        if candidate in modules:
                            edges.add(candidate)
            elif node.level == 1:
                base = package_name if node.module is None else f"{package_name}.{node.module}"
                if node.module is not None:
                    edges.add(base)
                for alias in node.names:
                    candidate = f"{base}.{alias.name}"
                    if candidate in modules:
                        edges.add(candidate)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == package_name or alias.name.startswith(package_name + "."):
                    edges.add(alias.name)
    return edges & modules


def package_cycle(package_name: str) -> list[str] | None:
    """First module-level import cycle in ``package_name`` (self edges ignored)."""
    modules = _package_modules(package_name)
    graph = {
        name: _module_level_edges_within(path, package_name, set(modules)) - {name}
        for name, path in modules.items()
    }
    white, gray, black = 0, 1, 2
    colors = {name: white for name in graph}
    trail: list[str] = []

    def visit(name: str) -> list[str] | None:
        colors[name] = gray
        trail.append(name)
        for child in sorted(graph[name]):
            if colors[child] == gray:
                return trail[trail.index(child):] + [child]
            if colors[child] == white:
                found = visit(child)
                if found is not None:
                    return found
        trail.pop()
        colors[name] = black
        return None

    for name in sorted(graph):
        if colors[name] == white:
            cycle = visit(name)
            if cycle is not None:
                return cycle
    return None


# ─── commands boundary ──────────────────────────────────────────────────────

# Legacy migration paths: commands must never enter the feature layer through
# them (they are removed in Task 16).
_LEGACY_PREFIXES = (
    "dragon_quant.storage",
    "dragon_quant.providers",
    "dragon_quant.runtime",
    "dragon_quant.models",
    "dragon_quant.utils",
    "dragon_quant.factors",
    "dragon_quant.auction",
    "dragon_quant.live_trade",
    "dragon_quant.assets_sync",
    "dragon_quant.scorers",
    "dragon_quant.cache",
    "dragon_quant.logging",
    "dragon_quant.orchestrator",
    "dragon_quant.data",
    "dragon_quant.sectors",
)

# Documented public entries a command may import.  Anything else (e.g.
# ``review_account.strategy.sell``, ``review_account.prep.compute``,
# ``infra.factor.builtin``, ``scan.scores``) is a deep import.
_COMMAND_PUBLIC_EXACT = frozenset({
    "dragon_quant._version",
    "dragon_quant.app",
    "dragon_quant.infra.clock",
    "dragon_quant.infra.provider.data",
    "dragon_quant.infra.provider.cookie",
    "dragon_quant.infra.provider.bundle",
    "dragon_quant.infra.storage.manager",
    "dragon_quant.infra.storage.connection",
    "dragon_quant.infra.factor",
    "dragon_quant.infra.factor.service",
    "dragon_quant.infra.assets.main",
    # purge-derived 的管理命令：DB 清理实现 + 复用 assets 同步进程锁（命令层组合）
    "dragon_quant.infra.factor.maintenance",
    "dragon_quant.infra.assets.sync.state",
    "dragon_quant.infra.assets.sync.errors",
    "dragon_quant.scan.orchestrator.main",
    "dragon_quant.scan.logger.main",
    "dragon_quant.review.main",
    "dragon_quant.review_account.main",
    "dragon_quant.review_account.prep.main",
    "dragon_quant.review_account.auction.main",
    "dragon_quant.review_account.live_trade.main",
    "dragon_quant.vpa.main",
})
_COMMAND_PUBLIC_TREES = (
    "dragon_quant.commands",
    # scan persistence package: main + documented scans/dragons/logs/blacklist/results
    "dragon_quant.scan.storage",
)

# Feature implementation modules a command must never reach into directly: their
# public symbols are re-exported by the owning ``main`` entry (Task 15 acceptance).
_COMMAND_FORBIDDEN_DEEP = (
    "dragon_quant.review_account.models",
    "dragon_quant.review_account.advice",
    "dragon_quant.review_account.data",
    "dragon_quant.vpa.report",
    "dragon_quant.vpa.storage",
)


def _command_imports() -> list[tuple[str, str, tuple[str, ...]]]:
    """``(path, base_module, expanded_names)`` for every command import statement."""
    pairs: set[tuple[str, str, tuple[str, ...]]] = set()
    for path in sorted((PACKAGE / "commands").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                base = node.module or ""
                expanded = tuple(
                    f"{base}.{alias.name}" if base else alias.name
                    for alias in node.names
                    if alias.name != "*"
                )
                pairs.add((_rel(path), base, expanded))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    pairs.add((_rel(path), alias.name, ()))
    return sorted(pairs)


def _allowed_command_import(name: str) -> bool:
    if name in _COMMAND_PUBLIC_EXACT:
        return True
    return any(_matches(name, tree) for tree in _COMMAND_PUBLIC_TREES)


def _command_deep_imports() -> list[tuple[str, str, tuple[str, ...]]]:
    offenders: list[tuple[str, str, tuple[str, ...]]] = []
    for path, base, expanded in _command_imports():
        if not base.startswith("dragon_quant."):
            continue
        if _allowed_command_import(base):
            continue
        # ``from dragon_quant.infra import clock`` imports a public submodule; the
        # expanded symbol is the module, so accept the statement when every
        # imported symbol resolves to a documented public entry.
        if expanded and all(_allowed_command_import(name) for name in expanded):
            continue
        offenders.append((path, base, expanded))
    return offenders


# ─── tests ──────────────────────────────────────────────────────────────────


def test_infra_has_no_module_level_feature_imports():
    assert module_level_edges("infra", target_prefixes=FEATURES) == []


def test_infra_storage_has_no_feature_imports_even_function_local():
    """``infra.storage`` 不得依赖任何 feature（函数内 import 也不行）。

    日志表的清理职责归 ``scan.storage.logs.clear_scan_logs``；``infra.storage`` 只做
    文件系统清理，调用方（``commands/storage.py``）组合两者。infra 的唯一批准的
    feature 只读例外是 ``infra.factor``（另一条测试单独覆盖）。
    """
    offenders: list[tuple[str, str]] = []
    for path in sorted((PACKAGE / "infra" / "storage").rglob("*.py")):
        names = _module_level_names(path) | _function_level_names(path)
        for name in sorted(names):
            if any(_matches(name, f"dragon_quant.{feature}") for feature in FEATURES):
                offenders.append((_rel(path), name))
    assert offenders == []


def test_factor_read_only_warehouse_imports_are_function_local():
    offenders: list[tuple[str, str, str]] = []
    for path in sorted((PACKAGE / "infra" / "factor").rglob("*.py")):
        for name in sorted(_module_level_names(path)):
            if any(_matches(name, f"dragon_quant.{feature}") for feature in FEATURES):
                offenders.append((_rel(path), name, "module-level"))
        for name in sorted(_function_level_names(path)):
            if not any(_matches(name, f"dragon_quant.{feature}")
                       for feature in FEATURES):
                continue
            if not (_matches(name, "dragon_quant.scan.storage")
                    or _matches(name, "dragon_quant.vpa.storage")):
                offenders.append((_rel(path), name, "function-level"))
    assert offenders == []


def test_forbidden_feature_edges_and_no_cycles():
    assert forbidden_edges() == []
    for package in (
        "dragon_quant.scan",
        "dragon_quant.infra",
        "dragon_quant.review",
        "dragon_quant.review_account",
        "dragon_quant.vpa",
        "dragon_quant.commands",
    ):
        cycle = package_cycle(package)
        assert cycle is None, f"{package} 模块级 import 环: " + " → ".join(cycle or [])


def test_commands_import_no_legacy_paths():
    offenders = sorted(
        (path, name)
        for path, base, expanded in _command_imports()
        for name in (base,) + expanded
        if any(_matches(name, prefix) for prefix in _LEGACY_PREFIXES)
    )
    assert offenders == []


def test_commands_import_only_documented_public_entries():
    assert _command_deep_imports() == []


def test_commands_do_not_deep_import_feature_modules():
    """Task 15 acceptance: main owns the re-exports; commands never bypass them."""
    offenders = sorted(
        (path, name)
        for path, base, expanded in _command_imports()
        for name in (base,) + expanded
        if any(_matches(name, prefix) for prefix in _COMMAND_FORBIDDEN_DEEP)
    )
    assert offenders == []
