import json
import re
import unittest
from dataclasses import fields
from pathlib import Path

from dragon_quant.review_account.models import StrategyConfig

ROOT = Path(__file__).resolve().parents[2] / "skills" / "dragon-quant"
SKILL = ROOT / "SKILL.md"
REFS = ["commands.md", "outputs.md", "playbooks.md", "operations.md", "safety.md"]
STRATEGY_FIELDS = [f.name for f in fields(StrategyConfig) if f.name != "source"]


def _frontmatter() -> str:
    return SKILL.read_text(encoding="utf-8").split("---\n", 2)[1]


class TestSkillContent(unittest.TestCase):

    def test_skill_exists_with_frontmatter(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        front = _frontmatter()
        self.assertIn("name: dragon-quant", front)
        self.assertIn("description:", front)
        self.assertIn("license: MIT", front)
        self.assertIn("min-cli-version:", front)
        self.assertIn("skill-version:", front)

    def test_skill_version_matches_package_version(self):
        package = json.loads(
            (ROOT.parents[1] / "package.json").read_text(encoding="utf-8")
        )
        front = _frontmatter()
        skill_version = re.search(r'skill-version:\s*"([^"]+)"', front).group(1)
        min_cli = re.search(r'min-cli-version:\s*"([^"]+)"', front).group(1)
        self.assertEqual(skill_version, package["version"])
        self.assertLessEqual([int(x) for x in min_cli.split(".")],
                             [int(x) for x in skill_version.split(".")])

    def test_description_has_chinese_and_english_triggers(self):
        front = _frontmatter()
        match = re.search(r"^description:\s*(.+)$", front, re.MULTILINE)
        self.assertIsNotNone(match, "description line missing from frontmatter")
        description = match.group(1)
        for token in ("真龙", "龙头", "复盘", "竞价", "回测", "scan", "prep", "auction", "buy", "sell"):
            self.assertIn(token, description)

    def test_authority_and_no_advice(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("AGENTS.md", text)
        self.assertIn("STRATEGY.md", text)
        self.assertIn("权威", text)
        self.assertIn("不提供投资建议", text)

    def test_scan_closed_book_requirements(self):
        text = SKILL.read_text(encoding="utf-8")
        for token in ("执行前检查", "scan", "dragons", "确认"):
            self.assertIn(token, text)

    def test_skill_size_cap(self):
        self.assertLessEqual(len(SKILL.read_text(encoding="utf-8").splitlines()), 300)

    def test_forbidden_content_boundaries_in_existing_files(self):
        paths = [SKILL] + sorted((ROOT / "references").glob("*.md"))
        blob = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        forbidden = [
            r"带动性\s*[:：]?\s*\d+\s*%",
            r"领涨性\s*[:：]?\s*\d+\s*%",
            r"抗跌性\s*[:：]?\s*\d+\s*%",
            r"流动性\s*[:：]?\s*\d+\s*%",
            r"资金承接\s*[:：]?\s*\d+\s*%",
            r"CREATE\s+TABLE",
            r"PRIMARY\s+KEY",
            r"FOREIGN\s+KEY",
            r"xq_a_token\s*=",
            r"xq_is_login",
            r"DQ_ASSETS_[A-Z_]+",
            r"assets_sync\.json",
            r"止损[^\n]{0,12}\d+(\.\d+)?\s*%",
            r"止盈[^\n]{0,12}\d+(\.\d+)?\s*%",
            r"回撤[^\n]{0,12}\d+(\.\d+)?\s*%",
            r"峰值[^\n]{0,12}\d+(\.\d+)?\s*%",
        ]
        for pattern in forbidden:
            self.assertIsNone(re.search(pattern, blob, re.IGNORECASE), pattern)
        for name in STRATEGY_FIELDS:
            self.assertIsNone(
                re.search(rf"\b{re.escape(name)}\b", blob),
                f"strategy field copied into skill: {name}",
            )

    def test_outputs_cover_key_contracts(self):
        text = (ROOT / "references" / "outputs.md").read_text(encoding="utf-8")
        for token in ("ranking_mode", "fallback_reason", "data_quality", "strict_5min",
                      "daily_fallback", "unverified", "is_weak", "checks",
                      "resolved_versions", "insufficient_data", "available_top_n",
                      # M4/M5/M6 复盘契约
                      "strategy_context", "review_candidates", "feedback",
                      "plan_review", "checkpoint", "auction_backfill_failed",
                      "prep_review_"):
            self.assertIn(token, text)

    def test_playbooks_cover_failure_modes(self):
        text = (ROOT / "references" / "playbooks.md").read_text(encoding="utf-8")
        for token in ("Cookie", "403", "停牌", "daily_fallback", "空池",
                      "fallback_reason", "版本漂移", "16:40", "09:30"):
            self.assertIn(token, text)

    def test_new_reference_size_caps(self):
        for name in ("outputs.md", "playbooks.md"):
            lines = (ROOT / "references" / name).read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(len(lines), 300, name)

    def test_operations_cover_contracts(self):
        text = (ROOT / "references" / "operations.md").read_text(encoding="utf-8")
        for token in ("T+1", "--date", "--at", "峰值", "并发", "assets push", "dry-run", "16:40"):
            self.assertIn(token, text)

    def test_safety_has_tiers_and_forbidden_ops(self):
        text = (ROOT / "references" / "safety.md").read_text(encoding="utf-8")
        for token in ("T0", "T1", "T2", "buy", "sell", "storage clear",
                      "assets push", "pull --apply", "backfill-dragons --prune",
                      "pull --force", "确认"):
            self.assertIn(token, text)

    def test_all_reference_links_exist(self):
        text = SKILL.read_text(encoding="utf-8")
        for name in REFS:
            self.assertIn(f"references/{name}", text)
            self.assertTrue((ROOT / "references" / name).exists(), name)

    def test_all_reference_size_caps(self):
        total = 0
        for name in REFS:
            lines = (ROOT / "references" / name).read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(len(lines), 300, name)
            total += len(lines)
        self.assertLessEqual(total, 1200)


def _strategy_output_section() -> str:
    """outputs.md 中「明日操盘策略」一节（到 auction 之前）。"""

    text = (ROOT / "references" / "outputs.md").read_text(encoding="utf-8")
    return text.split("### 明日操盘策略", 1)[1].split("## auction", 1)[0]


class TestSkillTomorrowStrategy(unittest.TestCase):
    """明日操盘策略：只消费 strategy_context，固定六段与四态动作映射。"""

    def test_skill_routes_tomorrow_strategy_to_prep_context(self):
        text = SKILL.read_text(encoding="utf-8")
        for token in ("明天买什么", "明日操盘策略", "复盘策略", "strategy_context",
                      "feedback_plans", "唯一动作依据", "observe_only",
                      "references/outputs.md"):
            self.assertIn(token, text)

    def test_outputs_fixed_six_sections(self):
        section = _strategy_output_section()
        headings = (
            "**1. 复盘日与数据质量**",
            "**2. 市场情绪锚与风险锚**",
            "**3. 主线板块、主线龙头与角色**",
            "**4. 全部详细标的（`positions[]`）**",
            "**5. 正反馈 / 负反馈与动作（`feedback_plans[]`）**",
            "**6. 组合提示与声明**",
        )
        positions = [section.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions), "六段顺序被改变")
        for token in ("复盘日与数据质量", "市场情绪锚", "风险锚", "主线板块",
                      "全部详细标的", "正反馈", "负反馈", "组合提示", "待验证",
                      "逐只覆盖全部", "下一交易日"):
            self.assertIn(token, section)

    def test_outputs_feedback_state_action_mapping(self):
        section = _strategy_output_section()
        for state, action in (
            ("confirmed", "execute_plan"),
            ("weakened", "downgrade_half"),
            ("invalidated", "cancel_entry"),
            ("unknown", "observe_only"),
        ):
            self.assertIn(state, section)
            self.assertIn(action, section)
        for token in ("allowed_actions", "expectations_unavailable", "不新建仓",
                      "唯一动作依据", "gap_low", "ratio_low", "seal_by"):
            self.assertIn(token, section)

    def test_outputs_position_semantics(self):
        section = _strategy_output_section()
        for token in ("单票建议仓位", "市场参考仓位", "不建议新建仓", "不是清仓",
                      "不可相加", "sum_exceeds_100", "不做归一",
                      "优先买哪只", "reason_code", "eligibility"):
            self.assertIn(token, section)

    def test_outputs_strategy_does_not_copy_thresholds(self):
        section = _strategy_output_section()
        forbidden = [
            r"gap_low\s*[=:：]\s*-?\d",
            r"ratio_low\s*[=:：]\s*-?\d",
            r"seal_by\s*[=:：]\s*\d",
            r"\d+(\.\d+)?\s*%\s*(?:仓位|止损|止盈)",
            r"权重\s*[=:：]\s*\d",
            r"带动性\s*\d+\s*%",
        ]
        for pattern in forbidden:
            self.assertIsNone(re.search(pattern, section), pattern)
        self.assertIn("待验证基线", section)

    def test_playbooks_strategy_context_fallback(self):
        text = (ROOT / "references" / "playbooks.md").read_text(encoding="utf-8")
        for token in ("strategy_context", "sc_v1", "旧摘要", "不拼凑",
                      "expectations_unavailable"):
            self.assertIn(token, text)
