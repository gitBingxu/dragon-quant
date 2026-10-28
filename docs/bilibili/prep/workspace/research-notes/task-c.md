---
task_id: c
role: 代码证据调查员（研究线程 C）
objective: 盘点 stock-ana 中可免费自动获取、可服务 A 股盘后复盘与 T+1 策略的真实数据能力，区分上游原始字段、本地派生、历史可重建性与接入风险
status: complete
confidence: medium
sources_found: 31
acceptance_met: yes
---

# stock-ana 数据能力代码证据笔记

调查日期：2026-10-28。调查方式为静态阅读 `/Users/bing/Repo/stock-ana` 的 provider、backend、离线接口文档、schema 与派生算法；**未运行任何真实网络采集，也未把 UI 展示字段反向当成上游能力**。第三方包只读取本地已安装源码。下文“可历史”优先指代码明确支持按日期/区间请求；若保留深度没有代码或文档证据，均标为“深度未证实”，不等于可完整重建历史。

## Sources

- [S1] 东财涨停专题池 provider，真实主机、六类池、原始字段字典与排序/空池风险 | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/eastmoney/index.js:69-104,146-158,259-304` | PRIMARY_CODE
- [S2] fuyao special-data provider，涨停/跌停/炸板/梯队/热榜/龙虎榜端点 | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/fuyao/groups/specialData.js:53-98` | PRIMARY_CODE
- [S3] fuyao 离线文档，涨停池参数与原始字段 | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/docs/fuyao/llms-full.txt:7019-7118` | OFFLINE_API_DOC
- [S4] fuyao 离线文档，炸板池与近 30 日梯队字段、每板位最多 4 只 | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/docs/fuyao/llms-full.txt:7202-7277,7279-7382` | OFFLINE_API_DOC
- [S5] 通达信天梯 lbtt provider，URL、请求体、鉴权与压缩约束 | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/lbtt/index.js:1-91` | PRIMARY_CODE
- [S6] lbtt 明细分类与梯队/晋级率本地计算、单次跨度约束 | `/Users/bing/Repo/stock-ana/backend/routes/emotion.js:125-229,569-594` | PRIMARY_CODE
- [S7] 情绪复盘辅助源、当日延迟、成交额兜底及回退逻辑 | `/Users/bing/Repo/stock-ana/backend/routes/emotion.js:232-369,403-539` | PRIMARY_CODE
- [S8] 涨跌停统一门面及三种来源的 ST 口径差异 | `/Users/bing/Repo/stock-ana/backend/routes/limit.js:1-17,60-158` | PRIMARY_CODE
- [S9] stock-sdk 后端代理，筹码、异动、涨停池与资金流路由和缓存 | `/Users/bing/Repo/stock-ana/backend/routes/stockSdk.js:1-55,131-185,187-267,288-358` | PRIMARY_CODE
- [S10] 本地 `stock-sdk@2.4.4` 实现，东财 K 线、fflow、push2ex 真实 URL 与请求/字段映射（压缩文件均为第 1 行） | `/Users/bing/Repo/stock-ana/node_modules/stock-sdk/dist/chunk-2WCFJCSN.js:1`；许可证声明 `/Users/bing/Repo/stock-ana/node_modules/stock-sdk/package.json:134-146` | VENDORED_PRIMARY_CODE
- [S11] tmdx 板块清单与成分股同步，真实本地 URL 与全量替换语义 | `/Users/bing/Repo/stock-ana/tools/market_sync/import_sector_members.js:1-113,116-143` | PRIMARY_CODE
- [S12] 行业/概念实时涨跌榜与盘后 DB 回退 | `/Users/bing/Repo/stock-ana/backend/services/conceptMoversService.js:17-41,90-127,129-200` | PRIMARY_CODE
- [S13] 板块资金与 DDE 聚合算法 | `/Users/bing/Repo/stock-ana/tools/lib/sectorAggregate.js:1-104` | PRIMARY_CODE
- [S14] SQLite 数据模型与不可重建说明 | `/Users/bing/Repo/stock-ana/storage/database/schema.sql:1-163` | PRIMARY_SCHEMA
- [S15] 龙头股本地 CLI 读取及盘后 scan 生产链 | `/Users/bing/Repo/stock-ana/backend/routes/dragonQuant.js:4-49,51-135`；`/Users/bing/Repo/stock-ana/tools/tasks/pipeline_config.js:301-315` | PRIMARY_CODE
- [S16] 同花顺市场宽度 provider 与后端归一化 | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/ths-quote-center/index.js:1-71`；`/Users/bing/Repo/stock-ana/backend/services/marketTempService.js:24-90` | PRIMARY_CODE
- [S17] 本地市场强弱/趋势/净流入比例计算 | `/Users/bing/Repo/stock-ana/backend/services/marketService.js:9-44` | PRIMARY_CODE
- [S18] 情绪 L/V/S 派生算法、权重、锚点与阶段规则 | `/Users/bing/Repo/stock-ana/view/src/features/tools/emotionReview/engine.ts:207-317,440-579`；`/Users/bing/Repo/stock-ana/view/src/features/tools/emotionReview/config.ts:340-377,434-469` | PRIMARY_ALGORITHM
- [S19] fuyao 龙虎榜文档，一年窗口与榜单原始字段 | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/docs/fuyao/llms-full.txt:2335-2466` | OFFLINE_API_DOC
- [S20] 东财龙虎榜 RPT 与同花顺席位 provider | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/eastmoney/index.js:212-257`；`/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/ths-lhb/index.js:1-79`；`/Users/bing/Repo/stock-ana/backend/routes/lhb.js:19-96` | PRIMARY_CODE
- [S21] 龙虎榜席位去重、Top5、全榜额与“游资相关净额”本地派生 | `/Users/bing/Repo/stock-ana/view/src/services/lhbDetail.ts:87-189,192-252,255-312` | PRIMARY_ALGORITHM
- [S22] 数猫竞价原始字段、key 与快照落库时点 | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/numcat/index.js:1-75`；`/Users/bing/Repo/stock-ana/backend/services/auctionService.js:68-99,101-169`；每日配额说明 `/Users/bing/Repo/stock-ana/README.md:62-65` | PRIMARY_CODE
- [S23] fuyao 竞价快照与短线基准文档 | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/docs/fuyao/llms-full.txt:1978-2054,2056-2123` | OFFLINE_API_DOC
- [S24] tmdx 逐笔历史、缓存与大资金本地聚合 | `/Users/bing/Repo/stock-ana/backend/services/tickFetcher.js:1-181`；`/Users/bing/Repo/stock-ana/backend/services/bigMoneyService.js:13-24,150-229,232-330` | PRIMARY_ALGORITHM
- [S25] 东财 darktrade 采集、字段落库与资金结构派生 | `/Users/bing/Repo/stock-ana/backend/utils/fetch_dark.js:43-55,61-132,265-277,323-378`；`/Users/bing/Repo/stock-ana/backend/services/darkpoolService.js:225-336` | PRIMARY_CODE
- [S26] 问财 DDE 登录态、字段映射与防封；Puppeteer 只用于刷新令牌 | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/iwencai/index.js:1-103`；`/Users/bing/Repo/stock-ana/backend/services/thsDataService.js:26-36,162-245,247-336`；`/Users/bing/Repo/stock-ana/backend/services/cookieRefresher.js:1-24,75-189` | PRIMARY_CODE
- [S27] 筹码 CYQ 本地算法与二次派生指标 | `/Users/bing/Repo/stock-ana/backend/routes/stockSdk.js:7-23,131-185`；`/Users/bing/Repo/stock-ana/node_modules/stock-sdk/dist/chunk-W625VPPB.js:1`；`/Users/bing/Repo/stock-ana/node_modules/stock-sdk/dist/chunk-2WCFJCSN.js:1`；`/Users/bing/Repo/stock-ana/view/src/features/tools/chipAnalysis/chipCore.ts:1-24,388-560,627-866` | VENDORED_PRIMARY_ALGORITHM
- [S28] 短线侠全球行情与腾讯分时，以及量能/全球代码映射 | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/duanxianxia/index.js:1-52`；`/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/gtimg/index.js:1-43`；`/Users/bing/Repo/stock-ana/backend/services/marketFooterService.js:74-185` | PRIMARY_CODE
- [S29] fuyao 资讯事件和主力资金“未开放外部接入”的明确文档；基金资讯 provider 边界 | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/docs/fuyao/llms-full.txt:2127-2175,7384-7437`；`/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/fuyao/groups/funds.js:1-43` | OFFLINE_API_DOC / PRIMARY_CODE
- [S30] fuyao 全市场日 K/复权 dump、复权事件与历史灌数边界 | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/fuyao/groups/marketDumps.js:1-24`；`/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/fuyao/groups/corporateActions.js:1-18`；`/Users/bing/Repo/stock-ana/tools/market_sync/seed_stock_daily_from_dump.js:1-39,128-180` | PRIMARY_CODE
- [S31] 热度榜主备源与历史热榜一年窗口 | `/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/fuyao/groups/specialData.js:9-50,68-91`；`/Users/bing/Repo/stock-ana/backend/services/heatService.js:38-84`；`/Users/bing/Repo/stock-ana/tools/stock-data-apis/docs/fuyao/llms-full.txt:6830-6883` | PRIMARY_CODE / OFFLINE_API_DOC

