# prep 新旧算法统一与派生数据清理 · 技术调研

> 日期：2026-10-30｜阶段：1（技术调研）｜状态：已通过（research-self-review: APPROVE）
>
> 范围：新六维 review 成为 prep／auction／buy 的唯一决策来源；修复主线 prev 未接线；保留原始资产，只清理可重建的派生数据。本轮不处理“最高板 262148”这一独立异常。

---

## 0. 用户确认的决策

1. 不允许旧五维候选评级与新六维复盘继续给出两个互相矛盾的推荐。
2. 新六维逻辑作为主算法，应用到 prep 展示、auction 候选池与 buy 候选输入。
3. 旧算法直接退役，不再作为第二套生产规则运行。
4. 清理采用用户选择的“仅清派生数据”：
   - 保留全部 `prep_assets` 原始证据与云端数据；
   - 可删除 `prep_snapshots` 与相关因子派生数据后重建；
   - 不删除 `auction_assets` / `auction_snapshots`，不处理 scan 的 `five_dim_composite`。
5. 真正删除必须先 dry-run，再由用户二次确认；不能在本轮代码实现时静默清空主库。

---

## 1. 现状证据

### 1.1 双头推荐的真实案例（2026-10-29）

- 光洋股份：旧 `verdict=ignore`，但新 `review_score=75.1 / eligible / position_pct=4%`。
- 中国卫星：旧 `verdict=light`、`composite_score=53.19`，旧推荐第 6；新评分 73.2、eligible、仓位 2%，新摘要第 2。
- 上海沪工：新评分 67.5，但 G1 公告硬风险否决、仓位 0。

根因不是排序，而是 `compute_candidates + evaluate_candidates` 与 `compute_review` 分别产出两套独立结论，随后同时展示和消费。

### 1.2 主线 unknown 的直接根因

`compute_mainlines` 需要 `prev` 做跨日比较；`service._compute_all` 当前调用 `compute_review(review_assets, trade_date)`，没有传入上一快照。`_prev_rank_map(None)` 永远为空，因此所有主线 `state=unknown`，且没有原因说明。

正确修复点是 service 注入严格早于当日的最近快照，不是修改状态阈值。

### 1.3 不能误删的共享事实

`compute_candidates` 同时承担：

- 多日真龙去重与真实入选日；
- 当日 THS 归因、资金、公告和风险事实装配；
- `facts` 供事实因子使用；
- 新 review 的 `legacy.composite_score / verdict / in_candidates` 只读证据。

这些事实仍需保留。应退役的是旧 `evaluate_candidates` 的推荐规则链，不是资金、公告、入选日等共享数据工具。`find_risk_announcement` 也被新链路使用，不能按文件整删。

---

## 2. 合并方案比较

| 方案 | 做法 | 评价 |
|---|---|---|
| A. 继续双跑，只改展示优先级 | 新算法排前，旧算法藏后 | 两套候选范围与评分仍影响 auction/buy，污染未消除，拒绝 |
| **B. 新六维唯一决策 + 旧接口兼容投影（推荐）** | 原始事实装配保留；review_candidates 为唯一权威；candidates 仅作旧字段投影和版本适配 | 下游无需维护第二套算法；旧字段消费方可渐进迁移；可保持 JSON 兼容 |
| C. 一次性删除 candidates 并改所有下游 | 全量改 schema 与所有消费方 | 改动面大、旧快照不可读、回滚困难，不符合简单优先 |

推荐 B。这里的 `candidates[]` 不再是第二套算法，而是同一份 `review_candidates[]` 的只读兼容视图。

---

## 3. 唯一决策与兼容映射

### 3.1 权威结果

- `review_candidates[]` 保持完整六维分、资格、仓位、角色、闸门与证据。
- 新六维权重、评分公式和硬闸门不在本次修改范围内。

### 3.2 旧字段仅作兼容

- `composite_score = review_score`；`rank` 按新复盘分排序。
- `verdict` 仅作确定性标签映射：S/A 且 position>0 → strong；其他 position>0 → light；position=0 → ignore；position=null → unrated。
- `ignore_reason` 来自新 `reason_code/gate_trace`，不再运行旧忽略规则。
- `is_true_dragon` 等兼容字段如用于旧买入过滤，必须明确其新语义或绕开旧过滤，不能让旧真龙身份再次否决新主线标的。
- 原始五维分与旧评级只能留在 `legacy` 作证据，不得进入生产排序。

### 3.3 下游候选一致性

新 0.8 快照的 auction/buy 候选读取路径应直接以 `eligible && position_pct>0` 为准，不再叠加旧 `composite_score>=50`、旧真龙身份或旧 verdict 过滤形成第二道算法。

旧 0.7 及更早快照继续走历史兼容分支，避免旧数据无法读取。

---

## 4. 派生数据清理范围

### 可删除（全部可重建）

- `prep_snapshots` 全部行；
- `factor_values` 中 prep 模块 7 个 + prep_review 模块 18 个（共 25 个精确因子名）的全部行；
- `factor_registry` 中上述 25 个因子的历史版本记录；
- 引用了这 25 个因子的 `factor_model_snapshots`（先 dry-run 逐行识别，混合模型必须展示完整名称与版本，避免误删无关模型）。

### 退役而非重建

- `prep_composite`、`prep_verdict` 两个旧评分因子的注册与实现；
- 其余 prep 事实因子（high_days、t_pct、t_turnover、limit_up_suc_rate、in_pool_today）保留；
- `prep_review_*` 在快照重建后重新注册和构建；`auction_*` 不纳入本次清理，因为其数据源是另一张 `auction_snapshots`。

### 明确保留

