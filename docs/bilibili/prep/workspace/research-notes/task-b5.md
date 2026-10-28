---
task_id: b5
role: Field Availability Analyst
objective: 区分封单等字段的 provider 可采、prep 保存与快照暴露层次
status: complete
confidence: high
sources_found: 4
acceptance_met: yes
---

## Sources
[1] dragon_quant/infra/models.py | 本地源码 | PRIMARY CODE | 2026-10-28
[2] dragon_quant/infra/provider/tdx.py | 本地源码 | PRIMARY CODE | 2026-10-28
[3] dragon_quant/review_account/prep/collectors.py | 本地源码 | PRIMARY CODE | 2026-10-28
[4] dragon_quant/infra/assets/dragons_mirror.py | 本地源码 | PRIMARY CODE | 2026-10-28

## Findings (facts only)
- 统一 Quote 已包含 bid1_price、bid1_volume、ask1_volume、volume、amount、float_market_cap、volume_ratio、limit_up/down 等字段。[1:37-63]
- TDX quote 适配器已映射 bid_price/bid_volume 与 main_net_amount。[2:224-229]
- prep 的 fund_flow collector 当前只持久化 main_net_amount、pct、turnover_rate、amount 四项，没有保存 Quote 的封单、流通市值、量比等其余字段。[3:256-264]
- dragons 镜像提供 composite_score、board_count、OHLC、pct、turnover、amount、market_cap、concepts 等，但不提供五维子分或封单明细。[4:142-157]

## Analysis (your synthesis)
- 新 prep 因子有相当部分可通过扩展现有收盘 Quote 资产获得，不必一开始引入网页 OCR。
- 数据采集存在不等于可历史重建：只有当日实际落盘的 Quote 才能证明当时封单；用今天的实时字段回填历史会造成时点污染。

## Support snippets / paraphrases for top claims
- Claim: 扩展收盘 Quote 资产比新网页采集更优先。
  Source: [1][2][3]
  Support: 字段已在统一模型和 TDX 适配器存在，仅 prep 持久化未消费。

## Conflicts / unresolved issues
- 需要真实运行验证 TDX 盘后 bid_volume 是否稳定非空、单位是否始终为手；模型注释部分沿用 gtimg 口径，不能仅凭字段存在判定数据质量。

## Leads discovered
- 对每项新因子记录 provider_field、saved_asset、snapshot_field 三层血缘。

## Gaps
- 未在真实网络运行采集，不对当前线上字段覆盖率作断言。
