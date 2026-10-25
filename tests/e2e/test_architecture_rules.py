"""C12 architectural rules for the M0 harness.

The harness must stay independent from the application and from pytest so that
it can bootstrap the E2E gate before any production refactor.  Injection files
run inside the child process edge; they may use stdlib at module import time and
are allowed function-local application imports only (none exist in M0).
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

E2E_ROOT = Path(__file__).resolve().parent
HARNESS = E2E_ROOT / "harness"
INJECTION = E2E_ROOT / "injection"
FORBIDDEN = {"dragon_quant", "pytest"}


def _import_roots(path: Path, *, top_level_only: bool) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    nodes = tree.body if top_level_only else ast.walk(tree)
    for node in nodes:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_c12_harness_never_imports_application_or_pytest():
    files = sorted(HARNESS.glob("*.py"))
    assert files, "harness modules must exist"
    for path in files:
        offenders = FORBIDDEN & _import_roots(path, top_level_only=False)
        assert not offenders, f"{path.name} imports {sorted(offenders)}"


def test_injection_module_level_imports_are_stdlib_only():
    files = sorted(INJECTION.rglob("*.py"))
    assert files, "injection modules must exist"
    for path in files:
        offenders = FORBIDDEN & _import_roots(path, top_level_only=True)
        assert not offenders, f"{path.name} has top-level imports {sorted(offenders)}"


def test_harness_is_stdlib_only_against_known_third_party():
    allowed = {
        "__future__",
        "argparse",
        "ast",
        "builtins",
        "contextlib",
        "copy",
        "dataclasses",
        "datetime",
        "errno",
        "hashlib",
        "http",
        "importlib",
        "json",
        "math",
        "os",
        "pathlib",
        "re",
        "sandbox",
        "sentinel",
        "shutil",
        "signal",
        "socket",
        "sqlite3",
        "subprocess",
        "sys",
        "tempfile",
        "time",
        "typing",
    }
    for path in sorted(HARNESS.glob("*.py")):
        roots = _import_roots(path, top_level_only=False)
        assert roots <= allowed, f"{path.name} imports outside the stdlib whitelist: {sorted(roots - allowed)}"


def _datetime_isinstance_sites(path: Path) -> list[int]:
    """Line numbers where ``isinstance(x, datetime)`` is used.

    The E2E clock freezes ``datetime.datetime`` with a subclass.  Such checks
    are only safe because the replacement overrides ``__instancecheck__``; any
    new site must be reviewed and pinned here.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    datetime_names: set[str] = set()
    datetime_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "datetime":
            for alias in node.names:
                if alias.name == "datetime":
                    datetime_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "datetime":
                    datetime_modules.add(alias.asname or alias.name)

    sites: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "isinstance"):
            continue
        if len(node.args) < 2:
            continue
        checks = [node.args[1]]
        if isinstance(node.args[1], ast.Tuple):
            checks = list(node.args[1].elts)
        for check in checks:
            if isinstance(check, ast.Name) and check.id in datetime_names:
                sites.append(node.lineno)
            elif (
                isinstance(check, ast.Attribute)
                and check.attr == "datetime"
                and isinstance(check.value, ast.Name)
                and check.value.id in datetime_modules
            ):
                sites.append(node.lineno)
    return sites


def test_datetime_isinstance_sites_are_audited():
    """M9C: ``datetime.datetime`` is never replaced, so isinstance stays ordinary.

    The frozen instant lives only on ``runtime.clock``.  The inventory stays
    pinned so a future refactor cannot silently reintroduce a replaced class and
    change ``factors/evaluation.py`` semantics.
    """

    expected = {"dragon_quant/factors/evaluation.py"}
    repo_root = E2E_ROOT.parents[1]
    found: dict[str, list[int]] = {}
    for path in sorted((repo_root / "dragon_quant").rglob("*.py")):
        sites = _datetime_isinstance_sites(path)
        if sites:
            found[path.relative_to(repo_root).as_posix()] = sites
    assert set(found) == expected, (
        "isinstance(..., datetime) 站点变化，必须重新审查时钟语义: "
        f"{ {name: lines for name, lines in found.items()} }"
    )
    assert found["dragon_quant/factors/evaluation.py"] == [65]


def _module_imports(path: Path) -> set[str]:
    """Absolute module names imported by ``path`` (import + from-import)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                modules.add(node.module)
    return modules


_CURRENT_TIME_CALLS = frozenset({
    ("datetime", "now"),
    ("datetime", "today"),
    ("datetime", "utcnow"),
    ("date", "today"),
    ("time", "time"),
    ("time", "strftime"),
    ("time", "localtime"),
    ("time", "mktime"),
})


def _call_chains(path: Path) -> set[str]:
    """Dotted call names such as ``clock.now`` / ``runtime_clock.set_clock``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    chains: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        parts: list[str] = []
        current = node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            chains.add(".".join(reversed(parts)))
    return chains


