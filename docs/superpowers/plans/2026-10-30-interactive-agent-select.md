# Interactive Agent Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans in one stage-3 execute_agent session. Steps use checkbox (`- [ ]`) syntax. No derived subagents.

**Goal:** 裸 `install` 在三流均为 TTY 时提供六 harness 的零依赖键盘多选，选择后复用现有安装流程。

**Architecture:** 新 `lib/agent-select.mjs` 隔离菜单状态、按键 reducer、readline 生命周期；bin 只判断交互条件并调用。`--agent`、JSON、任一标准流非 TTY 时沿用默认 claude-code。

**Tech Stack:** Node >=22.20 `node:readline`、node:test、零依赖。

**Spec:** `docs/solutions/2026-10-30-interactive-agent-select-design.md`

## Global Constraints

- 仅 install + 未传 --agent + 非 JSON + stdin/stdout/stderr 全 TTY 才弹菜单；agents 别名不显示。
- 默认 claude-code；↑/↓、空格、a、Enter、q/Ctrl+C；空选择不离场，取消 exit0 零写入。
- 菜单写 stderr；finally 恢复 rawMode、移除监听并 pause。
- package/skill-version=1.2.0，min-cli=0.7.4；不 commit/publish。

## Review Focus

- 仅 stderr 重定向必须跳过菜单，不能不可见等待。
- Enter 空选择不能误触发安装或退出。
- 取消和异常都必须恢复终端。
- --agent/JSON/non-TTY 的旧行为不能变化。
- 选择结果必须走现有 expandAgents 与 manifest，不能另建安装路径。

### Task 1: 菜单状态与 readline 生命周期

**Files:** Create `lib/agent-select.mjs`; Create `test/agent-select.test.mjs`

- [ ] **Step 1:** 写 shouldPromptInstall 三流矩阵、reducer 默认/移动/空格/a/空 Enter、fake stream q/Ctrl+C、raw/listener/pause 清理失败测试。
- [ ] **Step 2:** Run `node --test test/agent-select.test.mjs`，确认模块缺失 RED。
- [ ] **Step 3:** 按方案导出 shouldPromptInstall/createSelectionState/normalizeSelectionKey/reduceSelection/selectAgents 并实现。
- [ ] **Step 4:** 重跑确认 GREEN。

### Task 2: bin 选择策略与真实安装

**Files:** Modify `bin/dragon-quant-skills.mjs`; Modify `test/cli.test.mjs`

- [ ] **Step 1:** 写 --agent/JSON/任一流非 TTY 跳过、fake TTY 默认 Enter、双目标选定后真实安装、取消零写入测试。
- [ ] **Step 2:** Run `node --test test/cli.test.mjs`，确认 RED。
- [ ] **Step 3:** 通过 main(argv,io) 注入缝接入 selectAgents；选完沿用 expandAgents 和现有 install，不改 JSON。
- [ ] **Step 4:** 重跑确认 GREEN。

### Task 3: 版本文档与全量验证

**Files:** `package.json`、`skills/dragon-quant/SKILL.md`、`README.md`、`CHANGELOG.md`、`docs/agent/development.md`

- [ ] **Step 1:** 版本升 1.2.0，文档说明键盘、三流门禁、自动化 --agent/--json、取消语义。
- [ ] **Step 2:** Run `npm test`，Expected 全绿。
- [ ] **Step 3:** Run Skill Python 三文件 pytest 与 `git diff --check`，Expected 全绿。
- [ ] **Step 4:** code-self-review，回报 RED/GREEN、证据、known_issues、deviations、AC 映射；不 commit。
