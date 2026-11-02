# dragon-quant Agent Skill 交易日助手 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the single stage-3 execute_agent session. Steps use checkbox (`- [ ]`) syntax for tracking. Do not dispatch derived subagents.

**Goal:** 将现有全 CLI Skill 重构为仅开放七类稳定能力的交易日助手，并提供报告模板、三组因子入库核验、面板工作流与反馈闭环。

**Architecture:** 保留单 `SKILL.md`、references 渐进披露和现有 npx 安装器。`commands.md` 是唯一命令白名单，`operations.md` 拥有交易日/因子组，`reports.md` 拥有报告骨架，`outputs.md` 拥有字段语义；Python 业务逻辑不变。

**Tech Stack:** Markdown Agent Skill、Python `unittest`/pytest、Node `node:test`、现有 npm 打包白名单。

**Spec:** `docs/solutions/2026-10-30-agent-skill-trading-copilot-design.md`

## Global Constraints

- 只允许方案 §7.5 的命令面；其他命令不得以命令、路由或否定示例进入 Skill。
- 本次 `package.json` 与 `skill-version` 为 `1.0.0`，`min-cli-version` 独立声明 CLI >= `0.7.4`；Python 版本不变。
- `SKILL.md` ≤300 行，每个 reference ≤300 行，references 合计 ≤1200 行。
- 不改 Python 业务逻辑、CLI、安装器、E2E coverage map 或根 `AGENTS.md`。
- 报告默认对话交付；仅用户明确要求时写指定路径。
- 单能力请求不得隐式附带其他写操作；T1 需本轮明确请求或确认。
- 不复制评分权重、策略参数、表结构、视频阈值或收益承诺。
- 用户控制 git：本计划不执行 commit、push、PR 或 npm publish；每个任务结束只保留改动并报告。

## Review Focus

- 否定示例也可能泄露未开放命令：Task 1 用全文件禁用命令测试覆盖。
- “打开面板”不能退化为只给 URL：Task 2 固定复合意图默认尝试打开浏览器，`--no-browser` 仅显式场景。
- 09:30 后的 auction 仍是 09:25 终态：Task 2 用操作契约测试防止误写成只能盘后验证。
- `factor build` exit 0 仍可能含缺失：Task 2 固定逐因子状态与代表因子读回核验。
- 报告缺字段不能补 0/中性/市场结论：Task 3 用模板与缺失回退测试覆盖。

---

### Task 1: 白名单路由、安全边界与薄入口

**Files:**
- Modify: `dragon_quant/tests/test_skill_routes.py`
- Modify: `dragon_quant/tests/test_skill_content.py`
- Modify: `skills/dragon-quant/SKILL.md`
- Modify: `skills/dragon-quant/references/commands.md`
- Modify: `skills/dragon-quant/references/safety.md`

**Interfaces:**
- Produces: `commands.md` 的 `<!-- routes:start/end -->` 白名单表；`safety.md` 的 `<!-- safety:start/end -->` T0/T1 表；固定未开放话术和 issue URL。

- [ ] **Step 1: 写白名单与安全失败测试**
  将全命令集合相等断言替换为白名单子集、data/factor 子命令限制、全 Skill 禁用命令模式、T0/T1-only；显式禁止 `scan_v2` 与 `--source v1`。保留 help 参数存在性、单写类、体量和策略字段禁用测试。
- [ ] **Step 2: 验证 RED**
  Run: `.venv/bin/python -m pytest dragon_quant/tests/test_skill_routes.py dragon_quant/tests/test_skill_content.py -q`
  Expected: 因旧 Skill 暴露全命令和 T2 而失败。
- [ ] **Step 3: 实现薄入口与唯一白名单**
  重写 `SKILL.md` 为触发、预检、七类能力、固定话术和 reference 索引；将 `commands.md` 收敛到方案 §7.5；将 `safety.md` 收敛为同一白名单的 T0/T1 与确认规则。不得列排除命令。
- [ ] **Step 4: 验证 GREEN**
  Run: 同 Step 2。
  Expected: 白名单/安全相关测试通过；尚未实现的 operations/reports 测试不在本任务添加。

### Task 2: 交易日 workflow、UI 生命周期与三组因子

**Files:**
- Modify: `dragon_quant/tests/test_skill_content.py`
- Modify: `dragon_quant/tests/test_skill_routes.py`
- Modify: `skills/dragon-quant/references/operations.md`

**Interfaces:**
- Consumes: Task 1 白名单命令。
- Produces: `<!-- factor-groups:start/end -->` 的 scan 7 / prep 23 / auction 17 静态名单；单能力/复合 workflow；UI 与竞价时点契约。

- [ ] **Step 1: 写操作与因子失败测试**
  添加 `test_factor_groups_match_registry`，逐 source 对比 `FACTOR_SPECS`；断言默认 build/`--all` 禁用、逐因子状态、代表因子读回、单能力不附带写库、复合面板默认浏览器、09:30 后仍生成 09:25 报告。
- [ ] **Step 2: 验证 RED**
  Run: `.venv/bin/python -m pytest dragon_quant/tests/test_skill_content.py dragon_quant/tests/test_skill_routes.py -q`
  Expected: 旧 operations 缺三组名单与新 UI/时点契约而失败。
