# Approved Source Registry

> 评分为启发式：Authority / Recency / Relevance / Depth。项目代码用于证明“当前实现/已有集成”，不用于证明在线 SLA、数据许可或策略有效性。
> 代码快照：dragon-quant `2204f0fe31eeaef4ae8b6874827dc786c7c2b7ff`；stock-ana `e953f4759e940598f1d88fa3855df725a5e2f890`。stock-ana 相对路径以 `/Users/bing/Repo/stock-ana` 为根，dragon-quant 相对路径以 `/Users/bing/Repo/dragon-quant` 为根。

[1] 万战寻道复盘转写稿｜`docs/bilibili/prep/20240712 淘股吧万战寻道：手把手教你如何复盘 直播回放（建议收藏）.md`｜8.0/10｜方法原文，2024-07-12。
[2] 短线狂徒张三复盘转写稿｜`docs/bilibili/prep/99% 的散户不会正确复盘！高手炒股复盘全套流程！普通人亏钱的根源全在这.md`｜7.75/10｜方法原文，2026-04-28。
[3] 无名游资主线方法转写稿｜`docs/bilibili/prep/【复盘方法论】如何找出主线并且判断能不能参与？.md`｜8.0/10｜方法原文，2026-05-10。
[4] 华东导弹复盘转写稿｜`docs/bilibili/prep/游资华东导弹：如何进行高质量复盘.md`｜7.5/10｜方法原文，2024-06-23。
[5] 江浙陈某复盘转写稿｜`docs/bilibili/prep/只要每日重复这7个步骤，半个月，让每一个散户都学会专业复盘.md`｜7.0/10｜方法原文，2024-04-05。
[6] prep/auction 模块权威｜`docs/agent/prep-auction.md`｜10/10｜当前项目契约，2026-10-28 访问。
[7] prep 编排与计算｜`dragon_quant/review_account/prep/service.py`、`compute.py`｜10/10｜当前代码，2026-10-28 访问。
[8] 当前评级与展示｜`dragon_quant/review_account/verdicts.py`、`advice.py`｜10/10｜当前代码，2026-10-28 访问。
[9] prep 采集与健康｜`dragon_quant/review_account/prep/collectors.py`｜10/10｜当前代码，2026-10-28 访问。
[10] 策略与 Skill 契约｜`dragon_quant/review_account/strategy/STRATEGY.md`、`skills/dragon-quant/SKILL.md`、`references/outputs.md`｜10/10｜当前契约，2026-10-28 访问。
[11] 五维评分权威｜`archived_md/score/评分器Refactor.md`｜10/10｜当前 scan 规则，2026-10-28 访问。
[12] THS provider｜`dragon_quant/infra/provider/ths.py`｜10/10｜当前代码，2026-10-28 访问。
[13] Quote 与 prep 落库字段｜`dragon_quant/infra/models.py`、`review_account/prep/collectors.py`｜10/10｜当前代码，2026-10-28 访问。
[14] 龙头镜像字段｜`dragon_quant/infra/assets/dragons_mirror.py`｜10/10｜当前代码，2026-10-28 访问。
[15] 历史复盘设计｜`archived_md/review_account/复盘方法论.md`｜7.8/10｜项目历史设计，非当前状态。
[16] 集合竞价方法论｜`archived_md/auction/集合竞价强度判断方法论.md`｜8.5/10｜项目设计/PIT 约束。
[17] stock-ana 东财专题池 provider｜`/Users/bing/Repo/stock-ana/tools/stock-data-apis/src/providers/eastmoney/index.js`｜9/10｜真实集成代码，2026-10-28 访问；在线未实测。
[18] fuyao 特色数据 provider 与离线文档｜`stock-data-apis/src/providers/fuyao/groups/specialData.js`、`docs/fuyao/llms-full.txt`｜8.5/10｜接口契约；在线与鉴权未实测。
[19] lbtt provider 与情绪聚合｜`stock-data-apis/src/providers/lbtt/index.js`、`backend/routes/emotion.js`｜9/10｜真实集成代码与派生口径。
[20] stock-ana 情绪引擎｜`view/src/features/tools/emotionReview/engine.ts`、`config.ts`｜8.5/10｜已有 L/V/S 算法；参数样本有限。
[21] 板块成员与 schema｜`tools/market_sync/import_sector_members.js`、`storage/database/schema.sql`｜9/10｜证明当前成员及不可重建边界。
[22] 龙虎榜 providers｜stock-ana `providers/eastmoney`、`providers/ths-lhb`、`providers/fuyao` 与离线文档｜8.5/10｜真实集成/契约，历史深度部分未证实。
[23] 资金 providers｜stock-ana `stock-sdk` 本地实现、`backend/utils/fetch_dark.js`、`providers/iwencai`｜8.5/10｜免登录与登录态链路。
[24] 竞价 providers｜stock-ana `providers/numcat`、fuyao 离线竞价文档、`backend/services/auctionService.js`｜8.5/10｜字段与快照时点证据。
[25] 筹码链路｜stock-ana `backend/routes/stockSdk.js`、`view/.../chipCore.ts`、本地 `stock-sdk`｜8.5/10｜日 K + CYQ 二次模型，不是真实持仓分布。
[26] stock-ana 数据边界补充｜fuyao `llms-full.txt`、`providers/fuyao/groups/marketDumps.js`、`tools/market_sync/seed_stock_daily_from_dump.js`｜8.5/10｜未开放接口与历史重建边界。
[27] 当前公告 provider｜`dragon_quant/infra/provider/tdx.py:959-989`｜10/10｜巨潮标题/类型/日期/URL 当前实现。

