---
task_id: d2
role: Lead Spot-check
objective: 独立复核影响技术选型的外部关键证据
status: complete
confidence: high
sources_found: 6
acceptance_met: yes
---

## Sources
[1] Tushare `limit_list_ths` 官方文档 https://tushare.pro/document/2?doc_id=355
[2] 上证所信息网络价格页 https://www.sseinfo.com/services/cpfwjg/
[3] 深交所 `1842_xxpl` 官方 JSON（2026-10-27）
[4] 巨潮公告入口 https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice
[5] 东财 `getTopicZTPool` 20261027 样本
[6] 东财 `getTopicZTPool` 20251027 对照样本

## Findings (facts only)
- main 经 WebFetch 独立复核：`limit_list_ths` 文档列出封单量/额、首末封、打开次数、原因，要求 8000 积分，历史从 2023-11-01，约 16 点更新；量/额单位均写“元|个”。[1]
- 官方价格页列出 Level-2 历史 12 万元/年、Level-1 历史 3 万元/年。[2]
- 深交所官方响应含 `metadata/data`，2026-10-27 样本 `recordcount=52`，行含日期、代码、简称、成交金额/量、披露理由。[3]
- 巨潮公开页提供公告标题、发布时间、代码/简称与“查看全文”PDF 入口；页面有登录链接但公告浏览未显示必须登录。[4]
- 东财 20261027 样本首行有 `fbt/lbt/fund/zbc/lbc`，20251027 样本 `tc=0,pool=[]` 且 `qdate=20261027`。[5][6]

## Analysis (your synthesis)
- 关键结论可由主线程独立证据支撑：免费当前/近日字段与长历史/完整盘口之间存在明确边界。
- 旧日期空池不能用于推导精确保留天数，只能否定“任意多年免费历史可回放”。

## Support snippets / paraphrases for top claims
- Claim: 历史封单/盘口应自采冻结或走积分/付费路径。
  Source: [1][2][5][6]
  Support: 直接历史字段有积分门槛，官方历史盘口收费，免费池旧样本为空。

## Conflicts / unresolved issues
- 本次 main 复核使用 WebFetch，与 subagent headless 的 20261027 `tc` 数值不同（动态交易日数据/分页或采集时间差可能），但字段与旧日期空池结论一致；报告不引用当日 tc 作为市场统计。

## Leads discovered
- 生产 collector 应保存原始响应哈希、请求日期、实际行日期与分页总数，避免 qdate 误验收。

## Gaps
- 未使用登录或付费账号，未验证这些路径的实际字段值。
