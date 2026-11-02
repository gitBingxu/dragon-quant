# Premarket Plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans in one stage-3 execute_agent session. Steps use checkbox (`- [ ]`) syntax. No derived subagents.

**Goal:** 提供只读 `premarket` 完整上下文，并让 Skill 结合当日外部证据生成八段盘前预案。

**Architecture:** `commands/premarket.py` 薄入口调用 `review_account/premarket` Facade；Snapshot Adapter 只读完整 prep/auction/meta，纯函数负责 context/anchors/actions/report。Skill 的 `premarket.md` 与 `evidence.md` 负责外部信源和固定模板，不新增评分或交易策略。

**Tech Stack:** Python >=3.10 stdlib/sqlite3、pytest、Markdown Skill、Node `node:test`、离线 E2E。

**Spec:** `docs/solutions/2026-10-30-premarket-plan-design.md`

## Global Constraints

- premarket T0、无 provider/网络/重算/新表；只读 `_connect_readonly`（mode=ro、零 DDL、零 WAL/SHM sidecar）。
- 完整嵌入 prep result_json 与 auction analysis_json + created_at/config；绝不直写 SQL 或修改 prep/auction 行为。
- 严格读取 `<T` 最近 prep；`strictness=latest_strictly_before` + `proven_gap`，不伪造紧邻 T-1。
- frozen_at 缺失为 null；cache=unknown；账户=unknown；不得合成。
- 行动只复制 allowed_actions 与资格/仓位/预期/veto/data quality 的交集；不开放自动交易。
- Python 0.8.0、npm/skill 1.3.0、min-cli 0.8.0；不 commit/publish。

## Review Focus

- 空数据根和残缺旧库不得建表或产生 sidecar。
- 同日/未来 prep 不得进入上下文；明显 gap 必须可检测。
- before 09:25 已有行不能被当作 ready，缺数据不能变成看空。
- position 0/null、expectations 缺失、veto、degraded 的行动语义不能混。
- Skill 必须限制媒体/社交证据，不能把未核原文当硬否决。

### Task 1: 只读存储 Adapter

**Files:** `infra/assets/db.py`、`infra/assets/dragons_mirror.py`、Create `review_account/premarket/snapshot.py`、Create `review_account/premarket/__init__.py`、Create `dragon_quant/tests/test_premarket_snapshot.py`

- [ ] **Step 1:** 写 mode=ro 零 DDL/sidecar、完整 prep/auction/meta、严格早于 T、损坏/缺表/缺行测试。
- [ ] **Step 2:** Run pytest，确认缺少 premarket/readonly 路径 RED。
- [ ] **Step 3:** 为既有三个读取函数加向后兼容 readonly=True 或等价变体，实现 snapshot Adapter；旧调用默认行为不变。
- [ ] **Step 4:** 重跑确认 GREEN。

### Task 2: 上下文、三锚与行动上限

**Files:** Create `review_account/premarket/{main,context,anchors,actions}.py`; Create `dragon_quant/tests/test_premarket_context.py`、`test_premarket_actions.py`

- [ ] **Step 1:** 写 premarket_sc_v1 时点、严格性/proven_gap、缺失/损坏/not_ready/config mixed、三锚去重、行动过滤链和组合约束测试。
- [ ] **Step 2:** Run pytest，确认 RED。
- [ ] **Step 3:** 实现纯函数与 Facade，now/services 仅注入 clock，无 provider/I/O。
- [ ] **Step 4:** 重跑确认 GREEN。

### Task 3: CLI、human 摘要与 B1

**Files:** Create `commands/premarket.py`、`review_account/premarket/report.py`; Modify `dragon_quant/cli.py`; Create `dragon_quant/tests/test_premarket_cli.py`

- [ ] **Step 1:** 写日期/未来日期、exit 0/1/2、JSON/human、无网络无写入、-h/import B1 测试。
- [ ] **Step 2:** Run pytest，确认 RED。
- [ ] **Step 3:** 实现薄 handler 和固定 human 摘要，注册 premarket 与 help。
- [ ] **Step 4:** 重跑确认 GREEN。

### Task 4: Skill 盘前模板与证据规则

**Files:** Modify `skills/dragon-quant/SKILL.md` 与既有 references；Create `references/{premarket,evidence}.md`; Modify `dragon_quant/tests/test_skill_{content,routes}.py`

- [ ] **Step 1:** 写八类白名单、premarket T0、八段模板、三锚、四态、信源/时点/去重/冲突/降级/登录、行动交集与 refs 总≤1200 的失败测试。
- [ ] **Step 2:** Run Skill pytest，确认 RED。
- [ ] **Step 3:** 实现路由和两份按需 reference；保留其他白名单与禁止能力，不开放自动交易。
- [ ] **Step 4:** 重跑确认 GREEN。

### Task 5: 离线 E2E 与覆盖映射

**Files:** Modify `dragon_quant/tests/e2e/coverage-map.json`; Create premarket fixtures/golden/tests；Modify `test_independent_entries.py` 与版本断言

- [ ] **Step 1:** 为 premarket 新 parser 节点写独立临时根/网络隔离/空库/完整快照 golden；只新增，不改旧 golden。
- [ ] **Step 2:** 确认新用例 RED、coverage-map 非 54/16 时门禁失败。
- [ ] **Step 3:** 同步 54 节点/16 顶层命令及必要版本断言，完成新增 E2E。
- [ ] **Step 4:** 重跑新增 E2E 与 harness/architecture 测试确认 GREEN。

### Task 6: 版本文档与全量验证

**Files:** `pyproject.toml`、`dragon_quant/_version.py`（如存在）、`package.json`、`skills/dragon-quant/SKILL.md`、`README.md`、`CHANGELOG.md`、`docs/agent/{development,prep-auction}.md`、根 `AGENTS.md`（仅必要索引）

- [ ] **Step 1:** Python 0.8.0、npm/skill 1.3.0、min-cli 0.8.0；同步命令、只读语义、外部证据与版本。
- [ ] **Step 2:** Run Skill pytest + `npm test`，Expected 全绿。
- [ ] **Step 3:** Run 离线 Python 全量、E2E 全量与 B1 harness 自检，Expected 全绿；基线旧失败必须区分。
- [ ] **Step 4:** `git diff --check` + code-self-review，交付 RED/GREEN、证据、known_issues、deviations、AC 映射；不 commit。