## Findings (facts only)

### 一、直接接口：涨停池、梯队、封单、炸板、首次封板

1. **东财专题池是字段最完整的逐股涨停事件源之一。** 真实上游为 `https://push2ex.eastmoney.com/getTopicZTPool`，另有 `getYesterdayZTPool`、`getTopicQSPool`、`getTopicCXPool`、`getTopicZBPool`、`getTopicDTPool`；请求带 `date=YYYYMMDD`、固定公开 `ut` 与 Referer，不要 Cookie/登录。原始字段包括代码/名称、最新价与涨停价、涨跌幅、成交额、流通/总市值、换手率、`fund` 封板资金、`fbt` 首次封板时间、`lbt` 最后封板时间、`zbc` 炸板次数、`lbc` 连板数、`zttj` 涨停统计、`hybk` 行业。现役路由有 30 秒缓存。可按日期请求，但历史保留深度未证实；非交易日也可能 `rc=0 + 空池`，同值排序不稳定。[S1][S9]
2. **东财个股/全市场异动可补充封板事件时间。** `stock-sdk` 实际请求 `push2ex.eastmoney.com/getAllStockChanges` 与 `/getStockChanges`，事件类型含 `limit_up_seal`、`limit_up_open`、竞价上涨/下跌等；个股指定 `date`，历史聚合最多 60 天。映射字段为时间、事件类型/代码、价格、涨跌幅、附加信息与 `v`，不是封单队列或完整委托簿。[S9][S10]
3. **fuyao 涨停池提供封单额与峰值封单额。** `GET https://fuyao.aicubes.cn/api/a-share/special-data/limit-up-pool`，需 `X-api-key`，按 `date_ms` 查询；原始字段有 `is_st/is_new`、价格/涨幅、`limit_up_time`（文档仅称“涨停时间”，不能擅自等同东财 `fbt`）、涨停原因、`continue_day_cnt/text`、`seal_money` 当前封单额、`max_seal_money` 峰值封单额。历史保留深度未写明；后端自动分页，单页 200、最多 50 页。[S2][S3]
4. **fuyao 炸板池与梯队是不同数据形状。** 炸板池 `/limit-break-pool` 原始字段为 `open_times` 开板次数、换手率、成交额、价格/涨幅；梯队 `/limit-up-ladder` 无入参，仅近 30 个交易日，2/3/4/5/6/7+ 六档且**每板位最多 4 只**，含 `board_num`、`seal_nextday`、`sign_level`。因此该梯队不是完整市场梯队；运行时只把它作为 lbtt 失败后的降级源。[S4][S7]
5. **lbtt 天梯一次返回涨停/炸板/跌停三态，适合重建市场统计，但当前证明的字段不含封单额或首次封板时间。** 上游 `http://hot.icfqs.com:7615/TQLEX?Entry=CWServ.cfg_fx_lbtt&RI=`，POST `Params:[cat,d1,d2]`，无 Cookie/登录，但必须 Origin/Referer/UA，且压缩头处理脆弱。当前代码实际消费 `rqex` 日期、`ZQDM` 代码、`lbts` 连板数、`ztlb` 三态；单次跨度明确 ≤1 年。ST 口径为天然剔除，北交所保留。[S5][S6][S8]
6. **现有本地派生覆盖完整市场梯队和晋级率。** lbtt `ztlb=1/3/other` 被归为封住涨停/炸板/跌停，逐日计算涨停、跌停、炸板、连板家数、最高板、各板位家数、封板率，以及昨日首板/连板/各板位到今日仍涨停的晋级率。这里的“封板率”是家数比例，**不是封单额**。[S6]
7. **三源 ST 语义不能混用。** 门面明确标注 fuyao 池含 ST、lbtt 天然剔 ST、东财 `st_included=null`；东财计数回退也属含 ST 的降级口径。[S7][S8]