def _current_time_calls(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            pair = (func.value.id, func.attr)
            if pair in _CURRENT_TIME_CALLS:
                hits.append((f"{pair[0]}.{pair[1]}", node.lineno))
    return hits


def test_scan_and_review_current_time_flows_through_the_runtime_clock():
    """M9A: scan/review must not read current time from datetime/time directly."""
    repo_root = E2E_ROOT.parents[1]
    expectations = {
        "dragon_quant/orchestrator.py": {"clock.now", "clock.monotonic",
                                         "clock.stamp"},
        "dragon_quant/review.py": {"clock.now"},
    }
    for relative, required in expectations.items():
        with_test = repo_root / relative
        names = _imported_names(with_test)
        assert "dragon_quant.runtime.clock" in names, (
            f"{relative} must import the runtime clock seam")
        chains = _call_chains(with_test)
        missing = sorted(required - chains)
        assert not missing, f"{relative} misses clock calls: {missing}"
        offenders = _current_time_calls(with_test)
        assert not offenders, (
            f"{relative} still reads current time directly: {offenders}")


def test_m9b_current_time_flows_through_the_runtime_clock():
    """M9B1: review_account/live_trade/prep/auction/utils.trading use the clock."""
    repo_root = E2E_ROOT.parents[1]
    expectations = {
        "dragon_quant/review_account/data.py": {"clock.now"},
        "dragon_quant/review_account/prep/service.py": {"clock.now"},
        "dragon_quant/review_account/prep/collectors.py": {"clock.now"},
        "dragon_quant/review_account/prep/rebuild.py": {"clock.now"},
        "dragon_quant/live_trade/trader.py": {"clock.now"},
        "dragon_quant/auction/dates.py": {"clock.now"},
        "dragon_quant/utils/trading.py": {"clock.now"},
    }
    for relative, required in expectations.items():
        path = repo_root / relative
        names = _imported_names(path)
        assert "dragon_quant.runtime.clock" in names, (
            f"{relative} must import the runtime clock seam")
        missing = sorted(required - _call_chains(path))
        assert not missing, f"{relative} misses clock calls: {missing}"
        offenders = _current_time_calls(path)
        assert not offenders, (
            f"{relative} still reads current time directly: {offenders}")

    # Parsing-only modules must not acquire current time at all.
    static_paths = [repo_root / "dragon_quant/review_account/market.py"]
    static_paths += sorted((repo_root / "dragon_quant/vpa").rglob("*.py"))
    for path in static_paths:
        offenders = _current_time_calls(path)
        assert not offenders, (
            f"{path.relative_to(repo_root)} must not read current time: "
            f"{offenders}")


def test_m9b2_factors_cache_logging_storage_use_the_runtime_clock():
    """M9B2: factors/cache/logging/storage wall-time call sites use the clock seam."""
    repo_root = E2E_ROOT.parents[1]
    expectations = {
        "dragon_quant/factors/engine.py": {"clock.now", "clock.monotonic"},
        "dragon_quant/factors/matrix.py": {"clock.now"},
        "dragon_quant/factors/service.py": {"clock.now"},
        "dragon_quant/factors/finance.py": {"clock.now"},
        "dragon_quant/cache/data_cache.py": {"clock.now"},
        "dragon_quant/logging/logger.py": {"clock.now"},
        "dragon_quant/logging/query.py": {"clock.now"},
        "dragon_quant/storage/manager.py": {"clock.now"},
    }
    for relative, required in expectations.items():
        path = repo_root / relative
        names = _imported_names(path)
        assert "dragon_quant.runtime.clock" in names, (
            f"{relative} must import the runtime clock seam")
        missing = sorted(required - _call_chains(path))
        assert not missing, f"{relative} misses clock calls: {missing}"
        offenders = _current_time_calls(path)
        assert not offenders, (
            f"{relative} still reads current time directly: {offenders}")

    # Parsing-only modules must not acquire current time at all.
    parsing = repo_root / "dragon_quant/factors/evaluation.py"
    assert not _current_time_calls(parsing), (
        f"factors/evaluation.py must not read current time: "
        f"{_current_time_calls(parsing)}")


def test_fake_clock_is_runtime_only_and_keeps_app_imports_local():
    """M9C: one injection mode — the runtime clock seam — with no class patching."""
    path = INJECTION / "fake_clock.py"
    source = path.read_text(encoding="utf-8")
    functions = {
        node.name for node in ast.parse(source, filename=str(path)).body
        if isinstance(node, ast.FunctionDef)
    }
    assert {"install", "_install_runtime_clock"} <= functions
    assert "_install_legacy" not in functions, "legacy global freeze must stay gone"
    assert '"runtime"' in source
    assert "datetime.datetime =" not in source and "time.time =" not in source
    assert "dragon_quant" not in _import_roots(path, top_level_only=True)
    assert "runtime_clock.set_clock" in _call_chains(path)


def test_injection_never_replaces_datetime_or_time():
    """M9C: no injection module may assign a fake datetime/time source."""
    forbidden = {
        "datetime.datetime",
        "datetime.date",
        "time.time",
        "time.localtime",
        "time.strftime",
        "time.mktime",
    }
    offenders: list[str] = []
    for path in sorted(INJECTION.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                ):
                    pair = f"{target.value.id}.{target.attr}"
                    if pair in forbidden:
                        offenders.append(f"{path.name}:{node.lineno} {pair}")
    assert not offenders, (
        "injection 不得全局替换 datetime/time，只能注入 runtime.clock: "
        f"{offenders}")


#: M9C 审计清单：除 runtime.clock 适配器外，生产代码里仍有直接当前时间调用的
#: 文件 → 调用计数。任何新增/删除都必须在此显式登记，否则视为未受控时间。
_CURRENT_TIME_INVENTORY = {
    "dragon_quant/assets_sync/backends/s3.py": {"datetime.now": 1},
    "dragon_quant/assets_sync/backends/signer.py": {"datetime.now": 1},
    "dragon_quant/providers/tdx.py": {
        "time.time": 49, "time.mktime": 1,
        "time.strftime": 1, "time.localtime": 1},
    "dragon_quant/providers/tencent.py": {"time.time": 3, "time.mktime": 1},
    "dragon_quant/providers/ths.py": {
        "time.time": 14, "time.mktime": 3,
        "time.strftime": 1, "time.localtime": 1},
    "dragon_quant/providers/xueqiu.py": {"time.time": 5},
    "dragon_quant/runtime/clock.py": {"datetime.now": 1},
}

#: 每个保留调用的分类与理由（空理由视为未解释，测试会失败）。
_CURRENT_TIME_EXCEPTIONS = {
    "dragon_quant/runtime/clock.py":
        "SystemClock 适配器本体：唯一允许产生真实当前时间的位置。",
    "dragon_quant/providers/tdx.py":
        "E2E 用 fake provider 整体替换，模块体不执行；time.time 仅作耗时测量，"
        "time.mktime/strftime/localtime 只解析给定 K 线时间戳。",
    "dragon_quant/providers/tencent.py":
        "E2E 用 fake provider 整体替换，模块体不执行；time.time 仅耗时测量，"
        "time.mktime 只解析给定时间字符串。",
    "dragon_quant/providers/ths.py":
        "E2E 用 fake provider 整体替换，模块体不执行；time.time 仅耗时测量，"
        "mktime/strftime/localtime 只解析给定时间戳。",
    "dragon_quant/providers/xueqiu.py":
        "E2E 用 fake provider 整体替换，模块体不执行；time.time 仅耗时测量与"
        "请求防缓存参数，不写持久化墙钟字段。",
    "dragon_quant/assets_sync/backends/signer.py":
        "SigV4 签名时间戳，只在真实 S3 传输路径执行；E2E 使用 fake backend。",
    "dragon_quant/assets_sync/backends/s3.py":
        "服务端时钟偏差校验，只在真实 S3 传输路径执行；E2E 使用 fake backend。",
}


def test_every_current_time_call_is_classified():
    """Every direct current-time call outside runtime.clock has a reviewed reason."""
    repo_root = E2E_ROOT.parents[1]
    actual: dict[str, dict[str, int]] = {}
    for path in sorted((repo_root / "dragon_quant").rglob("*.py")):
        calls = _current_time_calls(path)
        if not calls:
            continue
        counts: dict[str, int] = {}
        for name, _line in calls:
            counts[name] = counts.get(name, 0) + 1
        actual[path.relative_to(repo_root).as_posix()] = counts

    assert actual == _CURRENT_TIME_INVENTORY, (
        "当前时间调用清单变化，必须重新分类（runtime 已迁移 / 纯解析常量 / 明确例外）:\n"
        f"actual={actual}")
    assert set(_CURRENT_TIME_EXCEPTIONS) == set(actual), (
        "每个保留调用点都必须有分类理由: "
        f"{sorted(set(actual) - set(_CURRENT_TIME_EXCEPTIONS))}")
    for name, reason in _CURRENT_TIME_EXCEPTIONS.items():
        assert len(reason.strip()) > 10, f"{name} 的例外理由过于笼统"

    # 时钟 seam 之外的旧生产调用点必须为零（M9C 迁移目标）。
    for migrated in (
        "dragon_quant/commands/_shared.py",
        "dragon_quant/commands/vpa.py",
        "dragon_quant/commands/prep.py",
        "dragon_quant/assets_sync/state.py",
        "dragon_quant/assets_sync/cli_service.py",
    ):
        assert not _current_time_calls(repo_root / migrated), (
            f"{migrated} must read current time through runtime.clock only")


def test_app_composition_root_does_not_import_cli_or_commands():
    """M2A: app.py is below the entrypoints, never on top of the command layer."""
    repo_root = E2E_ROOT.parents[1]
    imports = _module_imports(repo_root / "dragon_quant" / "app.py")
    offenders = sorted(
        name for name in imports
        if name in {"dragon_quant.cli"}
        or name.startswith("dragon_quant.commands")
    )
    assert not offenders, f"app.py must stay off the command layer: {offenders}"


_FEATURE_FILES = ("orchestrator.py", "review.py", "data.py")
_FEATURE_DIRS = ("review_account", "live_trade", "auction", "factors", "assets_sync", "vpa")


def test_feature_modules_do_not_import_the_composition_root():
    """M2A: feature modules consume optional Services; only the entry/command layer may import app."""
    repo_root = E2E_ROOT.parents[1]
    package = repo_root / "dragon_quant"
    feature_paths = [package / name for name in _FEATURE_FILES]
    for directory in _FEATURE_DIRS:
        feature_paths.extend(sorted((package / directory).rglob("*.py")))
    assert (package / "orchestrator.py").exists()
    assert (package / "review.py").exists()

    offenders: dict[str, list[str]] = {}
    for path in feature_paths:
        if not path.exists():
            continue
        hits = sorted(
            name for name in _module_imports(path)
            if name == "dragon_quant.app" or name.startswith("dragon_quant.app.")
        )
        if hits:
            offenders[path.relative_to(repo_root).as_posix()] = hits
    assert not offenders, f"feature modules must not import app: {offenders}"


def test_provider_bundle_has_a_single_definition():
    """M2A: runtime/providers.py is the only ProviderBundle definition site."""
    repo_root = E2E_ROOT.parents[1]
    sites: list[str] = []
    for path in sorted((repo_root / "dragon_quant").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(isinstance(node, ast.ClassDef) and node.name == "ProviderBundle"
               for node in tree.body):
            sites.append(path.relative_to(repo_root).as_posix())
    assert sites == ["dragon_quant/runtime/providers.py"], sites


# Feature objects that only their own factories may construct (M2 composition root).
_FEATURE_CONSTRUCTIONS = frozenset({
    "MarketData",
    "AccountSimulator",
    "Simulator",
    "LiveTrader",
    "S3Backend",
    "LocalDirBackend",
})


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def test_app_does_not_construct_feature_objects():
    """M2C: app.py assembles services only; feature objects stay in their own factories."""
    repo_root = E2E_ROOT.parents[1]
    called = _called_names(repo_root / "dragon_quant" / "app.py")
    offenders = sorted(called & _FEATURE_CONSTRUCTIONS)
    assert not offenders, f"app.py must not construct feature objects: {offenders}"


def test_runner_is_runnable_as_a_script():
    runner = HARNESS / "runner.py"
    source = runner.read_text(encoding="utf-8")
    assert "--self-check" in source
    assert "sys.dont_write_bytecode = True" in source


# ─── M3A command layer ──────────────────────────────────────────────────────


def _imported_names(path: Path) -> set[str]:
    """Dotted names touched by ``path`` (``from X import y`` → X and X.y)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
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


def test_command_modules_do_not_import_cli_statically():
    """M3A: old ``cli`` patch targets are reached via runtime lookup only."""
    repo_root = E2E_ROOT.parents[1]
    modules = sorted((repo_root / "dragon_quant" / "commands").glob("*.py"))
    assert modules, "the command layer must exist"
    offenders: dict[str, list[str]] = {}
    for path in modules:
        hits = sorted(
            name for name in _imported_names(path)
            if name == "dragon_quant.cli" or name.startswith("dragon_quant.cli.")
        )
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"command modules must not import cli: {offenders}"


def test_cli_loads_migrated_handlers_lazily():
    """M3A: cli.py imports the registry package, never a handler module."""
    repo_root = E2E_ROOT.parents[1]
    names = _imported_names(repo_root / "dragon_quant" / "cli.py")
    assert "dragon_quant.commands" in names or "dragon_quant" in names
    allowed = {"dispatch", "_shared"}   # package symbol / pure CLI helpers
    offenders = sorted(
        name for name in names
        if name.startswith("dragon_quant.commands.")
        and name.split(".")[2] not in allowed
    )
    assert not offenders, f"cli.py must load handlers lazily: {offenders}"


# ─── M3B command layer ──────────────────────────────────────────────────────

# Provider assembly belongs to ``app``/``runtime``; commands only consume a bundle.
_PROVIDER_CONSTRUCTION_NAMES = frozenset({
    "create_providers",
    "build_provider_bundle",
    "TdxProvider",
    "THSProvider",
    "XueqiuProvider",
    "TencentProvider",
})


def test_command_modules_never_construct_providers():
    """M3B: handlers receive providers; only the registry package assembles them."""
    repo_root = E2E_ROOT.parents[1]
    modules = sorted(
        path for path in (repo_root / "dragon_quant" / "commands").glob("*.py")
        if path.name != "__init__.py")
    assert modules, "the command layer must exist"
    offenders: dict[str, list[str]] = {}
    for path in modules:
        touched = _imported_names(path) | _called_names(path)
        hits = sorted(touched & _PROVIDER_CONSTRUCTION_NAMES)
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"command modules must not assemble providers: {offenders}"


# ─── M3C/M11B: final CLI decoupling ───────────────────────────────────────


def test_legacy_command_shim_is_deleted():
    """M11B: the compatibility forwarding shim is gone and unreferenced.

    ``commands.dispatch`` and the command modules are the only handler entry
    points; nothing in the package may keep importing the retired module.
    """
    repo_root = E2E_ROOT.parents[1]
    needle = "dragon_quant." + "cli_" + "commands"
    assert not (repo_root / "dragon_quant" / ("cli_" + "commands.py")).exists()
    assert importlib.util.find_spec(needle) is None

    offenders = sorted(
        path.relative_to(repo_root).as_posix()
        for path in (repo_root / "dragon_quant").rglob("*.py")
        if needle in path.read_text(encoding="utf-8")
    )
    assert not offenders, f"production code still references the deleted shim: {offenders}"


# ─── M4: auction feature service extraction ────────────────────────────────

_AUCTION_COLLECT_CALLS = frozenset({
    "collect_candidates_ex",
    "save_auction_asset",
    "replace_auction_snapshots",
    "get_auction",
    "batch_get_quotes",
    "tdx_trade_dates",
})
_AUCTION_RECOMPUTE_CALLS = frozenset({
    "list_auction_assets",
    "replace_auction_snapshots",
})
_AUCTION_SCORING_CALLS = frozenset({"evaluate", "rank_auctions", "verdict_to_payload"})
_AUCTION_ORCHESTRATION_CALLS = _AUCTION_COLLECT_CALLS | _AUCTION_SCORING_CALLS
_PROVIDER_FACTORY_CALLS = frozenset({"build_provider_bundle", "create_providers"})
_AUCTION_MODULES = ("service", "collect", "recompute", "scoring", "dates", "types")


def _repo_root() -> Path:
    return E2E_ROOT.parents[1]


def _auction_module(name: str) -> Path:
    return _repo_root() / "dragon_quant" / "auction" / f"{name}.py"


def _function_node(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path.name} 缺少函数 {name}")


def _top_level_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _calls_in(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _auction_imports(path: Path) -> set[str]:
    return {
        name for name in _module_imports(path)
        if name == "dragon_quant.auction" or name.startswith("dragon_quant.auction.")
    }


def test_auction_command_stays_thin_and_the_facade_has_no_orchestration():
    """M4: the command renders; ``service.py`` is a facade over split modules."""
    command = _repo_root() / "dragon_quant" / "commands" / "auction.py"
    service = _auction_module("service")

    lines = command.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 120, f"commands/auction.py 必须是薄 adapter，当前 {len(lines)} 行"

    command_calls = _called_names(command)
    assert {"run_auction", "run_auction_recompute"} <= command_calls, (
        "command must invoke the public auction service facade")
    offenders = sorted(command_calls & _AUCTION_ORCHESTRATION_CALLS)
    assert not offenders, f"command still orchestrates auction work: {offenders}"

    service_calls = _called_names(service)
    offenders = sorted(service_calls & _AUCTION_ORCHESTRATION_CALLS)
    assert not offenders, f"service facade still contains orchestration: {offenders}"
    defined = _top_level_functions(service)
    offenders = sorted(defined & {"run_auction", "run_auction_recompute"})
    assert not offenders, f"service facade must re-export, not implement: {offenders}"


def test_auction_service_modules_do_not_import_command_or_composition_root():
    """M4: every auction service module stays below commands/cli/app."""
    forbidden = {"dragon_quant.app", "dragon_quant.cli", "dragon_quant.commands"}
    for name in _AUCTION_MODULES:
        imports = _module_imports(_auction_module(name))
        offenders = sorted(
            item for item in imports
            if item in forbidden
            or any(item.startswith(f"{root}.") for root in forbidden))
        assert not offenders, f"auction/{name}.py imports an upper layer: {offenders}"


def test_auction_collect_resolves_missing_services_through_the_runtime_factory():
    """M4: direct/service-only collect calls still get a valid provider bundle."""
    collect = _auction_module("collect")
    resolver_calls = _calls_in(_function_node(collect, "_resolve_collect_services"))
    assert "build_provider_bundle" in resolver_calls, (
        "missing services must fall back to the runtime provider factory")
    assert "_resolve_collect_services" in _calls_in(_function_node(collect, "run_auction"))


def test_auction_recompute_constructs_no_providers():
    """M4: recompute stays offline (no provider construction, no transport)."""
    recompute = _auction_module("recompute")
    calls = _calls_in(_function_node(recompute, "run_auction_recompute"))
    offenders = sorted(calls & (
        _PROVIDER_FACTORY_CALLS
        | {"get_auction", "batch_get_quotes", "tdx_trade_dates"}
    ))
    assert not offenders, f"auction recompute is not offline: {offenders}"


def test_auction_modules_are_split_by_responsibility():
    """M4: collect/recompute/scoring/dates own distinct change reasons."""
    collect_calls = _called_names(_auction_module("collect"))
    assert _AUCTION_COLLECT_CALLS <= collect_calls, (
        f"collect.py is missing collection calls: {sorted(_AUCTION_COLLECT_CALLS - collect_calls)}")

    recompute_calls = _called_names(_auction_module("recompute"))
    assert _AUCTION_RECOMPUTE_CALLS <= recompute_calls, (
        f"recompute.py is missing recompute calls: {sorted(_AUCTION_RECOMPUTE_CALLS - recompute_calls)}")
    offenders = sorted(
        recompute_calls & (_AUCTION_COLLECT_CALLS - _AUCTION_RECOMPUTE_CALLS))
    assert not offenders, f"recompute.py still contains collection work: {offenders}"

    scoring_calls = _called_names(_auction_module("scoring"))
    assert _AUCTION_SCORING_CALLS <= scoring_calls, (
        f"scoring.py is missing scoring calls: {sorted(_AUCTION_SCORING_CALLS - scoring_calls)}")

    dates_calls = _called_names(_auction_module("dates"))
    assert "strptime" in dates_calls, "dates.py must own date parsing"


def test_auction_collect_and_recompute_share_one_scoring_implementation():
    """M4: scoring has a single implementation consumed by both use cases."""
    definitions = [
        path.name for path in sorted((_repo_root() / "dragon_quant" / "auction").glob("*.py"))
        if "_score_auction_assets" in _top_level_functions(path)
    ]
    assert definitions == ["scoring.py"], definitions

    for name in ("collect", "recompute"):
        imports = _module_imports(_auction_module(name))
        assert "dragon_quant.auction.scoring" in imports, (
            f"auction/{name}.py must consume the shared scoring module")


def test_auction_feature_modules_have_no_layering_cycles():
    """M4: service -> use cases -> scoring/dates/types; never backwards."""
    imports = {name: _auction_imports(_auction_module(name))
               for name in _AUCTION_MODULES}

    for name in ("collect", "recompute", "scoring", "dates", "types"):
        assert "dragon_quant.auction.service" not in imports[name], (
            f"auction/{name}.py must not import the facade back")

    assert "dragon_quant.auction.recompute" not in imports["collect"]
    assert "dragon_quant.auction.collect" not in imports["recompute"]
    for leaf in ("scoring", "dates", "types"):
        upward = imports[leaf] & {
            "dragon_quant.auction.collect",
            "dragon_quant.auction.recompute",
            "dragon_quant.auction.service",
        }
        assert not upward, f"auction/{leaf}.py imports an upper layer: {sorted(upward)}"

    assert {"dragon_quant.auction.collect",
            "dragon_quant.auction.recompute"} <= imports["service"], (
        "service.py must be the facade over both use cases")



# ─── M5A: prep package decomposition ───────────────────────────────────────

_PREP_DIR = _repo_root() / "dragon_quant" / "review_account" / "prep"
_PREP_MODULE = _repo_root() / "dragon_quant" / "review_account" / "prep.py"
_PREP_FILES = ("__init__", "service", "compute", "rebuild", "collectors")

# Names the old module exported and that operators/tests still patch or import.
_LEGACY_PATCH_TARGETS = frozenset({
    "PrepError", "run_prep", "PrepContext", "PrepPlan", "CollectorSpec",
    "ASSET_COLLECTORS", "TdxProvider", "THSProvider", "TencentProvider",
    "XueqiuProvider", "collect_macro", "YahooMacroSource", "assets_db",
    "dragons_mirror", "DragonsMirrorStale", "time", "SHANGHAI",
    "position_advice", "compute_sentiment", "compute_main_boards",
    "compute_candidates", "compute_macro", "_compute_all", "_lianban_count",
    "_collect_dragons", "_collect_all", "_collect_cninfo", "_collect_macro",
    "_collect_fund_flow", "_collect_market_breadth", "_breadth_suspicious",
    "_fund_flow_suspicious", "_stale_dates", "_read_dragon_day",
    "_validate_trade_date", "_window_degradations", "_run_collection",
    "plan_prep", "merge_report_entries", "asset_health",
    "resolve_window_dates",
})


def _prep_file(name: str) -> Path:
    return _PREP_DIR / f"{name}.py"


def _relative_modules(path: Path) -> set[str]:
    """Sibling modules a package file imports (``from .x import y`` → ``x``)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            modules.add(node.module)
    return modules


def _exported_names(path: Path) -> set[str]:
    """Top-level names a module binds (imports/aliases/assignments/defs/classes)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets
                         if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def test_prep_package_replaces_the_module_atomically():
    """M5A: one importable path only — prep.py and prep/ must never compete."""
    assert _PREP_DIR.is_dir(), "review_account/prep 必须是包"
    assert not _PREP_MODULE.exists(), "prep.py 与 prep/ 不得并存"
    for name in _PREP_FILES:
        assert _prep_file(name).is_file(), f"缺少 prep/{name}.py"


def test_prep_facade_exports_every_legacy_patch_target():
    """M5A: the facade keeps owning the names old patch targets mutate."""
    exported = _exported_names(_prep_file("__init__"))
    missing = sorted(_LEGACY_PATCH_TARGETS - exported)
    assert not missing, f"prep 门面缺少兼容导出: {missing}"


def test_prep_submodules_resolve_legacy_targets_at_runtime():
    """M5A/M5B: patched names must change behavior, not linger as inert aliases."""
    service = _prep_file("service")
    collectors = _prep_file("collectors")
    rebuild = _prep_file("rebuild")

    assert "legacy" in _exported_names(service) and "rebuild" in _relative_modules(service), (
        "service.py 必须经包门面运行期解析旧 patch 目标")
    assert "legacy" in _exported_names(collectors) and "rebuild" in _relative_modules(collectors), (
        "collectors.py 必须经包门面运行期解析旧 patch 目标")
    assert "legacy" in _exported_names(rebuild), (
        "rebuild.py 必须提供包门面的运行期解析入口")

    collect_all = _calls_in(_function_node(collectors, "_collect_all"))
    assert {"legacy", "_collectors"} <= collect_all, (
        "provider/registry 必须运行期解析，否则 patch 无效")
    offenders = sorted(collect_all & _PROVIDER_CONSTRUCTION_NAMES)
    assert not offenders, f"_collect_all 仍在静态构造 provider: {offenders}"

    run_prep = _calls_in(_function_node(service, "run_prep"))
    assert "legacy" in run_prep, "run_prep 必须运行期解析 provider/plan/collect 入口"

    for module, function in ((collectors, "_collect_dragons"),
                             (collectors, "_collect_macro"),
                             (rebuild, "rebuild_dragons_for_recompute")):
        calls = _calls_in(_function_node(module, function))
        assert "legacy" in calls, (
            f"{module.name}:{function} 必须运行期解析门面 patch 目标")


def test_prep_compute_and_rebuild_own_distinct_responsibilities():
    """M5A/M5B: compute is pure, rebuild owns the dragons window, collectors
    own collection/health/report, service orchestrates."""
    compute = _top_level_functions(_prep_file("compute"))
    assert {"compute_sentiment", "compute_main_boards", "compute_candidates",
            "compute_macro"} <= compute
    assert not compute & {"run_prep", "_collect_all", "plan_prep",
                          "rebuild_dragons_for_recompute"}
    imports = _module_imports(_prep_file("compute"))
    assert not imports & {"time", "dragon_quant.storage"}, (
        f"compute.py 必须保持纯计算（无 I/O）: {sorted(imports & {'time', 'dragon_quant.storage'})}")

    rebuild = _top_level_functions(_prep_file("rebuild"))
    assert {"rebuild_dragons_for_recompute", "build_dragons_payload",
            "_stale_dates", "_read_dragon_day"} <= rebuild
    assert not rebuild & {"run_prep", "compute_sentiment"}

    collectors = _top_level_functions(_prep_file("collectors"))
    assert {"_collect_dragons", "_collect_ths_limit_up_pool", "_collect_ths_block_top",
            "_collect_market_breadth", "_collect_fg_boards", "_collect_fund_flow",
            "_collect_cninfo", "_collect_macro", "_collect_all", "_classify_payload",
            "asset_health", "collection_health", "is_collectable", "plan_repairs",
            "merge_report_entries", "_prepare_assets",
            "_window_degradations"} <= collectors
    assert not collectors & {"run_prep", "plan_prep", "_run_collection",
                             "_compute_all", "resolve_window_dates",
                             "_validate_trade_date"}

    service = _top_level_functions(_prep_file("service"))
    assert {"run_prep", "plan_prep", "_run_collection",
            "_compute_all", "resolve_window_dates",
            "_validate_trade_date"} <= service
    assert not service & {"_collect_dragons", "_collect_all", "asset_health",
                          "_prepare_assets", "merge_report_entries"}


def test_prep_recompute_stays_offline_and_modules_have_no_layering_cycle():
    """M5A/M5B: compute → rebuild → collectors → service, never backwards.

    ``collectors`` owns collection and may consume compute/rebuild helpers, but
    must never import the service or the package root (import-time cycle).
    """
    graph = {name: _relative_modules(_prep_file(name)) for name in _PREP_FILES}

    assert graph["service"] == {"compute", "rebuild", "collectors"}
    assert graph["collectors"] == {"compute", "rebuild"}
    assert graph["rebuild"] == {"compute"}
    assert graph["compute"] == set()
    assert graph["__init__"] == {"compute", "rebuild", "collectors", "service"}

    assert "service" not in graph["collectors"], "collectors 不得反向依赖 service"
    assert not graph["rebuild"] & {"collectors", "service"}
    assert "collectors" not in graph["compute"]

    for name in _PREP_FILES:
        imports = _module_imports(_prep_file(name))
        offenders = sorted(
            item for item in imports if item == "dragon_quant.review_account.prep"
            or item.startswith("dragon_quant.review_account.prep."))
        assert not offenders, f"prep/{name}.py 静态导入包自身: {offenders}"

    recompute = _calls_in(_function_node(_prep_file("rebuild"),
                                         "rebuild_dragons_for_recompute"))
    offenders = sorted(recompute & (_PROVIDER_FACTORY_CALLS
                                    | {"get_auction", "batch_get_quotes"}))
    assert not offenders, f"recompute 不触网: {offenders}"


def test_prep_collectors_own_collection_with_a_single_definition_site():
    """M5B: every collector/health/report helper is defined once, in collectors."""
    package_files = sorted(_PREP_DIR.glob("*.py"))
    expected = {
        "_collect_dragons": "collectors.py",
        "_collect_ths_limit_up_pool": "collectors.py",
        "_collect_ths_block_top": "collectors.py",
        "_collect_market_breadth": "collectors.py",
        "_collect_fg_boards": "collectors.py",
        "_collect_fund_flow": "collectors.py",
        "_collect_cninfo": "collectors.py",
        "_collect_macro": "collectors.py",
        "_collect_all": "collectors.py",
        "_classify_payload": "collectors.py",
        "asset_health": "collectors.py",
        "collection_health": "collectors.py",
        "is_collectable": "collectors.py",
        "plan_repairs": "collectors.py",
        "merge_report_entries": "collectors.py",
        "_prepare_assets": "collectors.py",
        "run_prep": "service.py",
        "plan_prep": "service.py",
        "_run_collection": "service.py",
        "_compute_all": "service.py",
    }
    for name, filename in expected.items():
        sites = sorted(path.name for path in package_files
                       if name in _top_level_functions(path))
        assert sites == [filename], f"{name} 的定义位置应唯一: {sites}"


def test_prep_collectors_do_not_import_service_or_package_root():
    """M5B: collectors sit below the service; no import-time cycle is allowed."""
    collectors = _prep_file("collectors")
    relatives = _relative_modules(collectors)
    assert "service" not in relatives, "collectors 不得导入 service"
    assert "__init__" not in relatives, "collectors 不得导入包门面"

    imports = _module_imports(collectors)
    offenders = sorted(
        item for item in imports if item == "dragon_quant.review_account.prep"
        or item.startswith("dragon_quant.review_account.prep."))
    assert not offenders, f"collectors.py 不得静态导入包自身: {offenders}"

    assert {"compute", "rebuild"} <= relatives
    for function in ("_collect_all", "_collect_dragons", "_collect_macro"):
        calls = _calls_in(_function_node(collectors, function))
        assert "legacy" in calls, (
            f"collectors:{function} 必须运行期解析旧 patch 目标")


# ─── M6: assets CLI service extraction ─────────────────────────────────────

_ASSETS_COMMAND = _repo_root() / "dragon_quant" / "commands" / "assets.py"
_ASSETS_CLI_SERVICE = _repo_root() / "dragon_quant" / "assets_sync" / "cli_service.py"

# Backend/source/path assembly and sync orchestration belong to cli_service.
_ASSETS_ORCHESTRATION_CALLS = frozenset({
    "S3Backend",
    "LocalDirBackend",
    "load_config",
    "build_sources",
    "default_paths",
    "run_doctor",
    "backfill_dragons",
    "push",
    "pull",
    "status",
    "verify",
})


def test_m6_assets_command_is_a_thin_adapter():
    """M6: the command only maps arguments, renders output and exits."""
    assert _ASSETS_COMMAND.is_file()
    calls = _called_names(_ASSETS_COMMAND)
    offenders = sorted(calls & _ASSETS_ORCHESTRATION_CALLS)
    assert not offenders, f"commands/assets.py still orchestrates assets work: {offenders}"
    assert "run" in calls, "the command must call the cli_service entry point"

    imported = _imported_names(_ASSETS_COMMAND)
    assert "dragon_quant.assets_sync.cli_service" in imported, (
        "the command must consume the public cli_service")
    assembly_imports = sorted(
        name for name in imported
        if name.startswith((
            "dragon_quant.assets_sync.backends",
            "dragon_quant.assets_sync.credentials",
            "dragon_quant.assets_sync.doctor",
            "dragon_quant.assets_sync.sources",
            "dragon_quant.assets_sync.state",
        ))
    )
    assert not assembly_imports, (
        f"commands/assets.py must not import assemblers: {assembly_imports}")


def test_m6_assets_cli_service_owns_assembly_below_the_command_layer():
    """M6: cli_service assembles backends/sources/paths and owns no sync algorithm."""
    assert _ASSETS_CLI_SERVICE.is_file()
    imported = _imported_names(_ASSETS_CLI_SERVICE)
    forbidden = {
        "dragon_quant.app",
        "dragon_quant.cli",
        "dragon_quant.commands",
    }
    offenders = sorted(
        name for name in imported
        if name in forbidden or any(name.startswith(f"{root}.") for root in forbidden))
    assert not offenders, f"assets_sync/cli_service.py imports an upper layer: {offenders}"

    defined = _top_level_functions(_ASSETS_CLI_SERVICE)
    assert {"run", "parse_asset_date", "_build_backend"} <= defined
    offenders = sorted(
        defined & {"push", "pull", "status", "verify", "run_doctor",
                   "backfill_dragons"})
    assert not offenders, f"cli_service must not re-implement sync work: {offenders}"

    for module in (
        "dragon_quant.assets_sync.backends.s3",
        "dragon_quant.assets_sync.credentials",
        "dragon_quant.assets_sync.doctor",
        "dragon_quant.assets_sync.service",
        "dragon_quant.assets_sync.sources",
        "dragon_quant.assets_sync.state",
        "dragon_quant.storage.dragons_mirror",
    ):
        assert module in imported, f"cli_service must consume {module}"


def test_m6_backend_injection_is_a_run_level_seam():
    """M6: one injected backend covers every remote action; backfill stays local."""
    run_node = _function_node(_ASSETS_CLI_SERVICE, "run")
    keyword_only = {arg.arg for arg in run_node.args.kwonlyargs}
    assert "backend" in keyword_only, "run() must accept an injected backend"

    build_calls = _calls_in(_function_node(_ASSETS_CLI_SERVICE, "_build_backend"))
    assert {"S3Backend", "load_config"} <= build_calls, (
        "the default backend must be S3Backend built from loaded credentials")

    sync_calls = _calls_in(_function_node(_ASSETS_CLI_SERVICE, "_run_sync"))
    assert {"push", "pull", "status", "verify"} <= sync_calls, (
        "every remote action must dispatch to the existing sync service")
    doctor_calls = _calls_in(_function_node(_ASSETS_CLI_SERVICE, "_run_doctor"))
    assert "run_doctor" in doctor_calls, "doctor must use the injected/default backend"
    backfill_calls = _calls_in(_function_node(_ASSETS_CLI_SERVICE, "_run_backfill"))
    assert "backfill_dragons" in backfill_calls
    offenders = sorted(backfill_calls & {"S3Backend", "load_config", "_build_backend"})
    assert not offenders, f"backfill-dragons must stay local: {offenders}"


# ─── M7: factors public facade narrowing and dependency decycling ──────────

_FACTOR_PACKAGE = "dragon_quant.factors"
_FACTOR_INIT = _repo_root() / "dragon_quant" / "factors" / "__init__.py"

# 用户批准的 13 个公共出口（顺序即 __all__ 顺序）。
_M7_PUBLIC_EXPORTS = [
    "build",
    "list_factors",
    "query",
    "status",
    "export_matrix",
    "evaluate_factor",
    "fin_sync",
    "fin_calibrate",
    "fin_status",
    "parse_only",
    "FactorModel",
    "annotate_pool",
    "CleanConfig",
]

# 命令层只允许 ``from dragon_quant.factors import CleanConfig, service``：
# 门面取 CleanConfig，CLI 用例包装经文档化公共子模块 factors.service。
_M7_COMMAND_ALLOWED_FACTOR_IMPORTS = frozenset({_FACTOR_PACKAGE})
_M7_COMMAND_FACADE_NAMES = frozenset({"CleanConfig", "service"})

_M7_COMMAND_SERVICE_CALLS = frozenset({
    "build", "list_factors", "query", "status", "export_matrix",
    "evaluate_factor", "fin_sync", "fin_calibrate", "fin_status", "parse_only",
})


def _package_modules(package_name: str) -> dict[str, Path]:
    """dotted module name → source path for one package (root + subpackages)."""
    base = _repo_root() / Path(*package_name.split("."))
    modules: dict[str, Path] = {}
    for path in sorted(base.rglob("*.py")):
        if path.name == "__init__.py":
            relative = path.parent.relative_to(_repo_root())
        else:
            relative = path.relative_to(_repo_root()).with_suffix("")
        modules[".".join(relative.parts)] = path
    return modules


def _module_scope_statements(body: list[ast.stmt]):
    """Module-scope statements, descending into If/Try/With but never functions."""
    for node in body:
        yield node
        if isinstance(node, ast.If):
            yield from _module_scope_statements(node.body)
            yield from _module_scope_statements(node.orelse)
        elif isinstance(node, ast.Try):
            yield from _module_scope_statements(node.body)
            for handler in node.handlers:
                yield from _module_scope_statements(handler.body)
            yield from _module_scope_statements(node.orelse)
            yield from _module_scope_statements(node.finalbody)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            yield from _module_scope_statements(node.body)


def _module_level_edges(path: Path, package_name: str, modules: set[str]) -> set[str]:
    """Module-level import edges inside ``package_name`` only.

    Function-local (lazy) imports are intentionally excluded: the approved
    design keeps them to break runtime cycles.  ``from . import x`` is a
    sibling edge to ``package.x``, not an edge back to the package root.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    edges: set[str] = set()
    for node in _module_scope_statements(tree.body):
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


def _package_cycle(package_name: str) -> list[str] | None:
    modules = _package_modules(package_name)
    graph = {
        # A module importing names from its own package (e.g.
        # ``builtin/__init__`` re-exporting ``builtin.auction``) is a self
        # edge, not a cycle.  Self edges contribute nothing to reachability.
        name: _module_level_edges(path, package_name, set(modules)) - {name}
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


def test_m7_factors_facade_exports_exactly_thirteen_names():
    """M7: the package root is a lazy facade, not an eager re-export dump."""
    tree = ast.parse(_FACTOR_INIT.read_text(encoding="utf-8"), filename=str(_FACTOR_INIT))
    exported = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets):
            exported = ast.literal_eval(node.value)
            break
    assert exported == _M7_PUBLIC_EXPORTS, exported

    eager = sorted(
        name for name in _module_imports(_FACTOR_INIT)
        if name == "dragon_quant" or name.startswith("dragon_quant.")
    )
    assert not eager, f"factors/__init__.py 必须零 eager 应用 import: {eager}"


def test_m7_factors_submodules_never_import_the_package_root():
    """M7: submodules use direct sibling imports; no edge back to the root."""
    package = _repo_root() / "dragon_quant" / "factors"
    offenders: dict[str, list[int]] = {}
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits: list[int] = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom) and node.level == 0
                    and node.module == _FACTOR_PACKAGE):
                hits.append(node.lineno)
            elif isinstance(node, ast.Import) and any(
                    alias.name == _FACTOR_PACKAGE for alias in node.names):
                hits.append(node.lineno)
        if hits:
            offenders[path.relative_to(_repo_root()).as_posix()] = hits
    assert not offenders, f"factors 子模块静态回包根: {offenders}"


