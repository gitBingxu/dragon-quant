import json
import re
import unittest
from dataclasses import fields
from pathlib import Path

from dragon_quant.review_account.models import StrategyConfig

ROOT = Path(__file__).resolve().parents[2] / "skills" / "dragon-quant"
SKILL = ROOT / "SKILL.md"
REFS = ["commands.md", "outputs.md", "playbooks.md", "operations.md",
        "reports.md", "safety.md", "premarket.md", "evidence.md"]
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
        self.assertEqual(skill_version, package["version"])

    def test_description_has_chinese_and_english_triggers(self):
        front = _frontmatter()
        match = re.search(r"^description:\s*(.+)$", front, re.MULTILINE)
        self.assertIsNotNone(match, "description line missing from frontmatter")
        description = match.group(1)
        for token in ("行情", "真龙", "龙头", "复盘", "竞价", "回测", "账户", "因子",
                      "盘前", "scan", "prep", "auction", "review"):
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
                      "daily_fallback", "unverified", "is_weak",
                      "resolved_versions", "available_top_n",
                      # M4/M5/M6 复盘契约
                      "strategy_context", "review_candidates", "feedback",
                      "plan_review", "checkpoint", "auction_backfill_failed",
                      "prep_review_"):
            self.assertIn(token, text)

    def test_playbooks_cover_failure_modes(self):
        text = (ROOT / "references" / "playbooks.md").read_text(encoding="utf-8")
        for token in ("获取行情失败", "空输出", "15:00", "16:40", "09:25",
                      "missing_source", "UI", "端口", "缓存",
                      "当前功能还不稳定", "issues"):
            self.assertIn(token, text)

    def test_playbooks_issue_template(self):
        text = (ROOT / "references" / "playbooks.md").read_text(encoding="utf-8")
        for token in ("dragon-quant -v", "skill-version", "操作系统",
                      "想做的事", "脱敏", "期望结果"):
            self.assertIn(token, text)

    def test_new_reference_size_caps(self):
        for name in ("outputs.md", "playbooks.md"):
            lines = (ROOT / "references" / name).read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(len(lines), 300, name)

    def test_operations_cover_contracts(self):
        text = (ROOT / "references" / "operations.md").read_text(encoding="utf-8")
        for token in ("单能力", "每日量化入库", "--ui-only", "--no-browser",
                      "真实", "URL", "--true-only", "16:40", "09:25", "09:30",
                      "factor-groups"):
            self.assertIn(token, text)

    def test_operations_single_capability_no_implicit_write(self):
        text = (ROOT / "references" / "operations.md").read_text(encoding="utf-8")
        for token in ("不隐式", "只有用户明确", "不自动执行"):
            self.assertIn(token, text)

    def test_operations_ui_lifecycle(self):
        text = (ROOT / "references" / "operations.md").read_text(encoding="utf-8")
        for token in ("后台启动", "长驻", "默认尝试打开浏览器", "仅本机",
                      "停止方式", "不回测"):
            self.assertIn(token, text)

    def test_operations_factor_verification(self):
        text = (ROOT / "references" / "operations.md").read_text(encoding="utf-8")
        for token in ("reports[0].factors", "missing_source", "factor query",
                      "five_dim_composite", "prep_review_score", "auction_is_weak",
                      "--all", "不能只看退出码"):
            self.assertIn(token, text)

    def test_operations_forbids_default_build_and_all(self):
        text = (ROOT / "references" / "operations.md").read_text(encoding="utf-8")
        forbidden = [line for line in text.splitlines() if line.startswith("**禁止**")]
        self.assertTrue(forbidden, "operations.md 缺少禁止段")
        for line in forbidden:
            self.assertIn("默认 build", line)
            self.assertIn("--all", line)

    def test_safety_t0_t1_only(self):
        text = (ROOT / "references" / "safety.md").read_text(encoding="utf-8")
        for token in ("T0", "T1", "确认", "长驻", "schema", "是否继续"):
            self.assertIn(token, text)
        self.assertNotIn("T2", text)
        for pattern in (r"\bbuy\b", r"\bsell\b", r"\bvpa\b", r"\bblacklist\b",
                        r"dragon-quant\s+assets\b", r"dragon-quant\s+storage\b"):
            self.assertIsNone(re.search(pattern, text), pattern)

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


