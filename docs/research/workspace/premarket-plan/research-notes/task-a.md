---
task_id: a
role: Macro/news evidence researcher
status: complete
confidence: high
---

# 外围与新闻信源

## Sources
[1] FRED SP500/NASDAQCOM/DGS10 | https://fred.stlouisfed.org/ | 官方汇编日频
[2] SGX CN futures JSON | https://api.sgx.com/derivatives/v1.0/contract-code/CN?order=asc&orderby=delivery-month&category=futures | 官方合约/会话/时点
[3] Nikkei official daily CSV | https://indexes.nikkei.co.jp/nkave/historical/nikkei_stock_average_daily_en.csv | 官方EOD
[4] KRX Open API / snapshot | https://global.krx.co.kr/ | 官方，快照缺时点需降级
[5] US Treasury yield XML | https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=2026 | 官方CMT日频
[6] LBMA prices | https://www.lbma.org.uk/prices-and-data | 官方基准与许可
[7] 财联社电报 | https://www.cls.cn/telegraph | 媒体线索，JS页，无公开API
[8] 国务院/发文机关/交易所公告 | https://www.gov.cn/zhengce/zhengceku/ ; https://www.sse.com.cn/disclosure/listedinfo/announcement/ ; https://www.szse.cn/disclosure/listed/notice/index.html ; https://www.cninfo.com.cn/ | 一手
[9] X API rate limits | https://docs.x.com/x-api/fundamentals/rate-limits | 社交只作线索

## Findings
- 美股正式隔夜收盘必须有美国观测日与完成 session；FRED 可匿名取日频，第三方 Yahoo 仅作备选。[1]
- 日经/KOSPI 在 A 股盘前是盘中数据，不得写收盘；官方 EOD 与盘中快照要分开。[3][4]
- A50 必须保留合约月、日/夜会话、最后价/结算类型、last-update-time，不能压成一个报价。[2]
- 10Y 官方 CMT 是日频指示口径，不是实时债券成交；黄金现货/LBMA/GC 不能混算。[5][6]
- 财联社是媒体，不是政策/公司公告一手来源；无公开电报 API，不逆向私有接口。[7]
- 政策与公司事实回原文/交易所/巨潮；X 只作待核实线索。[8][9]

## Analysis
- 外部数据应分“正式收盘、盘中快照、日频基准、媒体转述”四类，各自带时点和证据等级。
- 多转载不等于多源确认；同一公告跨站按 ID/PDF 去重，更正保留版本链。
- 缺外围或原文时只降低市场判断，不得编数值或把旧闻刷新。

## Gaps
- 各站长期 SLA、配额和再分发许可未保证；实际运行需按当时可用性降级。