def test_m7_factor_package_has_no_module_level_import_cycles():
    """M7: module-level import graph of the factors package is acyclic."""
    cycle = _package_cycle(_FACTOR_PACKAGE)
    assert cycle is None, "factors 包级 import 环: " + " → ".join(cycle or [])


def test_m7_scorers_and_auction_package_roots_have_no_module_level_cycles():
    """M7 final gate: the sibling packages with the same root-import pattern."""
    for package in ("dragon_quant.scorers", "dragon_quant.auction"):
        cycle = _package_cycle(package)
        assert cycle is None, f"{package} 包级 import 环: " + " → ".join(cycle or [])


def test_m7_command_uses_only_the_factor_facade():
    """M7: commands/factor.py enters factors via the facade + public service module."""
    command = _repo_root() / "dragon_quant" / "commands" / "factor.py"
    tree = ast.parse(command.read_text(encoding="utf-8"), filename=str(command))
    factor_imports: set[str] = set()
    facade_names: set[str] = set()
    service_calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == _FACTOR_PACKAGE or node.module.startswith(_FACTOR_PACKAGE + "."):
                factor_imports.add(node.module)
                if node.module == _FACTOR_PACKAGE:
                    facade_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _FACTOR_PACKAGE or alias.name.startswith(_FACTOR_PACKAGE + "."):
                    factor_imports.add(alias.name)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name)
              and node.func.value.id == "service"):
            service_calls.add(node.func.attr)

    assert factor_imports == _M7_COMMAND_ALLOWED_FACTOR_IMPORTS, (
        "commands/factor.py 只能经公共门面（+ documented service 子模块名）进入 factors: "
        f"{sorted(factor_imports)}")
    assert facade_names == _M7_COMMAND_FACADE_NAMES, (
        "包根只取公共名 CleanConfig；CLI 用例包装必须经 factors.service: "
        f"{sorted(facade_names)}")
    missing = sorted(_M7_COMMAND_SERVICE_CALLS - service_calls)
    assert not missing, f"commands/factor.py 缺少 service 用例包装调用: {missing}"

