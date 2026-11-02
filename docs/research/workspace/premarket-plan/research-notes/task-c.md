---
task_id: c
role: Repository contract mapper
status: complete
confidence: high
---

# 快照字段与缺口

## Sources
[1] prep service/compute/review context/orchestrator | dragon_quant/review_account/prep/** | v0.8
[2] auction serialize/context/features/events | dragon_quant/review_account/auction/** | v2
[3] prep-auction owner | docs/agent/prep-auction.md | 表/时点契约
[4] factor owner | docs/agent/factor.md | 因子血缘与重复计数

## Findings
- prep 权威是 review_candidates；strategy_context 是白名单投影，不含完整 review.anchors 正负锚、coverage、risk_level 等。[1]
- T-1 macro 只有 jp_nikkei/cn_a50/ust10y/gold 日频，不含 T 日新闻或 T-1 16:40 后海外变化。[1]
- auction analysis_json 有完整 raw_factors/risk_events/evidence/windows；CLI snapshots/strategy_context 仅部分字段，转换 null/false 与完整 veto evidence 会丢失。[2]
- prep frozen_at/auction as_of 是阶段常量，不是实际采集时间；实际 created_at 需存储 meta。快照覆盖/重算不是 vintage 账本。[1][2][3]
- 缓存命中只覆盖已存资产、prep_snapshot_date=null，不能反推完整 T-1 池。[2]

## Analysis
现有 prep --date 会计算并写快照，不适合作为冻结 T-1 的只读来源；auction CLI 投影也不足以支撑完整预案。
推荐只读上下文适配器输出完整 prep result_json 与 auction analysis_json + 表 meta，而不是 SQL 或重算。
现有数据不能推出全市场竞价、T 日新闻/外围、实时均线、真实订单行为或实际成交仓位。

## Gaps
- height_d3_change、D3 seal 输入、完整板块成员/逐code入池来源等有已知缺失，必须显示 unavailable。
- 默认 CLI 无法完整审计 quote 抓取时刻，缓存预案需标运行时点和原快照时点。
