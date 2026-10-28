# Research Self-Review — Round 1

```yaml
review_type: research
round: 1
verdict: APPROVE
summary: "调研证据足以支撑阶段 2；唯一 MAJOR 是尚未正式展开三条技术路径的对比，已转为阶段 2 必做项。"
findings:
  - severity: MAJOR
    dimension: "候选方案"
    location: "第四节 对产品方向的研究性建议"
    finding: "报告给出了推荐产品原则，但没有在同一节正式比较至少三种技术路径。"
    evidence: "当前报告主要呈现分层确定性方案；Agent-first、多模态 OCR 路线和单总分静态建议路线只在数据/限制部分零散出现，未形成统一比较。"
    recommendation: "阶段 2 至少比较：①确定性证据图谱+角色评分+条件 playbook；②Agent/多模态主导复盘；③单总分静态建议基线。统一按可复现性、产品表达、数据成本、未来函数和下游风险比较，并说明推荐理由。"
  - severity: MINOR
    dimension: "事实准确性"
    location: "证据评估辅助输出 source-eval.json"
    finding: "启发式 source_evaluator 因缺少结构化元数据将 24 个外部来源评为 drop，不能单独作为来源接受依据。"
    evidence: "被 drop 的来源包含 Tushare 官方文档、深交所、巨潮和上证所信息网络；main 已对关键来源做 WebFetch 实测，task-d/task-d2 有原始证据。"
    recommendation: "保留人工 claim-fit 审核与实测记录，在 run-summary 登记该限制；不为迎合启发式分数更换更弱来源。"
scorecard:
  critical: 0
  major: 1
  minor: 1
  dimensions_passed: "5/6"
```

## 项目检查清单

- 需求边界：PASS。已确认单票独立仓位、T+1 条件策略、Agent 不编造数据、本阶段不写实现代码。
- 文档/路由：PASS。读取 prep-auction、development、factor、STRATEGY、ASSETS_ARCHITECTURE 及相关权威；现状结论以代码为准。
- Mermaid：N/A（调研报告）；阶段 2 产品/技术方案的架构、数据流与状态机必须配图。
- 依赖约束：PASS。未安装依赖、未修改产品代码、未写系统 Python。
- 数据与凭据：PASS。未读取或输出用户 Cookie/token；登录态仅列为后续用户配合项。
- Git 约束：PASS。未 commit/push；仅新增调研工作区文档。

## Gate Decision

无 CRITICAL，按工作流 **APPROVE → 阶段 2**。MAJOR/MINOR 转入 `known_issues`，其中技术路径比较是阶段 2 的强制验收项。
