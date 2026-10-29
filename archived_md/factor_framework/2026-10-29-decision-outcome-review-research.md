# prep / auction 决策结果回测 · 技术调研

> 日期：2026-10-29｜阶段：1（技术调研）｜状态：已通过（research-self-review: APPROVE）
>
> 范围：只设计“决策分组 × 多日结果”的回测方式，不调整评分、阈值、候选池、buy 或自动交易；本轮不实现产品代码。

---

## 0. 调研问题与验收边界

### 问题

1. 现有快照能否完整还原 prep 推荐／不推荐／数据不足与 auction 强／中／弱／否决／数据不足？
2. 现有 1／3／5 日可成交标签能复用多少，怎样让每个样本及剔除原因都可追溯？
3. 如何计算双向方向命中率、收益与覆盖率，同时避免小样本、不可成交剔除和日期相关性造成假象？
4. 回测入口放在哪里，既不混淆 `factor eval` 的单因子语义，也不增加不必要的顶层命令？

### 用户确认的边界

- 只回测既有 prep／auction 结论，不研究调分、调参、阈值搜索或自动优化。
- 65% 只是展示参考线，不触发任何策略判定或修改。
- prep 推荐组上涨命中率与不推荐组下跌命中率分别计算；数据不足永不混入不推荐。
- 支持 1／3／5 日、13bp 成本、零收益独立、不可成交独立计数。
- 每个样本可追溯到快照、进出场日期／价格、成本与剔除原因。
- 现有 prep／auction 评分、快照、候选池、buy 和默认输出不变。
- 本轮只交付调研与技术方案。

---

## 1. 现有能力盘点

### 1.1 快照与版本

- prep：`prep_snapshots.result_json` 含 `review_candidates[]`，可读取 `eligibility`、`position_pct`、`review_score`、`level` 等；v0.7 前的快照没有完整新复盘分组。
- auction：`auction_snapshots.analysis_json` v2 含 `strength`、`level`、`is_weak`、`unverified`、`data_quality` 与原始因子；v1 全部 `unverified=true`，不适合直接混入 v2 回测。
- 两者都有原始资产和确定性 `--recompute`，但回测不应改写这些快照。

**结论**：直接读冻结快照比依赖 `factor_values` 更完整，也不要求用户先执行 `factor build`。必须显式跳过不支持的历史版本并计数，不能把旧快照当成新决策。

### 1.2 真实本地覆盖（只读核对，2026-10-29）

- prep 共 6 日快照：v0.4×1、v0.5×3、v0.6×1、v0.7×1；唯一 v0.7 日有 60 个详细标的，其中 `position>0` 10 个、`position=0` 50 个。
- auction 共 43 条快照：v1×36、v2×7；v1 全部未验证，v2 才有稳定的数据质量语义。

这些样本只证明版本过滤与最小样本提示必不可少，不能用于证明策略有效。

### 1.3 已有收益标签

`infra/factor/review_shadow.py::build_tradable_labels` 已支持：

- 默认 1／3／5 交易日；
- `available_at` 后首个可成交开盘进入，第 N 个交易日开盘退出；
- 默认 13bp 往返成本；
- 一字涨停无法成交、停牌、低流动性、缺行情、区间不足分别剔除；
- 输出 `code / trade_date / horizon / entry_date / exit_date / entry_price / exit_price / value`；
- 无标签返回 `insufficient_data`，不伪造 0 收益。

时间语义符合本需求：prep 16:40 → 次日开盘；auction 09:25 → 当日开盘。默认不假设竞价一定可成交，使用日 K 正式开盘价。

### 1.4 已有评估不能替代本需求

`factor eval` 面向单因子，主要输出 RankIC、ICIR、NW-t、因子分组和覆盖率。它不能回答“prep 推荐组上涨命中率与不推荐组下跌命中率分别是多少”，也不保留完整 `position_pct` 决策分组。

`prep.feedback/plan_review` 只覆盖 T-1 → T+1 的计划执行与归因，不是 1／3／5 日决策分组回测。

---