### 二、直接接口：板块、题材、龙头与热度

8. **tmdx 提供行业/概念清单、成分股和实时涨跌榜。** 本机 `127.0.0.1:8000/api/v1/board-mac/list?board_type=HY|GN`、`/members?board_symbol=...`、`/ranking?...sort_by=change_pct`，无需外部登录，但依赖本地 tmdx 服务。成分同步会删除旧关系后重插；`sector_members` 表没有日期列，因此它是**当前成员快照**，不能据此证明历史成员归属。实时榜失败后回空，盘后则读 `stock_daily`。[S11][S12][S14]
9. **板块行情与资金指标有本地历史，但成员历史是缺口。** 现有聚合按当前 `sector_members` 将个股 `dark/bright/netflow` 求和、`activity` 求均值，将 DDE 净额/买卖额/散户数求和、DDE 净量求均值，写入带日期的行业/概念 `stock_daily` 行。重算旧日若使用今天的成员表会产生历史成员口径偏差。[S13][S14]
10. **“龙头股”不是新的外部上游，而是本地 dragon-quant 产物。** 盘后流水线执行 `dragon-quant scan --top 5 --force` 写入其自身 DB；HTTP 只执行 `dragon-quant dragons [--date] [--true-only]` 读取，输出代码、名称、概念、`is_true_dragon` 等。历史可查范围取决于本地 dragon-quant DB 已有扫描记录，不是 stock-ana provider 能独立重建的能力。[S15]
11. **lbtt 概览提到热门题材，但当前运行时未形成可用题材因子。** cat=2 的注释指出 `N009~N014` 为题材列且当日有值，然而 `parseOverview` 实际只取 `N007` 成交额、`N002/N003` 涨跌家数，合并输出也未带题材；逐列语义与可用性未证实。[S7]
12. **人气热度有实时与历史两种口径。** dq 热股实时主源 `https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock` 免 key，fuyao 为备份；字段为排名、热度、排名变化、涨跌幅等。fuyao `/hot-stock-list-history?date=yyyy-MM-dd` 需 key，仅支持一年、每天最多 30 条。实时主源的 `list_type=normal` 与 `skyrocket` 是不同榜单，不能互换。[S31]

