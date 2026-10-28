---
task_id: d
role: External Data Source Investigator
objective: 核实 A 股复盘/T+1 常见数据能否免费、自动化获取，并区分结构化接口、本地派生、DOM/OCR、登录态、付费与不可靠边界
status: complete
confidence: medium
sources_found: 31
acceptance_met: yes
---

> 调查日期：2026-10-28。以下“实测”均指本次匿名访问样本；公开网页内部 JSON 不等于有稳定性承诺的官方开放 API。未使用任何登录账号、付费 token 或既有项目实现。

## Sources

[1] 东方财富：涨停板行情（官方公开页面） | <https://quote.eastmoney.com/ztb/detail#type=ztgc> | Aut:8 Rec:10 Rel:9 Dep:9 = 9.0 | OFFICIAL PUBLIC PAGE / HEADLESS DOM | accessed 2026-10-28

[2] 东方财富：涨停股池公开 JSON（`getTopicZTPool`，20261027 样本） | <https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=5&sort=fbt%3Aasc&date=20261027> | Aut:8 Rec:10 Rel:9 Dep:6 = 8.3 | OFFICIAL-DOMAIN LIVE JSON / UNDOCUMENTED | accessed 2026-10-28

[3] 东方财富：炸板股池公开 JSON（`getTopicZBPool`，20261027 样本） | <https://push2ex.eastmoney.com/getTopicZBPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=2&sort=fbt%3Aasc&date=20261027> | Aut:8 Rec:10 Rel:9 Dep:6 = 8.3 | OFFICIAL-DOMAIN LIVE JSON / UNDOCUMENTED | accessed 2026-10-28

[4] 同花顺：涨停池 `dataapi` 匿名响应（20261027 样本） | <https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page=1&limit=3&field=199112&filter=HS%2CGEM2STAR&date=20261027&order_field=330329&order_type=0> | Aut:8 Rec:10 Rel:9 Dep:5 = 8.0 | OFFICIAL-DOMAIN LIVE JSON / UNDOCUMENTED | accessed 2026-10-28

[5] 同花顺问财：涨停原因、首封、封单量查询结果页 | <https://www.iwencai.com/result?w=2026%E5%B9%B410%E6%9C%8827%E6%97%A5%E6%B6%A8%E5%81%9C%EF%BC%8C%E6%B6%A8%E5%81%9C%E5%8E%9F%E5%9B%A0%EF%BC%8C%E9%A6%96%E6%AC%A1%E5%B0%81%E6%9D%BF%E6%97%B6%E9%97%B4%EF%BC%8C%E5%B0%81%E5%8D%95%E9%87%8F&querytype=stock> | Aut:8 Rec:10 Rel:9 Dep:8 = 8.8 | OFFICIAL PUBLIC PAGE / LOGIN WALL OBSERVED | accessed 2026-10-28

[6] Tushare Pro：同花顺涨跌停榜单 `limit_list_ths` 官方文档 | <https://tushare.pro/document/2?doc_id=355> | Aut:7 Rec:10 Rel:9 Dep:9 = 8.8 | VENDOR OFFICIAL API DOC / POINTS REQUIRED | accessed 2026-10-28

[7] 同花顺认证账号“涨停板复盘”：2026-04-23 复盘文章 | <http://t.10jqka.com.cn/pid_617651388.shtml> | Aut:7 Rec:7 Rel:8 Dep:7 = 7.3 | OFFICIAL-DOMAIN EDITORIAL / HTML + IMAGES | accessed 2026-10-28

[8] 东方财富：全 A 实时列表公开 JSON（字段与 5926 条总数样本） | <https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=3&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m%3A0%2Bt%3A6%2Cm%3A0%2Bt%3A80%2Cm%3A1%2Bt%3A2%2Cm%3A1%2Bt%3A23%2Cm%3A0%2Bt%3A81%2Bs%3A2048&fields=f12%2Cf14%2Cf2%2Cf3%2Cf5%2Cf6%2Cf8%2Cf20%2Cf21> | Aut:8 Rec:10 Rel:9 Dep:6 = 8.3 | OFFICIAL-DOMAIN LIVE JSON / UNDOCUMENTED | accessed 2026-10-28

