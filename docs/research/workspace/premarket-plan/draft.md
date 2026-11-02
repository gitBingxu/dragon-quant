# 盘前预案调研：从快照拼接到可审计的盘前决策

> 日期：2026-10-30｜类型：Full report｜ Stakes：High（决策支持，不自动交易）

## Executive Summary

90 分的盘前预案不是“更多股票、更多新闻、更多分数”，而是让每个行动都能回答：**依据哪一条何时可得的信息、满足什么条件、被什么条件否决、何时验证。**

推荐三层结构：

1. **只读快照上下文**：完整读取严格 T-1 prep v0.8 与 T auction v2 及存储 meta，保留冻结预期、正负锚、完整 veto 和真实快照时点；不重算、不写库、不读 SQL 拼装。
2. **当日外部证据**：外围正式收盘/盘中快照、政策与公司原文、媒体线索，全部带来源与 event/publish/effective/crawl 时间。
3. **条件化固定报告**：市场与账户闸门 → 三类锚 → 主线角色 → 候选四态 → 组合约束 → 开盘验证 → 盘后归因。动作只取既有 `allowed_actions` 与仓位、资格、数据质量、veto 的交集。

仅消费当前 `prep --json` 和 `auction --json` 不够：prep 默认会计算并写快照，auction CLI 又裁掉部分完整证据。因此建议增加只读 `premarket` 上下文命令，而不是让 Agent 重算 T-1 或直查 SQLite。

## 1. 数据契约与时点

### 1.1 T-1 prep：冻结计划，不是当日结论

权威输入为 `review_candidates`，而非兼容投影或旧 sentiment。必须保留：[1][2]

- `review.market`：T-1 市场状态、coeff、inputs、证据与环比；
- `review.mainlines`：主线状态、排名、role_map、证据；
- `review.anchors.positive/negative/risk`：短线与情绪正负锚；
- `review_candidates`：角色、资格、单票 position_pct 三态、仓位依据、expectations、gate_trace；
- `strategy_context.feedback_plans`：冻结 gap/ratio/seal_by 与 allowed_actions；
- `macro`：仅作 T-1 日频背景，不冒充 T 日盘前外围；
- `dragon_source` 与候选原始入选血缘，避免把五日窗口当同一截面。

`strategy_context` 可方便消费，但完整预案需要回读 `review` / `review_candidates`，因为投影不包含全部正负锚、风险与原始证据。[1][2]

### 1.2 T auction：竞价证据，不是盘中走势

完整 `analysis_json` 才能提供：effective open 血缘、价格/匹配量/未匹配、转换三态、完整 risk_events/veto、data_quality、窗口和 config。CLI 摘要会丢失部分字段，尤其 conversion null 与 false、完整 veto evidence、pre/tail 窗口。[3][4]

必须区分：

- 09:25 正式终态与 09:30 后验证；
- `from_cache` 与本次新采集；
- 阶段 `as_of`、实际盘口 last_time、表 created_at、Agent 本次运行 run_at；
- `unverified/unavailable`、真实弱、真实中性，三者不可压缩；
- `validation_status=baseline_unvalidated` 与本次数据不可用。

### 1.3 外部证据：每种口径单独标时

- 美股：正式隔夜日 K，保存美国观测日，未完成 bar 不得当收盘。[5]
- 日经/KOSPI：A 股盘前属盘中快照，不写成收盘。[11][12]
- A50：合约月、日/夜会话、最后价/结算类型、原始更新时间缺一不可。[6]
- 10Y：官方 CMT 日频，不写成实时收益率。[7]
- 黄金：LBMA、COMEX GC、XAU 供应商现货分列，不混算。[13]
- 新闻：event_time / publish_time / effective_time / first_seen 全留；旧闻再传播不是新增利好。

## 2. 外围与新闻的证据等级

推荐源顺序：

1. 政策原文、监管/交易所/公司公告与 PDF；
2. 官方指数、财政、央行与合约数据；
3. 官方汇编（如 FRED）与法定披露平台；
4. 财联社等授权媒体；
5. 第三方行情（Yahoo 等，必须标第三方和时点）；
6. 社交帖/博客只作待核实线索。

