import io
import re
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from dragon_quant import cli
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "skills" / "dragon-quant"

ALLOWED_TOP = {
    "data",
    "scan",
    "dragons",
    "review",
    "prep",
    "auction",
    "factor",
    "review-account",
}
ALLOWED_DATA = {"quote", "kline", "minute", "batch-quote"}
ALLOWED_FACTOR = {"build", "query"}

EXCLUDED_DATA = ["sector", "components", "cookie-status", "cookie-fetch", "cookie-set"]
EXCLUDED_FACTOR = ["list", "status", "eval", "decision-review", "export", "fin"]

TOP_LEVEL_RE = re.compile(r"^\s{2,}([a-z][a-z0-9-]+)\s{2,}", re.MULTILINE)


def _help(*args):
    buf = io.StringIO()
    with patch("sys.argv", ["dragon-quant", *args, "-h"]):
        try:
            with redirect_stdout(buf):
                cli.main()
        except SystemExit as exc:
            assert exc.code == 0
    return buf.getvalue()


def _top_level_commands() -> set:
    return set(TOP_LEVEL_RE.findall(_help()))


def _table(text, start, end):
    block = text.split(start, 1)[1].split(end, 1)[0]
    rows = []
    for line in block.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return []
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[1:]]


def _routes():
    text = (ROOT / "references" / "commands.md").read_text(encoding="utf-8")
    return _table(text, "<!-- routes:start -->", "<!-- routes:end -->")


def _safety():
    text = (ROOT / "references" / "safety.md").read_text(encoding="utf-8")
    return _table(text, "<!-- safety:start -->", "<!-- safety:end -->")


def _skill_blob() -> str:
    paths = [ROOT / "SKILL.md"] + sorted((ROOT / "references").glob("*.md"))
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def _route_command_parts(command: str):
    parts = command.split()
    assert parts[0] == "dragon-quant", command
    parent = parts[1]
    sub = parts[2] if len(parts) >= 3 and not parts[2].startswith("-") else None
    return parent, sub


class TestSkillRoutes(unittest.TestCase):

    def test_routes_parse_non_empty(self):
        self.assertGreaterEqual(len(_routes()), 14)

    def test_top_level_commands_exist(self):
        top = _help()
        for route in _routes():
            parent, _ = _route_command_parts(route["command"])
            self.assertIn(parent, top, route["command"])

    def test_whitelist_routes_subset(self):
        for route in _routes():
            parent, sub = _route_command_parts(route["command"])
            self.assertIn(parent, ALLOWED_TOP, route["command"])
            if parent == "data":
                self.assertIn(sub, ALLOWED_DATA, route["command"])
            elif parent == "factor":
                self.assertIn(sub, ALLOWED_FACTOR, route["command"])
            else:
                self.assertIsNone(sub, route["command"])

    def test_key_flags_exist(self):
        for route in _routes():
            parts = route["command"].split()
            help_args = [p for p in parts[1:] if not p.startswith("--")]
            help_text = _help(*help_args)
            for flag in route["key_flags"].split():
                if flag.startswith("--"):
                    self.assertIn(flag, help_text, f"{' '.join(help_args)} {flag}")

    def test_each_route_has_one_write_class(self):
        for route in _routes():
            self.assertIn(route["write_class"], {"T0", "T1"}, route["command"])

    def test_safety_commands_are_whitelist_only(self):
        safety = {row["command"] for row in _safety()}
        self.assertLessEqual(safety, ALLOWED_TOP)
        self.assertLessEqual(ALLOWED_TOP, safety, "白名单顶层命令必须在 safety 登记")

    def test_safety_is_t0_t1_only(self):
        for row in _safety():
            self.assertIn(row["tier"], {"T0", "T1"}, row["command"])

    def test_safety_explains_long_running_ui(self):
        text = (ROOT / "references" / "safety.md").read_text(encoding="utf-8")
        for token in ("长驻", "schema", "是否继续"):
            self.assertIn(token, text)

    def test_excluded_top_level_commands_absent(self):
        excluded = _top_level_commands() - ALLOWED_TOP
        self.assertTrue(excluded, "未能从 CLI help 枚举出被排除命令")
        blob = _skill_blob()
        for command in sorted(excluded):
            self.assertIsNone(
                re.search(rf"dragon-quant\s+{re.escape(command)}\b", blob), command
            )
            self.assertIsNone(
                re.search(rf"^\|\s*{re.escape(command)}\s*\|", blob, re.MULTILINE), command
            )

    def test_excluded_data_subcommands_absent(self):
        blob = _skill_blob()
        for sub in EXCLUDED_DATA:
            self.assertIsNone(
                re.search(rf"dragon-quant\s+data\s+{re.escape(sub)}\b", blob), sub
            )
        self.assertNotIn("dragon-quant data cookie", blob)

    def test_excluded_factor_subcommands_absent(self):
        blob = _skill_blob()
        for sub in EXCLUDED_FACTOR:
            self.assertIsNone(
                re.search(rf"dragon-quant\s+factor\s+{re.escape(sub)}\b", blob), sub
            )

    def test_hidden_alias_and_v1_absent(self):
        blob = _skill_blob()
        self.assertNotIn("scan_v2", blob)
        self.assertNotIn("--source v1", blob)
        self.assertNotIn("--source {v1", blob)

    def test_leaf_help_only(self):
        blob = _skill_blob()
        self.assertIn("叶子", blob)
        for parent_help in ("dragon-quant data -h", "dragon-quant factor -h"):
            self.assertNotIn(parent_help, blob)

    def test_backtest_routes_do_not_allow_ui_flag(self):
        """两段式 UI：回测行只能回测，开面板必须走独立 --ui-only。"""
        backtest_rows = 0
        for route in _routes():
            parent, _ = _route_command_parts(route["command"])
            if parent not in {"review", "review-account"}:
                continue
            flags = route["key_flags"].split()
            if "--ui-only" in route["command"].split() or "--ui-only" in flags:
                continue
            backtest_rows += 1
            self.assertNotIn("--ui", flags, f"{route['command']} 回测行不得允许 --ui")
        self.assertEqual(backtest_rows, 2)

    def test_excluded_capability_words_absent(self):
        """未开放能力的中文名称也不得以否定清单反向出现。"""
        blob = _skill_blob()
        for word in ("因子注册", "因子评估", "因子导出", "财务同步"):
            self.assertNotIn(word, blob, word)

    def test_unavailable_policy_verbatim(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        phrase = (
            "当前功能还不稳定，暂未对 Agent 开放。"
            "你可以到 https://github.com/gitBingxu/dragon-quant/issues 反馈需求或问题。"
        )
        self.assertIn(phrase, text)


class TestOutputsReviewContracts(unittest.TestCase):
    """复盘/反馈/因子读回契约仍在 outputs.md，且不复制权重表。"""

    def test_outputs_document_review_feedback_and_factor_contracts(self):
        text = (ROOT / "references" / "outputs.md").read_text(encoding="utf-8")
        for token in ("strategy_context", "review_candidates", "feedback",
                      "plan_review", "checkpoint", "auction_backfill_failed",
                      "resolved_versions", "prep_review_"):
            self.assertIn(token, text)
        # 权重/阈值唯一真源在 STRATEGY.md / review config，Skill 不得复制。
        for forbidden in ("带动性 30%", "领涨性 25%", "role_weights"):
            self.assertNotIn(forbidden, text)