class TestReports(unittest.TestCase):
    """prep 九段 / auction 七段报告骨架与缺失回退措辞。"""

    def _reports(self) -> str:
        return (ROOT / "references" / "reports.md").read_text(encoding="utf-8")

    def test_prep_report_template(self):
        text = self._reports()
        headings = (
            "# 复盘报告",
            "## 1. 复盘日与数据质量",
            "## 2. 市场环境与流动性",
            "## 3. 情绪与风格",
            "## 4. 主线题材",
            "## 5. 龙头与核心角色",
            "## 6. 当日计划与实际对照",
            "## 7. 明日条件计划",
            "## 8. 待验证条件与风险",
            "## 9. 声明",
        )
        positions = [text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions), "prep 九段顺序被改变")
        for token in ("事实", "解读", "待验证", "不在本次数据范围",
                      "不建议新建仓", "不是清仓", "observe_only",
                      "strategy_context", "position_pct", "不构成投资建议"):
            self.assertIn(token, text)

    def test_auction_report_template(self):
        text = self._reports()
        headings = (
            "# 竞价报告",
            "## 1. 快照时点与口径",
            "## 2. 大盘温度",
            "## 3. 板块方向",
            "## 4. 个股竞价结构",
            "## 5. 风险",
            "## 6. 开盘后验证",
            "## 7. 待验证与声明",
        )
        positions = [text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions), "auction 七段顺序被改变")
        for token in ("09:15–09:20", "09:20–09:25", "09:25", "09:30",
                      "匹配量", "不是实际成交量", "不在本次数据范围",
                      "from_cache", "unverified", "开盘后验证",
                      "不得倒填"):
            self.assertIn(token, text)

    def test_auction_after_open_still_0925(self):
        text = self._reports()
        for token in ("09:30 后", "09:25 终态", "实际运行时点"):
            self.assertIn(token, text)

    def test_report_default_in_chat(self):
        reports = self._reports()
        operations = (ROOT / "references" / "operations.md").read_text(encoding="utf-8")
        for token in ("默认对话交付", "指定路径", "确认"):
            self.assertIn(token, reports)
            self.assertIn(token, operations)

    def test_reports_do_not_copy_thresholds_or_promises(self):
        text = self._reports()
        for forbidden in ("胜率", "必涨", "稳赚", "主力一定", "收益承诺"):
            self.assertNotIn(forbidden, text)
        for pattern in (r"带动性\s*\d+\s*%", r"仓位\s*[:：]\s*\d+\s*%"):
            self.assertIsNone(re.search(pattern, text), pattern)


def _factor_groups():
    text = (ROOT / "references" / "operations.md").read_text(encoding="utf-8")
    block = text.split("<!-- factor-groups:start -->", 1)[1].split(
        "<!-- factor-groups:end -->", 1
    )[0]
    groups = {}
    for line in block.strip().splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        groups[key.strip()] = [name.strip() for name in value.split(",") if name.strip()]
    return groups


class TestFactorGroups(unittest.TestCase):
    """三组因子静态名单必须与 FACTOR_SPECS 的 source 分组完全一致。"""

    def test_factor_groups_match_registry(self):
        from dragon_quant.infra.factor.registry import FACTOR_SPECS

        groups = _factor_groups()
        self.assertEqual(set(groups), {"scan", "prep", "auction"})
        for source in ("scan", "prep", "auction"):
            expected = {spec.name for spec in FACTOR_SPECS if spec.source == source}
            self.assertEqual(set(groups[source]), expected, source)
            self.assertEqual(len(groups[source]), len(set(groups[source])), source)

    def test_factor_group_counts(self):
        groups = _factor_groups()
        self.assertEqual(len(groups["scan"]), 7)
        self.assertEqual(len(groups["prep"]), 23)
        self.assertEqual(len(groups["auction"]), 17)

    def test_factor_groups_exclude_other_families(self):
        block = (ROOT / "references" / "operations.md").read_text(encoding="utf-8")
        block = block.split("<!-- factor-groups:start -->", 1)[1].split(
            "<!-- factor-groups:end -->", 1
        )[0]
        self.assertNotIn("vpa_", block)
        self.assertNotIn("fin_", block)


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


