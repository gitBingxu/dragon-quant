# prep 盘后复盘升级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在保持旧 `candidates` 与下游交易行为不变的前提下，为 `prep` 增加市场/主线/角色分析、六维个股复盘分、独立单票仓位、T+1 条件反馈、Agent 策略上下文和因子评估能力。

**Architecture:** 沿用 `prep` 的“采集资产 → 健康过滤 → 纯计算 → 可重放快照”两段式。新逻辑放入 `review_account/prep/review/` 纯函数子包；旧 `candidates` 与新增 `review_candidates` 双轨隔离，资金/公告采用 legacy/review 双健康作用域，覆盖修复走独立 `review_collect`。

**Tech Stack:** Python ≥3.10、既有 TDX/THS/腾讯/雪球 provider、SQLite `assets.db`、pytest、既有 Node Skill 测试；不新增第三方运行依赖。

**Spec:**
- `docs/solutions/2026-10-28-prep-review-product-design.md`
- `docs/solutions/2026-10-28-prep-review-technical-design.md`

## Global Constraints

- 旧 `candidates` 仍由 `compute_candidates + evaluate_candidates` 产出，范围、字段和值不得改变；详细池只进 `review_candidates`。
- 旧 `collection_report`、`plan.collect`、P/A 排序、`buy/sell/review-account/auction` 默认行为不得改变。
- `review_v2_enabled` / `review_assets_enabled` 默认 `true`；关闭计算时只写 `review={"enabled":false,"reason":"disabled"}`，不写其余四个新内容键。
- 快照版本从 `0.6` 升至 `0.7`；新增五键为 `review` / `review_candidates` / `strategy_context` / `feedback` / `plan_review`。
- 快照必须是资产纯函数；`review/*` 禁止网络、文件、SQLite、环境和时钟 I/O。
- `infra/factor/*` 不得 import `review_account/*`；新因子全部 `default_enabled=False`，缺失=无行。
- provider 新能力用普通方法 + 默认 `raise NotImplementedError`，禁止新增 `@abstractmethod`。
- 默认主路径不依赖东财、登录态或 OCR；不新增 pandas/Node 运行依赖。
- 所有评分、仓位、反馈数字均为待验证基线，只能影子输出，不得接入真实买卖决策。
- 默认 CLI 保留旧输出为逐字前缀，后追加复盘摘要；JSON golden 的变更属于已批准行为变更。
- 不自动执行 `git commit` / `git push` / PR；每个独立切片完成后只提醒用户提交。
- 每个 execute_agent 会话只闭环一个 Task，必须在同会话加载 `code-self-review` 并回报真实自测证据。

## Review Focus

1. **补充详细池代码改变旧资产健康状态**：legacy 全 None+补充健康、legacy 健康+补充异常时，旧 report/candidates 必须不变（Task 3）。
2. **首采缺失、补采失败、主线换血**：独立 `review_collect` 只补缺失并可重试，不能进入 legacy `plan.collect`（Task 3）。
3. **T+1 未来函数与错池**：T 快照不可变；竞价只取 `prev_plan.positions`，同日补采失败才 `unknown`（Task 5）。
4. **缺失被伪装成分数或仓位**：维度缺失重算 coverage，`coverage<0.5` 分数 null，数据不足仓位 null，明确不参与仓位 0（Tasks 1、4）。
5. **展示与 Agent 把新旧仓位混用**：旧 CLI 前缀不变，新段必须写“单票建议仓位”；Skill 不得复制阈值或把旧市场仓位当单票仓位（Tasks 4、6）。

---

### Task 1: M1 配置与六维纯评分骨架

**Files:**
- Create: `dragon_quant/review_account/prep/review/__init__.py`
- Create: `dragon_quant/review_account/prep/review/config.py`
- Create: `dragon_quant/review_account/prep/review/scoring.py`
- Test: `dragon_quant/tests/test_review_config.py`
- Test: `dragon_quant/tests/test_review_scoring.py`

**Interfaces:**
- Produces: `ReviewConfig`、`CONFIG`、`CONFIG_VERSION="review_baseline_v1"`、`config_hash(cfg=CONFIG)->str`。
- Produces: `score_candidate(cand: dict, ctx: dict, config: ReviewConfig = CONFIG) -> dict`，返回六维 `dimensions`、`contributions`、`coverage`、`confidence`、`review_score`、`review_grade`、`missing_dims`、`weight_profile`。
- 数值以产品 §8–§10 为唯一来源：5 角色 + `diagnostic_non_mainline` 六个权重 profile；档位 S/A/B/C/D=85/75/65/50；`coverage<0.5` 分数 null。
- 本 Task 不消费 `composite_score`，不接采集、快照或仓位。

