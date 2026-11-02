# Evidence Registry — 盘前预案

## A. 一手机制与仓库契约（核心依据）
- `dragon_quant/review_account/prep/service.py`、`compute.py`、`review/*`：prep v0.8 权威字段、冻结预期、市场/主线/锚/仓位。
- `dragon_quant/review_account/auction/serialize.py`、`context.py`、`features.py`、`events.py`：auction v2 完整证据、veto、data_quality、PIT。
- `dragon_quant/review_account/strategy/STRATEGY.md`：账户与退出规则。
- `docs/agent/prep-auction.md`、`docs/agent/factor.md`：表、因子、缺失与重复计数。
- `docs/solutions/2026-10-28-prep-review-product-design.md`：四态条件、组合约束与反馈。
- SSE 2026 rules notice: https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml

## B. 外围事实源
- FRED SP500 / NASDAQCOM / DGS10：官方汇编日频；保存观测日，空值非0。
- SGX CN futures：官方合约月/会话/最后价/结算/更新时间；官网后端非 SLA 公共 API。
- Nikkei official daily CSV：官方 EOD；盘前当日只能另标盘中。
- KRX Open API / snapshot：官方；snapshot 无时点需降级。
- US Treasury yield XML：官方 CMT 日频，不是实时成交。
- LBMA/CME：官方基准/结算；机读失败时人工或第三方降级，口径不混用。

## C. 政策、公告与媒体
- 国务院与发文机关、SSE/SZSE/BSE、巨潮：政策与公司事实一手来源。
- 财联社 https://www.cls.cn/telegraph：媒体发现线索，非一手政策证据；无公开 API。
- X 官方 rate-limit 文档：仅说明获取约束，帖子本身只作待核实线索。

## D. 辅助方法
- FINRA stop-order risks：https://www.finra.org/investors/insights/stop-orders-factors-consider-during-volatile-markets
- `docs/bilibili/prep/**`、`docs/bilibili/auction/**`：仅作观察框架，阈值/胜率不作为事实。

## Rejected as factual anchors
- 搜索/AI 摘要、财经博客、自媒体、群聊、截图、蓝V、转发量、私有 APP 逆向接口。