财联社可用于发现市场新闻，但不能替代政策或公司原文，也不应逆向其私有 APP 接口。多家转载仍是一条来源；公告按官方 ID/PDF 去重，更正/补充保留版本链。无法取得原文时写“媒体转述，未核原文”，不能直接升级为硬否决或确定性方向。[8][9][14][15]

**降级原则**：外围缺失只降低市场情景覆盖；关键公司硬风险原文缺失则该候选不得新建。没有可靠信息不等于利好或中性。

## 3. 固定盘前预案模板

### 第 1 段：信息截止与覆盖

- T、T-1、run_at、信息截止、策略/快照 config；
- prep frozen_at + created_at、auction as_of + last_time + created_at、缓存状态；
- 外围/新闻/快照各自覆盖率与降级；
- 明示本报告不自动交易、不构成收益承诺。

### 第 2 段：市场与账户总闸门

顺序固定：

1. 关键数据是否可用；
2. 新增公告、监管、停牌、不可成交等硬风险；
3. T-1 市场基线与隔夜外围/新闻增量；
4. 账户实际配置的风险约束（不知道就写未知）；
5. 组合与同主线集中暴露。

输出只能是当前规则允许的风险状态：按计划观察、降档、只观察、不新建；不新造评分或收益预测。T-1 单票仓位仅作上限来源，组合不归一、不机械相加。

### 第 3 段：三类锚与四种情景

| 锚 | 选择方式 | 正常 | 异常示例 |
|---|---|---|---|
| 短线 | 主线 leader/vanguard + 最高标正锚，必要时负锚 | 龙头/跟风共振、晋级与封板符合预期 | 高开低走、strong_to_weak、负锚同步下杀 |
| 趋势 | 宽基/风格指数 + 主线 mainstay/capacity | 指数与容量共同承载 | 指数高开但容量不跟、持续受压/放量回落 |
| 情绪 | review 正负锚 + 涨跌停/炸板/溢价等背景 | 赚钱效应扩散 | 高标瓦解、跌停/炸板扩散、正负锚同弱 |

每类只设主锚和必要负锚；同一股票兼任只留一卡。锚必须能改变行动，否则删除。盘中不换最能支持原观点的锚。

四种情景：正常、超预期、低于预期、无法判断。每种写可观察表现和对应动作，不写“必修复/必退潮”。

### 第 4 段：主线与角色复核

- T-1 主线是 new/stable/strengthening/weakening/unknown 的哪一类；
- 隔夜公告是否改变逻辑，媒体转述是否尚未核原文；
- 龙头、先锋、中军、容量、跟风在竞价中的相对表现；
- 候选池不能代表全市场或板块竞价广度，缺失就标未知。

### 第 5 段：候选逐只条件表

每只必须含：

- 代码/名称/角色/原始入选与 T-1 快照血缘；
- T-1 eligibility、position_pct、position_basis、expectations；
- 新增公告与证据等级；
- 竞价 gap/ratio/turnover/转换/veto/data_quality；
- confirmed / weakened / invalidated / unknown 的触发与 allowed_actions；
- 09:30 后验证、取消条件和持仓退出规则归属。

动作交集规则：[1][2][3][10]

- position=null/0 或 eligibility 非 eligible：不新建；0 不等于清仓；
- 新硬风险、市场/账户闸门关闭、auction veto、不可成交：取消或禁止新建；
- 数据 unknown：observe_only，不把缺数据当看空；
- confirmed 仍需价格可执行与账户闸门，不自动买；
- weakened 只允许已有 downgrade_half/wait_pullback；
- 高于预期上沿只列追价警戒/等待复核，不发明新买点；
- 持仓退出走现有 sell 规则，不与“取消新建”混写。

### 第 6 段：组合与执行约束

汇总新开仓上限、同主线相关性、已有持仓、涨跌停/一字板、滑点与 T+1。明确哪些是观察、哪些允许进入开盘验证，避免所有候选都成为“待命买入”。

### 第 7 段：开盘后验证时间表

