"""Documentation/architecture consistency gates for the M12 final refactor.

These tests read repository files only (no application imports, no network, no
database).  They pin the claims that ``AGENTS.md`` / ``README.md`` /
``pyproject.toml`` make about the post-refactor structure against the real
repository, so documents cannot silently drift from code again.

The parser surface is taken from the reviewed E2E snapshot
(``tests/e2e/coverage-map.json``, 51 nodes / 15 top-level commands).  Every node
must be reachable from the README command reference.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS = REPO_ROOT / "AGENTS.md"
README = REPO_ROOT / "README.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
COVERAGE_MAP = REPO_ROOT / "tests" / "e2e" / "coverage-map.json"

TREE_LINE = re.compile(r"^(?P<prefix>[│ ]*)(?:├──|└──)\s+(?P<body>\S.*)$")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tree_block(path: Path) -> str:
    """Return the first fenced block that starts with the package root."""
    text = _read(path)
    for block in re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.S):
        if "dragon_quant/" in block.splitlines()[0:3]:
            return block
    raise AssertionError(f"no dragon_quant tree block in {path}")


def _tree_paths(path: Path) -> list[Path]:
    """Resolve every tree entry to a repository path."""
    resolved: list[Path] = []
    stack: dict[int, str] = {}
    malformed: list[str] = []
    for line in _tree_block(path).splitlines():
        match = TREE_LINE.match(line)
        if not match:
            continue
        prefix = match.group("prefix")
        if len(prefix) % 4:
            malformed.append(line)
            continue
        depth = len(prefix) // 4
        body = match.group("body").split("#", 1)[0].strip()
        for token in body.split(" / "):
            token = token.strip()
            if not token or any(char in token for char in "<>*"):
                continue
            is_dir = token.endswith("/")
            name = token.rstrip("/")
            parts = ["dragon_quant"]
            parts.extend(stack[index] for index in range(depth) if index in stack)
            parts.append(name)
            resolved.append(Path(*parts))
            if is_dir:
                stack[depth] = name
                for stale in [key for key in stack if key > depth]:
                    del stack[stale]
    assert not malformed, f"{path.name} has malformed tree indentation: {malformed}"
    return resolved


def _parser_nodes() -> list[str]:
    payload = json.loads(_read(COVERAGE_MAP))
    snapshot = payload["parser_snapshot"]
    assert snapshot["node_count"] == 51
    assert snapshot["top_level_count"] == 15
    return [node["command"] for node in snapshot["nodes"]]


def test_documented_tree_paths_exist():
    for document in (AGENTS, README):
        missing = [str(path) for path in _tree_paths(document) if not (REPO_ROOT / path).exists()]
        assert not missing, f"{document.name} documents nonexistent paths: {missing}"


def test_pyproject_description_is_five_dimension():
    # Keep this parseable on Python 3.10 (tomllib is 3.11+); only one field is
    # needed, so a targeted regex avoids a new dependency.
    match = re.search(r'^description\s*=\s*"([^"]+)"', _read(PYPROJECT), flags=re.M)
    assert match, "pyproject [project].description must be a literal string"
    description = match.group(1)
    assert "五维" in description
    assert "四维" not in description


def test_documented_snapshot_version_matches_production():
    production = _read(REPO_ROOT / "dragon_quant" / "review_account" / "prep" / "service.py")
    match = re.search(r'"version":\s*"([0-9.]+)"', production)
    assert match, "prep service must declare its snapshot version"
    version = match.group(1)
    assert version == "0.6"
    for document in (AGENTS, README):
        text = _read(document)
        assert f"version={version}" in text, f"{document.name} must document snapshot version={version}"
        assert "version=0.5" not in text, f"{document.name} still documents the retired 0.5 snapshot"


def test_documented_fallback_window_is_five_days():
    readme = _read(README)
    assert "前 5 日真龙并集" in readme
    assert "前 3 日真龙并集" not in readme
    assert "candidate_lookback_days`（默认 5）" in readme
    agents = _read(AGENTS)
    assert "之前 **5** 个真实交易日的**全部真龙**并集" in agents


def test_every_parser_node_has_a_readme_command():
    readme = _read(README)
    missing: list[str] = []
    for node in _parser_nodes():
        parts = node.split()
        top = re.escape(parts[0])
        if len(parts) == 1:
            pattern = rf"dragon-quant\s+{top}\b"
        else:
            leaf = re.escape(parts[-1])
            pattern = rf"dragon-quant\s+{top}\b[^\n]*\b{leaf}\b"
        if not re.search(pattern, readme):
            missing.append(node)
    assert not missing, "README command reference is missing parser nodes: " + ", ".join(missing)


def test_current_navigation_has_no_removed_modules():
    # Historical notes may mention what was removed; current navigation must not
    # present removed modules as live paths.
    for document in (AGENTS, README):
        navigation = _tree_block(document)
        assert "cli_commands" not in navigation, (
            f"{document.name} navigation tree still lists the removed cli_commands module"
        )
        assert "review_account/prep.py" not in navigation, (
            f"{document.name} navigation tree still lists the removed review_account/prep.py module"
        )
        text = _read(document)
        assert "dragon_quant/cli_commands" not in text
        assert "dragon_quant/review_account/prep.py" not in text


def test_new_architecture_packages_are_documented():
    documented = {str(path) for path in _tree_paths(AGENTS)}
    required = [
        "dragon_quant/app.py",
        "dragon_quant/commands",
        "dragon_quant/runtime/clock.py",
        "dragon_quant/runtime/providers.py",
        "dragon_quant/auction/service.py",
        "dragon_quant/auction/collect.py",
        "dragon_quant/auction/recompute.py",
        "dragon_quant/auction/scoring.py",
        "dragon_quant/auction/dates.py",
        "dragon_quant/review_account/prep",
        "dragon_quant/assets_sync/cli_service.py",
        "dragon_quant/providers/cookie_cli.py",
    ]
    missing = [name for name in required if name not in documented]
    assert not missing, f"AGENTS.md architecture overview misses: {missing}"


def test_no_argument_behavior_is_documented_truthfully():
    for document in (AGENTS, README):
        text = _read(document)
        assert "不带子命令" in text, f"{document.name} must document the actual no-argument behavior"
        assert "打印帮助" in text, f"{document.name} must say that no arguments prints help"
        # The old "no args == scan" claim is not what the parser does.
        assert "无参数默认 scan" not in text


def test_scan_date_and_extended_commands_are_documented():
    readme = _read(README)
    for snippet in (
        "dragon-quant scan --date",
        "dragon-quant data cookie-fetch",
        "dragon-quant data cookie-set",
        "dragon-quant logs list",
        "dragon-quant storage clear --cache",
    ):
        assert snippet in readme, f"README must document {snippet}"
