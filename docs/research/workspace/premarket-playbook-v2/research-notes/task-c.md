---
task_id: c
role: Evidence and market-structure analyst
objective: 核实 if-then planning 的行为科学证据与 A 股集合竞价的官方规则
status: complete
confidence: high for rules, medium for behavioral extrapolation
sources_found: 5
acceptance_met: yes
---

## Sources

[1] Gollwitzer & Sheeran (2006), Implementation Intentions and Goal Achievement: A Meta-Analysis of Effects and Processes | https://cancercontrol.cancer.gov/sites/default/files/2020-06/goal_intent_attain.pdf | Peer-reviewed meta-analysis | 2006
[2] 中国投资者网 — 沪深交易所交易规则对比（PDF） | https://www.investor.org.cn/xxzx/tjzl/tjnrgmjytx/bk/kj/jyxl_3455/202303/P020230307500878344055.pdf | Official investor education | 2023
[3] 上海证券交易所交易规则（2018 修订）相关通知转载 | https://jrj.sh.gov.cn/YWTBZCCX166/20180809/0031-152582.html | Government repost of SSE notice | 2018
[4] 中国证券业协会 — 什么是集合竞价与连续竞价？ | https://www.sac.net.cn/tzzzj/zxhd/tzabc/nwwd/201808/t20180807_35564.html | Official investor education | 2018
[5] 期货日报 — 上交所修订交易规则，对股票交易实施收盘集合竞价 | http://www.qhrb.com.cn/articles/230950 | Industry media reporting official rule | 2018

## Findings (facts only)

- Gollwitzer & Sheeran (2006) 的元分析覆盖 94 个独立检验（N≈8,461），发现“if situation Y, then I will do X”式的 implementation intentions 对目标达成有中等偏大效应（d=.65），并支持“特定机会的可及性提高”和“线索反应自动化”两个机制。[1]
- 该元分析的研究领域是健康行为、情绪调节、认知任务等，不是实盘交易；把结论外推到交易属于理论推导，不是直接实证。[1]
- 上交所/深交所开盘集合竞价时间为 9:15–9:25；9:15–9:20 可挂单可撤单，9:20–9:25 可挂单但不可撤单；9:25 集中撮合产生开盘价；9:25–9:30 只接受委托，不处理成交与撤单。[2][3][4][5]
- 集合竞价成交价按“最大成交量”原则确定；上交所与深交所在多个价格同时满足时的取舍规则不同（上交所取未成交量最小/中间价，深交所取距前收最近价）。[2][3][4]
- 收盘集合竞价：深市 14:57–15:00 不可撤单；沪市收盘价规则另有规定（本项目不属于盘前模板范围，仅作边界）。[2][5]

## Analysis

- “if-then”不是交易有效性证据，但它是**降低临场决策负荷**的可靠设计原则：把“看到什么 → 做什么”提前编码，减少盘中临时判断。[1]
- 由于交易领域缺少直接实证，模板应把 if-then 当作“执行纪律工具”，而不是“提高胜率的方法”；不能据此承诺收益。
- 集合竞价的制度细节直接决定盘前模板的时间表：9:15–9:20 的挂单可能是虚假的，9:20–9:25 才是真实委托，9:25 才是终态，9:25–9:30 不是新的竞价窗口。[2][3][4]
- 因此，模板必须把“09:20 前观察”“09:20–09:25 确认真实委托”“09:25 读取终态”“09:30 后验证”分开，不能用 09:25 前的虚拟匹配价当作成交事实。

## Support snippets / paraphrases for top claims

- Claim: if-then plans 有中等偏大效应。
  Source: [1]
  Support: 94 tests, d=.65；机制包括机会线索可及性与线索反应自动化。
- Claim: 9:20–9:25 不可撤单，9:25 才撮合。
  Source: [2][3][4]
  Support: 交易所规则与投资者教育材料一致列示。
- Claim: 9:25–9:30 只接受委托、不处理成交与撤单。
  Source: [2]
  Support: 中国投资者网对比表。

## Conflicts / unresolved issues

- 行为科学证据与交易场景之间存在外推缺口；不能声称“if-then 提高交易收益”。
- 不同交易所的成交价细则不同，模板不需要复制细则，只需引用“09:25 终态”语义。

## Leads discovered

- 可以把“implementation intentions”作为模板设计原则写进方案，但不要写进用户报告的事实栏。
- 模板的时间表应以交易所规则为骨架，以 practitioner 的观察项为内容。

## Gaps

- 未找到针对 A 股短线交易者的 if-then 计划实证研究。
- 未覆盖科创板/创业板盘后固定价格交易等特殊机制；盘前模板不需要。