### 三、直接接口与本地计算：市场宽度、情绪、量能

13. **同花顺 dq 提供实时市场宽度。** `GET /fuyao/up_down_distribution/distribution/v2/realtime` 返回上涨/下跌/平盘/涨停/跌停/停牌、更新时间和 11 档涨跌分布；`/fuyao/market_analysis_api/chart/v1/get_chart_data?chart_key=limit_up_minute` 返回 241 点的上涨、下跌、涨停、跌停分时。无需 Cookie，但要 UA/Referer/X-Requested-With；代码无历史日期入参，所以该源只证实实时/当日能力。后端缓存 10 秒。[S16]
14. **lbtt 概览可补历史宽度与成交额。** cat=2 原始 `N002/N003` 为涨/跌家数、`N007` 为成交额，按日期区间可取；当前情绪 API 没把 up/down 输出。当日量价行存在延迟，收盘后缺少成交额时才用腾讯 `sh000001 + sz399001` 分时最后一条累计额兜底，并要求日期一致且 ≥15:00；注释记录两源约 1% 系统差。[S7]
15. **腾讯分时与短线侠提供量能事实而非完整宏观数据。** 腾讯 `/appstock/app/minute/query?code=...` 每行是 `HHMM 价格 累计量(手) 累计额(元)`；短线侠 `/api/getLastAmount` 给昨日沪深分钟级成交额基线。二者均无登录；腾讯只证实当前交易日分时，短线侠只证实昨日基线。[S28]
16. **本地还有三套“宽度/强弱”派生，不能与真实涨跌家数混称。** `stock_daily` 计算 `power>1` 的强势比例、三个趋势信号至少两个为多的比例、`netflow>0` 的比例；情绪引擎则计算炸板率、涨停环比、最高板下降、L 温度、V 动能和阶段/子状态。L 权重为涨停 0.25、最高板 0.25、首板晋级 0.20、炸板率 0.30，并带绝对锚点、护栏、回滞和驻留规则。[S17][S18]
17. **长历史标定表与在线运行时是两条链。** `emotion_series` 保存约 980 日标定样本，但构建脚本与原料已缺失，schema 明确说历史样本无法重建；运行时 `/limit/emotion/review` 走 lbtt 在线聚合而非该表。[S7][S14]

