---
task_id: b
role: Premarket methodology researcher
status: complete
confidence: high
---

# 盘前方法

## Sources
[1] 2026-10-28 prep review product design | docs/solutions/2026-10-28-prep-review-product-design.md | 冻结预期、四态、组合/闸门
[2] STRATEGY.md | dragon_quant/review_account/strategy/STRATEGY.md | 账户与退出边界
[3] prep/auction methodology | archived_md/auction/集合竞价强度判断方法论.md | 09:25 PIT
[4] SSE 2026 trading rules notice | https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml | 官方规则入口
[5] FINRA stop-order risks | https://www.finra.org/investors/insights/stop-orders-factors-consider-during-volatile-markets | 执行风险

## Findings
- 市场/账户风险闸门先于个股；高分不能解除公告、停牌、不可成交或账户否决。[1][2]
- 盘前只冻结条件；正式开盘缺口、转换、量价和开盘后承接必须到对应时点判定。[3][4]
- 单票仓位独立，不能机械加成组合；空仓/回避是有效结果。[1]
- 止损价不保证成交价，T 日新买还受交收约束。[4][5]

## Analysis
推荐八段：数据边界 → 市场/账户闸门 → 三锚与情景 → 主线角色 → 逐只四态 → 组合执行 → 开盘验证 → 盘后归因。
三锚按决策问题去重：短线=主线龙头/先锋及高标，趋势=宽基+主线容量，情绪=正负锚与市场广度；不是现有字段原名，而是投影规则。
行动写“时点+条件+动作+上限/取消”，沿用 confirmed/weakened/invalidated/unknown 和 allowed_actions，不发明另一套买卖规则。

## Failure modes
- 高开当强、低开当必弱、候选池当全市场、匹配量当买入、缺失写0/中性、重复锚制造假共振、盘中换锚合理化。