- [x] **Step 1: 写配置与缺失语义失败测试**

覆盖：六 profile 权重行和=100；hash 稳定且改值必变；角色 null 必须选择 `diagnostic_non_mainline`；D1–D6 边界值；子分缺失按剩余权重归一；维度缺失剔除重算；贡献和=总分；`coverage<0.5` 为 null；极端 `composite_score` 不改变结果。

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest dragon_quant/tests/test_review_config.py dragon_quant/tests/test_review_scoring.py -q`
Expected: FAIL，原因是 `dragon_quant.review_account.prep.review` 不存在。

- [x] **Step 3: 实现配置与纯评分函数**

`config.py` 用 frozen dataclass/Mapping 集中全部权重、阈值、系数与预期；canonical JSON + SHA-256 前 12 位生成 hash。`scoring.py` 只接收普通 dict/list/标量，禁止 I/O；实现产品 §8 的 D1–D6、缺失重算、贡献、置信度和评级。`__init__.py` 仅导出规定公共符号。

- [x] **Step 4: 运行评分测试确认通过**

Run: `.venv/bin/python -m pytest dragon_quant/tests/test_review_config.py dragon_quant/tests/test_review_scoring.py -q`
Expected: 全部 PASS。

- [x] **Step 5: 运行 prep 邻近回归并自审**

Run: `.venv/bin/python -m pytest dragon_quant/tests/test_prep.py dragon_quant/tests/test_verdicts.py -q`
Expected: 全部 PASS。随后加载 `code-self-review`，附真实命令输出；BLOCKER 清零后回报 main。

- [x] **Step 6: 节点交付**

回报产物、自测证据、self-review verdict、known_issues/deviations、验收映射；提醒用户 M1 可独立提交，不自动 commit。

---

### Task 2: M2 市场、主线与角色计算

**Files:**
- Create: `dragon_quant/review_account/prep/review/market.py`
- Create: `dragon_quant/review_account/prep/review/mainline.py`
- Create: `dragon_quant/review_account/prep/review/roles.py`
- Test: `dragon_quant/tests/test_review_market.py`
- Test: `dragon_quant/tests/test_review_mainline.py`
- Test: `dragon_quant/tests/test_review_roles.py`

**Interfaces:**
- Consumes: Task 1 `ReviewConfig`。
- Produces: `compute_market_state(assets: dict, prev: dict | None) -> dict`。
- Produces: `compute_mainlines(assets: dict, prev: dict | None) -> list[dict]`。
- Produces: `assign_roles(pool_rows: list, block_rows: list, config: ReviewConfig) -> dict[str, dict]`。
- 公式和阈值严格使用产品 §5–§7：市场七子分、主线 N/H/C/S、五态、角色责任链和跨板块唯一主归属。

- [x] **Step 1: 写市场/主线/角色失败测试**

覆盖：分段插值边界；LU/LD 缺失=unknown；部分缺失归一；主线并列稳定排序；无 T-1=unknown；五角色顺序；全首板低置信；先锋缺时间跳过；成员缺失角色 null；多板块归属与并列；负锚使用 diagnostic profile。

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest dragon_quant/tests/test_review_market.py dragon_quant/tests/test_review_mainline.py dragon_quant/tests/test_review_roles.py -q`
Expected: FAIL，目标模块不存在。

- [x] **Step 3: 实现三个纯计算模块**

只从资产 dict 和 prev 快照读取数据；不 import provider/storage/service。角色顺序固定 leader→vanguard→mainstay→capacity→follower；未知不猜，保留竞争者、证据和 `unknown_reason`。

- [x] **Step 4: 运行本 Task 与 M1 回归**

Run: `.venv/bin/python -m pytest dragon_quant/tests/test_review_market.py dragon_quant/tests/test_review_mainline.py dragon_quant/tests/test_review_roles.py dragon_quant/tests/test_review_config.py dragon_quant/tests/test_review_scoring.py -q`
Expected: 全部 PASS。

- [x] **Step 5: 自审与节点交付**

加载 `code-self-review` 并附证据；回报 main，提醒用户 M2 可独立提交，不自动 commit。

---

### Task 3: M3 详细池、新资产、独立修复与快照 0.7

