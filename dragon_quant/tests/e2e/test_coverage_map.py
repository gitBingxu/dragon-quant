"""Machine-checkable coverage audit for the M1 offline E2E baseline.

This module never launches a business process.  It reads ``coverage-map.json``
and the E2E test sources, then proves:

* every parser node from the stage-1 snapshot (54 nodes / 16 top-level
  commands) maps to at least one *existing* test function;
* every test function under ``dragon_quant/tests/e2e`` is referenced by the map (no
  silently unmapped cases);
* hidden aliases, invocation facts (including the no-argument actual help),
  group-without-action behavior and the web/npm independent entries are
  explicitly mapped;
* the only remaining skip in the E2E suite is the declared filesystem-capability
  condition, never an unexplained placeholder.

The map also documents the merged R9-R11/RA7 Web mappings, the npm N4/N5
lifecycle merge and the AS16 push/pull shared-lock rationale.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

E2E_ROOT = Path(__file__).resolve().parent
REPO_ROOT = E2E_ROOT.parents[2]
MAP_PATH = E2E_ROOT / "coverage-map.json"


def _load_map() -> dict:
    payload = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    return payload


def _iter_test_functions() -> dict[str, ast.AST]:
    functions: dict[str, ast.AST] = {}
    for path in sorted(E2E_ROOT.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                functions[f"{relative}::{node.name}"] = node
    return functions


def _collect_refs(value: object, into: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "tests":
                assert isinstance(child, list)
                into.extend(str(item) for item in child)
            else:
                _collect_refs(child, into)
    elif isinstance(value, list):
        for child in value:
            _collect_refs(child, into)


def _normalize_ref(reference: str) -> str:
    return re.sub(r"\[.*\]$", "", reference)


def _covered_nodes(coverage: dict) -> dict[str, dict]:
    assert isinstance(coverage["nodes"], dict)
    return coverage["nodes"]


def test_parser_snapshot_has_54_nodes_and_16_top_level_commands():
    snapshot = _load_map()["parser_snapshot"]
    nodes = snapshot["nodes"]

    assert snapshot["node_count"] == len(nodes) == 54
    assert snapshot["top_level_count"] == sum(1 for node in nodes if " " not in node["command"]) == 16
    assert len({node["command"] for node in nodes}) == 54
    for node in nodes:
        assert node["command"].strip() == node["command"]
        assert node["flags"], f"{node['command']} must list its parser flags"

    # ``source``/``sha256`` are provenance only.  The gate is hermetic on purpose:
    # the stage-1 dump lives outside the repo and may be re-exported by M3, so a
    # live comparison here would make the M1 gate depend on an external artifact.
    assert snapshot["source"].endswith("parser_dump.json")
    assert re.fullmatch(r"[0-9a-f]{64}", snapshot["sha256"])


def test_every_parser_node_maps_to_existing_real_tests():
    payload = _load_map()
    coverage = payload["coverage"]
    snapshot_commands = {node["command"] for node in payload["parser_snapshot"]["nodes"]}
    mapped = _covered_nodes(coverage)

    assert set(mapped) == snapshot_commands, (
        f"unmapped parser nodes: {sorted(snapshot_commands - set(mapped))}; "
        f"unknown nodes: {sorted(set(mapped) - snapshot_commands)}"
    )

    real_tests = _iter_test_functions()
    missing: list[str] = []
    for node, entry in mapped.items():
        assert entry["tests"], f"parser node {node!r} has no test mapping"
        for reference in entry["tests"]:
            normalized = _normalize_ref(reference)
            if normalized not in real_tests:
                missing.append(f"{node} -> {reference}")
    assert not missing, "coverage map references unknown tests:\n" + "\n".join(missing)


def test_every_e2e_test_function_is_mapped():
    payload = _load_map()
    references: list[str] = []
    _collect_refs(payload["coverage"], references)
    mapped = {_normalize_ref(reference) for reference in references}
    real_tests = set(_iter_test_functions())

    unmapped = sorted(real_tests - mapped)
    assert not unmapped, "tests without a coverage-map entry:\n" + "\n".join(unmapped)
    assert not (mapped - real_tests), "coverage map references unknown tests:\n" + "\n".join(
        sorted(mapped - real_tests)
    )


def test_hidden_alias_invocation_facts_and_independent_entries_are_present():
    coverage = _load_map()["coverage"]

    assert set(coverage["hidden_aliases"]) == {"scan_v2"}
    assert "no_argument_actual_help" in coverage["invocation_facts"]
    assert "group_without_action" in coverage["invocation_facts"]
    assert set(coverage["independent_entries"]) == {"web", "npm"}

    for section in ("hidden_aliases", "invocation_facts", "independent_entries"):
        for name, entry in coverage[section].items():
            assert entry["tests"], f"{section}.{name} has no test mapping"


def test_only_declared_conditional_skips_remain():
    payload = _load_map()
    declared = {
        (item["file"], item["function"])
        for item in payload["conditional_skips"]
    }
    actual = _skip_sites()

    assert actual == declared, (
        "E2E skip placeholders changed without an explicit coverage-map reason.\n"
        f"undeclared: {sorted(actual - declared)}\n"
        f"stale: {sorted(declared - actual)}"
    )


def test_coverage_map_has_no_unknown_or_duplicate_nodes():
    payload = _load_map()
    coverage = payload["coverage"]
    nodes = coverage["nodes"]

    snapshot_commands = [node["command"] for node in payload["parser_snapshot"]["nodes"]]
    assert len(snapshot_commands) == len(set(snapshot_commands))
    assert set(coverage) == {
        "nodes",
        "hidden_aliases",
        "invocation_facts",
        "independent_entries",
        "cross_cutting",
    }
    assert payload["known_gaps"] == [], "M1F2 must not leave unexplained coverage gaps"

    # Every top-level command itself is a mapped node, not only its subcommands.
    top_level = {command for command in snapshot_commands if " " not in command}
    assert top_level == {key for key in nodes if " " not in key}


def test_lock_packaging_and_ui_cross_cutting_notes_are_explicit():
    cross = _load_map()["coverage"]["cross_cutting"]

    assert "r9_r11_review_ui" in cross
    assert "ra7_review_account_ui" in cross
    assert "npm_packaging_lifecycle" in cross
    assert "assets_sync_lock" in cross
    assert "lock" in cross["assets_sync_lock"]["notes"].lower()
    assert "push" in cross["assets_sync_lock"]["notes"].lower()
    assert "pull" in cross["assets_sync_lock"]["notes"].lower()
    assert "N4" in cross["npm_packaging_lifecycle"]["notes"]
    assert "N5" in cross["npm_packaging_lifecycle"]["notes"]

    for name in ("r9_r11_review_ui", "ra7_review_account_ui", "npm_packaging_lifecycle", "assets_sync_lock"):
        assert cross[name]["tests"], f"cross_cutting.{name} has no test mapping"


# ─── skip-site scanner (AST, no application import) ────────────────────────


def _skip_sites() -> set[tuple[str, str | None]]:
    sites: set[tuple[str, str | None]] = set()
    for path in sorted(E2E_ROOT.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(REPO_ROOT).as_posix()
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_skip_call(node.func):
                sites.add((relative, _enclosing_function(node, parents)))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if _is_skip_decorator(decorator):
                        sites.add((relative, node.name))
    return sites


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return None


def _attribute_chain(node: ast.AST) -> list[str]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return list(reversed(parts))


def _is_skip_call(func: ast.AST) -> bool:
    if isinstance(func, ast.Attribute):
        chain = _attribute_chain(func)
        if chain in (["pytest", "skip"], ["pytest", "importorskip"], ["unittest", "skip"]):
            return True
        if chain and chain[-1] in ("skipTest", "skip"):
            return True
    return False


def _is_skip_decorator(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    chain = _attribute_chain(node.func)
    return chain[:2] == ["pytest", "mark"] and chain[-1] in ("skip", "skipif")