[9] 东方财富：个股历史日 K 公开 JSON（平安银行样本） | <https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=0.000001&klt=101&fqt=0&lmt=3&end=20500000&fields1=f1%2Cf2%2Cf3%2Cf4%2Cf5%2Cf6&fields2=f51%2Cf52%2Cf53%2Cf54%2Cf55%2Cf56%2Cf57%2Cf58%2Cf59%2Cf60%2Cf61> | Aut:8 Rec:10 Rel:9 Dep:6 = 8.3 | OFFICIAL-DOMAIN LIVE JSON / UNDOCUMENTED | accessed 2026-10-28

[10] 上海证券交易所：每日成交概况公开 JSON（20261027） | <https://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C&PRODUCT_CODE=01%2C02%2C03%2C11%2C17&type=inParams&SEARCH_DATE=2026-10-27> | Aut:10 Rec:10 Rel:10 Dep:7 = 9.3 | EXCHANGE LIVE JSON / REFERER REQUIRED / UNDOCUMENTED | accessed 2026-10-28

[11] 东方财富：概念板块榜公开 JSON | <https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=3&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m%3A90%2Bt%3A3&fields=f12%2Cf14%2Cf2%2Cf3%2Cf6%2Cf8%2Cf62%2Cf104%2Cf105%2Cf128%2Cf136> | Aut:8 Rec:10 Rel:9 Dep:6 = 8.3 | OFFICIAL-DOMAIN LIVE JSON / UNDOCUMENTED | accessed 2026-10-28

[12] 东方财富：概念板块成分公开 JSON（BK1099 样本） | <https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=3&po=1&np=1&fltt=2&invt=2&fid=f3&fs=b%3ABK1099&fields=f12%2Cf14%2Cf2%2Cf3%2Cf5%2Cf6%2Cf8%2Cf20%2Cf21> | Aut:8 Rec:10 Rel:9 Dep:6 = 8.3 | OFFICIAL-DOMAIN LIVE JSON / UNDOCUMENTED | accessed 2026-10-28

[13] 深圳证券交易所：竞价交易公开信息汇总 JSON（2026-10-27） | <https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1842_xxpl&TABKEY=tab1&txtStart=2026-10-27&txtEnd=2026-10-27&random=0.1234> | Aut:10 Rec:10 Rel:10 Dep:9 = 9.8 | EXCHANGE STRUCTURED JSON / EXCEL METADATA | accessed 2026-10-28

[14] 深圳证券交易所：交易公开信息席位明细 JSON（000530 样本） | <https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1842_detal&TABKEY=tab1%2Ctab2&DQRQ=2026-10-27&ZQDM=000530&ZBDM=0903> | Aut:10 Rec:10 Rel:10 Dep:9 = 9.8 | EXCHANGE STRUCTURED JSON | accessed 2026-10-28

[15] 上海证券交易所：每日交易公开信息（主板） | <https://www.sse.com.cn/disclosure/diclosure/public/dailydata/> | Aut:10 Rec:10 Rel:10 Dep:8 = 9.5 | EXCHANGE PUBLIC PAGE / DYNAMIC TABLE | accessed 2026-10-28

[16] 腾讯行情：平安银行公开 Level-1 文本快照 | <https://qt.gtimg.cn/q=sz000001> | Aut:8 Rec:10 Rel:9 Dep:6 = 8.3 | PUBLIC VENDOR LIVE TEXT / GB18030 | accessed 2026-10-28

[17] 东方财富：个股最新分笔公开 JSON（`details/get`） | <https://push2.eastmoney.com/api/qt/stock/details/get?secid=0.000001&fields1=f1%2Cf2%2Cf3%2Cf4&fields2=f51%2Cf52%2Cf53%2Cf54%2Cf55&pos=-5> | Aut:8 Rec:10 Rel:9 Dep:5 = 8.0 | OFFICIAL-DOMAIN LIVE JSON / UNDOCUMENTED | accessed 2026-10-28

[18] 上证所信息网络有限公司：产品服务价格（Level-2 与历史数据） | <https://www.sseinfo.com/services/cpfwjg/> | Aut:10 Rec:10 Rel:10 Dep:10 = 10.0 | EXCHANGE-AFFILIATED OFFICIAL PRICE LIST | accessed 2026-10-28

[19] 东方财富：个股历史资金流公开 JSON（日频样本） | <https://push2his.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=3&klt=101&secid=0.000001&fields1=f1%2Cf2%2Cf3%2Cf7&fields2=f51%2Cf52%2Cf53%2Cf54%2Cf55%2Cf56%2Cf57%2Cf58%2Cf59%2Cf60%2Cf61%2Cf62%2Cf63%2Cf64%2Cf65> | Aut:8 Rec:10 Rel:9 Dep:6 = 8.3 | OFFICIAL-DOMAIN LIVE JSON / UNDOCUMENTED | accessed 2026-10-28