- 09:25：正式开盘、竞价末点、转换、veto；
- 09:30–10:00：实际分时量价、收复/跌破、板块共振、可成交性；
- 之后：封板/炸板/破昨收/退出规则；
- 盘后：计划 vs 实际 vs 执行，区分市场、模式、执行、数据问题。

系统若只在盘后计算反馈，就明确“人工/Agent 对照刻度”，不宣称实时已判定。

### 第 8 段：无法交易与盘后归因

“无合格候选”“正确回避”是有效结果。记录漏报、误报、数据缺失和新闻证伪，不根据单日收益倒改盘前事实。

## 4. 推荐实现分层

1. **只读 `premarket` 上下文命令**：输出 prep result_json、auction analysis_json、表 meta、pool/fallback/config 和结构化缺口；create=False、无网络、无重算。它是数据 Adapter，不是新策略。
2. **Skill 外部证据流程**：按上述信源矩阵采集、核验、去重；需要登录时提示用户，不逆向、不伪造。
3. **固定 Markdown 模板**：事实/工作假设/行动三段分离，所有动作可追溯到字段或 URL。
4. **测试**：PIT、缓存、缺失、冲突、四态交集、组合、新闻版本、全市场不可得、不得自动交易。

不采用：prep 重算冒充只读、Agent 直查 SQL、仅 CLI 摘要当完整证据、新评分/新买卖规则、统一 `.agents` 式的外部源假设。[1][3]

## 5. 非显而易见的关键结论

- **少一个可靠否决条件，比多一个看多指标更重要。**
- **新鲜的未知优于过期的确定。** 旧宏观与缓存必须保留原时点，不得刷新成“今天”。
- **外围和新闻只调整情景与风险预算，不直接给个股加分。** 个股行动仍由 prep 闸门、冻结预期和 auction 证据决定。
- **模板允许“不交易”。** 如果所有积极字段都填上，预案反而失去风险约束意义。

## 6. 局限与风险

- 外部官网后端无长期 SLA/配额保证，版权与再分发需另行遵守；运行时可能降级。
- 各 Agent 的网页工具不同，登录态只能由用户授权；无工具时必须明示缺口。
- 无全市场竞价、实时均线、真实订单、实际账户持仓成本，不能补造。
- 当前策略和 auction 均是待验证基线；报告可审计不等于信号有预测力或收益保证。

## References

[1] dragon-quant prep service and v0.8 snapshot contract. https://github.com/gitBingxu/dragon-quant/blob/main/dragon_quant/review_account/prep/service.py
[2] dragon-quant strategy_context projection. https://github.com/gitBingxu/dragon-quant/blob/main/dragon_quant/review_account/prep/review/context.py
[3] dragon-quant auction raw factors. https://github.com/gitBingxu/dragon-quant/blob/main/dragon_quant/review_account/auction/features.py
[4] dragon-quant auction context and CLI projection. https://github.com/gitBingxu/dragon-quant/blob/main/dragon_quant/review_account/auction/context.py
[5] FRED S&P 500 daily close. https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500
[6] SGX FTSE China A50 futures contract data. https://api.sgx.com/derivatives/v1.0/contract-code/CN?order=asc&orderby=delivery-month&category=futures
[7] US Treasury daily yield curve. https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=2026
[8] 财联社电报. https://www.cls.cn/telegraph
[9] 上交所上市公司公告. https://www.sse.com.cn/disclosure/listedinfo/announcement/
[10] FINRA, stop-order risks. https://www.finra.org/investors/insights/stop-orders-factors-consider-during-volatile-markets
[11] Nikkei official daily data. https://indexes.nikkei.co.jp/nkave/historical/nikkei_stock_average_daily_en.csv
[12] KRX official market data. https://global.krx.co.kr/main/main.jspx?bld=index_list
[13] LBMA prices and data. https://www.lbma.org.uk/prices-and-data
[14] 中国政府网政策库. https://www.gov.cn/zhengce/zhengceku/
[15] X API rate limits. https://docs.x.com/x-api/fundamentals/rate-limits
