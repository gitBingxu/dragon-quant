# Evaluation Report — premarket-playbook-v2

## Overall Verdict: PASS

## Dimension Scores

| Dimension | Weight | Score | Verdict | Notes |
|---|---:|---:|---|---|
| Evidence & grounding | 30% | 8/10 | PASS | 核心结构要素均有来源；官方规则与学术证据单列；中文实践标注为经验来源；未使用无法访问的页面支撑结论。 |
| Synthesis quality | 20% | 8/10 | PASS | 将英文“预承诺”骨架、A 股“三向预期/竞价验证”、项目“动作交集”整合为统一模板，不是简单拼接。 |
| Coverage & limitations | 20% | 8/10 | PASS | 覆盖英文/中文/行为科学/交易所规则；明确自媒体阈值不可直接采用、if-then 非交易实证、T+1 差异。 |
| Coherence & usability | 15% | 9/10 | PASS | 结论先行；差距表与模板骨架直接可落地；完整模板另附方案文档。 |
| Calibration & insight | 15% | 8/10 | PASS | 明确“更可执行 ≠ 更高胜率”；核心洞察是“按交易时间线重排，而不是增加数据”。 |

Weighted score: 8.15/10

## Critical issues

无。

## Recommended fixes

1. 中文实践来源多为自媒体/论坛，正式实现时应在模板中避免引用其具体阈值；本报告已声明，但实现文档需再次强调。
2. 若后续要声称“提高盘中表现”，需要项目内真实回测/对照，不在本报告证据范围内。

## Spot-checks

| # | Claim | Citation | Registry match | Evidence support | Verdict |
|---|---|---|---|---|---|
| 1 | 每只 watchlist 必须有 bias/trigger/invalidation/size 四栏 | [1] | 是 | 原文明确四栏并称填不满就不该入名单 | Supported |
| 2 | 9:20–9:25 可挂单不可撤单，9:25 集中撮合 | [15][16] | 是 | 交易所规则/投资者教育材料一致 | Supported |
| 3 | if-then 计划对目标达成有中等偏大效应（d=.65，94 tests） | [8] | 是 | 元分析原文；非交易领域 | Supported |
| 4 | A 股三档竞价锚点（±1%、150% 等） | [12] | 是 | 自媒体原文；口径与其他来源不完全一致 | Weak（已标注经验来源） |
| 5 | 情绪周期决定总仓位上限（试错 1–3 成…退潮 0–2 成） | [13] | 是 | 论坛文章；无统计验证 | Weak（已标注经验来源） |
| 6 | SMB 写 3–5 个 ideas 并预设 alerts/scripts | [7] | 是 | 原文步骤明确 | Supported |

## Final recommendation

- Publish as-is（作为研究结论与模板设计依据）；实现前由用户确认方案中的 4 个待确认项。