[20] 东方财富：Level-2 资金流与“逐单还原”方法说明 | <http://emdesk.eastmoney.com/pc_activity/pages/viptrade/pages/link/zjl.html> | Aut:8 Rec:8 Rel:10 Dep:9 = 8.8 | VENDOR OFFICIAL METHODOLOGY | accessed 2026-10-28

[21] AKShare：`stock_cyq_em` 筹码分布文档（数据源指向东方财富） | <https://akshare.akfamily.xyz/data/stock/stock_flow.html#stock-cyq-em> | Aut:6 Rec:10 Rel:8 Dep:8 = 8.0 | THIRD-PARTY WRAPPER DOC | accessed 2026-10-28

[22] Tushare Pro：每日指标 `daily_basic` 官方文档 | <https://tushare.pro/document/2?doc_id=32> | Aut:7 Rec:10 Rel:9 Dep:9 = 8.8 | VENDOR OFFICIAL API DOC / POINTS REQUIRED | accessed 2026-10-28

[23] Tushare Pro：每日筹码及胜率 `cyq_perf` 官方文档 | <https://tushare.pro/document/2?doc_id=293> | Aut:7 Rec:10 Rel:9 Dep:9 = 8.8 | VENDOR OFFICIAL API DOC / POINTS REQUIRED | accessed 2026-10-28

[24] 巨潮资讯：公告查询入口与 `hisAnnouncement/query` 服务 | <https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice> ; <https://www.cninfo.com.cn/new/hisAnnouncement/query> | Aut:10 Rec:10 Rel:10 Dep:8 = 9.5 | OFFICIAL DISCLOSURE / JSON METADATA + PDF | accessed 2026-10-28

[25] 东方财富：限售股解禁资料公开页面 | <https://data.eastmoney.com/dxf/default.html> | Aut:8 Rec:10 Rel:9 Dep:8 = 8.8 | VENDOR PUBLIC PAGE / STRUCTURED TABLE | accessed 2026-10-28

[26] 东方财富：限售解禁公开 JSON（`RPT_LIFT_STAGE`） | <https://datacenter-web.eastmoney.com/api/data/v1/get?sortColumns=FREE_DATE&sortTypes=-1&pageSize=2&pageNumber=1&reportName=RPT_LIFT_STAGE&columns=SECURITY_CODE%2CSECURITY_NAME_ABBR%2CFREE_DATE%2CCURRENT_FREE_SHARES%2CABLE_FREE_SHARES%2CLIFT_MARKET_CAP%2CFREE_RATIO%2CNEW%2CB20_ADJCHRATE%2CA20_ADJCHRATE%2CFREE_SHARES_TYPE%2CTOTAL_RATIO%2CNON_FREE_SHARES%2CBATCH_HOLDER_NUM&filter=%28FREE_DATE%3E%3D%272026-10-27%27%29%28FREE_DATE%3C%3D%272026-11-30%27%29> | Aut:8 Rec:10 Rel:9 Dep:6 = 8.3 | OFFICIAL-DOMAIN LIVE JSON / UNDOCUMENTED | accessed 2026-10-28

[27] 上海证券交易所：股票和可转债停复牌信息 | <https://www.sse.com.cn/disclosure/dealinstruc/suspension/stock/> | Aut:10 Rec:10 Rel:10 Dep:8 = 9.5 | EXCHANGE PUBLIC DYNAMIC TABLE | accessed 2026-10-28

[28] 中国证监会：资本市场违法失信信息公开查询平台说明 | <http://www.csrc.gov.cn/gansu/c104571/c1143898/content.shtml> | Aut:10 Rec:8 Rel:10 Dep:9 = 9.3 | REGULATOR OFFICIAL PAGE | accessed 2026-10-28

[29] 国家统计局：国家数据月度查询 | <https://data.stats.gov.cn/easyquery.htm?cn=A01> | Aut:10 Rec:10 Rel:10 Dep:6 = 9.0 | OFFICIAL DYNAMIC PAGE / INTERNAL QUERY ENDPOINT | accessed 2026-10-28

[30] 中国外汇交易中心/中国货币网：政府债券利率历史数据 | <https://www.chinamoney.com.cn/chinese/sddsintigy/> | Aut:10 Rec:10 Rel:10 Dep:8 = 9.5 | OFFICIAL DYNAMIC PAGE | accessed 2026-10-28