# ─── M10B: cookie library / independent CLI boundary ──────────────────────

_COOKIE_LIBRARY = _repo_root() / "dragon_quant" / "providers" / "cookie.py"
_COOKIE_CLI = _repo_root() / "dragon_quant" / "providers" / "cookie_cli.py"


def test_m10b_cookie_cli_owns_the_single_entry_implementation():
    """M10B: ``cookie_cli`` is the only CLI orchestration site.

    ``cookie.py`` keeps the library (read/write/fetch); the independent entry's
    argparse and status/set/fetch dispatch live in exactly one module.
    """
    assert _COOKIE_CLI.is_file(), "dragon_quant/providers/cookie_cli.py 必须存在"

    defined = _top_level_functions(_COOKIE_CLI)
    assert {"main", "build_parser"} <= defined, (
        f"cookie_cli.py 缺少 CLI 入口: {sorted({'main', 'build_parser'} - defined)}")

    main_node = _function_node(_COOKIE_CLI, "main")
    keyword_only = {arg.arg for arg in main_node.args.kwonlyargs}
    assert "fetch" in keyword_only, "main() 必须以 keyword-only 参数注入 fetch"
    positional = [arg.arg for arg in main_node.args.args]
    assert positional[:1] == ["argv"], f"main() 首个位置参数应为 argv: {positional}"

    providers = _repo_root() / "dragon_quant" / "providers"
    sites = [
        path.name for path in sorted(providers.glob("cookie*.py"))
        if "main" in _top_level_functions(path)
    ]
    assert sites == ["cookie_cli.py"], f"cookie CLI 编排实现必须唯一: {sites}"

    imports = _module_imports(_COOKIE_CLI)
    upper_layers = {"dragon_quant.app", "dragon_quant.cli",
                    "dragon_quant.commands"}
    offenders = sorted(
        name for name in imports
        if name in upper_layers
        or any(name.startswith(f"{root}.") for root in upper_layers))
    assert not offenders, f"cookie_cli.py 不得依赖上层入口: {offenders}"