### 四、直接接口：龙虎榜

18. **fuyao 榜单适合按日拉全市场龙虎榜。** `/api/a-share/special-data/dragon-tiger-list` 需 `X-api-key`，支持 `all/org/hot_money` 和一年内 `date=yyyy-MM-dd`。股票字段含买卖/净额及比率、概念、涨跌幅、人气、上榜原因、1/3 日榜、机构净额与机构买卖家数、游资相关净额；游资榜按游资名称聚合。它是盘后榜单，不是逐笔席位流。[S19]
19. **东财 RPT 提供个股盘后指标和席位。** `https://datacenter-web.eastmoney.com/api/data/v1/get` 的 `RPT_DAILYBILLBOARD_DETAILSNEW` 返回上榜理由、换手率、涨跌幅、收盘价、全榜买/卖/净额、成交额、流通市值、上榜后 1/3/5 日表现；`RPT_OPERATEDEPT_TRADE_DETAILSNEW` 返回营业部名称/代码与 `ACT_BUY/ACT_SELL/NET_AMT`。按股票和日期过滤、不要登录，历史深度未证实；代码明确警告东财席位可能漏“机构专用”。[S20]
20. **同花顺 `lhclientdate` 用来补完整机构/沪深股通席位。** 实际上游 `http://data.10jqka.com.cn/api/lhclientdate/date/YYYY-MM-DD/code/6位代码`，不要 Cookie，但需后端代理及 UA/Referer；返回 `analysis[].type/buy/sell`，席位有买卖额、净额、持股数、成交数量和席位 ID。按日期请求，历史深度未证实，且是明文 HTTP。[S20]
21. **“游资相关净买额”是本地启发式，不是上游字段。** 业务层按 `yybid/source_yyb_id` 去重，取买卖 Top5；全榜买卖额以东财 RPT 为准；把“沪深股通专用/机构专用”剔除后，对剩余席位净额求和得到 `hotMoneyNet`，因此它只是普通营业部候选口径。[S21]

### 五、资金：直接接口、本地逐笔与需登录态 DDE

22. **东财个股日级资金流可直接取。** `stock-sdk` 请求 `https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get`，`lmt=0`，支持 daily/weekly/monthly；映射日期、收盘、涨跌幅、主力/超大单/大单/中单/小单净流入及各自百分比。不要登录，后端缓存 60 秒、最多展示 250 条；实际上游可给的历史深度未由项目代码验证。[S9][S10]
23. **东财 darktrade 是另一条按日全市场采集链。** `https://quotederivates.eastmoney.com/datacenter/darktrade?...&date=YYYYMMDD&StartPage=...` 不要 Cookie，需浏览器头且响应 GBK 解码；原始数值键 `4/16/13/14/6/7/8/11` 被映射为代码、名称、价格、涨幅、dark、bright、netflow、activity。可按 DATE 拉取并分页落库，历史深度未证实。[S25]
24. **资金结构比率与分位是本地计算。** 代码将 `bright` 视为与东财 mainNetInflow 等价（注释称实测），计算 1/5 日主力净额率、DDE 净额率、散户数变化及全市场分位；注释同时明确“只做事实+分位，不做评分”，不能据此宣称资金有正向预测力。[S25]
25. **tmdx 逐笔支持历史日大资金复盘。** 本机 `/api/v1/transaction/history?market=...&code=...&date=...&start=...&count=800` 翻页；原始价格、手数、买卖方向被映射，`amount` 缺失时按 `price × vol × 100` 推算。历史日及当日 15:30 后可永久磁盘缓存；深度取决于 tmdx/本地缓存，项目未证明全市场多年历史。现有派生包括 242 分钟桶、大买/大卖额、分位自适应大单阈值、Top2000 候选和分钟级对倒嫌疑。[S24]
26. **问财 DDE 属于“需登录态 + headless 刷令牌”，不是免登录公共接口。** POST `https://www.iwencai.com/gateway/urp/v7/landing/getDataList`，必须 cookie + `hexin-v`；按日期分页取 DDE 大单净额、买卖金额、净量、散户数量、热度排名并落库。采集每页 100 条，2–4 秒随机延迟、指数退避和 cookie 轮换，说明反爬与稳定性成本较高。[S26]
27. **Puppeteer 不直接渲染提取业务表格。** 它只访问问财首页，等待 `v` cookie，再把新反爬令牌合并进旧登录态 cookie；旧 `u_ukey/u_dpass` 等登录态被保留。因此该能力同时落在“headless 渲染”和“需登录态”，不是无登录自动化。[S26]
28. **fuyao 文档里的 A 股主力资金不能算可用能力。** `/api/a-share/capital-flow/*` 虽展示快照/历史接口契约，但同一文档明确标注“暂未开放外部接入、示例不可用于外部调用”。[S29]