[31] 中证指数有限公司：沪深300指数详情 | <https://www.csindex.com.cn/#/indices/family/detail?indexCode=000300> | Aut:10 Rec:10 Rel:9 Dep:8 = 9.3 | INDEX PROVIDER OFFICIAL DYNAMIC PAGE | accessed 2026-10-28

## Findings (facts only)

### 涨停池、连板、炸板、封单与原因

- 东方财富公开页面无需登录即可由 headless 浏览器渲染涨停池，实际列有：代码、名称、涨跌幅、最新价、成交额、流通/总市值、换手率、**封板资金、首次封板时间、最后封板时间、炸板次数、涨停统计、连板数、所属行业**；页面还有日期输入框和涨停/昨日涨停/炸板/跌停等池切换。[1]
- 对应匿名 JSON 返回 `c/n/p/zdp/amount/ltsz/tshare/hs/lbc/fbt/lbt/fund/zbc/hybk/zttj(days,ct)`。例如多伦科技样本含 `fbt=92501`、`lbt=144913`、`fund=114816800`、`zbc=1`、`zttj={days:4,ct:4}`；与网页字段对照可确认首封、末封、封板资金和炸板次数。[1][2]
- 炸板池另有 `ztp`（涨停价）、`fbt`、`zbc` 等字段，可直接得到曾触板但未保持涨停的股票集合；它没有收盘封单字段，因为标的已不再封板。[3]
- 同一东财涨停池接口在请求 20261026 时返回 `tc=55`，但请求 20260105、20251027、20241025 时均返回 `tc=0,pool=[]`；这些旧日期响应的 `qdate` 仍为 20261027。`qdate` 不能当作所请求历史日期的有效性证明。[2]
- 同花顺匿名 `dataapi/limit_up/limit_up_pool` 对 20261027 返回股票代码、名称、`change_tag`、`is_again_limit`、市场及涨跌停计数等，但本次响应**没有**涨停原因、首封时间、封单量或封单额；请求 20241025 返回“date参数不合法”。[4]
- 问财查询“2026年10月27日涨停，涨停原因，首次封板时间，封单量”时，headless 页面出现短信/密码/App 扫码登录框，结果表未展示。故不能把问财当作已验证的免登录批量接口。[5]
- Tushare `limit_list_ths` 文档列出 `lu_desc`、`first_lu_time`、`last_lu_time`、`limit_order`（封单量）、`limit_amount`（封单额）、`lu_limit_order`（最大封单）、`open_num`、连板状态等；历史自 2023-11-01，约每日 16 点更新，但要求 **8000 积分**，不是已验证的零门槛免费历史源。文档对封单量/额单位仅写“元|个”，存在单位歧义。[6]
- 同花顺认证复盘文章正文可见涨停数、开板数、封板效率和主流题材；逐股复盘内容以多张长图嵌入，正文 DOM 没有逐股表格。该样本的逐股原因需要打开其“热点复盘”链接或对长图做 OCR，而不是直接 HTML 表格抽取。[7]

### 市场宽度、成交额与题材板块

- 东财全 A 列表匿名 JSON 本次报告 `total=5926`，样本字段含代码、名称、最新价、涨跌幅、成交量、成交额、换手率、总市值、流通市值。[8]
- 东财历史日 K 返回日期、开高低收、成交量、成交额、振幅、涨跌幅、涨跌额、换手率的序列；因此历史宽度与全市场成交额可由“历史股票全集 × 日 K”本地聚合，不是该接口直接给出的市场宽度字段。[9]
- 上交所官方每日成交概况 JSON 可直接按日期返回 `PRODUCT_CODE/TRADE_DATE/LIST_NUM/TRADE_VOL/TRADE_AMT/TOTAL_VALUE/NEGO_VALUE/AVG_PE_RATE` 等；20261027 样本覆盖主板 A/B、科创板等，匿名请求需要合适的 `Referer`。[10]
- 东财概念榜 JSON 返回板块代码/名称、板块涨幅、成交额、换手率、主力净额、上涨/下跌家数及领涨股；成分接口可按 `BK` 代码返回成分股行情。20261027 样本中概念总数为 516，BK1099 成分总数为 9。[11][12]

### 龙虎榜与席位

