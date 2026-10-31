"""M6 架构依赖规则（技术方案 §3.1 / §15.4）。

只读 AST / 源码，不 import 被测模块，避免用 import 成功与否掩盖循环依赖。
"""

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FACTOR_DIR = REPO_ROOT / "dragon_quant" / "infra" / "factor"
REVIEW_DIR = REPO_ROOT / "dragon_quant" / "review_account" / "prep" / "review"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def _hits(path: Path, forbidden) -> list[str]:
    modules = _imports(path)
    return sorted(
        module for module in modules
        if any(module == prefix or module.startswith(prefix + ".")
               for prefix in forbidden)
    )


class TestFactorLayerIsLeaf(unittest.TestCase):
    def test_factor_never_imports_review_account(self):
        offenders = {}
        for path in sorted(FACTOR_DIR.rglob("*.py")):
            hits = _hits(path, ("dragon_quant.review_account", "dragon_quant.review"))
            if hits:
                offenders[path.relative_to(REPO_ROOT).as_posix()] = hits
        self.assertEqual(offenders, {})

    def test_factor_never_imports_pandas_or_playwright(self):
        offenders = {}
        for path in sorted(FACTOR_DIR.rglob("*.py")):
            hits = _hits(path, ("pandas", "playwright"))
            if hits:
                offenders[path.relative_to(REPO_ROOT).as_posix()] = hits
        self.assertEqual(offenders, {})

    def test_prep_review_builtin_is_pure(self):
        path = FACTOR_DIR / "builtin" / "prep_review.py"
        self.assertTrue(path.exists())
        self.assertEqual(
            _hits(path, ("dragon_quant.infra.provider", "dragon_quant.infra.storage",
                         "dragon_quant.infra.assets", "sqlite3", "socket",
                         "urllib", "requests", "pandas")), [])

    def test_prep_review_is_registered(self):
        text = (FACTOR_DIR / "builtin" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("prep_review", text)

    def test_review_shadow_is_data_injected(self):
        path = FACTOR_DIR / "review_shadow.py"
        self.assertTrue(path.exists())
        self.assertEqual(
            _hits(path, ("dragon_quant.review_account", "dragon_quant.review",
                         "dragon_quant.infra.provider", "sqlite3", "socket",
                         "requests", "urllib", "pandas")), [])

    def test_decision_review_core_is_data_injected(self):
        """决策回测纯核心零 I/O：快照经 store 注入、行情经 market 注入。"""
        path = FACTOR_DIR / "decision_review.py"
        self.assertTrue(path.exists())
        self.assertEqual(
            _hits(path, ("dragon_quant.review_account", "dragon_quant.review",
                         "dragon_quant.infra.provider", "dragon_quant.infra.assets",
                         "sqlite3", "socket", "requests", "urllib", "pandas")), [])


class TestReviewPackageIsPure(unittest.TestCase):
    def test_review_does_not_import_provider_storage_or_service(self):
        forbidden = (
            "dragon_quant.infra.provider",
            "dragon_quant.infra.storage",
            "dragon_quant.infra.assets",
            "dragon_quant.review_account.prep.service",
            "dragon_quant.review_account.prep.collectors",
            "dragon_quant.review_account.prep.main",
            "dragon_quant.review_account.prep.rebuild",
        )
        offenders = {}
        for path in sorted(REVIEW_DIR.glob("*.py")):
            hits = _hits(path, forbidden)
            if hits:
                offenders[path.name] = hits
        self.assertEqual(offenders, {})

    def test_review_does_not_import_network_or_browser(self):
        forbidden = ("socket", "playwright", "requests", "urllib.request",
                     "http.client", "pandas")
        offenders = {}
        for path in sorted(REVIEW_DIR.glob("*.py")):
            hits = _hits(path, forbidden)
            if hits:
                offenders[path.name] = hits
        self.assertEqual(offenders, {})

    def test_source_unavailable_lives_in_shared_types(self):
        types_text = (FACTOR_DIR / "types.py").read_text(encoding="utf-8")
        self.assertIn("class SourceUnavailable", types_text)
        self.assertNotIn("def save_prep_snapshot", REVIEW_DIR.joinpath(
            "orchestrator.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class TestPremarketIsReadOnlyAndPure(unittest.TestCase):
    """premarket 只读：纯投影不触网/不 import provider/factor；CLI 薄入口。"""

    PREMARKET_DIR = (REPO_ROOT / "dragon_quant" / "review_account" / "premarket")

    def test_premarket_never_imports_provider_network_or_factor(self):
        forbidden = (
            "dragon_quant.infra.provider",
            "dragon_quant.infra.factor",
            "socket", "urllib", "requests", "http.client", "pandas",
            "dragon_quant.review_account.prep.service",
            "dragon_quant.review_account.prep.compute",
            "dragon_quant.review_account.auction.collect",
            "dragon_quant.review_account.auction.scoring",
        )
        offenders = {}
        for path in sorted(self.PREMARKET_DIR.glob("*.py")):
            hits = _hits(path, forbidden)
            if hits:
                offenders[path.name] = hits
        self.assertEqual(offenders, {})

    def test_premarket_never_writes_storage(self):
        blob = "\n".join(path.read_text(encoding="utf-8")
                         for path in sorted(self.PREMARKET_DIR.glob("*.py")))
        for token in ("save_prep_snapshot", "save_auction_asset",
                      "replace_auction_snapshots", "_ensure_schema",
                      "journal_mode=WAL", "CREATE TABLE"):
            self.assertNotIn(token, blob)

    def test_commands_premarket_is_a_thin_entry(self):
        path = REPO_ROOT / "dragon_quant" / "commands" / "premarket.py"
        self.assertEqual(
            _hits(path, ("dragon_quant.infra.assets", "sqlite3",
                         "dragon_quant.infra.provider", "socket", "urllib")),
            [])
