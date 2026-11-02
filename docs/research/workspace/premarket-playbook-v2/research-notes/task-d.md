---
task_id: d
role: Current-template gap analyst
objective: 对照外部范式与项目约束，定位现有八段模板“对盘中帮助不大”的结构性原因
status: complete
confidence: high
sources_found: 4
acceptance_met: yes
---

## Sources

[1] `skills/dragon-quant/references/premarket.md`（八段模板，148 行）
[2] `skills/dragon-quant/references/outputs.md`（`strategy_context` 六段、`feedback_plans` 唯一动作依据）
[3] `docs/agent/premarket.md`（`premarket_sc_v1` 字段、三锚、行动上限）
[4] `dragon_quant/tests/test_skill_content.py::TestPremarketSkill`（八段顺序、关键词、300 行上限）

## Findings (facts only)

- 现有模板的八段是：信息截止与覆盖 / 市场与账户总闸门 / 三类锚与四种情景 / 主线与角色复核 / 候选逐只条件表 / 组合与执行约束 / 开盘后验证时间表 / 无法交易与盘后归因。[1]
- 现有模板以“事实 | 工作假设 | 行动”三栏贯穿，每一段末尾要求“待验证/证伪”；动作必须取 `allowed_actions` 与资格、仓位、veto、`data_quality`、账户闸门的交集。[1][2][3]
- 现有模板的候选表 5A 有 12 列（代码/角色/血缘/eligibility/position_pct/position_basis/冻结预期/公告等级/竞价指标/status/09:30 后验证/持仓退出），5B 有四态条件表。[1]
- 现有模板的“开盘后验证时间表”只有 09:25 / 09:30–10:00 / 之后 / 盘后四行，且每行是概括性描述，不是逐时点检查清单。[1]
- 现有模板没有“一页结论 / 今日作战姿态 / 不做什么 / 盘中更新规则”这些段落；也没有 per-stock 的 trigger/invalidation/size/time-window 紧凑卡。[1]
- 测试硬编码八段标题、关键词与 300 行上限；任何模板重构都必须同步更新测试。[4]

## Analysis

现有模板的强项是**可审计、抗幻觉、合规**：事实与假设分离、时点分离、动作交集、不发明买点。这些不能丢。

“对盘中帮助不大”的结构性原因：

1. **按数据域组织，而不是按交易时间线组织**：读者要先读完信息截止、闸门、锚、主线，才看到候选表；盘中需要的是“现在几点、看什么、满足什么就做什么”。[1]
2. **候选表过宽**：12 列事实适合审计，不适合在 09:25 的几分钟内做决策；缺少“每只一张卡”的 trigger / invalidation / size / time window。[1]
3. **四态条件没有落到时间点**：`confirmed/weakened/invalidated/unknown` 有定义，但没有写成 09:25、09:30–09:35、09:35–10:00 的逐时点观察项与确认方式。[1]
4. **没有“作战姿态”与“风险预算”的一页结论**：用户要自己从闸门、仓位、组合、锚中推导今天该进攻、试错还是防守。[1]
5. **没有“不做什么”清单**：外部模板把“什么情况下什么都不做”当成核心字段；现有模板只有“无法交易与盘后归因”一节，且偏事后。[1]
6. **没有盘中更新协议**：什么新证据可以改变计划、什么不能（未核媒体、价格噪声、浮盈亏），以及更新后如何留痕。[1]
7. **确认方式缺失**：外部模板用突破回踩、VWAP/均价线、开盘区间、量能确认；现有模板只写“分时量价、收复/跌破、板块共振”，没有可观察的确认事件清单。[1]

## Support snippets / paraphrases for top claims

- Claim: 八段顺序固定且被测试锁定。
  Source: [4]
  Support: `test_premarket_template_has_eight_fixed_sections` 断言标题顺序。
- Claim: 候选表 12 列。
  Source: [1]
  Support: 5A 表头逐列列出。
- Claim: 开盘后时间表只有四行。
  Source: [1]
  Support: 第 7 段表格四行。

## Conflicts / unresolved issues

- 重构模板会与现有测试冲突；必须在同一 commit 更新测试与文档。
- 项目硬约束“不输出买卖指令、不发明买点”与“盘中可执行”之间存在张力；解决方式是“条件剧本 + 动作上限”，不是放开交易指令。

## Leads discovered

- 可保留八段数量以降低迁移成本，但把内容重排为“结论 → 情景 → 主线 → 作战卡 → 时间表 → 持仓 → 更新纪律 → 归因”。
- 可用“作战卡”替代 5A 的宽表；宽表移到附录或按需展开。

## Gaps

- 未统计用户实际盘中阅读路径；结论来自模板结构与外部范式对照。