- 深交所官方 `1842_xxpl` 直接返回公告日期、证券代码/简称、成交金额、成交量、披露原因及详情参数；响应元数据还声明可导出 `xlsx`。[13]
- 深交所官方 `1842_detal` 对单券直接返回买 1—买 5、卖 1—卖 5 的会员营业部名称、买入金额、卖出金额；本次无需登录即取得完整 JSON。[14]
- 上交所主板每日交易公开信息页面公开可用，注明统计自 2017-01-01，成交只含竞价交易，不含大宗交易和盘后固定价格交易；数据表为动态加载，静态抓取不一定得到表格。[15]

### 资金流、逐笔、盘口与集合竞价

- 腾讯公开文本快照含最新价、昨收、今开、成交量/额，以及买一至买五、卖一至卖五的价格和数量、时间戳、总/流通股本等；这是当前/最近交易快照，不是历史盘口序列。[16]
- 东财 `details/get` 可返回最新若干条“时间、价格、成交量、笔数/方向类标志”文本明细；本次 `pos=-5` 只返回最近 5 条，不能据此证明任意历史交易日逐笔可回放。[17]
- 上证所信息网络公司的官方价格页明确列出 Level-2 展示/非展示许可费用；历史数据产品亦收费，其中 Level-2 历史行情 12 万元/年、Level-1 历史行情 3 万元/年。交易所级历史盘口/逐笔不是免费公开数据。[18]
- 东财日频资金流 JSON 可直接返回按日期排列的五档资金净额序列；样本返回 2026-10-23、26、27 三行。[19]
- 东财官方方法页明确说明：交易所未提供个股实时及盘后资金流数据，各软件自行计算；其 Level-2 方法利用委托挂单、逐笔成交和逐单还原，主力净流入为超大单与大单流入减流出之和。因此“主力资金”是供应商算法指标，不是交易所标识的真实机构账户流。[20]

### 筹码、换手、流通盘

- AKShare 文档的 `stock_cyq_em` 指向东方财富筹码分布，返回日期、获利比例、平均成本、90%/70% 成本区间和集中度，但单次只有近 90 个交易日。[21]
- Tushare `daily_basic` 提供 `turnover_rate`（成交量/无限售流通股数）、`turnover_rate_f`（自由流通股口径）、`float_share` 和 `free_share`，可按历史日获取，但至少需要 2000 积分。[22]
- Tushare `cyq_perf` 文档明确筹码算法为三角分布法并按历史换手衰减搬移，历史从 2018 年开始，至少需要 5000 积分。这里的“筹码分布”是模型估计，不是登记结算公司的逐账户真实持仓。[23]

### 公告、监管、停复牌、业绩、解禁

- 巨潮 `hisAnnouncement/query` 使用匿名 POST（本次带 `Referer` 与 `X-Requested-With`，日期 2026-10-26 至 28、pageSize=3）返回 `totalAnnouncement=8344` 及 `secCode/secName/announcementTitle/announcementTime/adjunctUrl/adjunctType/announcementType` 等；原文路径指向 PDF。[24]
- 巨潮查询结果提供的是公告元数据与 PDF，不是统一结构化财务/风险字段。公告正文仍需本地 PDF 文本抽取；扫描或图片型页面才需要 OCR。[24]
- 东财解禁公开页面列出解禁日期、解禁数量、实际解禁数量、实际解禁市值、占解禁前流通市值比例等；其匿名 `RPT_LIFT_STAGE` JSON 还返回 `FREE_SHARES_TYPE/TOTAL_RATIO/NON_FREE_SHARES/BATCH_HOLDER_NUM`，可做日期过滤。[25][26]
- 上交所停复牌公开表字段包括证券代码、简称、业务类型、停牌起止日、停牌时间、停（复）牌原因；页面为动态表。[27]
- 证监会官方说明指定 `shixin.csrc.gov.cn` 查询行政处罚、市场禁入决定等违法失信信息；它是公开查询平台，不等于已有稳定批量 API。[28]

### 指数与宏观

- 中证指数官方指数详情为动态页面，本次静态正文抓取为空；指数成分/权重等内容应走其动态 DOM、下载文件或正式数据服务，不能认定静态 HTML 直接可得。[31]
- 国家统计局月度查询页的匿名内部 `easyquery QueryData` 请求本次返回 HTTP 403，错误含 `UrlACL/WAF`；公开页面存在不代表内部 JSON 可稳定匿名自动调用。[29]
- 中国货币网有官方政府债券利率历史数据页面，但本次静态抓取只见标题，表格需动态加载；可作为 headless DOM 候选，而非已验证的直接 JSON。[30]
- 对只需日频指数/宏观市场价格的场景，东财历史 K 线的结构化响应形态可复用于有对应证券 ID 的品种，但本次没有逐一验证所有宏观品种代码与历史覆盖。[9]