**Files:**
- Create: `dragon_quant/review_account/prep/review/pool.py`
- Create: `dragon_quant/review_account/prep/review/orchestrator.py`
- Modify: `dragon_quant/review_account/prep/collectors.py`
- Modify: `dragon_quant/review_account/prep/service.py`
- Modify: `dragon_quant/review_account/prep/compute.py`
- Modify: `dragon_quant/review_account/prep/main.py`
- Modify: `dragon_quant/infra/provider/base.py`
- Modify: `dragon_quant/infra/provider/tdx.py`
- Modify: `docs/agent/prep-auction.md`
- Modify: `README.md`
- Test: `dragon_quant/tests/test_review_pool.py`
- Test: `dragon_quant/tests/test_prep_review_integration.py`
- Test: `dragon_quant/tests/test_docs_consistency.py`

**Interfaces:**
- Produces: `derive_pool_codes(assets: dict, config: ReviewConfig) -> dict`，是采集与计算共用的唯一详细池实现。
- Produces: `plan_review_repairs(assets: dict, trade_date: str, today: str) -> ReviewRepairPlan`；`PrepPlan.review_collect` 与 legacy `collect` 完全分离。
- Produces: `compute_review(assets: dict, trade_date: str, *, config: ReviewConfig = CONFIG) -> dict`。
- Produces: provider `batch_get_daily_klines(...)`、`get_index_daily(...)`；普通方法默认 `NotImplementedError`。
- `fund_flow` / `cninfo` payload 增加 `legacy_codes` / `review_codes` / `review_health`；legacy 健康只看 dragons 子集。
- 快照升级 `0.7`，旧键不变；本 Task 接 `review` 和 `review_candidates`，其余新键在后续 Task 接入。

- [x] **Step 1: 写池、健康隔离、独立修复和快照失败测试**

必须含：详细池四类来源与截断；legacy 全 None+补充健康、legacy 健康+补充异常；新增 code 只触发 `review_collect`；失败下轮重试；主线换血更新日 K；旧 candidates/report 逐字段 golden；`review` 异常不中断旧链路；recompute 两次一致。

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest dragon_quant/tests/test_review_pool.py dragon_quant/tests/test_prep_review_integration.py -q`
Expected: FAIL，池、资产和快照新能力不存在。

- [x] **Step 3: 实现唯一池解析与 review 编排**

`pool.py` 无项目内依赖；采集与计算共用。`orchestrator.py` 组合 Task 1/2 纯函数，不触网。`collectors.py` 只允许额外 import `review.pool`，不得复制池规则。

- [x] **Step 4: 实现新资产、双健康作用域和独立修复**

加入 `stock_daily` / `holdings`，扩展现有资金/公告代码超集；`legacy_codes` 保持 dragons 并集。review 修复只补缺失并按键合并，不写 legacy report、不改 `plan.collect`；`review_assets_enabled=false` 时整个计划不生成。

- [x] **Step 5: 接入快照 0.7 与文档**

`_compute_all` 在旧逻辑后追加 `compute_review`；单候选/单因子异常隔离。`service.py` 版本改 0.7，同一切片同步 `prep-auction.md`、README 与 `test_docs_consistency.py`。不修改旧 candidates 代码路径。

- [x] **Step 6: 运行集成与兼容回归**

Run: `.venv/bin/python -m pytest dragon_quant/tests/test_review_pool.py dragon_quant/tests/test_prep_review_integration.py dragon_quant/tests/test_prep.py dragon_quant/tests/test_verdicts.py dragon_quant/tests/test_docs_consistency.py -q`
Expected: 全部 PASS，尤其健康隔离和 candidates golden。

- [x] **Step 7: 自审与节点交付**

加载 `code-self-review` 附证据；回报 main，提醒用户 M3 可独立提交，不自动 commit。

---

### Task 4: M4 建仓闸门、单票仓位、策略上下文与 CLI

**Files:**
- Create: `dragon_quant/review_account/prep/review/gates.py`
- Create: `dragon_quant/review_account/prep/review/sizing.py`
- Create: `dragon_quant/review_account/prep/review/context.py`
- Modify: `dragon_quant/review_account/prep/review/orchestrator.py`
- Modify: `dragon_quant/review_account/prep/advice.py`（或新建 review 展示模块，避免旧函数改义）
- Modify: `dragon_quant/commands/prep.py`
- Modify: `skills/dragon-quant/references/outputs.md`
- Modify: `README.md`
- Test: `dragon_quant/tests/test_review_gates.py`
- Test: `dragon_quant/tests/test_review_sizing.py`
- Test: `dragon_quant/tests/test_review_context.py`
- Test: `dragon_quant/tests/e2e/test_prep.py`

**Interfaces:**
- Produces: `evaluate_gates(cand: dict, ctx: dict, config: ReviewConfig) -> dict`。
- Produces: `size_position(scored: dict, market: dict, config: ReviewConfig) -> dict`，仓位三态 null/0/>0。
- Produces: `build_context(review: dict, config: ReviewConfig) -> dict`，`context_version="sc_v1"`。
- Produces: `format_review_lines(result: dict) -> list[str]`，只负责人读新段，不改变旧 formatter。
- `strategy_context.positions` 必须含全部详细池标的及原因，不得只含可买清单。

- [x] **Step 1: 写闸门、仓位、上下文和 CLI 失败测试**

覆盖 G0–G7 顺序；高分+硬风险=0；null 与 0；角色上限和系数；<0.5 归 0；组合和告警；持仓 0 不代表清仓；上下文与 review 数值一致；旧 CLI 前缀逐字不变，新段包含分数和“单票建议仓位”。

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest dragon_quant/tests/test_review_gates.py dragon_quant/tests/test_review_sizing.py dragon_quant/tests/test_review_context.py -q`
Expected: FAIL，目标函数不存在。