class TestPremarketSkill(unittest.TestCase):
    """盘前预案：八类白名单、T0、八段模板、三锚/四态、外部证据与体量。"""

    def _premarket(self) -> str:
        return (ROOT / "references" / "premarket.md").read_text(encoding="utf-8")

    def _evidence(self) -> str:
        return (ROOT / "references" / "evidence.md").read_text(encoding="utf-8")

    def test_skill_declares_eight_capabilities_and_premarket_route(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("八类", text)
        self.assertIn("盘前预案", text)
        self.assertIn("premarket", text)
        self.assertIn("T0", text)
        self.assertIn("references/premarket.md", text)
        self.assertIn("references/evidence.md", text)
        self.assertIn("不写库", text)
        self.assertIn("不触网", text)

    def test_premarket_template_has_eight_fixed_sections(self):
        text = self._premarket()
        headings = (
            "# 盘前预案",
            "## 1. 信息截止与覆盖",
            "## 2. 市场与账户总闸门",
            "## 3. 三类锚与四种情景",
            "## 4. 主线与角色复核",
            "## 5. 候选逐只条件表",
            "## 6. 组合与执行约束",
            "## 7. 开盘后验证时间表",
            "## 8. 无法交易与盘后归因",
            "## 附录：外部证据清单",
            "## 声明",
        )
        positions = [text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions), "盘前八段顺序被改变")

    def test_premarket_template_separates_fact_assumption_action(self):
        text = self._premarket()
        for token in ("事实", "工作假设", "行动", "待验证/证伪",
                      "信息截止", "T0", "cache", "trading_day",
                      "三锚", "短线", "趋势", "情绪",
                      "normal", "超预期", "低于预期", "无法判断",
                      "confirmed", "weakened", "invalidated", "unknown",
                      "allowed_actions", "action_ceiling", "observe_only",
                      "wait", "no_new_position", "position_pct",
                      "不是清仓", "expectations_unavailable", "gap_high",
                      "追价警戒", "现有 sell 规则", "09:25", "09:30",
                      "不得倒填", "不自动交易", "不构成投资建议"):
            self.assertIn(token, text)

    def test_premarket_actions_are_intersection_not_new_rules(self):
        text = self._premarket()
        for token in ("交集", "资格", "position_basis", "veto",
                      "data_quality", "账户闸门", "不新增评分",
                      "不发明买点"):
            self.assertIn(token, text)
        for forbidden in ("先买 A 再买 B", "优先买入顺序", "胜率", "必涨"):
            self.assertNotIn(forbidden, text)

    def test_evidence_hierarchy_and_time_fields(self):
        text = self._evidence()
        for token in ("官方原文", "交易所", "公司公告", "官方行情", "媒体",
                      "第三方", "社交", "source_level", "url",
                      "event_time", "publish_time", "effective_time",
                      "first_seen", "retrieved_at", "verification_status",
                      "official_original", "media_unverified", "social_lead",
                      "login_required", "去重", "冲突", "更正", "降级",
                      "登录", "不逆向", "不伪造", "时区", "口径"):
            self.assertIn(token, text)

    def test_evidence_media_cannot_hard_veto_and_missing_only_lowers_coverage(self):
        text = self._evidence()
        for token in ("不得直接硬否决", "只降低情景覆盖", "不填中性",
                      "不当作利好", "未核原文", "覆盖率"):
            self.assertIn(token, text)

    def test_premarket_and_evidence_size_caps(self):
        for name in ("premarket.md", "evidence.md"):
            lines = (ROOT / "references" / name).read_text(
                encoding="utf-8").splitlines()
            self.assertLessEqual(len(lines), 300, name)

    def test_outputs_and_reports_point_to_premarket_owner(self):
        outputs = (ROOT / "references" / "outputs.md").read_text(encoding="utf-8")
        reports = (ROOT / "references" / "reports.md").read_text(encoding="utf-8")
        self.assertIn("references/premarket.md", outputs)
        self.assertIn("references/premarket.md", reports)