## 2. 关键缺口与风险

### G1｜分组剔除原因不完整

`build_tradable_labels` 目前只有全局 `exclusions{reason:count}`。如果只调用一次，无法知道被剔除样本属于推荐组还是不推荐组，可能产生“难成交样本集中从某组消失，剩余命中率反而更高”的选择偏差。

**要求**：以只增方式补充逐样本 `exclusion_items`（code／snapshot_date／horizon／reason），保持原返回键与既有测试兼容。

### G2｜auction 历史数据不足组并不完整

`auction_snapshots` 只持久化已产出的逐股分析；命令运行时的 `empty/failed` 及 `strategy_context` 没有完整持久化为历史决策清单。因此回测可以覆盖已保存的 `unavailable`，但不能声称还原历史上全部空盘口／采集失败候选。

**要求**：报告明确 `coverage_basis="persisted_snapshots"`，缺失记录只记 `snapshot_missing`，不得伪造完整候选宇宙。若未来需要完整回测，再单独设计 run manifest，本期不新增表。

### G3｜版本混合会污染结果

prep v0.4–v0.6 没有统一的 `review_candidates`；auction v1 的 `unverified` 与 v2 含义不同。

**要求**：prep 仅接受 v0.7 新复盘结构，auction 仅接受 v2；旧版本计入 `unsupported_snapshot_version`，并提示先按当前实现重算，不静默跳过。

### G4｜65% 不能在小样本或相关样本上硬解释

同一天多只股票、相邻 horizon 的收益并非独立观测。简单 pooled 命中率会放大有效样本量。

**要求**：同时给出 pooled 命中率、逐日命中率均值、独立近似日数与 Wilson 区间；Wilson 只作描述性参考，并标注日期相关性。设置默认 `min_days=20`、`min_group_samples=30`，不足时 `quality=low_confidence`，不输出策略合格结论。

### G5｜分组与方向语义不能强行统一

- prep 推荐组期望上涨，不推荐组期望下跌，数据不足组没有方向期望。
- auction `强` 可对应上涨期望，`弱／vetoed` 可对应下跌期望；`中` 只展示涨跌分布，不计算方向命中率。
- `position_pct=null`、`unverified=true`、`strength=null` 都是数据不足，不是看空。

---

## 3. 建议的数据模型

### 3.1 决策观测（snapshot observation）

统一为：

```text
source, snapshot_version, snapshot_date, code, group, available_at,
score?, level?, veto?, data_quality_status?
```

source-specific adapter 只负责快照 → 决策观测：

- prep A：`eligibility=eligible && position_pct>0`；B：`position_pct==0`；U：其余／`position_pct=null`。
- auction 先判 unavailable，再判 vetoed，最后按 level 映射 strong／medium／weak。

### 3.2 样本账本（outcome ledger）

每个 `source + snapshot_date + code + horizon` 一行：

```text
sample_id, group, available_at, entry_date, exit_date,
entry_price, exit_price, cost_bps, net_return_pct,
outcome(up/down/flat/null), exclusion_reason
```

无法成交的样本保留在账本中，价格与收益允许 `null`，不得直接删除。

### 3.3 聚合指标

每组 × 每 horizon：

- observations、valid、excluded、coverage；
- up／down／flat 数量与比例；
- expected_direction（up/down/null）、direction_hit_rate（pooled）；
- daily_hit_rate_mean、days、effective_independent_days；
- Wilson 95% 区间及相关性提示；
- mean／median／p25／p75 net return；
- reference_line=0.65、above_reference（样本不足时 null）、quality；
- prep A−B、auction strong−weak 的平均净收益差。

RankIC 可继续由 `factor eval` 提供，不在本报告中重复实现或用来替代双向命中率。

---

## 4. 候选入口与架构比较