### 六、竞价

29. **数猫 `auc_kp` 字段最贴近现有竞价库。** POST `https://numcat.net/api/reference-proxy/stock/auc-kp`，body 需个人 `apikey`，支持 `tradedate=YYYYMMDD`。原始字段包括竞价涨幅/当日涨幅、竞价净额/成交额/量比/换手、昨日换手、自由流通市值、`fd_amount`、收盘、连板数、昨日连板数、ST、主题，以及 `ztwme/ztwme20/pre_ztwme/prev_ztwme` 等委买相关字段。README 说明 token 每日次数有限。[S22]
30. **数猫历史“日期结果”与历史“盘中快照”必须区分。** 每次请求落库时使用本机当前 `HHMMSS` 作为 `snap_time`，并按是否早于 09:20 标 `before_920/after_920`。所以补拉旧 `tradedate` 只能证明上游该日期的返回，不能重建当日 09:20 前后真实快照序列；只有已及时采集的本地快照才保留真实采集时点。[S22]
31. **fuyao 竞价快照是当前 live/final，不带历史日期。** `/api/a-share/auction/snapshot` 需 key、每次最多 100 个代码，字段为竞价价/涨幅/量/额/未匹配量/换手/相对昨日量/量比、昨收/开盘/最新价与流通市值；因此可自动取当前终态，但不能按该端点重建历史竞价。另一 `/short-term-benchmark` 支持 `date`，返回竞价涨幅与“高开/放量”等标签，历史深度未说明。[S23]
32. **tmdx 逐笔可事后识别竞价成交，但不是完整竞价盘口。** 逐笔保留 `buyorsell=8` 集合竞价标记；实际 09:25 也可能标成中性，需要按 09:15–09:30 时间并入首桶。它不含数猫/fuyao 的未匹配量或完整委买队列。[S24]

### 七、筹码：本地计算，不存在已证实的“上游筹码分布接口”

33. **stock-sdk 先取东财日 K，再在本地跑 CYQ。** 后端注释与包代码均支持这一事实：K 线上游为 `https://push2his.eastmoney.com/api/qt/stock/kline/get`，CYQ 用 150 个价格桶、OHLC 均价和换手率衰减生成获利盘、平均成本、70%/90% 成本区间与集中度、可选直方图。后端参数 `days=1..400`、`range=0..400`、复权方式与直方图模式；15:30 后按自然日冻结缓存，盘中 60 秒。[S9][S27]
34. **筹码可以按算法重算历史，但它是模型估计。** 只要相同历史 OHLC、换手率、复权与窗口输入可取得，就能重算同一 CYQ 序列；它不是交易所真实持仓账户分布，也不是上游直接返回的“筹码事实”。[S27]
35. **现有二次因子较多且均为本地派生。** 包括 C 集中度 60 日分位、P 获利盘、M 重心位移、B 低位净变化、S 主峰稳定天数，以及吸筹/拉升/派发/出清/沉淀标签；另有网格重采样、四区筹码迁移、离场率和买盘成本错配“偏空度”。这些因子依赖相邻日直方图和当日 K 线，不应误写成东财原生字段。[S27]

### 八、公告、新闻、宏观：已证实缺口