- `prep_assets`（含 collection_report、prev_plan、auction_feedback 等原始证据）；
- `auction_assets` / `auction_snapshots` 及全部 `auction_*` 因子数据；
- scan 的 `five_dim_composite` 与所有非 prep 因子；
- `factor_build_runs` 作为历史构建审计，不当作因子值或生产输入；不全表删除以免误伤其他因子审计；
- 云端对象、manifest、state。

### 当前本机 dry-run 前置事实（只读盘点）

- `prep_assets=73`（全部保留），`prep_snapshots=7`（全部可重算）。
- `factor_values=0`，因此当前没有因子值行可删，但仍需精确清理注册与后续新建值。
- 目标 25 个因子中，当前仅旧 prep 7 个存在持久化注册；`factor_build_runs=0`、`factor_model_snapshots=0`。
- 没有现成 purge API，需要新增 dry-run 计数、写前复核与单事务删除，不能依赖级联。

### 云端边界

本地清理不会删除云端旧派生数据。文档必须明确：迁移完成前禁止把旧云端 prep snapshots/factor 数据恢复到主库；隔离恢复用于审计不受此限。用户已接受云端不在本期处理。

---

## 5. 安全清理与重建路径

候选方式：

| 方式 | 评价 |
|---|---|
| 手工 SQL | 无 dry-run、无二次确认、易误删，拒绝 |
| **`assets purge-derived`（推荐）** | 默认 dry-run 输出各类计数；`--apply` 才在单事务删除；可选随后重建；不触云端 |
| 只加版本过滤不删除 | 旧数据仍占空间并可能误消费，不满足用户的清理要求 |

推荐顺序：

1. dry-run：列出 prep snapshots、factor values、registry、受影响 model snapshots 数量；
2. 用户二次确认后 `--apply`；
3. 按 prep 日期**升序**执行 `prep --recompute`，让 prev 主线状态逐日建立；
   - 清空后的首个重建日没有上一日计划，`prev_plan` 与 T+1 反馈必须允许合法为空，禁止伪造连续历史；后续日期随重建恢复；
4. 重新 build 保留的 prep／prep_review 因子；
5. 运行 decision-review 与全量回归，核对新旧冲突案例。

`--apply` 不应在普通功能测试中执行；E2E 只在独立临时库验证。

---

## 6. prev 修复与状态原因

- service 通过既有只读日期接口找严格 `< trade_date` 的最近 prep 快照，注入 `compute_review(..., prev=prev)`。
- 旧版本快照没有 `review.mainlines` 时，按 `prev_mainlines_missing` 降级，不伪造 stable。
- `unknown_reason` 必须补充：`prev_snapshot_missing` / `prev_mainlines_missing` / `mainline_score_missing` / `mainline_change_below_threshold`。
- 当前日与上一日都按新算法重算后，状态才具有跨日意义。

---

## 7. 主要风险与验收输入

1. **兼容字段再次形成旧过滤**：0.8 下游必须直接使用新 eligibility/position，不叠加旧身份和分数门槛。
2. **旧因子双份语义**：`prep_composite` 与 `prep_review_score` 不应并存；前者退役。
3. **先删后建顺序**：必须升序重算，否则 prev 无法正确建立。
4. **快照版本**：prep 升 0.8；decision-review 接受 0.7/0.8，因六维评分未变。
5. **缓存**：旧 auction 快照不会因 prep 合并自动更新，文档提示需要 `auction --force`。
6. **回归证据**：光洋、上海沪工、中国卫星必须在 prep 顶部与摘要中得到一致结论；机器人不再因缺 prev 固定 unknown。
7. **范围**：不改新六维权重、不处理最高板异常、不改 buy 的买卖规则本体，只改变其候选与评分输入。

---

## 8. 对阶段 2 的硬输入

- `review_candidates` 是唯一权威；`candidates` 只做同结果投影。
- 旧 `evaluate_candidates` 推荐规则退出生产；共享事实与风险工具保留。
- 0.8 下游候选集合与新 eligible+position>0 完全一致。
- `prep_composite` / `prep_verdict` 退役；其他因子不误删。
- `prep_assets`、auction 原始资产和云端不动。
- purge 默认 dry-run，apply 单事务、可审计、需二次确认，之后升序重建。
- prev 严格前一快照并带稳定 unknown_reason。
- 所有 schema、CLI、Skill、owner 文档、factor 文档、E2E golden 同步更新。

---

## 9. research-self-review 留痕

```yaml
review_type: research
round: 1
verdict: APPROVE
summary: "合并路径与清理边界可支撑方案；无 CRITICAL。"
findings:
  - severity: MAJOR
    dimension: 风险识别
    finding: "清空快照后首个重建日没有上一日计划，若伪造T+1连续反馈会污染新基线。"
    evidence: "prev_plan 在无 T-1 快照时按合法 empty/no_prev_plan 处理。"
    resolution: "加入升序重建不变量：首日反馈允许为空，后续随快照恢复。"
  - severity: MINOR
    dimension: 需求覆盖
    finding: "factor_build_runs 保留可能被误解为未清理全部派生记录。"
    resolution: "明确它是历史审计而非因子值/生产输入，混合构建不得按包含目标批删。"
  - severity: MINOR
    dimension: 事实准确性
    finding: "auction_* 与 prep_snapshots 不同源，不能因名称相关而连带清理。"
    resolution: "清理收敛为25个prep/prep_review精确因子名；auction资产、快照和因子全保留。"
scorecard:
  critical: 0
  major: 1
  minor: 2
  dimensions_passed: "6/6"
```

### known_issues

- 清空后的首个重建日缺少历史计划是合法降级，不是计算失败。
- `factor_build_runs` 仅作审计保留，不参与推荐或评分。
- 云端旧派生数据保留，迁移完成前禁止恢复到主库。