| 方向 | 做法 | 评价 |
|---|---|---|
| A. 扩展 `factor eval` | 加 `--decision-groups`，复用单因子输出 | 会混合因子 IC 与决策分组两套分母、指标和参数；prep 不推荐组与 auction veto 不是单因子值，拒绝 |
| **B. 新增 `factor decision-review`（推荐）** | factor 命令族下新增独立子命令；快照 adapter → 统一标签 → 分组聚合 → JSON/人读 | 属评估领域、无新顶层命令；不要求 build 因子；可复用现有错误码与 MarketData 注入 |
| C. 新增顶层 `review-decisions` | 独立命令与 service | 发现性好，但增加第 16 个顶层命令、coverage-map、路由与文档成本；当前只有两类来源，收益不足 |
| D. 独立研究脚本（非常规） | `scripts/` 一次性 Notebook/CLI，不进入产品命令 | 验证快，但重复实现市场加载、错误码、E2E 与 Skill 路由；长期难维护，不作为正式方案 |

推荐 B。共享骨架采用 **Template Method**；prep／auction 分组采用 **Strategy + Adapter**，隔离快照结构差异，不让因子层 import `review_account`。

---

## 5. 对阶段 2 的硬输入

1. 新入口只读快照与后续行情，不写 `prep_snapshots`、`auction_snapshots`、`factor_values` 或信号表。
2. prep／auction 版本白名单与缺失原因必须显式；旧版本不混入。
3. 复用 `build_tradable_labels`，只增逐样本 exclusion 明细，不改旧标签数值。
4. 每组保留完整 observation 分母及 coverage，不能只对成功产生收益的样本计算命中率。
5. `flat`、数据不足、不可成交、版本不支持、快照缺失分别计数。
6. 默认 1／3／5 日一次计算；13bp；不假设竞价成交；零收益不进上涨或下跌。
7. pooled 与 daily 指标并列；小样本只提示，不给策略有效／无效结论。
8. JSON 含配置、分组汇总、剔除计数和可选完整样本账本；人读只显示核心摘要。
9. `insufficient_data` 是有效回测结果，exit 0；参数错误 exit 2，运行时失败 exit 1。
10. 不研究或实现调参、自动优化、buy 改动、交易执行和胜率承诺。
11. 进入 horizon 循环前的剔除也要按每个请求的 horizon 展开到账本，保证每组 × 每 horizon 的 observation 分母一致。
12. 样本充足性按组独立判定；单组为空或不足只影响该组质量标记，不得把其他组的有效结果一并判为 insufficient_data。

---

## 6. 已知限制

- 当前真实新快照数量很少，首期主要价值是验证回测口径，而不是立即得出 65% 结论。
- auction 历史空盘口／失败候选没有完整 run manifest，本报告只对持久化快照覆盖负责。
- 日 K 开盘成交仍是可成交近似，不等于真实账户成交；沿用现有成本与剔除规则，不引入新的滑点模型。
- 多日 horizon 在区间末端会产生 `insufficient_horizon`，必须展示，不能静默缩短周期。

---

## 7. research-self-review 留痕

```yaml
review_type: research
round: 1
verdict: APPROVE
summary: "现有快照与标签足以支撑独立决策回测；无 CRITICAL。"
findings:
  - severity: MAJOR
    dimension: 可行动性
    finding: "早期剔除若只记一次，无法保证每组×horizon的分母一致。"
    evidence: "build_tradable_labels 在进入 horizon 循环前也会剔除样本。"
    resolution: "阶段2硬输入11：早期剔除按每个请求horizon展开到账本。"
  - severity: MINOR
    dimension: 事实准确性
    finding: "direction_hit_rate 与 pooled_hit_rate 并列会产生两个同名语义。"
    resolution: "合并为 direction_hit_rate（pooled）。"
  - severity: MINOR
    dimension: 需求覆盖
    finding: "单组为空不应拖垮其他组的有效回测结果。"
    resolution: "阶段2硬输入12：样本充足性按组独立判定。"
scorecard:
  critical: 0
  major: 1
  minor: 2
  dimensions_passed: "6/6"
```

### known_issues

- Wilson 区间只作描述性参考，不能消除同日和相邻 horizon 的相关性。
- auction 完整历史空盘口／失败候选仍需未来 run manifest，本期明确覆盖边界。