- [x] **Step 3: 实现纯闸门、仓位与上下文**

硬风险不可被高分解除；`diagnostic_non_mainline` 不获得 follower 角色或仓位上限。配置只来自 `CONFIG`，Agent 枚举只允许技术 §5 的规定值。

- [x] **Step 4: 接入快照与默认 CLI**

旧 4 行摘要和旧逐只清单原样输出，后追加 `format_review_lines`；新段失败只输出一行降级，不影响旧前缀。更新 README/Skill outputs，但 Skill 不复制权重和阈值。

- [x] **Step 5: 运行单元、prep 与 E2E 回归**

Run: `.venv/bin/python -m pytest dragon_quant/tests/test_review_gates.py dragon_quant/tests/test_review_sizing.py dragon_quant/tests/test_review_context.py dragon_quant/tests/test_prep_review_integration.py dragon_quant/tests/e2e/test_prep.py -q`
Expected: 全部 PASS；E2E 使用隔离临时根， golden 只按批准的新段/新键更新。

- [x] **Step 6: 自审与节点交付**

加载 `code-self-review` 附证据；回报 main，提醒用户 M4 可独立提交，不自动 commit。

---

### Task 5: M5 T+1 反馈、同日竞价补采与计划复盘

**Files:**
- Create: `dragon_quant/review_account/prep/review/feedback.py`
- Modify: `dragon_quant/review_account/prep/collectors.py`
- Modify: `dragon_quant/review_account/prep/service.py`
- Modify: `dragon_quant/infra/provider/base.py`
- Modify: `dragon_quant/infra/provider/tdx.py`
- Modify: `docs/agent/prep-auction.md`
- Test: `dragon_quant/tests/test_review_feedback.py`
- Test: `dragon_quant/tests/test_prep_review_integration.py`

**Interfaces:**
- Produces: `compute_feedback(prev_plan: dict, auction: dict, intraday: dict | None, daily: dict, trade_date: str) -> dict`。
- 新资产：`prev_plan` / `auction_feedback` / `t1_intraday`；后者 same_day_only。
- Produces: provider `get_auction_snapshot(code: str) -> dict | None`、`get_5min_kline_for_date(code: str, date: str)`，默认 `NotImplementedError`。
- 实际反馈只盘后计算；09:25/10:00 只是冻结 playbook 对照刻度。

- [x] **Step 1: 写 T+1 状态、补采与不可变失败测试**

