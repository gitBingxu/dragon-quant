---
task_id: b2
role: Downstream Contract Analyst
objective: 核对新 prep 产物对 Agent Skill 与交易消费方的影响
status: complete
confidence: high
sources_found: 3
acceptance_met: yes
---

## Sources
[1] skills/dragon-quant/SKILL.md | 本地源码 | PRIMARY CODE/CONTRACT | 2026-10-28
[2] skills/dragon-quant/references/outputs.md | 本地源码 | PRIMARY CONTRACT | 2026-10-28
[3] dragon_quant/review_account/strategy/STRATEGY.md | 本地策略权威 | PRIMARY CONTRACT | 2026-10-28

## Findings (facts only)
- 当前 Skill 是薄路由层，不承载评分权重、策略参数或表结构；prep 通过 `--json` 提供结构化结果。[1:13-21,74-75]
- 当前 Skill 明确说明 prep 仓位仅供人工参考、不参与下单，strong/light/ignore/unrated 是现有评级语义。[1:74][2:14-31]
- 下游 P/A 优先级把 prep strong/light 映射为 P=True，ignore/unrated 为 False；`prep_verdict_filter_enabled=True` 时可把 ignore 过滤出池。[3:120-132]
- 下游可读取 `sentiment.position_pct` 作为账户 sizing 的 market sentiment 输入，并可在开关启用时禁买。[3:61-67,88-106]
- 交易端已有四类买点诊断、竞价 Unknown/False/True 三态和独立卖出责任链；历史竞价数据缺口使 A 不能被普通历史回测宣称为有效。[3:90-158]

## Analysis (your synthesis)
- 新复盘分、单票建议仓位与 playbook 应新增版本化字段/产物，不能静默复用旧 `composite_score`、`verdict`、`position_pct` 并改变其语义。
- Agent 策略可以引用量化层的买入条件与风险否决，但执行层仍需保持既有 buy/sell 规则唯一权威，避免自然语言策略生成另一套交易规则。
- 旧消费方需通过显式迁移/开关选择新评分；默认兼容能防止产品升级意外改变真实买卖信号。

## Support snippets / paraphrases for top claims
- Claim: 改 verdict 会影响下游，不只是展示改版。
  Source: [3]
  Support: P 优先级和可选出池过滤直接消费 verdict 枚举。

## Conflicts / unresolved issues
- 用户目标要求 Agent 生成次日策略，而当前 Skill 声明“不提供投资建议”；需要在产品方案中明确这是本仓库量化结果的解释与条件计划，不泛化为任意荐股，并由用户决定是否调整该文案。

## Leads discovered
- 新增 strategy briefing 应是独立只读产物；写 prep 快照与 Agent 文本生成分离。

## Gaps
- 尚未确定新产物是否由现有 dragon-quant Skill 路由还是新增专用 Skill，属于后续技术框架决策。