## Analysis (your synthesis)

### 可得性矩阵（“直接结构化”均含未文档化网页接口，非稳定性保证）

| 数据类别 | 最可行的免费自动化形态 | 历史/登录/付费边界 | 结论 |
|---|---|---|---|
| T 日涨停池、连板梯队 | 东财池直接 JSON；`lbc/zttj` 可直接用，梯队按连板数本地分组。[1][2] | 近日可匿名；老日期样本为空，长历史需每日落库或另购。[2] | **近日直接结构化；长历史不可靠** |
| 涨停原因 | 同花顺复盘文章 DOM + 长图 OCR；问财需登录；Tushare THS 结构化但 8000 积分。[5][6][7] | 免费匿名、稳定、批量且长历史的结构化原因源未证实。 | **登录/OCR/付费三选一，免费直取不足** |
| 炸板、回封、首封/末封 | 东财涨停池与炸板池直接 JSON；回封可由 `zbc>0` 且最终仍在涨停池、`lbt>fbt` 辅助判定。[1][2][3] | “回封”语义需本地规则，不是独立交易所字段。 | **直接字段 + 本地判定** |
| 封单额 | 东财 `fund`/页面“封板资金”直接可读。[1][2] | 是供应商收盘池快照，不是交易所原始委托队列导出。 | **近日直接结构化** |
| 封单量 | 可在明确价格与单位后用 `fund / 涨停价` **估算**；Tushare THS 有直接字段但需 8000 积分。[2][6] | 东财不直接给量；估算依赖 `fund` 口径、价格精度和股/手单位，不能冒充原始封单量。 | **免费仅可近似派生；直接历史字段付费/积分** |
| 封成比 | 本地派生：若采用金额口径，可算 `fund/amount`；若采用量口径，先估封单量再除以成交量。[2] | “封成比”存在金额/成交量口径差异，必须固定公式；并非本接口现成字段。 | **本地派生** |
| 全市场宽度、成交额 | 全 A 日 K/列表本地聚合；上交所总成交额可直接 JSON。[8][9][10] | 历史股票全集、退市股、停牌股、市场规则需自行维护；单日官方成交概况不提供涨跌家数。 | **派生可行，完整历史有工程量** |
| 题材成分与强弱 | 东财概念榜 + 成分直接 JSON；强弱用板块涨幅、上涨家数、成交额/资金流排序。[11][12] | “题材”是供应商分类，跨源不一致；历史成分 PIT 未由当前接口证明。 | **当前直接结构化；历史成分需留快照** |
| 龙虎榜、席位 | 深交所官方汇总/明细 JSON；上交所公开动态表或第三方汇总。[13][14][15] | 只披露触发规则的证券与前几席，不是所有股票完整席位流。 | **官方免费可得** |
| 个股资金流 | 东财日频 JSON。[19][20] | 供应商算法，不是真实账户身份；不同供应商不可直接混算。 | **直接结构化但语义为估算** |
| 当前 Level-1 盘口 | 腾讯五档文本或东财快照，定时采集。[16] | 历史五档队列不会由当前快照补回；官方历史 L1 亦属收费产品。[18] | **当前免费；历史必须自采/付费** |
| 逐笔委托/逐笔成交 | 免费页面仅验证最新少量分笔；完整逐笔与历史走授权 Level-2/历史产品。[17][18] | 免费接口的覆盖、页数与历史不足以支撑完整回放。 | **完整历史免费不可靠** |
| 集合竞价成交与五档/未匹配量 | 9:15—9:25 定时抓当前 L1 快照与分笔并本地落库。[16][17] | 本次盘后调查未实测 9:25 未匹配量字段；历史竞价盘口不能靠当前快照补齐。 | **可自采，历史免费回看未证实** |
| 筹码分布 | 东财经 AKShare 近 90 日；更长历史 Tushare 5000 积分或自行按模型计算。[21][23] | 模型估计而非真实持仓；不同算法不可混用。 | **短窗免费，长史/统一模型有成本** |
| 换手、流通盘 | 日 K 自带换手；当前流通盘可从行情快照；严格历史自由流通口径用 Tushare 2000 积分或公告/PIT 自建。[9][16][22] | “流通 A 股”“无限售流通”“自由流通”“实际流通”不是同一口径。 | **粗口径免费，严格 PIT 较难** |
| 公告、业绩、监管、新闻 | 巨潮 JSON 元数据 + PDF 本地解析；证监会/交易所新闻与监管页面用 DOM。[24][28] | 财务指标和风险事件需二次结构化；图片 PDF 才需 OCR；新闻没有统一免费 API。 | **原文免费，语义结构化需本地处理** |
| 停复牌 | 交易所公开动态表 + 巨潮公告交叉核验。[24][27] | 动态表需 headless 或内部请求解析；盘前 T+1 决策要保留公告发布时间。 | **免费可得，需 DOM/公告解析** |
| 解禁 | 东财直接 JSON/表格，公告原文复核。[24][25][26] | 预计与实际解禁、股东数和股份类型要分开；当前值不自动成为完整 PIT 股本史。 | **直接结构化 + 公告复核** |
| 指数 | 行情日 K 可结构化；官方成分/权重走中证动态页或下载。[9][31] | 指数行情与官方成分 PIT 是两个问题；本次未证明免费完整历史权重。 | **行情易，官方历史成分较难** |
| 宏观 | 官方宏观页面 headless DOM/下载；市场类品种可用日 K。[9][29][30] | 国家数据内部 JSON 本次被 WAF 拒绝；发布日期与值所属期必须分开。 | **公开但自动化稳定性中等** |