36. **没有已实现的 A 股公告/个股新闻 provider。** 全库仅见 fuyao `funds.news`（基金资讯）和文档中的资讯事件库；后者 `/api/news/events/search` 明确“暂未开放外部接入”，AI 客户端自动带登录身份。因此不能把文档示例中的事件标题、摘要、评级、行业/概念关联当成免费可自动调用能力。[S29]
37. **复权事件不是公告新闻。** `corporate-actions/adjustment-factors` 只提供现金分红、送股等复权因子事件，不能替代公告正文、公告时间或风险文本。[S30]
38. **宏观只证实全球市场行情快照，不证实宏观事件/指标库。** `https://duanxianxia.cn/vendor/stockdata/global.json` 无登录，原始 `f2/f3/f12/f14` 被映射为价格、涨跌幅、代码和名称，覆盖日经、黄金、原油、道指、纳指、恒生、美元指数、离岸人民币、A50 等；无日期参数，属于当前行情而非 CPI、利率、政策、宏观日历历史。[S28]

### 九、基础行情与历史重建边界

39. **fuyao 日 K dump 是长历史基础原料。** `/api/dump/market-dumps/daily-k/download-url` 返回短时 S3 预签名链接，文档/代码注释称 10 年全市场未复权日 K；另有近 10 日 dump 和复权事件 dump。需 `X-api-key` 的直连实现已存在，但源码注释也记录鉴权文档冲突（Cookie vs key），所以当前可用性未做在线确认。[S30]
40. **dump 只能重建基础 OHLC/成交额，不能重建全部 stock_daily 因子。** 灌数脚本明确：`change` 无法从 dump 重建，DDE、强势度、量比、换手等 26 个通达信专有因子历史不可重建；复权因子需另一链路补齐。[S30]
41. **日常 `power` 等 TDX 因子来自 XLS 导入而非已证实的免费自动历史接口。** `stock_daily` 与 `kline_daily` 可保存历史结果，但原始事实依赖盘后“全部Ａ股_YYYYMMDD.xls”。有文件时可重导入并计算 `z_composite`，没有文件时不能由 dump 自动补齐这些专有字段。[S14][S30]

## Analysis (your synthesis)

- **覆盖最完整的盘后链路是“东财/fuyao/lbtt 涨停生态 + tmdx/本地日 K + 东财/问财资金 + 数猫竞价 + 本地 CYQ”。** 其中原始上游字段与派生因子边界清晰：东财给首封/封板资金，fuyao 给当前/峰值封单额，lbtt 给全市场三态统计，情绪与筹码标签则是本地计算。[S1][S3][S5][S6][S27]
- **“有 date 参数”不等于“历史可重建”。** 东财池、darktrade、同花顺席位、数猫都支持日期参数，但代码未证明保留深度；实时市场宽度、当前板块成员、fuyao 当前竞价快照更无历史入口。已明确的历史边界只有 lbtt 单次 ≤1 年、fuyao 龙虎榜/热榜 1 年、fuyao 梯队 30 日、stock-sdk 个股异动 60 日、fuyao 日 K dump 10 年等。[S1][S4][S6][S10][S11][S19][S22][S23][S30][S31]
- **同一指标必须保留来源口径。** 涨停计数有含 ST、剔 ST、未标 ST 三种口径；成交额 lbtt 与腾讯存在系统差；fuyao 梯队每板位截 4 只，不能替代 lbtt 全量梯队；东财席位可能缺机构，需要同花顺补。[S4][S7][S8][S20]
- **最脆弱的不是计算公式，而是非正式上游与时点。** 公开页面接口没有 SLA，依赖 Referer、固定 ut、GBK、压缩协商、WAF 镜像或 cookie；竞价与板块成员还要求及时快照，事后补拉无法恢复当时状态。[S1][S5][S11][S16][S22][S26]
- **许可要分代码与数据两层。** `stock-sdk` 包声明 ISC，只说明包代码许可；调查中没有找到东财、同花顺、通达信天梯、数猫、短线侠上游数据的再分发/商业使用授权证据，不能把“免 key”解释为“可自由再分发”。[S10]

## Support snippets / paraphrases for top claims

- Claim: 东财专题池原生提供首次封板、最后封板、封板资金、炸板次数和连板数。
  Source: [S1]
  Support: provider 的字段字典逐项把 `fbt/lbt/fund/zbc/lbc` 定义为上述语义，并给出六个 push2ex 池路径；不是从 UI 字段反推。