def test_m10b_cookie_module_main_delegates_to_cookie_cli():
    """M10B: the library module's ``__main__`` is a thin delegate."""
    tree = ast.parse(_COOKIE_LIBRARY.read_text(encoding="utf-8"))
    guards = [
        node for node in tree.body
        if isinstance(node, ast.If)
        and any(
            isinstance(part, ast.Name) and part.id == "__name__"
            for part in ast.walk(node.test))
    ]
    assert len(guards) == 1, "cookie.py 必须只有一个 __main__ 块"

    block = guards[0]
    calls = _calls_in(block)
    assert "main" in calls, "cookie.py 的 __main__ 必须委托 cookie_cli.main"
    assert not calls & {"ArgumentParser", "parse_args", "add_argument"}, (
        "cookie.py 不得保留第二份 status/set/fetch 编排")

    delegated = {
        (node.module, alias.name)
        for node in ast.walk(block)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert ("cookie_cli", "main") in delegated or (
        "dragon_quant.providers.cookie_cli", "main") in delegated, delegated

    library = _top_level_functions(_COOKIE_LIBRARY)
    assert {"get_xq", "set_xq", "fetch_xq"} <= library, (
        f"cookie.py 必须保留库函数: {sorted({'get_xq', 'set_xq', 'fetch_xq'} - library)}")
