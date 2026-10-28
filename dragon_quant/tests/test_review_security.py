"""M6 安全契约（技术方案 §11 / §15.4）。

三条断言：
  1. 快照与代码不含 Cookie / token / api-key 等凭据；
  2. OCR / 主观证据（``subjective_evidence``）**只**能作为提示，绝不进硬闸门或评分；
  3. 复盘包不引入网络、登录或浏览器依赖。
"""

import ast
import json
import re
import unittest
from pathlib import Path

from dragon_quant.review_account.prep.review import (
    scoring,
    gates,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REVIEW_DIR = REPO_ROOT / "dragon_quant" / "review_account" / "prep" / "review"
FACTOR_DIR = REPO_ROOT / "dragon_quant" / "infra" / "factor"

# 定向匹配：避免误伤 ``roles.py`` 里「涨停原因 token」这种业务词。
SENSITIVE_PATTERNS = (
    r"xq_a_token",
    r"xq_is_login",
    r"(?i)\bcookie\s*[:=]",
    r"(?i)\bauthorization\s*[:=]",
    r"(?i)\bapi[_-]?key\s*[:=]",
    r"(?i)\baccess[_-]?token\s*[:=]",
    r"(?i)\bsecret[_-]?key\s*[:=]",
)
SENSITIVE_VALUE = re.compile(r"(?i)xq_a_token|xq_is_login|api[_-]?key\s*[:=]")

FORBIDDEN_IMPORTS = (
    "dragon_quant.infra.provider",
    "dragon_quant.infra.storage",
    "dragon_quant.infra.assets",
    "playwright",
    "requests",
    "urllib.request",
    "http.client",
)


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def _walk_json(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json(item)
    elif isinstance(value, str):
        yield value


class TestNoCredentials(unittest.TestCase):
    def test_review_and_factor_sources_have_no_credentials(self):
        for directory in (REVIEW_DIR, FACTOR_DIR / "builtin"):
            for path in sorted(directory.glob("*.py")):
                text = path.read_text(encoding="utf-8")
                for pattern in SENSITIVE_PATTERNS:
                    self.assertIsNone(
                        re.search(pattern, text),
                        f"{path.relative_to(REPO_ROOT)} contains {pattern}")

    def test_snapshot_payload_has_no_credentials(self):
        from dragon_quant.review_account.prep.review import compute_review
        from dragon_quant.tests.test_factors_prep_review import _candidate

        assets = {
            "cookie": "xq_a_token=deadbeef; xq_is_login=1",
            "quotes": {"A": {"cookie": "xq_a_token=deadbeef"}},
            "ths_limit_up_pool": {"rows": [{"code": "A", "name": "样例A"}]},
            "fund_flow": {"quotes": {"A": {"pct": 10.0, "amount": 5e8}}},
        }
        payload = compute_review(assets, "2026-10-28")
        blob = json.dumps(payload, ensure_ascii=False)
        for token in _walk_json(payload):
            self.assertIsNone(SENSITIVE_VALUE.search(token), token)
        self.assertNotIn("xq_a_token", blob)
        self.assertNotIn("deadbeef", blob)


class TestOcrNeverGates(unittest.TestCase):
    def test_review_modules_do_not_consume_subjective_evidence(self):
        for name in ("gates.py", "scoring.py", "sizing.py", "orchestrator.py",
                     "market.py", "mainline.py", "roles.py"):
            text = (REVIEW_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("subjective_evidence", text, name)

    def test_subjective_evidence_does_not_change_gate_or_score(self):
        base = {
            "role": "leader", "mainline_hit": True,
            "review_score": 80.0, "review_grade": "A",
            "confidence": 0.9, "coverage": 0.9,
            "pool_available": True, "t_pct": 10.0,
            "amount": 5e8, "risk": {"level": "none"},
        }
        with_ocr = {**base, "subjective_evidence": [
            {"source": "ocr", "text": "公司被立案调查", "confidence": 0.99}]}
        self.assertEqual(gates.evaluate_gates(base),
                         gates.evaluate_gates(with_ocr))
        self.assertEqual(scoring.score_candidate(base),
                         scoring.score_candidate(with_ocr))

    def test_review_package_has_no_network_or_login_imports(self):
        offenders: dict[str, list[str]] = {}
        for path in sorted(REVIEW_DIR.glob("*.py")):
            hits = [module for module in _module_imports(path)
                    if any(module == bad or module.startswith(bad + ".")
                           for bad in FORBIDDEN_IMPORTS)]
            if hits:
                offenders[path.name] = sorted(hits)
        self.assertEqual(offenders, {})


if __name__ == "__main__":
    unittest.main()