### 封单收盘历史快照的专门判断

1. **当前免费池解决的是“供应商保存后的收盘封板资金”，不是完整历史委托簿。** 东财网页和 JSON 能给出封板资金、首末封、炸板次数，但不给原始封单量或逐笔撤单过程。[1][2]
2. **不能把近日可查询外推为任意历史可回放。** 本次 2026-01-05、2025-10-27、2024-10-25 样本均为空，而近一交易日有值；未确定确切保留窗口，只能确定“长历史免费回看不可靠”。[2]
3. **网页登录壁垒确实存在，但不同站点不同。** 东财公开池页面和 JSON 本次无需登录；问财同类复合查询出现登录框；同花顺 `dataapi` 匿名可访问却只给精简字段。不能把“同花顺全部要登录”或“东财也要登录才能看池”作为统一结论。[1][4][5]
4. **若策略必须使用真实历史封单量/队列，证据指向三条路：每日自行冻结快照、使用带积分的 THS 历史字段、采购授权历史 L1/L2。** 盘后多年再用免费当前盘口补采不可行。[6][16][18]
5. **由封板资金换算封单量只能标为估计值。** 需同时保存涨停价、金额单位、股/手换算和供应商口径；封成比也必须声明金额口径或量口径，不能将估算当交易所原始事实。[2][6]

### 非显而易见的边界

- “免费 JSON”与“官方开放 API”不是同义词。东财、腾讯、上交所部分内部接口虽可匿名返回结构化数据，但没有本次可见的 SLA、版本兼容或批量授权承诺；工程上需要 schema 校验、速率限制、降级和原始快照。[2][8][10][16]
- T 日可计算不等于 T 日可完整重建。最脆弱的是**历史盘口、集合竞价未匹配量、历史封单、题材历史成分和真实自由流通 PIT**；这些更依赖当日留痕或付费数据，而不是更复杂的爬虫。[2][12][16][18][22]
- 对复盘字段，原文/算法血缘比“是否有数字”更重要。资金流、筹码、涨停原因、题材分类均含供应商加工，不宜与交易所原始披露混为同一事实层。[7][11][20][23]

## Support snippets / paraphrases for top claims

- Claim: 东财匿名池可直接支撑近日涨停梯队、首末封、炸板与封板资金。
  Source: [1][2][3]
  Support: 页面列名与 JSON 键一一对应；实测样本含 `fbt/lbt/fund/zbc/lbc/zttj`，炸板池另含触板价和炸板次数，未要求登录。

- Claim: 东财历史池不能被视为多年免费档案。
  Source: [2]
  Support: 同一路径仅改日期时，20261026 有 55 条；20260105、20251027、20241025 均为 `tc=0,pool=[]`，且 `qdate` 仍显示 20261027。

- Claim: 同花顺免费匿名完整字段与问财免登录均不能按经验假定。
  Source: [4][5][6]
  Support: `dataapi` 匿名响应只有精简股票与计数信息；问财结果页出现登录框；有完整封单/原因字段的 Tushare THS 文档则明确要求 8000 积分。

