---
task_id: b4
role: Five-Dimension Contract Analyst
objective: 核对现有五维与复盘新增因子的重叠和可复用证据
status: complete
confidence: high
sources_found: 1
acceptance_met: yes
---

## Sources
[1] archived_md/score/评分器Refactor.md | 当前 scan 评分权威 | Aut:10 Rec:10 Rel:10 Dep:10 = 10 | PRIMARY CONTRACT | 2026-10-28

## Findings (facts only)
- 五维目标是识别已出现的龙头，不包含买卖点、账户交易或未来收益预测。[1:1-8]
- 现有五维已覆盖稳定封板时点、个股/板块脉冲带动、板块共鸣、连板与五日领涨、抗跌、换手、封单强度和资金承接。[1:81-206]
- 封单强度已有明确口径：有效涨停时腾讯买一量/当日成交量，并叠加分钟开板稳定性；普通非涨停买一不能冒充封单。[1:152-173]
- 分钟和盘口只是采样证据，不能证明逐笔封板稳定性或真实资金因果。[1:12-14]
- 评分配置集中于 registry，异常/降级有明确语义，测试通过不代表真实行情有效性或盈利能力。[1:51-58,208-222]

## Analysis (your synthesis)
- “ prep 没有封单因子”应精确表述为“当前 prep 快照未消费该字段”，而非整个系统没有采集/计算能力。
- 新复盘评分应复用五维原始子项作为个股质量证据，但重新计算 T 日上下文，且避免在总分中多次计入同一封板/换手信息。
- 单票建仓仓位和未来策略超出五维原设计边界，不能声称五维综合分天然等于仓位分。

## Support snippets / paraphrases for top claims
- Claim: 封单量自动化已有项目内实现证据。
  Source: [1]
  Support: 腾讯盘口 bid1_volume 与 volume 已用于 scan 的封单强度；盘后历史快照是否可补建仍需另行判断。

## Conflicts / unresolved issues
- 五维当前候选主要为主板，理想复盘是否覆盖 20cm/ST/北交所是新产品边界，不能静默沿用过滤而不说明。

## Leads discovered
- 新数据矩阵应区分“provider 能取”“collector 已保存”“snapshot 已暴露”三个层次。

## Gaps
- 本线程未核实 dragons 镜像是否保留全部五维 details，需要技术方案阶段检查实际持久化字段。
