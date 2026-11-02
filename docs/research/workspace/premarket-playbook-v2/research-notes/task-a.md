---
task_id: a
role: Practitioner template analyst (English)
objective: 提取英文专业交易者 pre-market plan / game plan / if-then 模板的结构要素
status: complete
confidence: medium-high
sources_found: 7
acceptance_met: yes
---

## Sources

[1] TraderNest — Daily Trading Routine: Pre/During/Post Template | https://tradernest.ai/blog/daily-trading-routine | Practitioner blog | 2026
[2] TheTwoTraders — Pre-Market Trading Routine: A 30-Minute Checklist | https://thetwotraders.com/pre-market-trading-routine/ | Practitioner blog | 2026
[3] TradeRadar — A 10-Minute Premarket Checklist | https://traderadar.app/articles/ten-minute-premarket-checklist | Practitioner blog | 2026
[4] ChartGuru — Case Study: How a Day Trader Uses ChartGuru | https://www.chartguru.io/blog/day-trader-case-study-chartguru | Vendor case study | 2026
[5] Eialgos — If/Then Trading Plans: Copyable Templates | https://blog.eialgosinc.com/blog/if-then-trading-plans | Practitioner blog | 2026
[6] The5ers — How to Build a Trading Plan: Template & Example | https://the5ers.com/how-to-build-a-trading-plan/ | Prop-firm education | 2026
[7] SMB Training — Why I Game Plan Every Day | https://www.smbtraining.com/blog/why-i-game-plan-every-day | Prop-desk practitioner | n.d.

## Findings (facts only)

- TraderNest 的盘前 30 分钟分块：5 分钟宏观/日历，10 分钟多周期结构，10 分钟 3–7 只 watchlist，5 分钟写下当日最大亏损与最大交易次数。[1]
- 每只 watchlist 必须有四栏：bias（long/short/neutral）、trigger（激活价格）、invalidation（证伪价格）、size（按止损距离和固定 1% 风险计算）；填不满四栏的标的不进入名单。[1]
- TraderNest 的五点盘前入场检查：在名单内、到达触发价、高周期仍支持、止损在预先定义的失效位、风险符合 1% 规则；"五个 yes 才执行，四个 yes 就跳过"。[1]
- TheTwoTraders 明确区分三种工具：交易计划（长期）、盘前 routine（今天是否/在哪里交易）、盘前 checklist（每笔订单前）；routine 的七项包括 readiness、账户与持仓、隔夜盘、预定事件、watchlist 与关键位、情景与提醒、风险与当日决策。[2]
- TheTwoTraders 的当日决策只有三档：正常交易 / 减小仓位 / 不交易；"stand aside" 是有效结果；情景只告诉你在哪里看，不是订单。[2]
- TradeRadar 的 10 分钟清单：日历 → 盘前异动扫描 → 新闻 → 关键位（前收、隔夜高低、支撑、阻力、失效位）→ 提醒与“什么情况下什么都不做”；执行约束单列点差、流动性、仓位、最大可接受亏损。[3]
- TradeRadar 的 setup 排序：先排隔夜风险与预定事件，再排已核实的公司催化，再排有明确背景的板块/市场异动；无法解释的跳空和高流动性差的票进 wait list。[3]
- ChartGuru 的单票计划五问：bias（只做多/只做空/中性）、trigger area（哪个位置需要突破/守住/拒绝）、invalidation（什么价格证明看错）、risk（是否能在亏损上限内定仓）、no-trade zone（哪里太乱不做）。[4]
- ChartGuru 的盘中确认方式：突破回踩、VWAP 收复/拒绝、开盘区间突破、趋势线突破、更高低点/更低高点、量能/动量确认；低信心时“等待”本身就是结论。[4]
- Eialgos 的 if-then 句式："If [condition] AND [zone], then [action] with size X and stop Y."；condition 是市场状态，zone 是预先标记的价格/量能区，trigger 是可验证的确认事件。[5]
- Eialgos 的退出与风控模板：止损（跌破定义位立即全退）、时间止损（N 个交易日内未达目标则退出）、移动止盈（创新高后跌回前区间则减半）、日亏损上限触发后减半/停止；没有合格 setup 时的动作就是 no trade。[5]
- The5ers 的单笔风险常用 0.5%–2%，默认 1%；示例日亏上限 3%；要求写下仓位计算公式、每日/每周亏损上限、复盘频率与日志字段（风险%、仓位、入场理由、结果、规则遵守）。[6]
- SMB 的每日 game plan：扫描盘前量能异动 → 读新闻 → 写 3–5 个 ideas + 2nd day plays → 15–20 分钟晨会 → 为每个 idea 设置价格提醒和“pre-written trading scripts”；脚本按 S1–S3/R1–R2 价位写多空情景，盘中按脚本执行。[7]
- SMB 的周度复盘触发：如果不到 50% 的晨间 ideas 提供“excellent risk/reward trades”，就检查是市场变了、分析错了，还是只有某些 setup 有效。[7]

## Analysis

- 英文模板的共同骨架不是“预测”，而是**预先承诺（pre-commitment）**：市场状态 → 3–7 个标的 → 每只的 bias/trigger/invalidation/size → 情景与提醒 → 风险预算 → 盘前检查 → 盘中只执行已确认的剧本 → 盘后归因。
- 可执行性的关键在**字段粒度**：只有“看多/看空”不够，必须落到“哪个价位触发、哪个价位证伪、用多大仓位、什么时间窗口内有效”。[1][4][5]
- “不交易”被当成一等结果，而不是失败：没有触发、风险预算用完、数据不足、readiness 差都可以直接 stand aside。[1][2][3]
- 盘中确认不是重复判断方向，而是检查预设的确认事件（突破回踩、VWAP、开盘区间、量能）。[4]
- 这些模板大多为 T+0/期货/美股设计；A 股不能照搬当日买卖与止损，但可以照搬“字段 + 情景 + 检查点”的组织方式。

## Support snippets / paraphrases for top claims

- Claim: 每只标的必须有 bias/trigger/invalidation/size 四栏。
  Source: [1]
  Support: 文章原文列出四栏，并称“If you cannot fill those four fields, the instrument does not belong”。
- Claim: 计划是条件剧本，不是订单。
  Source: [2]
  Support: “The scenario only tells you where to look; it is not an order.”
- Claim: if-then 必须带 zone、trigger、size、stop。
  Source: [5]
  Support: 模板句 "If [condition] AND [zone], then [action] with size X and stop Y."
- Claim: 盘前计划要写 3–5 个 ideas 并为每个 idea 预设提醒与脚本。
  Source: [7]
  Support: SMB 的每日步骤明确列出 “Enter 3-5 ideas”“Enter alerts and trading scripts for all trading ideas”。

## Conflicts / unresolved issues

- 英文模板普遍假设可当日进出、可做空、可设置止损单；A 股 T+1、涨跌停、集合竞价制度不同，不能直接使用其“当日止损/加仓”语义。
- SMB 的“3–5 ideas”与 TraderNest 的“3–7 watchlist”是经验值，无统一证据；只能作为上限建议，不作为硬规则。

## Leads discovered

- SMB 的“pre-written trading scripts”可作为“候选作战卡”的直接原型。
- TradeRadar 的“what would make you do nothing”可作为“不做什么”清单。
- Eialgos 的 time stop / trailing exit 模板可映射到项目现有卖出规则，而不是新造规则。

## Gaps

- 未找到公开的、经过同行评审的“盘前模板有效性”研究；现有证据主要是从业者经验与行为科学的外推。
- 未覆盖机构交易台的正式 game plan 文档（多为内部资料）。