[28] 东财涨停板公开页面｜https://quote.eastmoney.com/ztb/detail#type=ztgc｜9.0/10｜官方公开页面，2026-10-28 匿名实测。
[29] 东财涨停池公开 JSON｜https://push2ex.eastmoney.com/getTopicZTPool（20261027 与旧日期对照样本）｜8.3/10｜官方域名未文档化接口，字段及历史空池实测。
[30] THS dataapi 与问财查询页｜https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool；https://www.iwencai.com/result｜8.2/10｜匿名精简字段与登录墙实测。
[31] Tushare THS 涨跌停榜单文档｜https://tushare.pro/document/2?doc_id=355｜8.8/10｜官方字段、8000 积分、2023-11-01 起。
[32] 同花顺认证复盘文章｜http://t.10jqka.com.cn/pid_617651388.shtml｜7.3/10｜公开摘要与长图形态实测。
[33] 深交所交易公开信息汇总/席位 JSON｜https://www.szse.cn/disclosure/deal/public/index.html 及 `1842_xxpl`/`1842_detal` 端点｜9.8/10｜交易所结构化数据，2026-10-28 实测。
[34] 上交所每日交易公开信息｜https://www.sse.com.cn/disclosure/diclosure/public/dailydata/｜9.5/10｜交易所公开动态表。
[35] 腾讯 L1 与东财最新分笔｜https://qt.gtimg.cn/q=sz000001；https://push2.eastmoney.com/api/qt/stock/details/get｜8.2/10｜当前快照/最新少量分笔，非历史序列。
[36] 上证所信息网络产品价格｜https://www.sseinfo.com/services/cpfwjg/｜10/10｜官方收费边界。
[37] 东财资金流与方法说明｜https://push2his.eastmoney.com/api/qt/stock/fflow/kline/get；http://emdesk.eastmoney.com/pc_activity/pages/viptrade/pages/link/zjl.html｜8.6/10｜字段与供应商算法口径。
[38] 巨潮公告入口与查询服务｜https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice；https://www.cninfo.com.cn/new/hisAnnouncement/query｜9.5/10｜官方公告元数据与 PDF。
[39] 筹码与流通口径文档｜https://akshare.akfamily.xyz/data/stock/stock_flow.html#stock-cyq-em；https://tushare.pro/document/2?doc_id=293；https://tushare.pro/document/2?doc_id=32｜8.2/10｜模型、历史与积分边界。
[40] 解禁/停复牌/监管官方或公开页面｜https://data.eastmoney.com/dxf/default.html；https://www.sse.com.cn/disclosure/dealinstruc/suspension/stock/；http://www.csrc.gov.cn/gansu/c104571/c1143898/content.shtml｜9.0/10｜公开页面与动态表。
[41] 宏观与指数官方页面｜https://data.stats.gov.cn/easyquery.htm?cn=A01；https://www.chinamoney.com.cn/chinese/sddsintigy/；https://www.csindex.com.cn/#/indices/family/detail?indexCode=000300｜9.0/10｜动态 DOM/WAF 实测边界。
[42] 东财全 A/日 K/概念板块公开 JSON｜https://push2.eastmoney.com/api/qt/clist/get；https://push2his.eastmoney.com/api/qt/stock/kline/get｜8.3/10｜行情与板块字段样本，未文档化。