覆盖 confirmed/invalidated/weakened/unknown；weakened→confirmed 限一次；`prev_plan` 代码而非当天池；现有 auction 未覆盖时同日补采；补采失败才 `auction_backfill_failed`；T 快照 hash 不变；历史日不补采；plan_review 四类归因。

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest dragon_quant/tests/test_review_feedback.py dragon_quant/tests/test_prep_review_integration.py -q`
Expected: FAIL，反馈资产与计算不存在。

- [x] **Step 3: 实现反馈纯函数与资产采集**

先读本地 auction，再对 `prev_plan.positions` 缺口同日 TDX 补采；不改 auction 命令、池或 JSON。历史缺口不可补；无 prev_plan 时反馈为空而非错误。

- [x] **Step 4: 接入快照与计划复盘**

T+1 只写自己的 `feedback` / `plan_review`；实际执行数据缺失时标 unknown，不编造成交价/仓位。同步 owner 文档的请求预算和可采性矩阵。

- [x] **Step 5: 运行反馈、集成和 prep 回归**

Run: `.venv/bin/python -m pytest dragon_quant/tests/test_review_feedback.py dragon_quant/tests/test_prep_review_integration.py dragon_quant/tests/test_prep.py dragon_quant/tests/e2e/test_prep.py -q`
Expected: 全部 PASS；T 快照 hash 与旧 candidates 不变。

- [x] **Step 6: 自审与节点交付**

加载 `code-self-review` 附证据；回报 main，提醒用户 M5 可独立提交，不自动 commit。

---

### Task 6: M6 因子注册、评估、影子指标与最终文档

**Files:**
- Create: `dragon_quant/infra/factor/builtin/prep_review.py`
- Modify: `dragon_quant/infra/factor/engine.py`
- Modify: `docs/agent/factor.md`
- Modify: `docs/agent/architecture.md`
- Modify: `docs/agent/prep-auction.md`
- Modify: `skills/dragon-quant/references/outputs.md`
- Test: `dragon_quant/tests/test_factors_prep_review.py`
- Test: `dragon_quant/tests/test_review_security.py`
- Test: `dragon_quant/tests/test_review_purity.py`
- Test: `dragon_quant/tests/test_architecture_rules.py`
- Test: `dragon_quant/tests/test_skill_content.py`
- Test: `dragon_quant/tests/test_skill_routes.py`

**Interfaces:**
- 新因子以技术 §13 为准：score/grade/position/market/mainline/role/D1–D6/资金封单子项/feedback，全部默认关。
- `params` 必须含 `review_config_version` 和 `review_config_hash`；新族分母使用 `len(review_candidates)`，不是旧 `ctx.codes`。
- 影子报告至少含 confirmed 命中率、invalidated 正确回避率、unknown 占比、实际新增 HTTP 数、截断率、review 竞价覆盖率。

- [x] **Step 1: 写因子、架构、安全和文档失败测试**

覆盖：null/unknown 无行；config hash 变化产生新版本；review 缺失不删旧行；新代码可落因子行；无 provider/storage/review_account 反向 import；无 Cookie/token/api-key；OCR 不进硬闸门；Skill 不含权重阈值；文档版本一致。

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest dragon_quant/tests/test_factors_prep_review.py dragon_quant/tests/test_review_security.py dragon_quant/tests/test_review_purity.py -q`
Expected: FAIL，新因子/安全契约未实现。

- [x] **Step 3: 实现只读因子族与源适配**

`prep_review.py` 零 I/O、零 review_account import；`engine.py` 只透传新增块。实现 config hash 版本和缺失语义，不把仓位或 feedback 注入生产模型。

- [x] **Step 4: 完成评估、影子报告和 owner 文档**

接入可成交标签、消融、滚动 OOS 与影子指标；历史不足返回 `insufficient_data`，不能宣称有效。文档只引用技术方案权重章节，不复制第二份参数表；同步架构、factor、prep-auction 和 Skill outputs。

- [x] **Step 5: 运行完整项目门禁**

Run:
`.venv/bin/python -m pytest dragon_quant/tests -q --ignore=dragon_quant/tests/e2e --ignore=dragon_quant/tests/smoke`
`.venv/bin/python -m pytest dragon_quant/tests/e2e -q`
`.venv/bin/python dragon_quant/tests/e2e/harness/runner.py --self-check`
`npm test`
Expected: 全部 PASS；失败必须如实报告，不得重跑淡化。

- [x] **Step 6: 自审与节点交付**

加载 `code-self-review` 附完整证据；回报 main 做阶段 3 单次验收。提醒用户 M6 可独立提交，不自动 commit；阶段 3.5 后再讨论影子期运行，不直接接入 buy/sell。

---

## Plan Self-Review

- **Spec coverage:** M1–M6 均有独立 Task；产品评分/仓位/Skill 与技术资产/反馈/因子/文档均有落点。CLI 新段归到 M4（需要仓位结果），与方案 M3/M4 的展示顺序略有调整，但不改变最终契约。
- **Step scan:** 每个 Task 都有失败测试、实现、通过验证、自审和交付；没有自动提交步骤。
- **Type consistency:** `ReviewConfig`、`compute_review`、`derive_pool_codes`、`plan_review_repairs`、`compute_feedback`、`build_context` 在 Task 间保持一致。
- **Review Focus:** 五类高风险输入均映射到 Task 3/5/1/4/6 的具体测试。
- **Proportion:** 计划只锁定文件、接口、数值来源和验证，不转写完整函数体；详细公式仍以已批准产品/技术方案为唯一权威。
