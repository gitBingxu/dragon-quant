---
task_id: b
role: Current Implementation Analyst
objective: 确认当前 prep 能力、计算口径、输出与可复用边界
status: complete
confidence: high
sources_found: 7
acceptance_met: yes
---

## Sources

[1] docs/agent/prep-auction.md | 本地模块权威文档 | Aut:10 Rec:10 Rel:10 Dep:10 = 10 | PROJECT CONTRACT | 2026-10-28 访问
[2] dragon_quant/review_account/prep/service.py | 本地源码 | Aut:10 Rec:10 Rel:10 Dep:10 = 10 | PRIMARY CODE | 2026-10-28 访问
[3] dragon_quant/review_account/prep/compute.py | 本地源码 | Aut:10 Rec:10 Rel:10 Dep:10 = 10 | PRIMARY CODE | 2026-10-28 访问
[4] dragon_quant/review_account/verdicts.py | 本地源码 | Aut:10 Rec:10 Rel:10 Dep:10 = 10 | PRIMARY CODE | 2026-10-28 访问
[5] dragon_quant/review_account/prep/collectors.py | 本地源码 | Aut:10 Rec:10 Rel:10 Dep:10 = 10 | PRIMARY CODE | 2026-10-28 访问
[6] dragon_quant/review_account/advice.py | 本地源码 | Aut:10 Rec:10 Rel:10 Dep:10 = 10 | PRIMARY CODE | 2026-10-28 访问
[7] dragon_quant/infra/factor/builtin/prep.py | 本地源码 | Aut:10 Rec:10 Rel:9 Dep:9 = 9.5 | PRIMARY CODE | 2026-10-28 访问

## Findings (facts only)

- 当前 prep 是采集段与计算段分离的两段式流水线；采集资产落入 `prep_assets`，计算只读可用资产生成 `prep_snapshots`，支持零采集 `--recompute`。[1:16][2:146-208,265-317]
- 当前有 8 类采集资产：dragons、THS 涨停池、THS 题材榜、市场宽度、FG 板块、资金流、巨潮公告、宏观。[5:329-345]
- 候选池是含 T 在内最近 5 个交易日的真龙并集；同 code 按综合分、排名去重，完全同分取较新入选日。[1:20-21][3:287-395]
- 候选的 `composite_score` 来自其入选日的五维真龙评分，并非 prep 在 T 日重新计算的复盘量化分。[3:287-300,331-339][7:120-132,267-272]
- 当前总仓位公式只使用涨停家数与跌停家数，输出 0/0.3/0.5/0.8 四档；炸板率、昨日涨停溢价、晋级率、最高板、龙虎榜数被计算但不进入仓位公式。[1:33,36-37][3:27-35,201-245]
- 当前主线按 THS 题材榜的涨停家数排序，保留前 5，附带最高板与真龙命中数；THS 缺失时回退为真龙 concepts 计数前 3。[3:248-284]
- 个股输出 strong/light/ignore/unrated 四档，而非连续复盘得分；strong/light 规则同时使用入选日五维分、T 日涨停池、炸板、主线、资金和公告。[4:20-39,259-341,409-438]
- 当前 CLI 的“建议仓位”是 `sentiment.position_pct`，属于全市场总仓位建议；逐股只展示评级与 2-3 行维度说明，没有逐股仓位。[6:266-281,294-348]
- 宏观只进入摘要理由，不进入总仓位公式或个股规则。[1:29-30][6:109-174,177-245]
- 当前快照顶层为 sentiment、main_boards、macro、candidates、dragon_source，版本 0.6；没有次日情绪锚、市场角色图、正负反馈条件、建议失效条件或 Agent 策略输入契约。[2:200-208]
- 资产健康检查、逐因子异常隔离、显式 degraded、同日资产时点校验和历史日不可采矩阵已经存在。[2:146-199][5:388-435,524-607]
- 因子层目前只从 prep 快照派生 7 个因子，其中 `prep_composite` 仍是候选入选日五维分，`prep_verdict` 是 3/2/0 档位映射。[7:1-14,31-32,258-309]

## Analysis (your synthesis)

- 可复用的核心不是现有评分公式，而是“原始资产 → 健康过滤 → 纯计算 → 可重放快照”的骨架，以及数据血缘和降级语义。[1][2][5]
- 新需求需要新增一个独立的 T 日复盘评分与单票仓位层，不能把现有市场总仓位或入选日五维分改名冒充。[2][3][4][6]
- 新需求还需要新增“市场状态/主线/角色关系/次日条件计划”产物；现有 `main_boards` 只是板块列表，无法表达情绪锚、龙头与反馈网络。[2][3]
- 次日反馈与盘后评分应分层：盘后资产固定且可重放，T+1 竞价/盘中资产在各自时点追加，避免把未来数据写进 T 日快照。[1][2]

## Support snippets / paraphrases for top claims

- Claim: 当前没有逐股量化复盘分与逐股仓位。
  Source: [4][6]
  Support: 个股仅四档 verdict；仓位展示读取 sentiment.position_pct。
- Claim: 现有快照不能直接生成完整次日剧本。
  Source: [2]
  Support: 顶层只有情绪、主线列表、宏观、候选和血缘，无角色及反馈计划。

## Conflicts / unresolved issues

- 模块文档标题与 verdicts.py 注释仍出现“近三日”，实际 dragons 窗口为 5 日；以代码与 prep-auction 当前权威为准。
- 当前 market_breadth 自身计算涨停列表，但情绪主口径使用 THS total，后续新因子需要明确多源对账而非混用。

## Leads discovered

- 技术方案应保持 prep 快照为资产纯函数，并为新评分版本建立可重放配置。
- Skill 消费应读取版本化结构化产物，而不是解析 CLI 中文文本。

## Gaps

- 本线程不判断新增因子的外部可得性，交由线程 C/D。
- 本线程不决定新评分权重、仓位映射或反馈阈值，需结合方法论与后续回测方案设计。
