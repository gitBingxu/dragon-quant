# Multi-Agent Skill Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in one stage-3 execute_agent session. Steps use checkbox (`- [ ]`) syntax. Do not dispatch derived subagents.

**Goal:** 让 dragon-quant-skills 的 install / doctor / uninstall 支持六个真实 harness 的多选安装，并保留 agents 兼容别名。

**Architecture:** 以 `lib/paths.mjs` 的 `AGENT_TARGETS` 为唯一注册表，统一路径、基准目录、env 覆盖和提示。现有 manifest、安装状态机及顺序写流程不变；bin 增加自托管写前守卫，doctor 复用目标提示。

**Tech Stack:** Node >=22.20 ESM、node:test、零运行时依赖。

**Spec:** `docs/solutions/2026-10-30-multi-agent-skill-install-design.md`

## Global Constraints

- 六目标：`claude-code,codex,openclaw,hermes,deepseek,pi`；`agents` 仅作兼容别名，不进入 `all`。
- 路径严格采用方案 §4；`--target-dir` 始终优先，官方 env 只覆盖 global。
- 不改 manifest schema、lib/install.mjs、lib/uninstall.mjs、CLI 检测或多目标失败不回滚语义。
- package/skill-version = `1.1.0`，min-cli-version = `0.7.4`；不发布 npm、不 commit/push。
- 不自动修改 Hermes/Pi trust，不宣称六客户端真实加载已验证。

## Review Focus

- OpenClaw 在包根的 `<cwd>/skills` 与源目录重合：任何 install/uninstall --force 都必须被写前守卫拒绝。
- other-scope 与 doctor --all-scopes 也必须传 cwd/env，不能只在主循环正确。
- 测试进程可能已有官方 env：helper 默认清空，避免本机环境污染断言。
- Pi/OpenClaw 使用 cwd/workspace 而非 git root；嵌套调用必须单独测试。
- dry-run 不输出“已生效”提示，agents[].hints 必须为空。

---

### Task 1: 目标注册表与路径解析

**Files:** `lib/paths.mjs`、`test/paths.test.mjs`

**Interfaces:** 新增 `HARNESS_AGENTS`、`getAgentHints`、`getSameNameNote`；`resolveSkillsRoot({agent,scope,projectRoot,home,cwd,env,targetDir})`。

- [ ] **Step 1:** 为六目标 project/global、三个 env 覆盖、空 env、相对/`~/` env、cwd/workspace、targetDir、all 精确集合、agents 兼容与去重写失败测试。
- [ ] **Step 2:** Run `node --test test/paths.test.mjs`，确认因目标/函数缺失而 RED。
- [ ] **Step 3:** 按方案 §3.2–§3.3 实现注册表与纯函数解析；不改其它安装模块。
- [ ] **Step 4:** 重跑同一命令，确认 GREEN。

### Task 2: 多选 CLI 与自托管安全

**Files:** `bin/dragon-quant-skills.mjs`、`test/cli.test.mjs`

**Interfaces:** 消费 Task 1 注册表；install agents[] 只新增 `hints:string[]`；其它顶层 JSON 键不变。

- [ ] **Step 1:** 写 claude-code,hermes 双目录安装、all dry-run、doctor/uninstall 多选、目标提示、other-scope env、包根 openclaw install/uninstall --force 拒绝、dry-run hints 为空、部分失败保留已写目标的失败测试。run helper 默认清除三个官方 env。
- [ ] **Step 2:** Run `node --test test/cli.test.mjs`，确认 RED。
- [ ] **Step 3:** 所有 resolve 调用传 cwd/env；增加任何写操作前的自托管预检；输出目标 hints 与动态支持列表；保持顺序失败即停。
- [ ] **Step 4:** 重跑同一命令，确认 GREEN。

### Task 3: doctor 目标提示与打包 E2E

**Files:** `lib/doctor.mjs`、`test/doctor.test.mjs`、`test/packaging.test.mjs`

**Interfaces:** 复用 `getAgentHints/getSameNameNote`；doctor JSON 结构不变。

- [ ] **Step 1:** 写 codex 无 Claude precedence、Hermes 实际 trust 路径、Pi /reload、DeepSeek 自动发现、reload hints 去重及 tarball 双目标安装失败测试。
- [ ] **Step 2:** 分别运行 `node --test test/doctor.test.mjs test/packaging.test.mjs`，确认 RED。
- [ ] **Step 3:** 实现 doctor 提示聚合；扩展本地 tarball E2E 到 claude-code,hermes。
- [ ] **Step 4:** 重跑同一命令，确认 GREEN。

### Task 4: 版本、文档与全量验证

**Files:** `package.json`、`skills/dragon-quant/SKILL.md`、`README.md`、`CHANGELOG.md`、`docs/agent/development.md`

- [ ] **Step 1:** package/skill-version 升 1.1.0，min-cli 保持 0.7.4；文档列出支持名称、多选/all、路径、env、trust 和自托管限制。
- [ ] **Step 2:** Run `npm test`，Expected 全绿。
- [ ] **Step 3:** Run `.venv/bin/python -m pytest dragon_quant/tests/test_skill_package.py dragon_quant/tests/test_skill_content.py dragon_quant/tests/test_skill_routes.py -q`，Expected 全绿。
- [ ] **Step 4:** 运行 `git diff --check`，加载 code-self-review，交付真实 RED/GREEN、测试、known_issues、deviations 与 AC 映射；不 commit。
