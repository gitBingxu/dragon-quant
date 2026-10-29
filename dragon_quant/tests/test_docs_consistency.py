"""Documentation/architecture consistency gates (root constitution + module docs).

These tests read repository files only (no application imports, no network, no
database).  They pin the claims that ``AGENTS.md`` (the root constitution),
``docs/agent/*.md`` (module docs), ``README.md`` and ``pyproject.toml`` make about
the repository against the real repository, so documents cannot silently drift
from code again.

Layout contract (``archived_md/agents/2026-10-27-agents-progressive-disclosure-design.md``):

* the root ``AGENTS.md`` is the constitution (hard constraints, module routing,
  cross-module gates) and stays under 20 000 bytes / 240 lines;
* the §4 workflow body is single-owned by the parent ``AGENTS.md`` outside this
  repository; the root may only keep a one-line pointer and must never copy the
  body back in (the tests assert the non-duplication and never read paths
  outside the repository);
* every module detail lives in exactly one ``docs/agent/<module>.md`` owner and
  is reachable from the single root index;
* the tests below were *moved* from "read AGENTS.md only" to "read the whole doc
  set", never weakened, plus new size / link / ownership gates.

The parser surface is taken from the reviewed E2E snapshot
(``dragon_quant/tests/e2e/coverage-map.json``, 52 nodes / 15 top-level commands).  Every node
must be reachable from the README command reference.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS = REPO_ROOT / "AGENTS.md"
README = REPO_ROOT / "README.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
COVERAGE_MAP = REPO_ROOT / "dragon_quant" / "tests" / "e2e" / "coverage-map.json"

DOCS_DIR = REPO_ROOT / "docs" / "agent"
ARCHITECTURE = DOCS_DIR / "architecture.md"
STRATEGY = REPO_ROOT / "dragon_quant" / "review_account" / "strategy" / "STRATEGY.md"
AGENT_DOCS = sorted(DOCS_DIR.glob("*.md"))

# AC1 uses the decimal reading of "20 KB": 20 * 1024 would let a 20.4 KB file pass.
ROOT_MAX_BYTES = 20_000
ROOT_MAX_LINES = 240

EXPECTED_MODULE_DOCS = {
    "architecture.md",
    "scan.md",
    "review.md",
    "trading.md",
    "prep-auction.md",
    "assets.md",
    "factor.md",
    "development.md",
}

# token -> owner module doc.  Each token is module-owned and must NOT reappear in
# the root (that would mean the rule flowed back into the constitution).
# Cross-module hard constraints (lazy storage init, zero ``import pandas``, ...)
# stay in the root on purpose and therefore must never be listed here.
CRITICAL_TOKEN_OWNERS = {
    "summarize_review_rows": "review.md",
    "version=0.7": "prep-auction.md",
    "collection_report": "prep-auction.md",
    "factor_ranking_enabled": "factor.md",
    "idx_buy_sell_open": "trading.md",
    "pull --force": "assets.md",
    "dragon_review_strict": "review.md",
}

TREE_LINE = re.compile(r"^(?P<prefix>[│ ]*)(?:├──|└──)\s+(?P<body>\S.*)$")
LINK_TARGET = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", flags=re.S)

# Root must not resurrect the content classes that were moved out (AC4).
ROOT_FORBIDDEN_TOKENS = (
    "10jqka.com.cn",
    "gtimg",
    "Chrome UA",
    "## 反爬要点",
    "## 当前状态",
    "### ✅ 已完成",
    "### 📝 已知修复",
    "| 表 | 用途",
)

# §4 workflow body is single-owned by the parent AGENTS.md (2026-10-27 user
# decision): the project root keeps a one-line pointer and never the body.
WORKFLOW_BODY_ANCHORS = (
    "阶段 0：需求澄清与验收标准",
    "阶段 1：技术调研",
    "阶段 2：技术方案",
    "阶段 3：代码实现",
    "阶段 3.5：轻量终审",
    "main 阶段验收规则",
    "自审与收敛规则",
    "禁止派生 subagent",
    "节点报告（精简）",
)
WORKFLOW_POINTER = "父级 `AGENTS.md` §4"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _agents_documents() -> dict[Path, str]:
    """Root constitution plus every module doc, as ``{path: text}``."""
    documents = {AGENTS: _read(AGENTS)}
    documents.update({doc: _read(doc) for doc in AGENT_DOCS})
    return documents


def _docset_text() -> str:
    """Concatenated text of root + module docs, for cross-document assertions."""
    return "\n".join(_agents_documents().values())


def _tree_block(path: Path) -> str:
    """Return the first fenced block whose first line is the package root.

    Tightened on purpose: several module docs contain bash/Python blocks, and a
    loose ``"dragon_quant/" in block`` probe could mistake them for the tree.
    """
    for block in FENCED_BLOCK.findall(_read(path)):
        lines = block.splitlines()
        if lines and lines[0].strip().startswith("dragon_quant/"):
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


def _link_targets(text: str) -> list[str]:
    """Every markdown link target, stripped of fragments/whitespace."""
    targets: list[str] = []
    for raw in LINK_TARGET.findall(text):
        target = raw.strip().split("#", 1)[0].strip()
        if target:
            targets.append(target)
    return targets


def _root_index_links() -> list[str]:
    """Module-doc links declared by the root index table rows.

    Only table rows of the index count (each row = one module doc).  Body prose is
    allowed to link the same doc again as a convenience without breaking the
    "one row per module doc" contract.
    """
    links: list[str] = []
    for line in _read(AGENTS).splitlines():
        row = line.strip()
        if not row.startswith("| ["):
            continue
        links.extend(target for target in _link_targets(row) if target.startswith("docs/agent/"))
    return links


def _local_markdown_links(path: Path) -> list[str]:
    """Local ``.md`` link targets of ``path`` (no http/https/mailto, no anchors)."""
    local: list[str] = []
    for target in _link_targets(_read(path)):
        if target.startswith(("http://", "https://", "mailto:", "tel:")):
            continue
        if not target.endswith(".md"):
            continue
        local.append(target)
    return local


def _parser_nodes() -> list[str]:
    payload = json.loads(_read(COVERAGE_MAP))
    snapshot = payload["parser_snapshot"]
    assert snapshot["node_count"] == 52
    assert snapshot["top_level_count"] == 15
    return [node["command"] for node in snapshot["nodes"]]


def test_documented_tree_paths_exist():
    # 测试树迁入 dragon_quant/tests 后，仓库根锚点必须仍指向项目根。
    assert (REPO_ROOT / "pyproject.toml").is_file()
    assert (REPO_ROOT / "dragon_quant").is_dir()
    for document in (ARCHITECTURE, README):
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


def test_prep_auction_documents_review_gates_and_context():
    """M4: owner doc must describe gates/eligibility/position/context/CLI segment."""

    prep_doc = _read(DOCS_DIR / "prep-auction.md")
    for token in ("eligibility", "position_pct", "position_basis", "gate_trace",
                  "strategy_context", "sum_exceeds_100", "frozen_at",
                  "不建议新建仓"):
        assert token in prep_doc, f"prep-auction.md 缺少 M4 契约: {token}"
    assert "闸门/仓位/`strategy_context` 由后续切片接入" not in prep_doc


def test_documented_snapshot_version_matches_production():
    production = _read(REPO_ROOT / "dragon_quant" / "review_account" / "prep" / "service.py")
    match = re.search(r'"version":\s*"([0-9.]+)"', production)
    assert match, "prep service must declare its snapshot version"
    version = match.group(1)
    assert version == "0.7"

    # The owner moved from AGENTS.md to prep-auction.md, but neither owner is
    # allowed to lose its own assertion: no "somewhere in the doc set" shortcut.
    prep_doc = _read(DOCS_DIR / "prep-auction.md")
    assert f"version={version}" in prep_doc, (
        "docs/agent/prep-auction.md (snapshot owner) must document version=" + version
    )
    readme = _read(README)
    assert f"version={version}" in readme, f"README.md must document snapshot version={version}"

    stale = [str(path.relative_to(REPO_ROOT)) for path, text in
             [(AGENTS, _read(AGENTS)), *_agents_documents().items(), (README, readme)]
             if "version=0.5" in text]
    assert not stale, f"retired 0.5 snapshot still documented in: {sorted(set(stale))}"


def test_documented_fallback_window_is_five_days():
    readme = _read(README)
    assert "前 5 日真龙并集" in readme
    assert "前 3 日真龙并集" not in readme
    assert "candidate_lookback_days`（默认 5）" in readme
    trading = _read(DOCS_DIR / "trading.md")
    assert "之前 **5** 个真实交易日的**全部真龙**并集" in trading, (
        "docs/agent/trading.md (candidate-pool owner) must keep the five-day fallback sentence"
    )
    assert "之前 **5** 个真实交易日的**全部真龙**并集" not in _read(AGENTS)


def _documented_tree_paths() -> set[str]:
    documented: set[str] = set()
    for document in (ARCHITECTURE, README):
        documented |= {str(path) for path in _tree_paths(document)}
    return documented


def _assert_canonical_are_documented(canonical: list[str]) -> None:
    missing_files = [name for name in canonical if not (REPO_ROOT / name).exists()]
    assert not missing_files, f"canonical files missing: {missing_files}"
    documented = _documented_tree_paths()
    missing_docs = [name for name in canonical if name not in documented]
    assert not missing_docs, f"main tree misses canonical paths: {missing_docs}"


def test_task12_canonical_entrypoints_are_documented():
    """Task 12 canonical paths exist and are part of the documented main tree."""
    _assert_canonical_are_documented([
        "dragon_quant/review/__init__.py",
        "dragon_quant/review/main.py",
        "dragon_quant/review_account/main.py",
        "dragon_quant/review_account/storage.py",
        "dragon_quant/review_account/prep/main.py",
        "dragon_quant/vpa/main.py",
    ])


def test_task13_canonical_entrypoints_are_documented():
    """Task 13 canonical paths exist and are part of the documented main tree."""
    _assert_canonical_are_documented([
        "dragon_quant/review_account/auction/__init__.py",
        "dragon_quant/review_account/auction/main.py",
        "dragon_quant/review_account/auction/collect.py",
        "dragon_quant/review_account/auction/recompute.py",
        "dragon_quant/review_account/auction/scoring.py",
        "dragon_quant/review_account/auction/dates.py",
        "dragon_quant/review_account/auction/registry.py",
        "dragon_quant/review_account/auction/strength.py",
        "dragon_quant/review_account/auction/serialize.py",
        "dragon_quant/review_account/auction/report.py",
        "dragon_quant/review_account/auction/types.py",
        "dragon_quant/review_account/live_trade/__init__.py",
        "dragon_quant/review_account/live_trade/main.py",
        "dragon_quant/review_account/live_trade/service.py",
        "dragon_quant/review_account/live_trade/trader.py",
        "dragon_quant/review_account/live_trade/signal_engine.py",
        "dragon_quant/review_account/live_trade/shadow.py",
        "dragon_quant/review_account/live_trade/storage.py",
    ])


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
    for document in (ARCHITECTURE, README):
        navigation = _tree_block(document)
        assert "cli_commands" not in navigation, (
            f"{document.name} navigation tree still lists the removed cli_commands module"
        )
        assert "review_account/prep.py" not in navigation, (
            f"{document.name} navigation tree still lists the removed review_account/prep.py module"
        )
        assert "providers/" not in navigation, (
            f"{document.name} navigation tree still lists the removed providers/ package"
        )
        assert "cookie_cli.py" not in navigation, (
            f"{document.name} navigation tree still lists the removed cookie_cli.py module"
        )
    text = _docset_text() + "\n" + _read(README)
    assert "dragon_quant/cli_commands" not in text
    assert "dragon_quant/review_account/prep.py" not in text
    assert "dragon_quant.providers" not in text, (
        "doc set still documents the removed providers package entry"
    )
    assert "cookie_cli" not in text, (
        "doc set still documents the removed cookie_cli adapter"
    )


def test_docs_describe_the_new_tree_first():
    """Task 15: the first tree block is the final main tree, not the migration shim tree."""
    for document in (ARCHITECTURE, README):
        block = _tree_block(document)
        for expected in ("commands/", "scan/", "review/", "review_account/", "vpa/", "infra/"):
            assert expected in block, f"{document.name} main tree misses {expected}"
        assert "scorers/" not in block, (
            f"{document.name} main tree still lists the legacy scorers/ package")
        # A top-level ``storage/`` line would be the old shim tree; infra/storage
        # renders as ``├── storage/`` under an ``infra/`` parent and must stay.
        assert "\n├── storage/" not in block, (
            f"{document.name} main tree still lists the legacy top-level storage/ package")
        assert "\n├── factors/" not in block, (
            f"{document.name} main tree still lists the legacy top-level factors/ package")


def test_new_architecture_packages_are_documented():
    documented = {str(path) for path in _tree_paths(ARCHITECTURE)}
    required = [
        "dragon_quant/app.py",
        "dragon_quant/commands",
        "dragon_quant/infra/clock.py",
        "dragon_quant/infra/provider/bundle.py",
        "dragon_quant/infra/storage/main.py",
        "dragon_quant/infra/factor/main.py",
        "dragon_quant/infra/assets/main.py",
        "dragon_quant/scan/orchestrator/main.py",
        "dragon_quant/scan/storage/main.py",
        "dragon_quant/scan/logger/main.py",
        "dragon_quant/scan/cache/main.py",
        "dragon_quant/scan/scores/main.py",
        "dragon_quant/review/main.py",
        "dragon_quant/review_account/main.py",
        "dragon_quant/review_account/prep/main.py",
        "dragon_quant/review_account/auction/main.py",
        "dragon_quant/review_account/live_trade/main.py",
        "dragon_quant/review_account/strategy/main.py",
        "dragon_quant/vpa/main.py",
        "dragon_quant/tests",
    ]
    missing = [name for name in required if name not in documented]
    assert not missing, f"docs/agent/architecture.md architecture overview misses: {missing}"


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


# --------------------------------------------------------------------------- #
# Root constitution gates (AC1/AC2/AC4) + doc-set gates (AC3/AC5)
# --------------------------------------------------------------------------- #
def test_agents_root_size_gate():
    text = _read(AGENTS)
    size = len(text.encode("utf-8"))
    lines = len(text.splitlines())
    assert size <= ROOT_MAX_BYTES, f"AGENTS.md is {size} bytes (> {ROOT_MAX_BYTES})"
    assert lines <= ROOT_MAX_LINES, f"AGENTS.md is {lines} lines (> {ROOT_MAX_LINES})"


def test_module_doc_layout_matches_plan():
    actual = {doc.name for doc in AGENT_DOCS}
    assert actual == EXPECTED_MODULE_DOCS, (
        f"docs/agent layout drifted from the approved 8-doc plan: {sorted(actual)}"
    )


def test_agents_index_covers_all_module_docs():
    links = _root_index_links()
    expected = {f"docs/agent/{doc.name}" for doc in AGENT_DOCS}
    assert set(links) == expected, (
        f"root index mismatch; expected {sorted(expected)}, found {sorted(set(links))}"
    )
    # Every module doc is routed to exactly once: no duplicated index rows.
    assert len(links) == len(set(links)), f"root index duplicates module docs: {links}"
    for target in links:
        assert (REPO_ROOT / target).is_file(), f"root index link target missing: {target}"
    # The three pre-existing authorities must stay discoverable from the root.
    root = _read(AGENTS)
    for authority in (
        "dragon_quant/review_account/strategy/STRATEGY.md",
        "dragon_quant/infra/assets/ASSETS_ARCHITECTURE.md",
        "archived_md/score/评分器Refactor.md",
    ):
        assert authority in root, f"root index must point at the existing authority {authority}"


def test_module_docs_have_routing_header():
    for doc in AGENT_DOCS:
        text = _read(doc)
        first_line = text.splitlines()[0] if text.splitlines() else ""
        assert first_line.startswith("# "), f"{doc.name} must start with a level-1 title"
        for field in ("> 触发：", "> 本文权威：", "> 上游权威："):
            assert field in text, f"{doc.name} routing header is missing {field!r}"


def test_agents_has_no_deferred_content():
    text = _read(AGENTS)
    for block in FENCED_BLOCK.findall(text):
        first_line = block.splitlines()[0].strip() if block.splitlines() else ""
        assert not first_line.startswith("dragon_quant/"), (
            "AGENTS.md must not embed the directory tree (it lives in docs/agent/architecture.md)"
        )
    for token in ROOT_FORBIDDEN_TOKENS:
        assert token not in text, f"AGENTS.md must not reintroduce deferred content: {token!r}"


def test_workflow_body_is_single_owned_outside_project_root():
    """§4 正文由父级 AGENTS.md 拥有；项目根只保留指针，不复制正文。"""
    text = _read(AGENTS)
    for anchor in WORKFLOW_BODY_ANCHORS:
        assert anchor not in text, (
            f"AGENTS.md must not duplicate the §4 workflow body (found {anchor!r}); "
            "the body is owned by the parent AGENTS.md"
        )
    assert WORKFLOW_POINTER in text, (
        "AGENTS.md must keep a one-line pointer to the parent AGENTS.md §4"
    )


def test_local_markdown_links_resolve():
    documents = [AGENTS, *AGENT_DOCS]
    broken: list[str] = []
    for document in documents:
        base = document.parent
        for target in _local_markdown_links(document):
            if (base / target).is_file():
                continue
            if (base / unquote(target)).is_file():
                continue
            broken.append(f"{document.relative_to(REPO_ROOT)} -> {target}")
    assert not broken, "broken local markdown links: " + ", ".join(broken)


def test_web_ui_summary_contract_preserved():
    review_doc = _read(DOCS_DIR / "review.md")
    for token in (
        "GET /api/dragons",
        "summarize_review_rows",
        "derived_completed",
        "derived_pending",
        "derived_status",
    ):
        assert token in review_doc, (
            f"docs/agent/review.md lost the Web UI summary contract token {token!r}"
        )


def test_critical_tokens_have_single_owner():
    root = _read(AGENTS)
    for token, owner in sorted(CRITICAL_TOKEN_OWNERS.items()):
        owner_text = _read(DOCS_DIR / owner)
        assert token in owner_text, f"{token!r} must be documented in docs/agent/{owner}"
        assert token not in root, (
            f"{token!r} is owned by docs/agent/{owner}; the root must stay a pointer"
        )
