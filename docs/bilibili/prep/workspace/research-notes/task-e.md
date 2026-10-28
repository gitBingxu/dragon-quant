---
task_id: e
role: Lead Synthesis
objective: 汇总方法论与现状，形成供阶段 2 使用的产品原则
status: complete
confidence: high
sources_found: 16
acceptance_met: yes
---

## Findings (facts only)
- 五篇材料都包含不同形式的市场→主线/个股→计划链条，且都包含自我交易或执行反馈。[A][B]
- 当前 prep 的候选五维分、市场仓位和个股 verdict 是三种不同旧语义，下游已消费 verdict 与 sentiment.position_pct。[B][B2]
- 当前 Quote/THS 原始资产已有部分封单与盘口字段，prep 快照未全部消费。[B4][B5]
- stock-ana 的情绪与筹码是本地算法，部分数据源有 key、登录、配额或历史边界。[C]

## Analysis (your synthesis)
- 产品应是“全量观察、分层评分、独立仓位、条件计划、执行复盘”闭环，而不是把当前候选池加几个字段。
- 详细评分池应包含近 5 日真龙、T 日主线核心、全市场正负锚与持仓关联；市场/主线计算不能受这五类池限制。
- 建议至少区分市场状态、主线持续、个股复盘、数据置信度四种输出；T+1 反馈另行按时点计算。
- 个股建议仓位是独立单票预备仓位，由分数、角色、市场状态、风险与数据完整度决定，不做组合归一。
- 正负反馈必须相对于角色化预期基准，而非统一高开/低开阈值；基准在 T 日冻结，T+1 只比较。
- Agent 只消费版本化 strategy_context 和动作枚举；量化层保留风险否决、数值和证据，Agent 负责组织语言。
- 新增字段/版本并保持旧消费默认不变，是避免意外改变 buy/sell 的硬约束。

## Support snippets / paraphrases for top claims
- Claim: 需要全量观察而非只对旧真龙打分。
  Source: [A]
  Support: 华东导弹要求全部涨停每日过一遍，其他文档也要求从市场选择主线后再选核心。
- Claim: 不能复用旧字段改义。
  Source: [B2]
  Support: verdict 和 position_pct 已影响 P/A、候选过滤及 sizing。

## Conflicts / unresolved issues
- 用户要求独立单票仓位，多个建议不能视为组合同时执行；需要在 UI/JSON/Skill 三处持续提示。
- 当前 prep 排除东财，stock-ana 部分最佳字段来自东财；技术选型必须单独请求决策或提供不依赖东财的主路径。

## Leads discovered
- 角色化 scorecard + 规则责任链 + playbook 状态机是适合阶段 2 的框架候选。
- 结构化 strategy_context 应与 Agent 自然语言报告分离，以保持快照可重放。

## Gaps
- 新权重、阈值和仓位档位没有有效性证据，只能作为待验证产品默认，不可直接成为交易生产参数。