- [ ] **Step 3: 实现 operations.md**
  写入七类 step-by-step workflow、每日复合 opt-in、UI 后台/真实 URL/降级、完整三组 `--only` 名单、build JSON 逐因子核验与代表 query。严格采用方案 §10.1 的 47 个名字。
- [ ] **Step 4: 验证 GREEN**
  Run: 同 Step 2。
  Expected: 因子组与操作契约通过。

### Task 3: prep/auction 报告与输出语义

**Files:**
- Modify: `dragon_quant/tests/test_skill_content.py`
- Create: `skills/dragon-quant/references/reports.md`
- Modify: `skills/dragon-quant/references/outputs.md`

**Interfaces:**
- Consumes: prep/auction CLI JSON 与既有 `strategy_context`。
- Produces: prep 九段、auction 七段模板；`reports.md` 管结构，`outputs.md` 管字段语义。

- [ ] **Step 1: 写模板失败测试**
  添加 `test_prep_report_template`、`test_auction_report_template`、`test_report_default_in_chat`，覆盖事实/解读/待验证、缺失不可用、市场/板块不在范围、09:25 前匹配量非成交量、09:30 后语义、position 与 `observe_only`。
- [ ] **Step 2: 验证 RED**
  Run: `.venv/bin/python -m pytest dragon_quant/tests/test_skill_content.py -q`
  Expected: `reports.md` 不存在或模板不全而失败。
- [ ] **Step 3: 实现模板并裁剪 outputs**
  按方案 §9 创建 `reports.md`；从 `outputs.md` 删除非白名单命令解读，保留 prep 六段字段语义、auction 数据质量与账户三态。维持 `_strategy_output_section` 所需标题顺序，或同步更新该 helper。
- [ ] **Step 4: 验证 GREEN 与体量**
  Run: `.venv/bin/python -m pytest dragon_quant/tests/test_skill_content.py -q`
  Expected: 全部内容测试通过，references 总行数 ≤1200。

### Task 4: 异常 playbook 与 issue 闭环

**Files:**
- Modify: `dragon_quant/tests/test_skill_content.py`
- Modify: `skills/dragon-quant/references/playbooks.md`
- Modify: `skills/dragon-quant/SKILL.md`（仅当索引/话术需对齐）

**Interfaces:**
- Consumes: Task 1 固定话术与 issue URL。
- Produces: 白名单内异常分流和脱敏 issue 最小信息模板。

- [ ] **Step 1: 写 playbook 失败测试**
  覆盖行情失败/空输出、scan 15:00、prep 16:40、auction 09:25/缓存、因子 `missing_source`、UI 启动失败、固定不稳定话术与 issue 模板；排除非白名单修复命令。
- [ ] **Step 2: 验证 RED**
  Run: `.venv/bin/python -m pytest dragon_quant/tests/test_skill_content.py -q`
  Expected: 旧 playbook 含越界修复路径且缺新模板。
- [ ] **Step 3: 实现 playbook**
  每节使用症状/诊断/处理/停止条件；issue 模板包含版本、时间、能力、预期、实际、脱敏错误和数据质量，不自动创建 issue。
- [ ] **Step 4: 验证 GREEN**
  Run: `.venv/bin/python -m pytest dragon_quant/tests/test_skill_content.py dragon_quant/tests/test_skill_routes.py -q`
  Expected: 全部 Skill 内容/路由测试通过。

### Task 5: 版本、发布内容与用户文档

**Files:**
- Modify: `package.json`
- Modify: `skills/dragon-quant/SKILL.md` frontmatter
- Modify: `docs/agent/development.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: 三处 `0.7.4` 版本契约；与实现一致的安装、白名单、测试说明。

- [ ] **Step 1: 对齐版本与文档**
  将 npm/skill/min-cli 改为 `0.7.4`；development 更新六份 references、白名单边界与测试；README 简要新增 Agent Skill 安装和七类能力；CHANGELOG 在 Unreleased 记录本次收敛。
- [ ] **Step 2: 验证版本与打包**
  Run: `.venv/bin/python -m pytest dragon_quant/tests/test_skill_package.py dragon_quant/tests/test_skill_content.py -q && npm test`
  Expected: 全部通过；npm pack 包含新增 `reports.md`，不包含 docs/Python/数据。

### Task 6: 全量自动验证与交付证据

**Files:**
- Verify only；若失败，回到拥有该行为的 Task 修复并先补回归测试。

- [ ] **Step 1: 运行目标测试**
  Run: `.venv/bin/python -m pytest dragon_quant/tests/test_skill_package.py dragon_quant/tests/test_skill_content.py dragon_quant/tests/test_skill_routes.py -q`
  Expected: 全绿，无 warning 掩码。
- [ ] **Step 2: 运行 Node 全量**
  Run: `npm test`
  Expected: 全绿。
- [ ] **Step 3: 运行离线 Python 全量**
  Run: `.venv/bin/python -m pytest dragon_quant/tests -q --ignore=dragon_quant/tests/e2e --ignore=dragon_quant/tests/smoke`
  Expected: 全绿；任何失败均如实记录。
- [ ] **Step 4: 代码自审与节点报告**
  加载 `code-self-review`，附真实命令输出；返回产物、verdict、known_issues、deviations、AC 映射和 main_acceptance。行为场景 S1–S14 由 main 后续独立编排，不在本 execute_agent 内派生 subagent。