- Claim: 同花顺公开复盘文章的逐股内容不是纯 DOM 表格。
  Source: [7]
  Support: 正文可见市场摘要和“热点复盘”链接，逐股复盘以三张图片嵌入，故 OCR 或另开动态页面是实际处理成本。

- Claim: 龙虎榜可用交易所官方结构化数据，不必先依赖第三方。
  Source: [13][14][15]
  Support: 深交所汇总及详情 JSON 给出披露原因、成交信息与买五/卖五席位金额；上交所公开页给出统计起点和竞价交易口径。

- Claim: 免费当前盘口与完整历史逐笔/盘口之间有明确付费边界。
  Source: [16][17][18]
  Support: 腾讯文本含五档当前量价，东财仅实测最近分笔；上证所信息网络官方价格页对 Level-1/Level-2 历史数据分别标价。

- Claim: “主力资金”是算法衍生值。
  Source: [19][20]
  Support: 日频 JSON 可直接返回净额序列，但供应商方法页明确交易所不提供该项，软件基于委托和逐笔数据自行计算。

- Claim: 巨潮公告可自动发现，但正文仍需文档解析。
  Source: [24]
  Support: 匿名 POST 返回代码、标题、时间、公告类型和 PDF 相对路径；返回结构没有统一的风险事件或财务报表正文。

- Claim: 宏观公开网页不等于稳定 JSON API。
  Source: [29][30][31]
  Support: 国家数据内部请求实测 403/WAF；中国货币网和中证指数静态抓取未得到动态表格正文。

## Conflicts / unresolved issues

- Tushare 文档同时给出 `limit_order`（封单量）与 `limit_amount`（封单额），但单位都写成“元|个”；在未取得实际响应前，不能直接断言量是股、手还是其他单位。[6]
- 东财 `qdate` 与请求日期不一致：历史请求响应仍可能显示最近交易日，因此不能仅凭 HTTP 200、`rc=0` 或 `qdate` 判定历史补采成功。[2]
- 同花顺匿名 `dataapi` 的精简响应与 Tushare 文档所列完整 THS 字段形成明显能力差异；可能是权限、接口版本或产品加工差异，本次未登录，无法进一步归因。[4][6]
- 本次旧日期为空证明这些样本不可用，但不能推出东财固定只保留某一精确天数；确切保留窗口仍未知。[2]
- 国家数据 403 可能包含 IP/WAF 策略因素，不能推广为所有网络环境永远不可用；只能标记本次自动化直接访问不可靠。[29]
- 未核实各公开网页的自动化使用条款、商业授权和正式速率限制；“技术可访问”不等于“任何用途均获授权”。

## Leads discovered

- 同花顺登录态下的问财结果与 `dataapi` 完整字段对照：可判断精简响应究竟是匿名限制还是接口改版。[4][5]
- Tushare `limit_list_ths` 实际付费样本：用于核定封单量单位、历史覆盖、与东财 `fund` 的数值口径差。[6]
- 上交所每日交易公开信息的动态 XHR 与表格导出：可进一步减少 headless 依赖。[15]
- 中证指数官方成分/权重下载与历史文件：用于区分免费当前成分和 PIT 历史成分。[31]
- 9:15—9:25 实盘窗口抓包：核对腾讯/东财是否提供集合竞价未匹配量、匹配量及可撤销阶段变化，不能用盘后结果替代。[16][17]
- 巨潮公告分类代码和 PDF 文本层质量抽样：建立“直接文本抽取 / 图片 OCR”路由，而非所有公告一律 OCR。[24]

## Gaps

- 未使用登录账号、付费积分或采购数据，因此登录后字段、付费 API 实际值和授权历史数据覆盖率未验证。
- 未确定东财/同花顺涨停池的精确历史保留窗口，也未做逐日连续完整性审计。
- 未在集合竞价时段实测未匹配量；当前仅能确认免费当前 L1 与最新分笔可访问，历史竞价盘口能力仍是缺口。
- 未对全市场约 6000 只股票做批量速率、分页完整性、退市股覆盖或断点恢复压测；本次只验证样本响应与字段。
- 未逐一验证全球指数、A50、黄金、美债等宏观品种在东财/其他免费源中的证券 ID、交易时段和历史覆盖。
- 未执行同花顺复盘长图 OCR，也未评估其准确率；只确认图片化这一处理要求。
- 未全面盘点新闻门户、券商研报、社交媒体或所有商业终端；本线程优先官方/交易所/巨潮和实际公开页面。