- Claim: lbtt 的封板率和晋级率均为本地统计，不是封单数据。
  Source: [S6]
  Support: `parseDetail` 只消费日期、代码、`lbts/ztlb`，以家数算封板率，并通过相邻交易日代码集合算晋级率。
- Claim: 筹码分布是日 K 驱动的本地模型。
  Source: [S27]
  Support: `stock-sdk` 在 150 桶上按 OHLC 与换手率迭代衰减/分配筹码，再计算成本区间和获利盘；后端只是调用该算法。
- Claim: 数猫补拉旧日期不能恢复历史盘中采集时点。
  Source: [S22]
  Support: `saveAuctionSnapshot` 用运行时当前时间生成 `snapTime` 和 `phase`，与 `tradedate` 分开保存。
- Claim: fuyao 的资讯事件与 A 股主力资金文档不构成可用上游。
  Source: [S29]
  Support: 两处文档都明确写“暂未开放外部接入”，资金示例还注明不可用于外部调用。
- Claim: 基础日 K 可灌 10 年，但 TDX 专有因子与情绪长样本不能随之重建。
  Source: [S14][S30]
  Support: schema 与灌数脚本分别声明 `emotion_series` 构建链缺失、26 个专有因子无法由 dump 重建。

## Conflicts / unresolved issues

- **lbtt 历史深度表述不一致。** 前端注释有“深度可达数年”，后端明确“单次跨度 ≤1 年”，当前路由又只展示 5–60 日。本文采用更保守、可执行的后端约束；跨多次请求能否覆盖数年未验证。[S6][S7]
- **fuyao market-dumps 鉴权文档冲突。** 源码注释记录子文档写登录 Cookie、overview 写 `/api + X-api-key`；实现选择 key，但本次未联网验证。[S30]
- **实时宽度与情绪宽度的口径不同。** dq 是实时全市场分布，lbtt 是天然剔 ST 的天梯生态，`stock_daily` 强弱比例又是自定义趋势/资金口径，不能直接拼成一条无源标签的时间序列。[S6][S16][S17]
- **东财资金“历史深度”未证实。** `lmt=0` 和日/周/月参数说明能请求序列，但项目没有固定最早日期或覆盖率测试。[S9][S10]
- **darktrade 字段语义主要靠既有映射与注释。** 原始响应使用数字键，未找到独立上游字段字典；本文只确认当前代码映射，不扩展解释其他数字键。[S25]
- **板块热门题材只有注释级证据。** `N009~N014` 没被解析或返回，逐列字段含义和稳定性仍未知。[S7]

## Leads discovered

- fuyao 离线完整文档 `/Users/bing/Repo/stock-ana/tools/stock-data-apis/docs/fuyao/llms-full.txt` 是后续核对参数/历史窗口的首要来源，且含“未开放”标记，能避免把文档展示当可用接口。
- 本地 `stock-sdk@2.4.4` 压缩实现保留了东财真实 URL 和 CYQ 公式，可继续用于核对 K 线参数、资金流字段与事件覆盖。
- `storage/database/schema.sql` 已明确多张表的重建边界；评估盘后复盘资产时应把“原始可重取”“只能保留快照”“本地派生可重算”分开登记。
- 后续若要提高置信度，最有价值的是离线样本/测试或经批准的只读连通性验证：东财池最早日、darktrade 最早日、数猫旧日期返回语义、lbtt 全列字典、fuyao dump 实际鉴权。

## Gaps

- 本次按要求未运行真实网络，因此只证明**代码实现与离线文档中的能力**，不证明 2026-10-28 当下端点仍可访问、未改版或未触发风控。
- 未找到上游数据许可、再分发权、商用条款或正式 SLA；公开接口的长期使用与数据发布合规性仍未确定。
- 未验证东财专题池、RPT、fflow、darktrade、同花顺席位、数猫、暗盘/逐笔的最长历史深度与缺失率。
- 未找到 A 股公告正文/公告时间、个股新闻、宏观指标/政策事件的可外部自动调用实现；也未发现 OCR 数据链路。
- 未证实历史板块成员、历史竞价多时点快照、完整委托队列/封单时序的可重建来源。
- `emotion_series` 与 TDX 专有因子存在明确历史重建缺口；任何“全历史重算”结论都不能覆盖这些字段。
